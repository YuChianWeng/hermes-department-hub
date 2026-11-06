"""FastAPI backend: authorization, refusal-by-design, minimal data, no writes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from department_lib.dashboard import DashboardService
from department_lib.store import Store
from tests.support import SHA, dept, event, produce, run, task, FakeBoard

ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api():
    """Import dashboard/plugin_api.py exactly like the gateway does (by path)."""
    path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("department_hub_plugin_api_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def api(monkeypatch, tmp_path):
    module = _load_plugin_api()
    store = Store(tmp_path / "state.db")
    d = dept()
    store.register_department(d)
    for tid, role in (("t1", "root"), ("t2", "work")):
        store.bind_task(d.dept_id, tid, role=role)
    produce(store, "t1")

    board = FakeBoard({
        "t1": task("t1", "done", events=[
            event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
            event("claimed", 201, 6, source_status="review"),
            event("completed", 300, 6),
        ], runs=[run(5, "worker-a", "review_requested", outcome="review_requested", artifact_sha256=SHA),
                 run(6, "reviewer-a", "completed", outcome="completed",
                     summary="REVIEW: PASS", artifact_sha256=SHA)]),
        "t2": task("t2", "blocked", events=[
            event("blocked", 400, None, reason="waiting for the operator key")]),
    })
    svc = DashboardService(store, board)
    monkeypatch.setattr(module, "build_service", lambda settings=None: svc)

    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/department-hub")
    return module, app, store, svc


def client(app) -> TestClient:
    return TestClient(app)


# ── reads ───────────────────────────────────────────────────────────────────

def test_list_departments_returns_kanban_sourced_snapshot(api):
    _module, app, _store, _svc = api
    response = client(app).get("/api/plugins/department-hub/departments")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["source"] == "kanban"
    assert body["departments"][0]["id"] == "dev"
    assert body["departments"][0]["work"]["state"] == "blocked"


def test_department_detail_reports_review_and_blocking(api):
    _module, app, _store, _svc = api
    response = client(app).get("/api/plugins/department-hub/departments/dev")
    assert response.status_code == 200
    body = response.json()
    tasks = {t["task_id"]: t for t in body["department"]["tasks"]}
    assert tasks["t1"]["review"]["verdict"] == "PASS"
    assert tasks["t1"]["review"]["artifact_sha256"] == SHA
    assert tasks["t2"]["blocked"]["reason"] == "waiting for the operator key"


def test_raw_missing_review_provenance_is_not_presented_as_pass(api):
    _module, app, _store, svc = api
    raw = task("t1", "done", events=[
        event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
        event("completed", 300, 6)],
        runs=[run(5, "worker-a", "review_requested"),
              run(6, "reviewer-a", "done", outcome="completed", artifact_sha256=SHA)])
    svc.board.tasks["t1"] = raw
    svc.invalidate()
    response = client(app).get("/api/plugins/department-hub/departments/dev")
    assert response.status_code == 200
    entry = next(t for t in response.json()["department"]["tasks"] if t["task_id"] == "t1")
    assert entry["review"]["verdict"] == "NONE"
    assert entry["acceptance"] is None and entry["final_report"] is None


def test_real_producer_rejection_is_presented_as_rejected_not_unreviewed(api):
    _module, app, _store, svc = api
    svc.board.tasks["t1"] = task("t1", "ready", events=[
        event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
        event("claimed", 201, 6, source_status="review"),
        event("changes_requested", 300, 6, reason="Fix the race")],
        runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
              run(6, "reviewer-a", "done", outcome="changes_requested")])
    svc.invalidate()
    response = client(app).get("/api/plugins/department-hub/departments/dev")
    assert response.status_code == 200
    entry = next(t for t in response.json()["department"]["tasks"] if t["task_id"] == "t1")
    assert entry["review"]["verdict"] == "REQUEST_CHANGES"
    assert entry["acceptance"] is None and entry["final_report"] is None


def test_unknown_department_is_404(api):
    _module, app, _store, _svc = api
    response = client(app).get("/api/plugins/department-hub/departments/nope")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_department"


def test_events_endpoint_is_bounded(api):
    _module, app, store, _svc = api
    for i in range(3):
        store.record_event(event("completed", 100 + i, i), "dev")
    response = client(app).get("/api/plugins/department-hub/departments/dev/events?limit=2")
    assert response.status_code == 200
    assert len(response.json()["events"]) == 2
    assert response.json()["limit"] == 2


def test_health_reports_read_only_and_caller(api):
    _module, app, _store, _svc = api
    body = client(app).get("/api/plugins/department-hub/health").json()
    assert body["read_only"] is True
    assert body["state"] == "ok"
    assert body["caller"]["kind"] == "loopback"      # no token on the test app
    assert body["kanban"]["available"] is True


# ── writes are refused, and nothing changes ─────────────────────────────────

def _fingerprint(store: Store) -> str:
    digest = hashlib.sha256()
    for table in ("departments", "tasks", "events", "receipts", "verdicts", "acceptances",
                  "deliveries", "escalations", "review_rounds"):
        rows = store.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        digest.update(table.encode())
        for row in rows:
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def test_create_department_is_blocked_and_writes_nothing(api):
    _module, app, store, _svc = api
    before = _fingerprint(store)
    response = client(app).post("/api/plugins/department-hub/departments",
                                json={"id": "ops", "name": "Ops"})
    assert response.status_code == 409
    body = response.json()
    assert body["blocked"] is True
    assert body["code"] == "unsafe_mutation_blocked"
    assert body["requested_id"] == "ops"
    assert body["not_attempted"]
    assert _fingerprint(store) == before
    assert "ops" not in store.departments()


def test_edit_and_delete_are_blocked(api):
    _module, app, store, _svc = api
    before = _fingerprint(store)
    patch = client(app).patch("/api/plugins/department-hub/departments/dev", json={"name": "X"})
    delete = client(app).delete("/api/plugins/department-hub/departments/dev")
    assert patch.status_code == 409 and delete.status_code == 409
    assert patch.json()["code"] == "unsafe_mutation_blocked"
    assert delete.json()["code"] == "unsafe_mutation_blocked"
    assert _fingerprint(store) == before


def test_plan_endpoint_validates_without_writing(api):
    _module, app, store, _svc = api
    before = _fingerprint(store)
    ok = client(app).post("/api/plugins/department-hub/departments/plan", json={
        "id": "ops", "name": "Operations", "manager_profile": "manager-b",
        "members": ["worker-b"], "reviewers": ["reviewer-b"]})
    assert ok.status_code == 200
    assert ok.json()["valid"] is True
    assert "department_bind" in " ".join(ok.json()["operator_steps"])
    bad = client(app).post("/api/plugins/department-hub/departments/plan", json={
        "id": "Bad ID", "name": "x", "manager_profile": "m", "members": ["a"], "reviewers": ["b"]})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "invalid_department"
    assert _fingerprint(store) == before


# ── authorization ───────────────────────────────────────────────────────────

class _FakeRequest:
    def __init__(self, *, native=None, token=False, auth_required=False):
        class _State:
            pass
        self.state = _State()
        if native is not None:
            self.state.native_http_principal = native
        self.state.token_authenticated = token

        class _AppState:
            pass
        self.app = type("App", (), {})()
        self.app.state = _AppState()
        self.app.state.auth_required = auth_required


def test_principal_never_comes_from_the_caller(api):
    module, _app, _store, _svc = api
    assert module._principal(_FakeRequest(native={"profile_id": "/home/x/.hermes"}))["kind"] == "native"
    assert module._principal(_FakeRequest(token=True))["kind"] == "token"
    assert module._principal(_FakeRequest())["kind"] == "loopback"
    with pytest.raises(HTTPException) as err:
        module._principal(_FakeRequest(auth_required=True))
    assert err.value.status_code == 401


def test_auth_required_app_refuses_reads(api):
    _module, app, _store, _svc = api
    app.state.auth_required = True
    response = client(app).get("/api/plugins/department-hub/departments")
    assert response.status_code == 401


def test_auth_required_app_refuses_mutations_before_the_refusal(api):
    _module, app, _store, _svc = api
    app.state.auth_required = True
    response = client(app).post("/api/plugins/department-hub/departments", json={})
    assert response.status_code == 401
    

# ── minimal data ────────────────────────────────────────────────────────────

def test_responses_carry_no_personal_or_internal_fields(api):
    _module, app, _store, _svc = api
    text = json.dumps({
        "list": client(app).get("/api/plugins/department-hub/departments").json(),
        "detail": client(app).get("/api/plugins/department-hub/departments/dev").json(),
        "health": client(app).get("/api/plugins/department-hub/health").json(),
    })
    for forbidden in ("chat-secret-123", "chat_id", "manager_platform", "/home/", "workspace_path",
                      "api_server", "session_id"):
        assert forbidden not in text, forbidden


def test_board_failure_is_503_not_a_fake_empty_page(api, monkeypatch):
    module, app, store, _svc = api

    class Broken:
        def get(self, task_id):
            from department_lib.kanban_cli import BoardError
            raise BoardError("unavailable", "board database is not present")

        def health(self):
            return {"available": False, "code": "missing", "detail": "board database not present"}

        def titles(self, ids):
            return {}

    from department_lib.dashboard import DashboardService as DS
    monkeypatch.setattr(module, "build_service", lambda settings=None: DS(store, Broken()))
    body = client(app).get("/api/plugins/department-hub/departments").json()
    assert body["board"]["available"] is False
    assert body["departments"][0]["unknown_tasks"] == 2
    assert body["departments"][0]["work"]["state"] == "unknown"

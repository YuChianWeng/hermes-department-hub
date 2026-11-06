"""M2 read model: Kanban-sourced status, evidence, and honest unknowns."""
from __future__ import annotations

import json

import pytest

from department_lib.config import ConfigError
from department_lib.dashboard import DashboardService, MUTATION_CODE
from department_lib.delivery import final_key
from department_lib.store import Store
from tests.support import SHA, dept, event, produce, run, task, FakeBoard


@pytest.fixture()
def store(tmp_path):
    st = Store(tmp_path / "state.db")
    d = dept()
    st.register_department(d)
    st.bind_task(d.dept_id, "t1", role="root")
    produce(st, "t1")
    return st, d


def service(store, board, **kw):
    return DashboardService(store, board, **kw)


def passing_task(status="done"):
    return task(
        "t1", status,
        events=[
            event("created", 100),
            event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
            event("claimed", 201, 6, source_status="review"),
            event("completed", 300, 6),
        ],
        runs=[run(5, "worker-a", "review_requested", outcome="review_requested", artifact_sha256=SHA),
              run(6, "reviewer-a", "completed", outcome="completed", verdict="PASS",
                  artifact_sha256=SHA)],
    )


# ── list view ───────────────────────────────────────────────────────────────

def test_list_reports_kanban_status_and_manager(store):
    st, d = store
    svc = service(st, FakeBoard({"t1": passing_task()}))
    payload = svc.list_departments()
    assert payload["source"] == "kanban"
    assert payload["departments"][0]["manager"] == "manager-a"
    assert payload["departments"][0]["members"] == ["worker-a"]
    assert payload["departments"][0]["work"]["state"] == "done"
    assert payload["departments"][0]["work"]["last_activity_at"] == 300


def test_open_root_container_does_not_make_done_work_look_queued(store):
    st, d = store
    st.bind_task(d.dept_id, "t2", role="work")
    root = task("t1", "ready", events=[event("created", 100)])
    work = task("t2", "done", events=[event("created", 100), event("completed", 300, 6)],
                runs=[run(6, "worker-a", "completed", outcome="completed")])
    payload = service(st, FakeBoard({"t1": root, "t2": work})).list_departments()
    rollup = payload["departments"][0]["work"]
    assert rollup["state"] == "done"
    assert rollup["scope"] == "work_tasks"
    assert rollup["items_considered"] == 1
    assert rollup["by_status"] == {"done": 1}


def test_list_derives_blocked_and_reports_reason(store):
    st, _ = store
    blocked = task("t1", "blocked", events=[
        event("created", 100),
        event("blocked", 400, None, reason="needs the API key from the operator"),
    ])
    payload = service(st, FakeBoard({"t1": blocked})).list_departments()
    assert payload["departments"][0]["work"]["state"] == "blocked"
    assert payload["departments"][0]["work"]["blocked"] == 1


def test_blocked_without_reason_is_marked_unrecorded(store):
    st, d = store
    blocked = task("t1", "blocked", events=[event("blocked", 400, None)])
    detail = service(st, FakeBoard({"t1": blocked})).department("dev")
    entry = detail["department"]["tasks"][0]
    assert entry["blocked"]["recorded"] is True
    assert entry["blocked"]["reason"] is None
    assert entry["blocked"]["is_current"] is True


def test_unreadable_task_is_unknown_never_empty(store):
    st, _ = store
    svc = service(st, FakeBoard({"t1": passing_task()}, fail={"t1": "unavailable"}))
    detail = svc.department("dev")
    task_entry = detail["department"]["tasks"][0]
    assert "unknown" in task_entry
    assert task_entry["unknown"]["code"] == "unavailable"
    assert detail["department"]["work"]["state"] == "unknown"
    assert detail["department"]["unknown_tasks"] == 1
    assert detail["department"]["unknown_task_details"][0]["task_id"] == "t1"


def test_board_unavailable_is_degraded_not_empty(store):
    st, _ = store
    board = FakeBoard({"t1": passing_task()},
                      health={"available": False, "code": "missing", "detail": "board database not present"})
    payload = service(st, board).list_departments()
    assert any(d["code"] == "board_unavailable" for d in payload["degraded"])
    assert payload["board"]["available"] is False


def test_task_cap_truncates_instead_of_scanning_forever(store):
    st, d = store
    for extra in ("t2", "t3"):
        st.bind_task(d.dept_id, extra, role="work")
    board = FakeBoard({t: passing_task() for t in ("t1", "t2", "t3")})
    svc = service(st, board, limits={"max_tasks_per_department": 2})
    detail = svc.department("dev")
    assert len(detail["department"]["tasks"]) == 2
    assert detail["department"]["task_count"] == 3
    assert detail["department"]["tasks_truncated"] is True


# ── review / acceptance / delivery evidence ─────────────────────────────────

def test_review_evidence_comes_from_board_runs(store):
    st, _ = store
    detail = service(st, FakeBoard({"t1": passing_task()})).department("dev")
    review = detail["department"]["tasks"][0]["review"]
    assert review["verdict"] == "PASS"
    assert review["run_id"] == 6
    assert review["reviewer"] == "reviewer-a"
    assert review["artifact_sha256"] == SHA
    assert review["revision"] == 1


def test_request_changes_sets_revision_and_rejected_verdict(store):
    st, _ = store
    failed = task("t1", "ready", events=[
        event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
        event("claimed", 201, 6, source_status="review"),
        event("changes_requested", 300, 6, reason="artifact missing"),
    ], runs=[run(5, "worker-a", "review_requested", outcome="review_requested", artifact_sha256=SHA),
             run(6, "reviewer-a", "review", outcome="changes_requested")])
    detail = service(st, FakeBoard({"t1": failed})).department("dev")
    review = detail["department"]["tasks"][0]["review"]
    assert review["verdict"] == "REQUEST_CHANGES"
    assert review["revision"] == 2
    assert review["repair_rounds"] == 1


def test_acceptance_and_delivery_come_from_the_store(store):
    st, d = store
    st.put_verdict(verdict_id="v1", dept_id="dev", task_id="t1", run_id=6,
                   reviewer_profile="reviewer-a", revision=1, artifact_sha256=SHA,
                   verdict="PASS", note="", event_id="run:6")
    st.put_acceptance(acceptance_id="a1", dept_id="dev", task_id="t1", verdict_id="v1",
                      revision=1, artifact_sha256=SHA, manager_profile="manager-a")
    key = final_key("dev", "t1")
    st.put_delivery(key, "dev", "t1", "final", {"schema": "department-hub/final/v1"})
    svc = service(st, FakeBoard({"t1": passing_task()}))
    assert svc.list_departments()["departments"][0]["final_report"]["state"] == "pending"

    st.set_delivery_state(key, "confirmed", "read-back found")
    svc.invalidate()
    detail = svc.department("dev")
    entry = detail["department"]["tasks"][0]
    assert entry["acceptance"]["revision"] == 1
    assert entry["final_report"]["state"] == "confirmed"
    assert detail["department"]["final_report"]["state"] == "delivered"
    assert detail["department"]["final_report"]["latest_task_id"] == "t1"


def test_no_acceptance_is_reported_as_not_accepted(store):
    st, _ = store
    detail = service(st, FakeBoard({"t1": passing_task()})).department("dev")
    entry = detail["department"]["tasks"][0]
    assert entry["acceptance"] is None
    assert entry["final_report"] is None


# ── durable events ──────────────────────────────────────────────────────────

def test_durable_events_are_bounded_and_newest_first(store):
    st, d = store
    for i in range(5):
        st.record_event(event("completed", 100 + i, i), "dev")
    payload = service(st, FakeBoard({"t1": passing_task()})).department("dev")
    kinds = [e["kind"] for e in payload["events"]]
    assert len(kinds) == 5
    assert [e["at"] for e in payload["events"]] == [104, 103, 102, 101, 100]
    assert all(e["event_id"] for e in payload["events"])


def test_events_endpoint_unknown_department_raises(store):
    st, _ = store
    with pytest.raises(KeyError):
        service(st, FakeBoard({})).events("nope")


# ── cache ───────────────────────────────────────────────────────────────────

def test_snapshot_is_cached_within_ttl(store):
    st, _ = store
    clock = {"t": 1000.0}
    board = FakeBoard({"t1": passing_task()})
    svc = service(st, board, clock=lambda: clock["t"], ttl=5)
    svc.list_departments()
    svc.list_departments()
    assert board.get_calls == 1
    clock["t"] += 6
    svc.list_departments()
    assert board.get_calls == 2


# ── data minimisation ───────────────────────────────────────────────────────

def test_payload_has_no_personal_or_path_fields(store):
    st, _ = store
    svc = service(st, FakeBoard({"t1": passing_task()}))
    blob = json.dumps({"list": svc.list_departments(), "detail": svc.department("dev")})
    for forbidden in ("chat-secret-123", "chat_id", "manager_platform", "/home/", "workspace_path",
                      "api_server"):
        assert forbidden not in blob, forbidden


# ── department planning / refusal ───────────────────────────────────────────

def test_plan_validates_and_returns_operator_steps(store):
    st, _ = store
    svc = service(st, FakeBoard({}))
    plan = svc.plan_department({
        "id": "ops", "name": "Operations", "manager_profile": "manager-b",
        "members": ["worker-b"], "reviewers": ["reviewer-b"],
    })
    assert plan["valid"] is True
    assert plan["department"] == {"id": "ops", "name": "Operations", "manager": "manager-b",
                                  "members": ["worker-b"], "reviewers": ["reviewer-b"],
                                  "max_repair_rounds": 2, "active": True}
    assert "\"ops\"" in plan["config_snippet"]
    assert "chat_id" not in plan["department"]
    assert any("department_bind" in step for step in plan["operator_steps"])
    # nothing was written
    assert "ops" not in st.departments()


def test_plan_rejects_manager_that_is_also_a_reviewer(store):
    st, _ = store
    svc = service(st, FakeBoard({}))
    with pytest.raises(ConfigError):
        svc.plan_department({"id": "ops", "name": "Ops", "manager_profile": "manager-b",
                             "members": ["worker-b"], "reviewers": ["manager-b"]})


def test_mutation_refusal_is_stable_and_promises_no_write(store):
    refusal = DashboardService.mutation_refusal("create_department")
    assert refusal["blocked"] is True
    assert refusal["code"] == MUTATION_CODE
    assert refusal["not_attempted"] == ["config write", "profile install", "gateway restart",
                                        "board write"]

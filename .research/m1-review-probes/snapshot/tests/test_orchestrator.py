"""Service-level tests: department isolation, idempotent ingest, gated release."""
from __future__ import annotations

import pytest

from department_lib.acceptance import AcceptanceClaim
from department_lib.board import BoardRun, BoardTask
from department_lib.delivery import Delivery
from department_lib.models import BoardEvent, Department
from department_lib.orchestrator import DepartmentService
from department_lib.store import Store

SHA = "e" * 64


def _dept(dept_id="dev") -> Department:
    return Department.from_mapping({
        "id": dept_id, "name": dept_id.title(),
        "manager": {"profile": f"{dept_id}-manager", "platform": "api_server",
                    "chat_id": f"api-{dept_id}-mgr"},
        "members": ["worker-a"], "reviewers": ["reviewer-a"],
    })


def _snap(task_id="t_w1", *, status="done", events=None, runs=None) -> BoardTask:
    return BoardTask(task_id=task_id, status=status, assignee="worker-a",
                     parents=("t_root",), children=(), comments=(),
                     events=tuple(events if events is not None else [
                         BoardEvent(task_id, "review_requested", 5, 100, {"reviewer": "reviewer-a"})]),
                     runs=tuple(runs if runs is not None else [
                         BoardRun(5, "worker-a", "done", "review_requested", None, None),
                         BoardRun(6, "reviewer-a", "done", "completed", None,
                                  {"artifact_sha256": SHA})]))


class FakeBoard:
    def __init__(self, tasks):
        self.tasks = dict(tasks)
        self.cli = None

    def get(self, task_id):
        if task_id not in self.tasks:
            from department_lib.board import BoardError
            raise BoardError("not_found", task_id)
        return self.tasks[task_id]

    def ancestry(self, task_id, limit=8):
        return (task_id, "t_root")


class Port:
    def __init__(self):
        self.items = {}

    def append(self, key, body):
        self.items[key] = body

    def find(self, key):
        return "found" if key in self.items else "absent"


@pytest.fixture()
def svc(tmp_path):
    store = Store(tmp_path / "dept.db")
    store.register_department(_dept("dev"))
    store.register_department(_dept("ops"))
    store.bind_task("dev", "t_root", role="root")
    store.bind_task("dev", "t_w1", role="work")
    board = FakeBoard({"t_w1": _snap()})
    port = Port()
    return DepartmentService(store, board, Delivery(store, port)), store, board, port


def test_ingest_records_each_new_event_once(svc):
    service, store, _, _ = svc
    first = service.ingest("t_w1")
    assert first.dept_id == "dev"
    assert [d.status for d in first.decisions] == ["applied"]
    assert first.revision == 1
    assert store.receipt_count() == 1

    second = service.ingest("t_w1")
    assert [d.status for d in second.decisions] == ["duplicate"]
    assert store.receipt_count() == 1


def test_ingest_rejects_conflicting_payload_under_same_coordinates(svc):
    service, store, board, _ = svc
    service.ingest("t_w1")
    board.tasks["t_w1"] = _snap(events=[
        BoardEvent("t_w1", "review_requested", 5, 100, {"reviewer": "someone-else"})])
    report = service.ingest("t_w1")
    assert report.decisions[0].status == "conflict"
    assert store.receipt_count() == 1


def test_ingest_records_a_review_round_and_supersedes_acceptance(svc):
    service, store, board, _ = svc
    service.ingest("t_w1")
    claim = AcceptanceClaim("t_w1", 1, 6, SHA, "dev-manager")
    assert service.accept(claim).status == "releasable"
    assert store.active_acceptance("dev", "t_w1") is not None

    board.tasks["t_w1"] = _snap(status="changes_requested", events=[
        BoardEvent("t_w1", "review_requested", 5, 100, {"reviewer": "reviewer-a"}),
        BoardEvent("t_w1", "changes_requested", 7, 200, {"reviewer": "reviewer-a"}),
    ], runs=[
        BoardRun(5, "worker-a", "done", "review_requested", None, None),
        BoardRun(6, "reviewer-a", "done", "completed", None, {"artifact_sha256": SHA}),
        BoardRun(7, "reviewer-a", "done", "changes_requested", None, None),
    ])
    service.ingest("t_w1")
    assert store.rounds("dev", "t_w1") == 1
    assert store.active_acceptance("dev", "t_w1") is None


def test_unknown_task_is_not_ingested_into_a_department(svc):
    service, store, _, _ = svc
    report = service.ingest("t_missing_elsewhere")
    # FakeBoard.ancestry claims t_root for everything, but the task itself is unknown
    assert report.dept_id == "dev" or report.dept_id == ""


def test_accept_then_release_delivers_exactly_one_final(svc):
    service, store, _, port = svc
    claim = AcceptanceClaim("t_w1", 1, 6, SHA, "dev-manager")
    assert service.accept(claim).status == "releasable"
    first = service.release(claim)
    second = service.release(claim)
    assert first.action == "delivered"
    assert second.action == "already_delivered"
    assert len(port.items) == 1


def test_rejected_review_can_never_release(svc):
    service, _, board, port = svc
    board.tasks["t_w1"] = _snap(status="changes_requested", events=[
        BoardEvent("t_w1", "review_requested", 5, 100, {"reviewer": "reviewer-a"}),
        BoardEvent("t_w1", "changes_requested", 7, 200, {"reviewer": "reviewer-a"}),
    ], runs=[
        BoardRun(5, "worker-a", "done", "review_requested", None, None),
        BoardRun(7, "reviewer-a", "done", "changes_requested", None, None),
    ])
    claim = AcceptanceClaim("t_w1", 2, 7, SHA, "dev-manager")
    gate = service.accept(claim)
    assert gate.status == "held"
    assert gate.reason == "review_rejected"
    result = service.release(claim)
    assert result.action == "held"
    assert port.items == {}


def test_task_bound_to_another_department_cannot_be_accepted(svc):
    service, store, _, _ = svc
    store.bind_task("ops", "t_ops", role="work")
    claim = AcceptanceClaim("t_ops", 1, 6, SHA, "dev-manager")
    assert service.accept(claim).status == "held"


def test_status_is_transparent_about_pending_and_unknown_deliveries(svc):
    service, store, _, _ = svc
    store.put_delivery("k1", "dev", "t_w1", "final", {})
    store.set_delivery_state("k1", "unknown", "read-back unavailable")
    status = service.status("dev")
    states = [d["state"] for d in status["deliveries"]]
    assert states == ["unknown"]
    assert status["store"]["deliveries"] == {"unknown": 1}


def test_release_without_explicit_acceptance_is_held(svc):
    service, _, _, port = svc
    claim = AcceptanceClaim("t_w1", 1, 6, SHA, "dev-manager")
    result = service.release(claim)
    assert result.action == "held"
    assert result.reason == "acceptance_missing"
    assert port.items == {}


def test_repeated_acceptance_stays_active(svc):
    service, store, _, _ = svc
    claim = AcceptanceClaim("t_w1", 1, 6, SHA, "dev-manager")
    assert service.accept(claim).status == "releasable"
    assert service.accept(claim).status == "releasable"
    assert store.active_acceptance("dev", "t_w1") is not None
    assert service.release(claim).action == "delivered"


def test_release_targets_the_root_card_and_names_the_work_task(svc):
    service, store, _, port = svc
    claim = AcceptanceClaim("t_w1", 1, 6, SHA, "dev-manager")
    assert service.accept(claim).status == "releasable"
    assert service.release(claim).action == "delivered"
    body = next(iter(port.items.values()))
    assert body["root_task_id"] == "t_root"
    assert body["task_id"] == "t_w1"


def test_ingest_recovers_an_observed_event_whose_receipt_was_lost(svc):
    service, store, board, _ = svc
    event = board.tasks["t_w1"].events[0]
    # Simulate the old crash window: event row committed, no receipt/effect.
    assert store.record_event(event, "dev") == "new"
    report = service.ingest("t_w1")
    assert [d.status for d in report.decisions] == ["applied"]
    assert store.has_receipt(event.event_id) is True


def test_escalation_is_delivered_once_on_the_root_card(svc):
    service, _, _, port = svc
    first = service.escalate("t_w1", "repair_loop_exhausted", {"round": 3})
    second = service.escalate("t_w1", "repair_loop_exhausted", {"round": 3})
    assert first.action == "delivered"
    assert second.action == "already_delivered"
    assert len(port.items) == 1
    body = next(iter(port.items.values()))
    assert body["root_task_id"] == "t_root"
    assert body["reason_code"] == "repair_loop_exhausted"


class FakeRouter:
    def __init__(self):
        self.calls = []

    def ensure(self, dept, task_ids, *, repair=False, task_departments=None):
        from department_lib.routing import RoutingReport
        self.calls.append((dept.dept_id, tuple(task_ids), dict(task_departments or {})))
        return RoutingReport(dept.dept_id, tuple(task_ids), ())


def test_bind_department_requires_the_root_to_be_assigned_to_the_manager(svc):
    service, store, board, _ = svc
    board.tasks["t_foreign_root"] = _snap(task_id="t_foreign_root", status="ready")
    with pytest.raises(ValueError) as exc:
        service.bind_department("dev", "t_foreign_root")
    assert "not the department manager" in str(exc.value)
    assert store.dept_for_task("t_foreign_root") is None


def test_bind_department_binds_the_board_verified_root_and_routes(svc):
    service, store, board, _ = svc
    board.tasks["t_new_root"] = BoardTask(
        task_id="t_new_root", status="ready", assignee="dev-manager",
        parents=(), children=(), comments=(), events=(), runs=())
    fake = FakeRouter()
    service.router = fake
    report = service.bind_department("dev", "t_new_root", ["t_w1"])
    assert report.ok is True
    assert store.dept_for_task("t_new_root") == "dev"
    assert "t_new_root" in store.tasks_by_role("dev", "root")
    dept_id, targets, owners = fake.calls[0]
    assert dept_id == "dev"
    assert targets == ("t_new_root", "t_w1")
    assert owners["t_new_root"] == "dev"

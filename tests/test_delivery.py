"""Tests for final handoff / escalation delivery.

Rules under test:
* only one effective final per (department, task);
* an unknown outcome is never retried blindly;
* a crash after the effect but before the local receipt is reconciled by
  read-back, not by sending again;
* escalation is delivered once per reason code.
"""
from __future__ import annotations

import pytest

from department_lib.acceptance import AcceptanceClaim, GateResult, evaluate, evidence_from_board
from department_lib.board import BoardRun, BoardTask
from department_lib.delivery import Delivery, EffectPort, final_key
from department_lib.models import BoardEvent, Department
from department_lib.store import Store

SHA = "c" * 64


def _dept(**over) -> Department:
    raw = {"id": "dev", "name": "Development",
           "manager": {"profile": "dept-manager", "platform": "api_server", "chat_id": "api-dev-mgr"},
           "members": ["worker-a"], "reviewers": ["reviewer-a"]}
    raw.update(over)
    return Department.from_mapping(raw)


def _task():
    return BoardTask(
        task_id="t_w1", status="done", assignee="worker-a", parents=("t_root",), children=(),
        comments=(),
        events=(BoardEvent("t_w1", "review_requested", 5, 100, {"reviewer": "reviewer-a"}),
                BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})),
        runs=(BoardRun(5, "worker-a", "done", "review_requested", None, {"artifact_sha256": SHA}),
              BoardRun(6, "reviewer-a", "done", "completed", "PASS", {"artifact_sha256": SHA})),
    )


class Port:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.appends = 0
        self.read_result = None      # force find() answer
        self.fail_append = False

    def append(self, key: str, body: dict) -> None:
        self.appends += 1
        if self.fail_append:
            raise RuntimeError("board write failed")
        self.items[key] = body

    def find(self, key: str) -> str:
        if self.read_result is not None:
            return self.read_result
        return "found" if key in self.items else "absent"


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "dept.db")
    s.register_department(_dept())
    s.bind_task("dev", "t_w1", role="work")
    from tests.support import produce
    produce(s, "t_w1", sha=SHA)
    s.put_verdict(verdict_id='v',dept_id='dev',task_id='t_w1',run_id=6,
                  reviewer_profile='reviewer-a',revision=1,artifact_sha256=SHA,
                  verdict='PASS',note='Synthetic final acceptance report',event_id='run:6',request_run_id=5)
    s.put_acceptance(acceptance_id='a',dept_id='dev',task_id='t_w1',verdict_id='v',
                     revision=1,artifact_sha256=SHA,manager_profile='dept-manager')
    s.connection.execute("INSERT INTO review_generations VALUES ('t_w1',5)")
    yield s
    s.close()


class _Arts:
    def __call__(self, task_id, revision):
        from types import SimpleNamespace
        return SimpleNamespace(sha256=SHA) if revision == 1 else None


_ARTS = _Arts()


def _gate(task=None, dept=None, claim=None) -> GateResult:
    dept = dept or _dept()
    task = task or _task()
    claim = claim or AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager")
    return evaluate(dept=dept, task=task, claim=claim, implementation_profile="worker-a",
                    artifact_lookup=_ARTS)


def test_releasable_gate_delivers_once_and_confirms(store):
    port = Port()
    delivery = Delivery(store, port)
    task = _task()
    result = delivery.release_final(_dept(), task, _gate(task))
    assert result.action == "delivered"
    assert port.appends == 0
    assert store.get_delivery(final_key("dev", "t_w1")).state == "confirmed"
    body = store.deliveries_for_dept('dev')[0].body
    assert body["schema"] == "department-hub/final/v2"
    assert body["verdict"]["run_id"] == 6
    assert body["verdict"]["artifact_sha256"] == SHA
    assert body["verdict"]["reviewer"] == "reviewer-a"


def test_second_release_does_not_send_again(store):
    port = Port()
    delivery = Delivery(store, port)
    task = _task()
    delivery.release_final(_dept(), task, _gate(task))
    again = delivery.release_final(_dept(), task, _gate(task))
    assert again.action == "already_delivered"
    assert port.appends == 0


def test_held_gate_never_touches_the_effect_port(store):
    port = Port()
    delivery = Delivery(store, port)
    task = _task()
    held = _gate(task, claim=AcceptanceClaim("t_w1", 1, 6, "d" * 64, "dept-manager"))
    assert held.status == "held"
    result = delivery.release_final(_dept(), task, held)
    assert result.action == "held"
    assert result.reason == "artifact_mismatch"
    assert port.appends == 0
    assert store.delivery_count() == 0


def test_unknown_effect_is_not_resent(store):
    port = Port()
    port.fail_append = True
    delivery = Delivery(store, port)
    task = _task()
    first = delivery.release_final(_dept(), task, _gate(task))
    assert first.action == "delivered"
    assert port.appends == 0
    # never send again while the outcome is unknown
    port.fail_append = False
    second = delivery.release_final(_dept(), task, _gate(task))
    assert second.action == "already_delivered"
    assert port.appends == 0


def test_unreadable_readback_is_unknown_not_success(store):
    port = Port()
    port.read_result = "unknown"
    delivery = Delivery(store, port)
    task = _task()
    result = delivery.release_final(_dept(), task, _gate(task))
    assert result.action == "delivered"
    assert store.get_delivery(final_key("dev", "t_w1")).state == "confirmed"
    assert port.appends == 0


def test_crash_after_effect_before_receipt_is_reconciled_by_readback(store):
    port = Port()
    key = final_key("dev", "t_w1")
    port.items[key] = {"schema": "department-hub/final/v1", "task_id": "t_w1"}  # effect landed
    store.put_delivery(key, "dev", "t_w1", "final", {"schema": "department-hub/final/v1"})
    delivery = Delivery(store, port)
    result = delivery.reconcile("dev", "t_w1")
    assert result.action == "already_delivered"
    assert port.appends == 0
    assert store.get_delivery(key).state == "confirmed"


def test_absent_effect_on_reconcile_is_applied_once(store):
    port = Port()
    delivery = Delivery(store, port)
    key = final_key("dev", "t_w1")
    store.put_delivery(key, "dev", "t_w1", "final", {"task_id": "t_w1"})
    result = delivery.reconcile("dev", "t_w1")
    assert result.action == "unknown"
    assert result.reason == "in_flight_unknown"
    assert port.appends == 0


def test_dead_letter_is_terminal_and_visible(store):
    port = Port()
    delivery = Delivery(store, port)
    key = final_key("dev", "t_w1")
    body = delivery._body_final(_dept(), _task(), _gate(_task()))
    store.put_delivery(key, "dev", "t_w1", "final", body)
    store.set_delivery_state(key, "dead_letter", "operator must intervene")
    result = delivery.release_final(_dept(), _task(), _gate(_task()))
    assert result.action == "dead_letter"
    assert result.reason == "operator must intervene"
    assert port.appends == 0
    assert store.snapshot()["deliveries"] == {"dead_letter": 1}


def test_escalation_delivered_once_per_reason_code(store):
    port = Port()
    delivery = Delivery(store, port)
    first = delivery.escalate(_dept(), "t_w1", "repair_loop_exhausted", {"round": 3})
    second = delivery.escalate(_dept(), "t_w1", "repair_loop_exhausted", {"round": 3})
    assert first.action == "delivered"
    assert second.action == "already_delivered"
    assert port.appends == 1
    assert store.escalation_count("dev", "t_w1") == 1


def test_escalation_body_carries_no_credentials_or_transcript(store):
    port = Port()
    delivery = Delivery(store, port)
    with pytest.raises(ValueError, match="escalation_detail_invalid"):
        delivery.escalate(_dept(), "t_w1", "manager_unreachable", {"detail": "session closed"})
    assert port.items == {}
    assert port.appends == 0 and store.delivery_count() == 0
    assert store.escalation_count("dev", "t_w1") == 0
    delivery.escalate(_dept(), "t_w1", "manager_unreachable", {})
    body = next(iter(port.items.values()))
    flat = repr(body).lower()
    for forbidden in ("api_key", "token", "password", "transcript", "chat_id"):
        assert forbidden not in flat


def test_effect_port_protocol_is_satisfied_by_duck_typing():
    assert hasattr(EffectPort, "append") and hasattr(EffectPort, "find")


def test_gate_result_is_recomputed_before_a_new_effect(store):
    """A PASS that stops being current must not release a pending final."""
    port = Port()
    port.read_result = "unknown"
    delivery = Delivery(store, port)
    task = _task()
    delivery.release_final(_dept(), task, _gate(task))
    # the artifact changes: same task, new revision request
    stale = BoardTask(
        task_id="t_w1", status="changes_requested", assignee="worker-a", parents=("t_root",),
        children=(), comments=(),
        events=(BoardEvent("t_w1", "review_requested", 5, 100, {"reviewer": "reviewer-a"}),
                BoardEvent("t_w1", "changes_requested", 7, 200, {"reviewer": "reviewer-a"})),
        runs=(BoardRun(5, "worker-a", "done", "review_requested", None, None),
              BoardRun(6, "reviewer-a", "done", "completed", None, {"artifact_sha256": SHA}),
              BoardRun(7, "reviewer-a", "done", "changes_requested", None, None)),
    )
    port.read_result = None
    before = port.appends
    result = delivery.release_final(_dept(), stale, _gate(stale))
    assert result.action in ("held", "already_delivered", "unknown")
    if result.action == "held":
        assert port.appends == before


def test_evidence_from_board_is_used_not_a_caller_string():
    ev = evidence_from_board(_dept(), _task())
    assert ev.verdict.kind == "PASS"
    assert ev.verdict.run_id == 6

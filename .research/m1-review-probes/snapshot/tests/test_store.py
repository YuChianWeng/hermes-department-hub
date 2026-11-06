"""Unit tests for department_lib.store — durability, receipts, uniqueness."""
from __future__ import annotations

import sqlite3

import pytest

from department_lib.models import BoardEvent, Department
from department_lib.store import Store, StoreError


def _dept(dept_id="dev", **over) -> Department:
    raw = {
        "id": dept_id,
        "name": "Development",
        "manager": {"profile": f"{dept_id}-manager", "platform": "api_server",
                    "chat_id": f"api-{dept_id}-mgr"},
        "members": ["worker-a"],
        "reviewers": ["reviewer-a"],
    }
    raw.update(over)
    return Department.from_mapping(raw)


def _ev(task_id="t_w1", kind="completed", run_id=3, created_at=100, payload=None) -> BoardEvent:
    return BoardEvent(task_id=task_id, kind=kind, run_id=run_id, created_at=created_at,
                      payload=payload or {"summary": "ok"})


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "dept.db")
    yield s
    s.close()


def test_schema_is_created_and_wal_enabled(tmp_path):
    s = Store(tmp_path / "dept.db")
    try:
        mode = s.connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {r[0] for r in s.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert mode.lower() == "wal"
        assert {"departments", "tasks", "events", "receipts", "verdicts",
                "acceptances", "review_rounds", "deliveries", "escalations"} <= tables
    finally:
        s.close()


def test_department_roundtrip_and_listing(store):
    store.register_department(_dept("dev"))
    store.register_department(_dept("ops"))
    assert store.get_department("dev").manager_chat_id == "api-dev-mgr"
    assert sorted(store.departments()) == ["dev", "ops"]
    assert store.get_department("nope") is None


def test_register_department_replaces_same_id_but_not_other_id(store):
    store.register_department(_dept("dev"))
    store.register_department(_dept("dev", name="Development v2"))
    assert store.get_department("dev").name == "Development v2"


def test_task_binding_is_single_owner(store):
    store.register_department(_dept("dev"))
    store.register_department(_dept("ops"))
    store.bind_task("dev", "t_w1", role="work")
    assert store.dept_for_task("t_w1") == "dev"
    assert store.dept_for_task("t_unknown") is None
    with pytest.raises(StoreError) as exc:
        store.bind_task("ops", "t_w1", role="work")
    assert "already bound" in str(exc.value)


def test_task_role_is_validated(store):
    store.register_department(_dept("dev"))
    with pytest.raises(StoreError):
        store.bind_task("dev", "t_w1", role="overlord")
    with pytest.raises(StoreError):
        store.bind_task("missing-dept", "t_w1", role="work")


def test_all_tasks_and_root_task_are_bounded_views(store):
    store.register_department(_dept("dev"))
    store.bind_task("dev", "t_root", role="root")
    store.bind_task("dev", "t_w1", role="work")
    assert store.root_task("dev") == "t_root"
    assert store.all_tasks() == ["t_root", "t_w1"]
    assert store.tasks_by_role("dev", "work") == ["t_w1"]
    assert store.root_task("missing") is None


def test_record_event_new_duplicate_conflict(store):
    store.register_department(_dept("dev"))
    ev = _ev()
    assert store.record_event(ev, "dev") == "new"
    # same coordinates + same payload => duplicate, never a second row
    assert store.record_event(_ev(payload={"summary": "ok"}), "dev") == "duplicate"
    # same coordinates + different payload => conflict, still one identity
    assert store.record_event(_ev(payload={"summary": "tampered"}), "dev") == "conflict"
    rows = store.events_for_task("t_w1")
    assert len(rows) == 1
    assert rows[0].payload_digest == _ev().payload_digest
    assert rows[0].state == "conflict"


def test_receipt_is_written_once_per_event(store):
    store.register_department(_dept("dev"))
    ev = _ev()
    store.record_event(ev, "dev")
    assert store.record_receipt(ev.event_id, "dev", "t_w1", "completed", "applied", "ok") is True
    assert store.record_receipt(ev.event_id, "dev", "t_w1", "completed", "applied", "ok") is False
    assert store.has_receipt(ev.event_id) is True
    assert store.receipt_count() == 1


def test_events_and_receipts_survive_restart(tmp_path):
    path = tmp_path / "dept.db"
    s = Store(path)
    s.register_department(_dept("dev"))
    ev = _ev()
    s.record_event(ev, "dev")
    s.record_receipt(ev.event_id, "dev", "t_w1", "completed", "applied", "ok")
    s.close()

    reopened = Store(path)
    try:
        assert reopened.get_department("dev").name == "Development"
        assert reopened.record_event(_ev(), "dev") == "duplicate"
        assert reopened.has_receipt(ev.event_id) is True
    finally:
        reopened.close()


def test_transaction_rolls_back_domain_and_receipt_together(store):
    store.register_department(_dept("dev"))
    ev = _ev()
    store.record_event(ev, "dev")
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO review_rounds (dept_id, task_id, reason, created_at) "
                         "VALUES ('dev','t_w1','rejected once',1.0)")
            conn.execute("INSERT INTO receipts (event_id, dept_id, task_id, kind, decision, "
                         "detail, created_at) VALUES (?,?,?,?,?,?,?)",
                         (ev.event_id, "dev", "t_w1", "completed", "applied", "", 1.0))
            raise RuntimeError("crash before commit")
    assert store.rounds("dev", "t_w1") == 0
    assert store.has_receipt(ev.event_id) is False


def test_review_rounds_increment_and_are_bounded_queries(store):
    store.register_department(_dept("dev"))
    assert store.rounds("dev", "t_w1") == 0
    assert store.add_round("dev", "t_w1", "first rejection") == 1
    assert store.add_round("dev", "t_w1", "second rejection") == 2
    assert store.rounds("dev", "t_w1") == 2


def test_review_round_requires_known_department(store):
    with pytest.raises(StoreError):
        store.add_round("missing", "t_w1", "x")


def test_delivery_put_is_idempotent_per_key(store):
    store.register_department(_dept("dev"))
    first = store.put_delivery("k1", "dev", "t_root", "final", {"status": "accepted"})
    again = store.put_delivery("k1", "dev", "t_root", "final", {"status": "accepted"})
    assert first.delivery_key == again.delivery_key
    assert store.delivery_count() == 1
    store.set_delivery_state("k1", "confirmed", "read-back ok")
    assert store.get_delivery("k1").state == "confirmed"


def test_delivery_state_machine_rejects_unknown_state(store):
    store.register_department(_dept("dev"))
    store.put_delivery("k1", "dev", "t_root", "final", {})
    with pytest.raises(StoreError):
        store.set_delivery_state("k1", "totally-done", "")


def test_escalation_is_recorded_once_per_reason(store):
    store.register_department(_dept("dev"))
    assert store.add_escalation("dev", "t_w1", "repair_loop_exhausted", {"round": 3}) is True
    assert store.add_escalation("dev", "t_w1", "repair_loop_exhausted", {"round": 3}) is False
    assert store.add_escalation("dev", "t_w1", "manager_unreachable", {}) is True
    assert store.escalation_count("dev", "t_w1") == 2


def test_snapshot_reports_transparent_counts(store):
    store.register_department(_dept("dev"))
    ev = _ev()
    store.record_event(ev, "dev")
    store.record_receipt(ev.event_id, "dev", "t_w1", "completed", "applied", "ok")
    store.put_delivery("k1", "dev", "t_root", "final", {})
    snap = store.snapshot()
    assert snap["departments"] == 1
    assert snap["events"] == {"observed": 1}
    assert snap["receipts"] == {"applied": 1}
    assert snap["deliveries"] == {"pending": 1}


def test_store_rejects_writes_after_close(tmp_path):
    s = Store(tmp_path / "dept.db")
    s.close()
    with pytest.raises(StoreError):
        s.register_department(_dept("dev"))

"""Event admission, idempotency, ordering and visibility tests."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deptproto.board import FakeBoard
from deptproto.effects import FileJournalPort
from deptproto.events import Event
from deptproto.orchestrator import Orchestrator
from deptproto.store import Store


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class EventTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.store = Store.open(self.tmp / "dept.db")
        self.board = FakeBoard()
        self.port = FileJournalPort(self.tmp / "effects.jsonl")
        self.orch = Orchestrator(self.store, board=self.board, effect_port=self.port,
                                 session_profiles={"sess-manager": "manager",
                                                   "sess-default": "default"})
        self.dept = self.orch.register_department(
            "d", "Dept", manager_profile="manager", report_task_id="t_report")
        self.item = self.orch.register_work_item(
            "d", "t_work", role="work", owner_profile="manager", parents=["t_setup"])
        self.board.add_task("t_report")
        self.board.add_task("t_setup")
        self.board.add_task("t_work", parents=["t_setup"])
        self.p = self.tmp / "a1.txt"
        self.p.write_text("one")

    def tearDown(self):
        self.store.close()
        self._td.cleanup()

    def ev(self, eid, seq, kind, payload, item=None):
        return Event(eid, "d", item or self.item.item_id, kind, seq, payload)

    def test_same_event_id_with_different_payload_does_not_reapply(self):
        a1 = self.ev("evt-a", 1, "artifact.published", {
            "rev": 1, "sha256": sha("one"), "path": str(self.p), "producer": "w"})
        r1 = self.orch.ingest_event(a1)
        self.assertEqual(r1.disposition, "applied")
        forged = self.ev("evt-a", 1, "artifact.published", {
            "rev": 2, "sha256": sha("two"), "path": str(self.p), "producer": "w"})
        r2 = self.orch.ingest_event(forged)
        self.assertEqual(r2.disposition, "rejected")
        self.assertEqual(r2.reason, "duplicate_event")
        self.assertEqual(len(self.store.artifacts_for(self.item.item_id)), 1)
        self.assertEqual(self.store.item_status(self.item.item_id), "artifact_ready")

    def test_rejected_event_is_visible_with_reason(self):
        bad = self.ev("evt-bad", 9, "review.requested", {
            "reviewer_profile": "reviewer", "review_task_id": "t_review"})
        r = self.orch.ingest_event(bad)
        self.assertEqual(r.reason, "out_of_order")
        rej = self.store.rejections_for("d")
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej[0]["reason"], "out_of_order")
        self.assertEqual(rej[0]["event_id"], "evt-bad")

    def test_missing_subscription_is_a_dead_letter_not_processed(self):
        res = self.orch.ingest_board_event("d", {
            "external_event_id": "board-evt-1", "task_id": "t_work",
            "kind": "completed", "subscription": None,
        })
        self.assertEqual(res.disposition, "dead_letter")
        self.assertEqual(res.reason, "missing_subscription")
        dl = self.store.alerts_for("d", kind="missing_subscription")
        self.assertTrue(dl)
        self.assertEqual(self.store.pending_events("d"), [])

    def test_admission_is_not_completion(self):
        # Admit an event but do not process it yet.
        admitted = self.store.admit_event(self.ev("evt-1", 1, "artifact.published", {
            "rev": 1, "sha256": sha("one"), "path": str(self.p), "producer": "w"}))
        self.assertTrue(admitted.accepted)
        ev = self.store.pending_events("d")
        self.assertEqual(len(ev), 1)
        self.assertIsNone(ev[0]["processed_at"])
        self.assertEqual(self.store.item_status(self.item.item_id), "draft")
        # No delivery can be produced from admission alone.
        out = self.orch.deliver_final("d")
        self.assertEqual(out.status, "held")
        # Processing is the step that changes authoritative state.
        self.orch.process_pending()
        self.assertEqual(self.store.item_status(self.item.item_id), "artifact_ready")
        self.assertIsNotNone(self.store.event_row("evt-1")["processed_at"])

    def test_board_event_admission_receipt_and_processing(self):
        self.orch.ingest_event(self.ev("evt-art", 1, "artifact.published", {
            "rev": 1, "sha256": sha("one"), "path": str(self.p), "producer": "w"}))
        r = self.orch.ingest_board_event("d", {
            "external_event_id": "board-evt-2", "task_id": "t_work",
            "kind": "completed", "subscription": {"notifier_profile": "manager",
                                                  "chat_id": "sess-manager"},
        })
        self.assertEqual(r.disposition, "admitted")
        # admission alone is not a completion receipt
        self.assertFalse(self.store.board_event_processed("board-evt-2"))
        self.orch.process_pending()
        self.assertTrue(self.store.board_event_processed("board-evt-2"))
        receipts = self.store.receipts_for("d")
        final_receipt = next(x for x in receipts
                             if x["operation_key"] == "board-event:board-evt-2")
        observation = self.store.observation_for("board-evt-2")
        self.assertIsNotNone(observation)
        evidence = json.loads(final_receipt["evidence_json"])
        self.assertEqual(evidence["observation_id"], observation["observation_id"])
        self.assertEqual(evidence["artifact_sha"], observation["artifact_sha"])
        self.assertEqual(observation["rev"], 1)

    def test_wrong_owner_board_event_is_rejected_before_processing(self):
        r = self.orch.ingest_board_event("d", {
            "external_event_id": "board-evt-3", "task_id": "t_work",
            "kind": "completed", "subscription": {"notifier_profile": "default",
                                                  "chat_id": "sess-default"},
        })
        self.assertEqual(r.disposition, "dead_letter")
        self.assertEqual(r.reason, "wrong_owner")
        self.assertEqual(self.store.item_status(self.item.item_id), "draft")
        self.assertEqual(self.orch.deliver_final("d").status, "held")


if __name__ == "__main__":
    unittest.main(verbosity=2)

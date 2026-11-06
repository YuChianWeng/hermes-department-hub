"""Domain / acceptance-gate tests for the Department Hub isolated prototype.

These are the fail-first tests: written before the implementation existed.
They encode the required guarantees from the task card:

- normal path: synthetic review REQUEST_CHANGES -> rework -> fresh independent
  PASS on the current artifact -> manager acceptance -> exactly one final.
- negative paths: stale PASS, wrong artifact, unaccepted, duplicate and
  out-of-order events, wrong owner, wrong child/report edges must never send a
  final report.
- effect/receipt crash boundaries are exercised in test_crash_recovery.py.
"""
from __future__ import annotations

import hashlib
import json
import os
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


class Harness:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.store = Store.open(tmp / "dept.db")
        self.board = FakeBoard()
        self.port = FileJournalPort(tmp / "effects.jsonl")
        self.orch = Orchestrator(self.store, board=self.board, effect_port=self.port)
        self.dept = self.orch.register_department(
            "dept-eng", "Engineering", manager_profile="manager",
            report_task_id="t_report", manager_root_task_id="t_root",
        )
        self.item = self.orch.register_work_item(
            self.dept.dept_id, "t_work", role="work", owner_profile="manager",
            parents=["t_setup"],
        )
        self.board.add_task("t_report")
        self.board.add_task("t_work", parents=["t_setup"])
        self.board.add_task("t_setup")
        self.seq = 0

    def event(self, kind, payload, item_id=None):
        self.seq += 1
        return Event(
            event_id=f"evt-{self.seq:04d}",
            dept_id=self.dept.dept_id,
            item_id=item_id or self.item.item_id,
            kind=kind,
            seq_no=self.seq,
            payload=payload,
        )

    def publish(self, text="artifact-v1", rev=None):
        rev = rev if rev is not None else self.store.next_rev(self.item.item_id)
        path = self.tmp / f"artifact-{rev}.txt"
        path.write_text(text)
        return self.orch.ingest_event(self.event("artifact.published", {
            "rev": rev, "sha256": sha(text), "path": str(path), "producer": "worker",
        }))

    def request_review(self, reviewer="reviewer", task="t_review"):
        return self.orch.ingest_event(self.event("review.requested", {
            "reviewer_profile": reviewer, "review_task_id": task,
        }))

    def verdict(self, value, reviewer="reviewer", artifact_sha=None):
        return self.orch.ingest_event(self.event("review.verdict", {
            "verdict": value,
            "reviewer_profile": reviewer,
            "review_task_id": "t_review",
            "artifact_sha": artifact_sha,
        }))

    def accept(self, accepted_by="manager", verdict_id=None, artifact_sha=None):
        return self.orch.ingest_event(self.event("manager.acceptance", {
            "accepted_by": accepted_by,
            "verdict_id": verdict_id,
            "artifact_sha": artifact_sha,
        }))

    def deliver(self):
        return self.orch.deliver_final(self.dept.dept_id)

    def pass_and_accept(self):
        self.publish("draft-1")
        self.request_review()
        self.verdict("PASS")
        self.accept()

    def journal(self):
        p = self.tmp / "effects.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


class NormalPathTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.store.close()
        self._td.cleanup()

    def test_request_changes_then_fresh_pass_then_accept_then_single_final(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        r = h.verdict("REQUEST_CHANGES")
        self.assertEqual(r.disposition, "applied")
        self.assertEqual(h.store.item_status(h.item.item_id), "changes_requested")

        # Rework produces a NEW artifact and a NEW review request.
        h.publish("draft-2")
        self.assertEqual(h.store.item_status(h.item.item_id), "rework_ready")
        h.request_review()
        v = h.verdict("PASS")
        self.assertEqual(v.disposition, "applied")
        self.assertEqual(h.store.item_status(h.item.item_id), "approved")

        a = h.accept()
        self.assertEqual(a.disposition, "applied")
        self.assertEqual(h.store.item_status(h.item.item_id), "accepted")

        out = h.deliver()
        self.assertEqual(out.status, "confirmed", out)
        self.assertEqual(h.store.item_status(h.item.item_id), "reported")
        lines = h.journal()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["kind"], "final_report")
        self.assertIn("final:", lines[0]["marker"])

        # A second delivery attempt must not create a second effect.
        again = h.deliver()
        self.assertEqual(again.status, "already_delivered")
        self.assertEqual(len(h.journal()), 1)

    def test_publish_artifact_is_content_addressed_and_new_rev(self):
        h = self.h
        h.publish("one")
        h.publish("two")
        arts = h.store.artifacts_for(h.item.item_id)
        self.assertEqual([a["rev"] for a in arts], [1, 2])
        self.assertEqual(arts[0]["sha256"], sha("one"))
        self.assertEqual(arts[1]["sha256"], sha("two"))


class NegativeGateTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.store.close()
        self._td.cleanup()

    def _pass_and_accept(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        h.verdict("PASS")
        h.accept()

    def test_unaccepted_pass_never_sends_final(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        h.verdict("PASS")
        out = h.deliver()
        self.assertEqual(out.status, "held")
        self.assertEqual(out.reason, "no_acceptance")
        self.assertEqual(h.journal(), [])
        self.assertEqual(h.store.deliveries_for(h.dept.dept_id), [])

    def test_stale_pass_on_older_artifact_never_sends_final(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        h.verdict("PASS")
        h.publish("draft-2")  # newer artifact after PASS -> stale
        h.accept(verdict_id=None, artifact_sha=None)  # acceptance always binds current PASS below
        out = h.deliver()
        self.assertEqual(out.status, "held")
        self.assertIn(out.reason, {"stale_pass", "wrong_artifact", "no_acceptance"})
        self.assertEqual(h.journal(), [])

    def test_verdict_for_wrong_artifact_is_rejected(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        res = h.verdict("PASS", artifact_sha=sha("some-other-artifact"))
        self.assertEqual(res.disposition, "rejected")
        self.assertEqual(res.reason, "wrong_artifact")
        # no approval happened
        self.assertNotEqual(h.store.item_status(h.item.item_id), "approved")
        self.assertEqual(h.journal(), [])

    def test_wrong_owner_verdict_is_rejected(self):
        h = self.h
        h.publish("draft-1")
        h.request_review(reviewer="reviewer")
        res = h.verdict("PASS", reviewer="someone-else")
        self.assertEqual(res.disposition, "rejected")
        self.assertEqual(res.reason, "wrong_owner")
        self.assertNotEqual(h.store.item_status(h.item.item_id), "approved")
        self.assertEqual(h.journal(), [])

    def test_duplicate_event_is_rejected_and_state_unchanged(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        e = h.event("review.verdict", {
            "verdict": "PASS", "reviewer_profile": "reviewer",
            "review_task_id": "t_review", "artifact_sha": sha("draft-1"),
        })
        first = h.orch.ingest_event(e)
        second = h.orch.ingest_event(e)
        self.assertEqual(first.disposition, "applied")
        self.assertEqual(second.disposition, "rejected")
        self.assertEqual(second.reason, "duplicate_event")
        h.accept()
        h.deliver()
        self.assertEqual(len(h.journal()), 1)

    def test_out_of_order_event_is_rejected(self):
        h = self.h
        h.publish("draft-1")  # seq 1
        skipped = Event("evt-skip", h.dept.dept_id, h.item.item_id,
                        "review.requested", 3, {"reviewer_profile": "reviewer",
                                                "review_task_id": "t_review"})
        res = h.orch.ingest_event(skipped)
        self.assertEqual(res.disposition, "rejected")
        self.assertEqual(res.reason, "out_of_order")
        self.assertEqual(h.store.item_status(h.item.item_id), "artifact_ready")
        self.assertEqual(h.journal(), [])

    def test_acceptance_for_non_pass_verdict_is_rejected(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        h.verdict("REQUEST_CHANGES")
        # Acceptance referencing the changes-request verdict must not pass.
        v = h.store.latest_verdict(h.item.item_id)
        res = h.accept(verdict_id=v["verdict_id"], artifact_sha=v["artifact_sha"])
        self.assertEqual(res.disposition, "rejected")
        self.assertEqual(res.reason, "wrong_acceptance")
        self.assertEqual(h.journal(), [])

    def test_wrong_report_edge_blocks_final(self):
        h = self.h
        h.pass_and_accept()
        # The detached report card is wrongly linked into the work DAG.
        h.board.add_task("t_report", parents=["t_work"])
        out = h.deliver()
        self.assertEqual(out.status, "held")
        self.assertEqual(out.reason, "wrong_report_edge")
        self.assertEqual(h.journal(), [])

    def test_work_item_cannot_use_pending_manager_root_as_parent(self):
        h = self.h
        with self.assertRaises(ValueError) as cm:
            h.orch.register_work_item("dept-eng", "t_other", "work", "manager",
                                      parents=["t_root"])
        self.assertIn("wrong_parent_edge", str(cm.exception))
        self.assertEqual(h.journal(), [])

    def test_artifact_hash_mismatch_is_rejected(self):
        h = self.h
        path = h.tmp / "tampered.txt"
        path.write_text("real-content")
        res = h.orch.ingest_event(h.event("artifact.published", {
            "rev": 1, "sha256": "0" * 64, "path": str(path), "producer": "w"}))
        self.assertEqual(res.disposition, "rejected")
        self.assertEqual(res.reason, "artifact_mismatch")
        self.assertEqual(h.store.item_status(h.item.item_id), "draft")
        self.assertEqual(h.journal(), [])

    def test_review_card_must_not_be_report_card(self):
        h = self.h
        h.publish("draft-1")
        res = h.orch.ingest_event(h.event("review.requested", {
            "reviewer_profile": "reviewer", "review_task_id": "t_report",
        }))
        self.assertEqual(res.disposition, "rejected")
        self.assertEqual(res.reason, "wrong_report_edge")


class EffectUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.store.close()
        self._td.cleanup()

    def test_unknown_effect_is_not_reported_as_success(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        h.verdict("PASS")
        h.accept()
        h.port.hide_readback = True  # effect applies, read-back unavailable
        out = h.deliver()
        self.assertEqual(out.status, "unknown")
        self.assertEqual(len(h.journal()), 1)  # effect really happened once
        d = h.store.deliveries_for(h.dept.dept_id)[0]
        self.assertEqual(d["status"], "effect_unknown")
        self.assertTrue(h.store.alerts_for(h.dept.dept_id, kind="unknown_effect"))
        # Recovery with working read-back confirms the SAME effect, no duplicate.
        h.port.hide_readback = False
        rec = h.orch.recover(h.dept.dept_id)
        self.assertEqual(rec.status, "confirmed")
        self.assertEqual(len(h.journal()), 1)

    def test_permanent_unreadable_effect_becomes_dead_letter(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        h.verdict("PASS")
        h.accept()
        h.port.hide_readback = True
        h.deliver()
        for _ in range(5):
            h.orch.recover(h.dept.dept_id)
        d = h.store.deliveries_for(h.dept.dept_id)[0]
        self.assertEqual(d["status"], "dead_letter")
        self.assertTrue(h.store.alerts_for(h.dept.dept_id, kind="delivery_dead_letter"))
        # Never claim delivery happened.
        self.assertFalse([r for r in h.store.receipts_for(h.dept.dept_id)
                           if r["operation_key"].startswith("final:")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

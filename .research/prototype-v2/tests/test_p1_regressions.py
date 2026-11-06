"""Behavior-first RED tests for the six P1 findings in the v1 independent review.

These tests encode the *required* behavior, not the v1 implementation.  They
were written before the v2 fixes and were required to fail on product behavior
(not on missing imports or broken fixtures).  Each class maps to one finding in
``.research/prototype-v1-independent-review.md``.

Finding map
-----------
1. recovery bypasses the acceptance/artifact/topology gate
2. unknown effect is blindly re-sent
3. acceptance/verdict ignores declared artifact identity; mutable file is sent
4. unknown session owner is admitted and "completed"
5. report with any parent/child edge can still send a final
6. domain mutation and event receipt are not one atomic unit
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import deptproto.orchestrator as orchestrator_module
from deptproto.board import FakeBoard
from deptproto.effects import FileJournalPort
from deptproto.events import Event
from deptproto.orchestrator import Orchestrator
from deptproto.store import Store


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TimeoutAfterWritePort(FileJournalPort):
    """Effect really lands, then the caller observes a timeout."""

    def apply(self, marker, body):
        super().apply(marker, body)
        raise TimeoutError("synthetic post-write timeout")


class Harness:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.store = Store.open(tmp / "dept.db")
        self.board = FakeBoard()
        self.port = FileJournalPort(tmp / "effects.jsonl")
        self.session_profiles = {"sess-manager": "manager", "sess-reviewer": "reviewer"}
        self.orch = Orchestrator(self.store, board=self.board, effect_port=self.port,
                                 session_profiles=self.session_profiles)
        self.dept = self.orch.register_department(
            "dept-eng", "Engineering", manager_profile="manager",
            report_task_id="t_report", manager_root_task_id="t_root")
        self.item = self.orch.register_work_item(
            self.dept.dept_id, "t_work", role="work", owner_profile="manager",
            parents=["t_setup"])
        self.board.add_task("t_report")
        self.board.add_task("t_setup")
        self.board.add_task("t_work", parents=["t_setup"])
        self.seqs: dict[str, int] = {}

    def event(self, kind, payload, item_id=None):
        item_id = item_id or self.item.item_id
        self.seqs[item_id] = self.seqs.get(item_id, 0) + 1
        return Event(f"evt-{item_id[-4:]}-{self.seqs[item_id]:04d}",
                     self.dept.dept_id, item_id, kind, self.seqs[item_id], payload)

    def publish(self, text="artifact-v1", rev=None, item_id=None):
        item_id = item_id or self.item.item_id
        rev = rev if rev is not None else self.store.next_rev(item_id)
        path = self.tmp / f"artifact-{item_id[-4:]}-{rev}.txt"
        path.write_text(text)
        return self.orch.ingest_event(self.event("artifact.published", {
            "rev": rev, "sha256": sha(text), "path": str(path),
            "producer": "worker"}, item_id=item_id))

    def request_review(self, reviewer="reviewer", task="t_review", item_id=None):
        return self.orch.ingest_event(self.event("review.requested", {
            "reviewer_profile": reviewer, "review_task_id": task}, item_id=item_id))

    def verdict(self, value, reviewer="reviewer", artifact_sha=None, item_id=None):
        return self.orch.ingest_event(self.event("review.verdict", {
            "verdict": value, "reviewer_profile": reviewer,
            "review_task_id": "t_review", "artifact_sha": artifact_sha},
            item_id=item_id))

    def accept(self, accepted_by="manager", verdict_id="auto", artifact_sha="auto",
               rev="auto", item_id=None):
        item_id = item_id or self.item.item_id
        item = self.store.get_item(item_id)
        verdict = self.store.latest_pass(item_id)
        request = self.store.request_by_id(verdict["request_id"]) if verdict else None
        if verdict_id == "auto":
            verdict_id = verdict["verdict_id"] if verdict else None
        if artifact_sha == "auto":
            artifact_sha = verdict["artifact_sha"] if verdict else None
        if rev == "auto":
            rev = request["rev"] if request else None
        payload = {"accepted_by": accepted_by, "verdict_id": verdict_id,
                   "artifact_sha": artifact_sha, "rev": rev,
                   "item_id": item_id}
        return self.orch.ingest_event(self.event("manager.acceptance", payload,
                                                 item_id=item_id))

    def pass_and_accept(self, text="draft-1"):
        self.publish(text)
        self.request_review()
        self.verdict("PASS", artifact_sha=sha(text))
        return self.accept()

    def deliver(self):
        return self.orch.deliver_final(self.dept.dept_id)

    def journal(self):
        p = self.tmp / "effects.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def close(self):
        self.store.close()


class Finding1RecoveryStaleGate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def _crash_after_intent(self):
        original = orchestrator_module.crashpoint

        def stop(point):
            if point == "after_intent":
                raise RuntimeError("synthetic stop after durable intent")

        orchestrator_module.crashpoint = stop
        try:
            with self.assertRaises(RuntimeError):
                self.h.deliver()
        finally:
            orchestrator_module.crashpoint = original

    def test_recovery_does_not_revive_a_stale_intent(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        self._crash_after_intent()
        h.publish("fresh-unreviewed")          # new artifact, no review/acceptance
        gate = h.deliver()
        self.assertEqual(gate.status, "held", gate.to_dict())
        rec = h.orch.recover(h.dept.dept_id)
        self.assertIn(rec.status, {"held", "superseded"}, rec.to_dict())
        self.assertEqual(h.journal(), [])
        delivery = h.store.latest_delivery(h.dept.dept_id)
        self.assertNotEqual(delivery["status"], "confirmed")
        self.assertNotEqual(h.store.item_status(h.item.item_id), "reported")

    def test_recovery_marks_an_already_sent_effect_as_historical_when_stale(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        # Let the effect land, then die before the receipt is recorded.
        original = orchestrator_module.crashpoint

        def stop(point):
            if point == "after_effect_before_receipt":
                raise RuntimeError("synthetic stop after effect")

        orchestrator_module.crashpoint = stop
        try:
            with self.assertRaises(RuntimeError):
                h.deliver()
        finally:
            orchestrator_module.crashpoint = original
        self.assertEqual(len(h.journal()), 1)
        h.publish("fresh-unreviewed")
        rec = h.orch.recover(h.dept.dept_id)
        self.assertIn(rec.status, {"held", "superseded"}, rec.to_dict())
        self.assertEqual(len(h.journal()), 1)  # never resent
        delivery = h.store.latest_delivery(h.dept.dept_id)
        self.assertNotEqual(delivery["status"], "confirmed")
        self.assertNotEqual(h.store.item_status(h.item.item_id), "reported")
        # The historical fact is recorded, not hidden.
        self.assertTrue(h.store.has_receipt(delivery["operation_key"]))


class Finding2UnknownEffect(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_unknown_effect_is_never_resent(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        h.port.hide_readback = True
        out = h.deliver()
        self.assertEqual(out.status, "unknown", out.to_dict())
        self.assertEqual(len(h.journal()), 1)
        for _ in range(3):
            rec = h.orch.recover(h.dept.dept_id)
            self.assertEqual(rec.status, "unknown", rec.to_dict())
        self.assertEqual(len(h.journal()), 1)  # the P1 bug re-sent it 3 times
        delivery = h.store.latest_delivery(h.dept.dept_id)
        self.assertEqual(delivery["status"], "effect_unknown")
        self.assertFalse([r for r in h.store.receipts_for(h.dept.dept_id)
                          if r["operation_key"].startswith("final:")])

    def test_post_write_timeout_is_unknown_and_not_retried(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        h.port = TimeoutAfterWritePort(h.tmp / "effects.jsonl", hide_readback=True)
        h.orch.effect_port = h.port
        out = h.deliver()
        self.assertEqual(out.status, "unknown", out.to_dict())
        self.assertEqual(len(h.journal()), 1)
        for _ in range(2):
            self.assertEqual(h.orch.recover(h.dept.dept_id).status, "unknown")
        self.assertEqual(len(h.journal()), 1)


class Finding3ExactArtifactBinding(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_acceptance_must_name_the_exact_verdict_sha_and_rev(self):
        h = self.h
        h.publish("original")
        h.request_review()
        h.verdict("PASS", artifact_sha=sha("original"))
        wrong = h.accept(artifact_sha=sha("different"))
        self.assertEqual(wrong.disposition, "rejected", wrong.to_dict())
        omitted_verdict = h.accept(verdict_id=None)
        self.assertEqual(omitted_verdict.disposition, "rejected",
                         omitted_verdict.to_dict())
        omitted_sha = h.accept(artifact_sha=None)
        self.assertEqual(omitted_sha.disposition, "rejected", omitted_sha.to_dict())
        omitted_rev = h.accept(rev=None)
        self.assertEqual(omitted_rev.disposition, "rejected", omitted_rev.to_dict())
        self.assertEqual(h.journal(), [])

    def test_verdict_must_name_the_exact_sha(self):
        h = self.h
        h.publish("original")
        h.request_review()
        res = h.verdict("PASS", artifact_sha=None)
        self.assertEqual(res.disposition, "rejected", res.to_dict())
        self.assertEqual(h.store.item_status(h.item.item_id), "in_review")

    def test_cross_item_verdict_with_identical_bytes_is_rejected(self):
        h = self.h
        item2 = h.orch.register_work_item("dept-eng", "t_work2", "work",
                                          "manager", parents=["t_setup"])
        h.board.add_task("t_work2", parents=["t_setup"])
        h.publish("same-bytes", item_id=h.item.item_id)
        h.publish("same-bytes", item_id=item2.item_id)
        h.request_review(item_id=h.item.item_id)
        h.request_review(item_id=item2.item_id)
        h.verdict("PASS", artifact_sha=sha("same-bytes"), item_id=h.item.item_id)
        h.verdict("PASS", artifact_sha=sha("same-bytes"), item_id=item2.item_id)
        foreign = h.store.latest_pass(item2.item_id)
        res = h.accept(verdict_id=foreign["verdict_id"],
                       artifact_sha=sha("same-bytes"), rev=1)
        self.assertEqual(res.disposition, "rejected", res.to_dict())
        self.assertEqual(h.journal(), [])

    def test_mutating_the_recorded_artifact_fails_closed(self):
        h = self.h
        self.assertEqual(h.pass_and_accept("original").disposition, "applied")
        artifact = h.store.latest_artifact(h.item.item_id)
        Path(artifact["path"]).write_text("MUTATED AFTER APPROVAL")
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(h.journal(), [])


class Finding4UnknownOwner(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_unmapped_session_is_never_admitted_or_completed(self):
        h = self.h
        res = h.orch.ingest_board_event(h.dept.dept_id, {
            "external_event_id": "unmapped-event", "task_id": "t_work",
            "kind": "completed",
            "subscription": {"notifier_profile": "manager",
                             "chat_id": "unmapped-session"}})
        self.assertEqual(res.disposition, "dead_letter", res.to_dict())
        self.assertEqual(res.reason, "unknown_owner")
        h.orch.process_pending(h.dept.dept_id)
        self.assertFalse(h.store.board_event_processed("unmapped-event"))
        self.assertFalse([r for r in h.store.receipts_for(h.dept.dept_id)
                          if r["operation_key"].startswith("board-event:")])


class Finding5DetachedReport(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_report_linked_to_setup_cannot_send(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        h.board.link("t_setup", "t_report")
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(out.reason, "wrong_report_edge")
        self.assertEqual(h.journal(), [])

    def test_report_with_an_unknown_child_cannot_send(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        h.board.add_task("t_unknown_child")
        h.board.link("t_report", "t_unknown_child")
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(out.reason, "wrong_report_edge")
        self.assertEqual(h.journal(), [])


class Finding6Atomicity(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_verdict_and_status_are_one_unit(self):
        h = self.h
        h.publish("draft-1")
        h.request_review()
        original = h.store.set_item_status

        def fail(*args, **kwargs):
            raise RuntimeError("synthetic failure between verdict and status")

        h.store.set_item_status = fail
        res = h.verdict("PASS", artifact_sha=sha("draft-1"))
        h.store.set_item_status = original
        self.assertNotEqual(res.disposition, "applied", res.to_dict())
        self.assertIsNone(h.store.latest_verdict(h.item.item_id))
        self.assertEqual(h.store.item_status(h.item.item_id), "in_review")
        # The event is not permanently rejected; it is still pending.
        self.assertEqual(len(h.store.pending_events(h.dept.dept_id)), 1)
        # Retry after the fault is removed applies exactly once.
        h.orch.process_pending(h.dept.dept_id)
        self.assertEqual(h.store.item_status(h.item.item_id), "approved")
        self.assertEqual(len(h.store.verdicts_for(h.item.item_id)), 1)
        self.assertEqual(len(h.store.pending_events(h.dept.dept_id)), 0)
        self.assertEqual(h.journal(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

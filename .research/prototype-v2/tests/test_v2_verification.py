"""v2 verification: process-level crash atomicity and fail-closed boundaries.

The v1 review asked for more than an injected exception.  These tests kill a
real child process at each persistence boundary and inspect the reopened SQLite
state, then verify recovery applies the mutation exactly once.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import deptproto.orchestrator as orchestrator_module
from deptproto.board import FakeBoard
from deptproto.effects import FileJournalPort
from deptproto.events import Event
from deptproto.orchestrator import Orchestrator
from deptproto.store import Store
from test_p1_regressions import Harness, sha

PY = sys.executable
CRASH_RC = 137


def run_cli(args, cwd=ROOT, env_extra=None):
    env = os.environ.copy()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT),
                "DEPT_PROTOTYPE_TEST": "1"})
    if env_extra:
        env.update(env_extra)
    return subprocess.run([PY, "-m", "deptproto.cli", *args], cwd=cwd,
                          text=True, capture_output=True, env=env, timeout=120)


class AtomicMutationCrashTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def status(self):
        r = run_cli(["status", "--dir", str(self.dir), "--dept", "dept-eng"])
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _crash_at(self, point):
        r = run_cli(["verdict-crash", "--dir", str(self.dir), "--crash-at", point])
        self.assertEqual(r.returncode, CRASH_RC,
                         f"{point}: rc={r.returncode} stderr={r.stderr}")
        st = self.status()
        self.assertEqual(st["item_status"], "in_review", point)
        self.assertEqual(st["verdicts"], 0, point)
        self.assertEqual(st["event_receipts"], 2, point)   # artifact + request
        self.assertEqual(st["pending_events"], 1, point)
        self.assertEqual(st["delivery_receipts"], 0, point)

    def test_each_mutation_boundary_rolls_back_then_recovers_once(self):
        for point in ("after_verdict_insert", "after_mutation_before_receipt",
                      "after_receipt_before_processed"):
            with self.subTest(point=point):
                self._td.cleanup()
                self._td = tempfile.TemporaryDirectory()
                self.dir = Path(self._td.name)
                self._crash_at(point)
                r = run_cli(["recover-events", "--dir", str(self.dir),
                             "--dept", "dept-eng"])
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                self.assertEqual(json.loads(r.stdout)["processed"], 1)
                st = self.status()
                self.assertEqual(st["item_status"], "approved")
                self.assertEqual(st["verdicts"], 1)
                self.assertEqual(st["event_receipts"], 3)
                self.assertEqual(st["pending_events"], 0)
                again = run_cli(["recover-events", "--dir", str(self.dir),
                                 "--dept", "dept-eng"])
                self.assertEqual(json.loads(again.stdout)["processed"], 0)
                self.assertEqual(self.status()["verdicts"], 1)

    def test_crash_after_commit_is_idempotent(self):
        r = run_cli(["verdict-crash", "--dir", str(self.dir),
                     "--crash-at", "after_processed"])
        self.assertEqual(r.returncode, CRASH_RC, r.stderr)
        st = self.status()
        self.assertEqual(st["item_status"], "approved")
        self.assertEqual(st["verdicts"], 1)
        self.assertEqual(st["event_receipts"], 3)
        self.assertEqual(st["pending_events"], 0)
        again = run_cli(["recover-events", "--dir", str(self.dir),
                         "--dept", "dept-eng"])
        self.assertEqual(json.loads(again.stdout)["processed"], 0)
        self.assertEqual(self.status()["verdicts"], 1)

    def test_concurrent_recovery_applies_the_event_once(self):
        r = run_cli(["verdict-crash", "--dir", str(self.dir),
                     "--crash-at", "after_verdict_insert"])
        self.assertEqual(r.returncode, CRASH_RC, r.stderr)
        env = {**os.environ, "PYTHONPATH": str(ROOT),
               "PYTHONDONTWRITEBYTECODE": "1"}
        procs = [subprocess.Popen(
            [PY, "-m", "deptproto.cli", "recover-events",
             "--dir", str(self.dir), "--dept", "dept-eng"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env) for _ in range(2)]
        results = []
        for p in procs:
            out, err = p.communicate(timeout=120)
            results.append((p.returncode, out, err))
        self.assertTrue(all(rc == 0 for rc, _, _ in results), results)
        processed = sum(json.loads(out)["processed"] for _, out, _ in results)
        self.assertEqual(processed, 1, results)
        st = self.status()
        self.assertEqual(st["verdicts"], 1)
        self.assertEqual(st["event_receipts"], 3)


class ProcessLevelStaleEffectTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.assertEqual(run_cli(["seed", "--dir", str(self.dir)]).returncode, 0)

    def tearDown(self):
        self._td.cleanup()

    def journal(self):
        p = self.dir / "effects.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def test_effect_sent_then_stale_is_historical_not_resent(self):
        r = run_cli(["deliver", "--dir", str(self.dir), "--dept", "dept-eng",
                     "--crash-at", "after_effect_before_receipt"])
        self.assertEqual(r.returncode, CRASH_RC, r.stderr)
        self.assertEqual(len(self.journal()), 1)
        pub = run_cli(["publish", "--dir", str(self.dir),
                       "--text", "fresh-unreviewed"])
        self.assertEqual(pub.returncode, 0, pub.stderr + pub.stdout)
        gate = run_cli(["deliver", "--dir", str(self.dir), "--dept", "dept-eng"])
        self.assertEqual(json.loads(gate.stdout)["status"], "held")
        rec = run_cli(["recover", "--dir", str(self.dir), "--dept", "dept-eng"])
        out = json.loads(rec.stdout)
        self.assertEqual(out["status"], "held")
        self.assertEqual(out["reason"], "stale_after_effect")
        self.assertEqual(len(self.journal()), 1)
        st = json.loads(run_cli(["status", "--dir", str(self.dir),
                                 "--dept", "dept-eng"]).stdout)
        self.assertEqual(st["delivery"]["status"], "historical_confirmed")
        self.assertEqual(st["item_status"], "rework_ready")
        self.assertEqual(st["delivery_receipts"], 1)


class ArtifactImmutabilityTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_mutating_the_source_after_publish_does_not_change_delivered_bytes(self):
        h = self.h
        self.assertEqual(h.pass_and_accept("original").disposition, "applied")
        artifact = h.store.latest_artifact(h.item.item_id)
        Path(artifact["source_path"]).write_text("MUTATED SOURCE")
        out = h.deliver()
        self.assertEqual(out.status, "confirmed", out.to_dict())
        self.assertEqual(Path(artifact["path"]).read_text(), "original")
        body = h.journal()[0]["body"]
        payload = json.loads(body.splitlines()[1])
        self.assertEqual(payload["artifact_sha256"], sha("original"))

    def test_mutating_the_recorded_blob_fails_closed(self):
        h = self.h
        self.assertEqual(h.pass_and_accept("original").disposition, "applied")
        artifact = h.store.latest_artifact(h.item.item_id)
        Path(artifact["path"]).write_text("MUTATED BLOB")
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(out.reason, "artifact_corrupt")
        self.assertEqual(h.journal(), [])


class ReportTopologyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_board_read_error_fails_closed(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")

        class UnknownBoard(FakeBoard):
            def task_state(self, task_id):
                if task_id == "t_report":
                    return {"status": "unknown", "task": None, "error": "synthetic"}
                return super().task_state(task_id)

        board = UnknownBoard()
        board.tasks = h.board.tasks
        h.orch.board = board
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(out.reason, "board_read_unknown")
        self.assertEqual(h.journal(), [])

    def test_report_missing_fails_closed(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        del h.board.tasks["t_report"]
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(out.reason, "report_missing")
        self.assertEqual(h.journal(), [])

    def test_work_parents_must_match_the_authoritative_board(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
        h.board.add_task("t_other")
        h.board.link("t_other", "t_work")
        out = h.deliver()
        self.assertEqual(out.status, "held", out.to_dict())
        self.assertEqual(out.reason, "work_parent_mismatch")
        self.assertEqual(h.journal(), [])


class OwnerAndEffectConflictTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self._td.name))

    def tearDown(self):
        self.h.close()
        self._td.cleanup()

    def test_revoked_session_at_processing_time_produces_no_receipt(self):
        h = self.h
        h.publish("draft-1")
        r = h.orch.ingest_board_event(h.dept.dept_id, {
            "external_event_id": "revoked-event", "task_id": "t_work",
            "kind": "completed",
            "subscription": {"notifier_profile": "manager",
                             "chat_id": "sess-manager"}})
        self.assertEqual(r.disposition, "admitted")
        h.orch.session_profiles.pop("sess-manager")
        h.orch.process_pending(h.dept.dept_id)
        self.assertTrue(h.store.board_event_processed("revoked-event"))
        self.assertIsNone(h.store.observation_for("revoked-event"))
        self.assertFalse([r for r in h.store.receipts_for(h.dept.dept_id)
                          if r["operation_key"].startswith("board-event:")])
        row = h.store.board_event_row("revoked-event")
        self.assertEqual(row["disposition"], "failed")

    def test_conflicting_existing_effect_is_never_confirmed_or_resent(self):
        h = self.h
        self.assertEqual(h.pass_and_accept().disposition, "applied")
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
        line = (h.tmp / "effects.jsonl").read_text().strip()
        record = json.loads(line)
        record["body"] = "CONFLICTING BODY"
        (h.tmp / "effects.jsonl").write_text(json.dumps(record, sort_keys=True) + "\n")
        rec = h.orch.recover(h.dept.dept_id)
        self.assertEqual(rec.status, "unknown", rec.to_dict())
        self.assertEqual(rec.reason, "effect_conflict")
        self.assertEqual(len(h.journal()), 1)
        delivery = h.store.latest_delivery(h.dept.dept_id)
        self.assertEqual(delivery["status"], "effect_unknown")
        self.assertTrue(h.store.alerts_for(h.dept.dept_id, kind="effect_conflict"))
        self.assertFalse([r for r in h.store.receipts_for(h.dept.dept_id)
                          if r["operation_key"].startswith("final:")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

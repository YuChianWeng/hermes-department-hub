"""Crash-before/after-every-effect-boundary and restart-recovery tests.

Each boundary is exercised in a REAL child process that calls os._exit(137),
so SQLite transactions and the durable effect journal are what remain on disk.
A recovery process then reconciles against read-back and must never duplicate
the external effect.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CRASH_RC = 137


def run_cli(args, cwd, env_extra=None):
    env = os.environ.copy()
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT),
        "DEPT_PROTOTYPE_TEST": "1",
    })
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, "-m", "deptproto.cli", *args],
                          cwd=cwd, text=True, capture_output=True, env=env, timeout=120)


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def seed(self):
        r = run_cli(["seed", "--dir", str(self.dir)], ROOT)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def journal(self):
        p = self.dir / "effects.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def status(self):
        r = run_cli(["status", "--dir", str(self.dir), "--dept", "dept-eng"], ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def boundary_case(self, point, expected_effects_before_recovery):
        self.seed()
        r = run_cli(["deliver", "--dir", str(self.dir), "--dept", "dept-eng",
                     "--crash-at", point], ROOT)
        self.assertEqual(r.returncode, CRASH_RC,
                         f"{point}: expected crash rc, got {r.returncode}: {r.stderr}")
        self.assertEqual(len(self.journal()), expected_effects_before_recovery, point)
        # Restart: recovery must reconcile without duplicating the effect.
        r2 = run_cli(["recover", "--dir", str(self.dir), "--dept", "dept-eng"], ROOT)
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        out = json.loads(r2.stdout)
        self.assertEqual(out["status"], "confirmed", (point, out))
        self.assertEqual(len(self.journal()), 1, point)
        st = self.status()
        self.assertEqual(st["delivery"]["status"], "confirmed")
        self.assertEqual(st["delivery_receipts"], 1)
        # Idempotent after recovery.
        r3 = run_cli(["deliver", "--dir", str(self.dir), "--dept", "dept-eng"], ROOT)
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertEqual(json.loads(r3.stdout)["status"], "already_delivered")
        self.assertEqual(len(self.journal()), 1, point)

    def test_crash_after_intent_before_effect(self):
        self.boundary_case("after_intent", 0)

    def test_crash_before_effect(self):
        self.boundary_case("before_effect", 0)

    def test_crash_after_effect_before_receipt(self):
        self.boundary_case("after_effect_before_receipt", 1)

    def test_crash_after_receipt_before_confirm(self):
        self.boundary_case("after_receipt_before_confirm", 1)

    def test_crash_after_admit_before_process(self):
        # Event admission is durable, processing is not: recovery must process
        # exactly once and then permit the rest of the normal path.
        r = run_cli(["ingest-crash", "--dir", str(self.dir), "--crash-at",
                     "after_admit_before_process"], ROOT)
        self.assertEqual(r.returncode, CRASH_RC, r.stderr)
        st = self.status()
        self.assertEqual(st["item_status"], "draft")
        self.assertEqual(st["pending_events"], 1)
        r2 = run_cli(["recover-events", "--dir", str(self.dir), "--dept", "dept-eng"], ROOT)
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        st = self.status()
        self.assertEqual(st["item_status"], "artifact_ready")
        self.assertEqual(st["pending_events"], 0)
        self.assertEqual(st["artifacts"], 1)

    def test_concurrent_delivery_has_one_effect(self):
        self.seed()
        procs = []
        for _ in range(2):
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "deptproto.cli", "deliver",
                 "--dir", str(self.dir), "--dept", "dept-eng"],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}))
        outs = []
        for p in procs:
            stdout, stderr = p.communicate(timeout=120)
            outs.append((p.returncode, stdout, stderr))
        self.assertTrue(all(rc == 0 for rc, _, _ in outs), outs)
        statuses = sorted(json.loads(o)["status"] for _, o, _ in outs)
        self.assertIn("confirmed", statuses)
        self.assertEqual(len(self.journal()), 1)
        self.assertEqual(self.status()["delivery_receipts"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

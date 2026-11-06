"""Fail-before-start tests: no process may start with a path outside the lab."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deptproto.preflight import PreflightError, assert_lab_env


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.lab = Path(self._td.name).resolve()
        self.env = {
            "HOME": str(self.lab / "home"),
            "HERMES_HOME": str(self.lab / "home" / ".hermes"),
            "HERMES_KANBAN_HOME": str(self.lab / "board"),
            "HERMES_KANBAN_DB": str(self.lab / "board" / "kanban.db"),
            "XDG_CONFIG_HOME": str(self.lab / "xdg" / "config"),
            "XDG_CACHE_HOME": str(self.lab / "xdg" / "cache"),
            "XDG_DATA_HOME": str(self.lab / "xdg" / "data"),
            "XDG_STATE_HOME": str(self.lab / "xdg" / "state"),
            "XDG_RUNTIME_DIR": str(self.lab / "runtime"),
            "TMPDIR": str(self.lab / "tmp"),
            "TMP": str(self.lab / "tmp"),
            "TEMP": str(self.lab / "tmp"),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "UV_OFFLINE": "1",
            "PIP_NO_INDEX": "1",
        }

    def tearDown(self):
        self._td.cleanup()

    def test_valid_lab_environment_passes(self):
        out = assert_lab_env(self.env, self.lab)
        self.assertTrue(out["ok"])
        self.assertEqual(out["resolved_root"], str(self.lab))

    def test_production_board_path_is_rejected_before_start(self):
        env = dict(self.env, HERMES_KANBAN_DB="/home/ubuntu/.hermes/kanban.db")
        with self.assertRaises(PreflightError) as cm:
            assert_lab_env(env, self.lab)
        self.assertIn("HERMES_KANBAN_DB", str(cm.exception))

    def test_home_outside_lab_is_rejected(self):
        env = dict(self.env, HOME="/home/ubuntu")
        with self.assertRaises(PreflightError):
            assert_lab_env(env, self.lab)

    def test_symlink_escaping_lab_is_rejected(self):
        outside = Path(self._td.name + "-outside")
        outside.mkdir()
        link = self.lab / "escape"
        link.symlink_to(outside, target_is_directory=True)
        env = dict(self.env, HOME=str(link))
        with self.assertRaises(PreflightError):
            assert_lab_env(env, self.lab)
        outside.rmdir()

    def test_worker_pin_and_socket_vars_are_rejected(self):
        for key, val in [
            ("HERMES_KANBAN_TASK", "t_deadbeef"),
            ("HERMES_KANBAN_RUN_ID", "1"),
            ("HERMES_PROFILE", "normal-worker"),
            ("HERMES_GATEWAY_SESSION", "sess"),
            ("SSH_AUTH_SOCK", str(self.lab / "agent.sock")),
            ("HTTP_PROXY", "http://127.0.0.1:1"),
        ]:
            env = dict(self.env)
            env[key] = val
            with self.assertRaises(PreflightError, msg=key):
                assert_lab_env(env, self.lab)

    def test_missing_lazy_install_guards_are_rejected(self):
        env = dict(self.env)
        env.pop("HERMES_DISABLE_LAZY_INSTALLS")
        with self.assertRaises(PreflightError):
            assert_lab_env(env, self.lab)

    def test_missing_required_path_is_rejected(self):
        env = dict(self.env)
        env.pop("TMPDIR")
        with self.assertRaises(PreflightError):
            assert_lab_env(env, self.lab)


if __name__ == "__main__":
    unittest.main(verbosity=2)

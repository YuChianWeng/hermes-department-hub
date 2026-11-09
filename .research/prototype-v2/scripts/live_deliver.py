"""Live-lab delivery runner: real Kanban board effect + crash/recovery.

Used only inside the sandbox. Seeds come from live_stage.py; the report card is
a real Kanban card and the effect is a real CLI comment, read back through
`kanban show --json`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from deptproto.board import CliBoard
from deptproto.effects import BoardEffectPort
from deptproto.orchestrator import Orchestrator
from deptproto.store import Store

PROTO = Path(os.environ["PROTO_ROOT"])
LAB = Path(os.environ["PROTO_LAB"])
SRC = Path("~/.hermes/hermes-agent")
PY = str(SRC / "venv/bin/python")
CLI = str(PROTO / "scripts/hermes_cli.py")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--dept", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--crash-at")
    ap.add_argument("--recover", action="store_true")
    args = ap.parse_args()

    if args.crash_at:
        os.environ["DEPT_CRASH_AT"] = args.crash_at
        os.environ.setdefault("DEPT_CRASH_EVIDENCE",
                              str(Path(args.dir) / "crash-evidence.json"))
    directory = Path(args.dir)
    store = Store.open(directory / "dept.db")
    board = CliBoard(PY, CLI, LAB)
    port = BoardEffectPort(board, args.report, author="manager")
    session_map = {}
    meta = directory / "session-profiles.json"
    if meta.exists():
        session_map = json.loads(meta.read_text())
    orch = Orchestrator(store, board=board, effect_port=port,
                        session_profiles=session_map)
    try:
        result = orch.recover(args.dept) if args.recover else orch.deliver_final(args.dept)
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())

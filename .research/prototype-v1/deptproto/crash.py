"""Deterministic crash injection for effect/receipt boundary tests.

Set ``DEPT_CRASH_AT`` to a boundary name and the process hard-exits with 137 at
that exact point. This is a real process death: SQLite transactions roll back
or persist as committed, and whatever the effect port already wrote stays.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def crashpoint(name: str) -> None:
    if os.environ.get("DEPT_CRASH_AT") != name:
        return
    evidence = os.environ.get("DEPT_CRASH_EVIDENCE")
    if evidence:
        try:
            Path(evidence).write_text(json.dumps({
                "boundary": name,
                "pid": os.getpid(),
                "at": time.time(),
            }))
        except OSError:
            pass
    os._exit(137)

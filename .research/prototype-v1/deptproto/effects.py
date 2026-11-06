"""Effect ports for the single external side effect: the final report.

Guarantees are idempotent-apply + authoritative read-back. The prototype never
claims exactly-once: if the effect cannot be read back, the delivery is marked
``effect_unknown`` and surfaced, never reported as delivered.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


class EffectPort:
    def apply(self, marker: str, body: str) -> dict[str, Any]:
        raise NotImplementedError

    def find(self, marker: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError


class FileJournalPort(EffectPort):
    """Durable append-only journal with fsync; idempotent by marker.

    ``hide_readback`` simulates the read-back channel being unavailable after a
    successful effect, which is exactly the unknown-result case.
    """

    def __init__(self, path: Path, hide_readback: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.hide_readback = hide_readback

    def _lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def apply(self, marker: str, body: str) -> dict[str, Any]:
        existing = self.find(marker)
        if existing is not None:
            return existing
        record = {"marker": marker, "body": body, "kind": "final_report",
                  "at": time.time()}
        raw = json.dumps(record, sort_keys=True) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, raw.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def find(self, marker: str) -> Optional[dict[str, Any]]:
        if self.hide_readback:
            return None
        for rec in self._lines():
            if rec.get("marker") == marker:
                return rec
        return None


class BoardEffectPort(EffectPort):
    """Live adapter: post one comment on the detached report card via public CLI."""

    def __init__(self, board, report_task_id: str, author: str,
                 hide_readback: bool = False):
        self.board = board
        self.report_task_id = report_task_id
        self.author = author
        self.hide_readback = hide_readback

    def apply(self, marker: str, body: str) -> dict[str, Any]:
        existing = self.find(marker)
        if existing is not None:
            return existing
        self.board.post_comment(self.report_task_id, self.author, body)
        return {"marker": marker, "task_id": self.report_task_id, "author": self.author}

    def find(self, marker: str) -> Optional[dict[str, Any]]:
        if self.hide_readback:
            return None
        snap = self.board.task(self.report_task_id)
        if not snap:
            return None
        for c in snap.get("comments") or []:
            if marker in (c.get("body") or ""):
                return {"marker": marker, "task_id": self.report_task_id,
                        "author": c.get("author"), "body": c.get("body")}
        return None


def body_for(dept_id: str, delivery_id: str, report: dict[str, Any],
             marker: str) -> str:
    payload = {
        "prototype": "deptproto-v1",
        "dept_id": dept_id,
        "delivery_id": delivery_id,
        "acceptance_id": report.get("acceptance_id"),
        "verdict_id": report.get("verdict_id"),
        "artifact_sha256": report.get("artifact_sha256"),
        "review_rev": report.get("review_rev"),
        "status": "accepted",
        "note": "synthetic isolated research prototype; not a production acceptance",
        "marker": marker,
    }
    return ("DEPT-HUB PROTOTYPE FINAL (synthetic only)\n"
            + json.dumps(payload, sort_keys=True, ensure_ascii=False)
            + f"\n{marker}")

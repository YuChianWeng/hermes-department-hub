"""Effect ports with an explicit three-state read-back contract.

v1 conflated "not found" with "cannot read".  v2 never treats an unreadable
effect as absent: ``probe`` returns FOUND, ABSENT (authoritative) or UNKNOWN,
and the orchestrator only sends when ABSENT is authoritative.  Read-back also
verifies the exact target and the complete body, not a marker substring.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

FOUND = "found"
ABSENT = "absent"
UNKNOWN = "unknown"
CONFLICT = "conflict"


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class EffectProbe:
    status: str
    ref: Optional[dict[str, Any]] = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.status == FOUND


class EffectPort:
    def apply(self, marker: str, body: str) -> dict[str, Any]:
        raise NotImplementedError

    def probe(self, marker: str, expected_body: str) -> EffectProbe:
        raise NotImplementedError

    # Legacy helper kept for tests/tools; never use it to decide to re-send.
    def find(self, marker: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError


class FileJournalPort(EffectPort):
    """Durable append-only journal with fsync.

    ``hide_readback`` simulates the read-back channel being unavailable.  It
    reports UNKNOWN, never ABSENT -- the exact v1 defect.
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

    def probe(self, marker: str, expected_body: str) -> EffectProbe:
        if self.hide_readback:
            return EffectProbe(UNKNOWN, detail={"why": "read_back_unavailable"})
        try:
            records = self._lines()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return EffectProbe(UNKNOWN, detail={"why": "read_back_error",
                                                "error": repr(exc)})
        matches = [r for r in records if r.get("marker") == marker]
        if not matches:
            return EffectProbe(ABSENT, detail={"records": len(records)})
        bad = [r for r in matches if r.get("body") != expected_body]
        if bad:
            return EffectProbe(CONFLICT, ref=matches[0],
                               detail={"count": len(matches),
                                       "body_mismatch": len(bad)})
        ref = dict(matches[0])
        ref["duplicates"] = len(matches) - 1
        return EffectProbe(FOUND, ref=ref,
                           detail={"count": len(matches),
                                   "body_sha256": body_digest(expected_body)})

    def find(self, marker: str) -> Optional[dict[str, Any]]:
        if self.hide_readback:
            return None
        try:
            for rec in self._lines():
                if rec.get("marker") == marker:
                    return rec
        except (OSError, ValueError, json.JSONDecodeError):
            return None
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
        self.board.post_comment(self.report_task_id, self.author, body)
        return {"marker": marker, "task_id": self.report_task_id,
                "author": self.author, "body": body}

    def probe(self, marker: str, expected_body: str) -> EffectProbe:
        if self.hide_readback:
            return EffectProbe(UNKNOWN, detail={"why": "read_back_disabled"})
        state = self.board.task_state(self.report_task_id)
        if state["status"] == "unknown":
            return EffectProbe(UNKNOWN, detail={"why": "board_read_unknown"})
        if state["status"] == "missing":
            return EffectProbe(ABSENT, detail={"why": "report_task_missing"})
        comments = state["task"].get("comments") or []
        matches = [c for c in comments if marker in (c.get("body") or "")]
        if not matches:
            return EffectProbe(ABSENT, detail={"comments": len(comments)})
        bad = [c for c in matches
               if (c.get("body") or "") != expected_body
               or c.get("author") != self.author]
        if bad:
            return EffectProbe(CONFLICT, ref={"task_id": self.report_task_id,
                                              "author": bad[0].get("author")},
                               detail={"count": len(matches),
                                       "body_or_author_mismatch": len(bad)})
        ref = {"marker": marker, "task_id": self.report_task_id,
               "author": matches[0].get("author"), "body": matches[0].get("body")}
        return EffectProbe(FOUND, ref=ref,
                           detail={"count": len(matches),
                                   "body_sha256": body_digest(expected_body)})

    def find(self, marker: str) -> Optional[dict[str, Any]]:
        state = self.board.task_state(self.report_task_id)
        if state["status"] != "found":
            return None
        for c in state["task"].get("comments") or []:
            if marker in (c.get("body") or ""):
                return {"marker": marker, "task_id": self.report_task_id,
                        "author": c.get("author"), "body": c.get("body")}
        return None


def body_for(dept_id: str, delivery_id: str, report: dict[str, Any],
             marker: str, items: Optional[list[dict[str, Any]]] = None) -> str:
    payload = {
        "prototype": "deptproto-v2",
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
    if items is not None:
        payload["items"] = items
    return ("DEPT-HUB PROTOTYPE FINAL (synthetic only)\n"
            + json.dumps(payload, sort_keys=True, ensure_ascii=False)
            + f"\n{marker}")

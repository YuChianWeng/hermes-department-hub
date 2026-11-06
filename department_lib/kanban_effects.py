"""Board comment effect port: escalation and deprecated pre-v5 recovery only.

**v5 final delivery never uses this port.** `Delivery.release_final()` commits the
report inside the plugin's own SQLite database and never calls ``append`` or
``find``: no final board comment is written, no remote read-back is needed, and a
crash between a comment and a local commit cannot happen because there is no
comment. ``effect_fencing_unavailable`` / ``requires_upstream_fence`` are gone
from v5; there is no upstream conditional-effect protocol here to rely on.

What remains is escalation — a marked comment on the department **root** card
(the card the coordinator watches), with the body still naming the accepted work
task — and read-only recovery of deprecated pre-v5 remote intents.

``find`` is fail-closed: an empty candidate set or any incomplete board read
returns ``unknown``, never ``absent``. ``absent`` means "every candidate was
read completely and none carries the marker".
"""
from __future__ import annotations

import json
from typing import Callable, Iterable

from .kanban_cli import BoardError

__all__ = ["CommentEffectPort", "marker"]

MARKER_PREFIX = "department-hub:"


def marker(kind: str, key: str) -> str:
    return f"<!-- {MARKER_PREFIX}{kind}:{key} -->"


class CommentEffectPort:
    # No `requires_upstream_fence` attribute. It was the pre-v5 marker for the
    # removed `effect_fencing_unavailable` final hold; v5 finals never reach this
    # port, so advertising an upstream-fence requirement would mislead readers
    # (and M2) into thinking a board-level fence still gates final delivery.

    def __init__(self, cli, candidates: Iterable[str] | Callable[[], Iterable[str]] | None = None):
        self.cli = cli
        self._candidates = candidates

    def _candidate_tasks(self) -> list[str]:
        source = self._candidates
        if callable(source):
            try:
                source = source()
            except Exception:
                return []
        try:
            out: list[str] = []
            for item in source or []:
                text = str(item).strip()
                if text and text not in out:
                    out.append(text)
            return out
        except Exception:
            return []

    def append(self, key: str, body: dict) -> None:
        # Escalation path (the only v5 caller). Prefer the department root card so
        # the handoff lands where the coordinator looks; fall back to the work card
        # for a flat binding.
        task_id = body.get("root_task_id") or body.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("effect body has no root_task_id/task_id")
        # `/final/v1` is a deprecated pre-v5 marker shape: kept so recovery can
        # still describe old intents, never produced by v5 final delivery.
        kind = "final" if str(body.get("schema", "")).endswith("/final/v1") else "escalation"
        text = marker(kind, key) + "\n" + json.dumps(body, ensure_ascii=False, sort_keys=True)
        self.cli.comment(task_id, text, author="department-hub")

    def find(self, key: str) -> str:
        """Return found/absent/unknown by reading every bound candidate card."""
        candidates = self._candidate_tasks()
        if not candidates:
            # No bounded read is possible; "absent" would be an unsupported
            # claim that the effect never landed.
            return "unknown"
        for kind in ("final", "escalation"):
            needle = marker(kind, key)
            for task_id in candidates:
                try:
                    data = self.cli.show(task_id)
                except BoardError:
                    return "unknown"
                except Exception:
                    return "unknown"
                if not isinstance(data, dict) or "comments" not in data:
                    return "unknown"
                comments = data["comments"]
                if not isinstance(comments, list):
                    return "unknown"
                for comment in comments:
                    if isinstance(comment, dict) and needle in str(comment.get("body", "")):
                        return "found"
        return "absent"

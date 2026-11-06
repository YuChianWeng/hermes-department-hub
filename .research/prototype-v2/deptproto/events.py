"""Event and result value objects shared by the prototype."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Event:
    """A domain event with a stable caller-supplied event id.

    ``seq_no`` is a per-work-item monotonic sequence enforced by the store;
    gaps and replays are rejected and made visible instead of being buffered.
    """
    event_id: str
    dept_id: str
    item_id: Optional[str]
    kind: str
    seq_no: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "dept_id": self.dept_id,
            "item_id": self.item_id,
            "kind": self.kind,
            "seq_no": self.seq_no,
            "payload": self.payload,
        }


@dataclass
class Result:
    """Outcome of an admission / processing / delivery operation.

    ``disposition`` is the canonical field; ``status`` is an alias used by the
    delivery API so tests and reports can speak about delivery status directly.
    """
    disposition: str
    reason: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.disposition

    @property
    def accepted(self) -> bool:
        return self.disposition == "admitted"

    def to_dict(self) -> dict[str, Any]:
        out = {"status": self.disposition, "reason": self.reason}
        if self.detail:
            out["detail"] = self.detail
        return out

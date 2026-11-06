"""Domain models for the Department Hub plugin.

Pure data + validation. No I/O, no Hermes imports: everything here is unit
testable without a running gateway, board or profile.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "Department",
    "ConfigError",
    "canonical_json",
    "digest",
    "BoardEvent",
    "IngestDecision",
]

_DEPT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ConfigError(ValueError):
    """Raised when a department declaration is structurally invalid."""


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for every identity and payload digest.

    Key order must not change identity; non-finite floats are rejected instead
    of silently serialised as ``NaN`` (which would make two different payloads
    hash alike on some decoders).
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )


def digest(obj: Any) -> str:
    """SHA-256 of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _require_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    return value.strip()


def _require_str_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ConfigError(f"{where} must be a non-empty list of profile names")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where}[{i}] must be a non-empty profile name")
        out.append(item.strip())
    if len(set(out)) != len(out):
        raise ConfigError(f"{where} contains duplicate entries")
    return tuple(out)


@dataclass(frozen=True)
class Department:
    """One department: a manager binding plus execution and review profiles.

    A Hermes profile is **not** a security boundary. This declaration only
    binds routing targets; it grants nothing.
    """

    dept_id: str
    name: str
    manager_profile: str
    manager_platform: str
    manager_chat_id: str
    members: tuple[str, ...]
    reviewers: tuple[str, ...]
    max_repair_rounds: int = 2
    active: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Department":
        if not isinstance(raw, Mapping):
            raise ConfigError("department must be a mapping")
        dept_id = _require_text(raw.get("id"), "id")
        if not _DEPT_ID_RE.match(dept_id):
            raise ConfigError(
                "id must match ^[a-z0-9][a-z0-9_-]{0,63}$ (lowercase letters, digits, - and _)",
            )
        name = _require_text(raw.get("name"), "name")

        manager = raw.get("manager")
        if not isinstance(manager, Mapping):
            raise ConfigError("manager must be a mapping with profile/platform/chat_id")
        manager_profile = _require_text(manager.get("profile"), "manager.profile")
        manager_platform = _require_text(manager.get("platform", "api_server"), "manager.platform")
        manager_chat_id = _require_text(manager.get("chat_id"), "manager.chat_id")

        members = _require_str_list(raw.get("members"), "members")
        reviewers = _require_str_list(raw.get("reviewers"), "reviewers")

        if manager_profile in members:
            raise ConfigError("manager.profile must not also be a member (manager does not implement)")
        if manager_profile in reviewers:
            raise ConfigError("manager.profile must not also be a reviewer (manager accepts, not reviews)")
        overlap = sorted(set(members) & set(reviewers))
        if overlap:
            raise ConfigError(
                f"reviewers must stay independent of implementers; also in members: {', '.join(overlap)}",
            )

        rounds = raw.get("max_repair_rounds", 2)
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
            raise ConfigError("max_repair_rounds must be an integer >= 0")

        active = raw.get("active", True)
        if not isinstance(active, bool):
            raise ConfigError("active must be a boolean")

        return cls(
            dept_id=dept_id,
            name=name,
            manager_profile=manager_profile,
            manager_platform=manager_platform,
            manager_chat_id=manager_chat_id,
            members=members,
            reviewers=reviewers,
            max_repair_rounds=rounds,
            active=active,
        )

    def as_dict(self) -> dict:
        return {
            "id": self.dept_id,
            "name": self.name,
            "manager": {
                "profile": self.manager_profile,
                "platform": self.manager_platform,
                "chat_id": self.manager_chat_id,
            },
            "members": list(self.members),
            "reviewers": list(self.reviewers),
            "max_repair_rounds": self.max_repair_rounds,
            "active": self.active,
        }


@dataclass(frozen=True)
class BoardEvent:
    """One Kanban board event, as published by ``kanban show --json``.

    ``event_id`` is built from board coordinates only (task, kind, run,
    timestamp). The payload body is fingerprinted separately so that a replay
    with the *same* coordinates but a *different* payload is detectable as a
    conflict instead of being silently accepted as a new event.
    """

    task_id: str
    kind: str
    run_id: int | None
    created_at: int
    payload: dict = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return digest({
            "task_id": self.task_id,
            "kind": self.kind,
            "run_id": self.run_id,
            "created_at": self.created_at,
        })

    @property
    def payload_digest(self) -> str:
        return digest(self.payload)


@dataclass(frozen=True)
class IngestDecision:
    event_id: str
    status: str          # applied | duplicate | conflict | out_of_order | rejected
    reason: str = ""
    receipt_written: bool = False

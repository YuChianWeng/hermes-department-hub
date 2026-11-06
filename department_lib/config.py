"""Department configuration: parse + validate the `departments:` block.

The plugin never reads profile files. A department is a declaration that binds
one manager profile/session, execution members and independent reviewers.
"""
from __future__ import annotations

from typing import Any, Mapping

from .models import ConfigError, Department

__all__ = ["load_departments", "ConfigError"]


def load_departments(raw: Mapping[str, Any] | None) -> dict[str, Department]:
    """Return ``{dept_id: Department}``; raise :class:`ConfigError` on any invalid entry.

    Accepted shapes::

        {"departments": [ {id: dev, ...}, ... ]}
        {"departments": {id: dev, ...}}          # single-department shorthand
        None / {}                                # no departments configured
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError("plugin settings must be a mapping")
    entries = raw.get("departments")
    if entries is None:
        return {}
    if isinstance(entries, Mapping):
        if "id" not in entries:
            raise ConfigError(
                "departments must be a list of department mappings "
                "(a single department is written as one mapping with an 'id')",
            )
        entries = [entries]
    if not isinstance(entries, (list, tuple)):
        raise ConfigError("departments must be a list of department mappings")

    out: dict[str, Department] = {}
    managers: dict[str, str] = {}
    sessions: dict[tuple[str, str], str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"departments[{index}] must be a mapping")
        try:
            dept = Department.from_mapping(entry)
        except ConfigError as exc:
            raise ConfigError(f"departments[{index}]: {exc}") from exc

        if dept.dept_id in out:
            raise ConfigError(f"departments[{index}]: duplicate department id '{dept.dept_id}'")
        if dept.manager_profile in managers:
            raise ConfigError(
                f"departments[{index}]: manager profile '{dept.manager_profile}' is already bound "
                f"to department '{managers[dept.manager_profile]}'",
            )
        session_key = (dept.manager_platform, dept.manager_chat_id)
        if session_key in sessions:
            raise ConfigError(
                f"departments[{index}]: manager session {session_key[0]}:{session_key[1]} is already "
                f"bound to department '{sessions[session_key]}'",
            )
        managers[dept.manager_profile] = dept.dept_id
        sessions[session_key] = dept.dept_id
        out[dept.dept_id] = dept
    return out

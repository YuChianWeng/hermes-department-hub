"""Fail-before-start checks.

No Hermes process (CLI, gateway, model server) is allowed to start unless every
environment path resolves inside the lab root, worker pins are absent, lazy
installs are disabled, and the namespace has no external route.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

REQUIRED_PATH_FIELDS = (
    "HOME", "HERMES_HOME", "HERMES_KANBAN_HOME", "HERMES_KANBAN_DB",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP",
)

FORBIDDEN_FIELDS = (
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_PROFILE",
    "HERMES_GATEWAY_SESSION", "HERMES_DELEGATED_CHILD_CONTEXT",
    "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
)

REQUIRED_FLAGS = {
    "HERMES_DISABLE_LAZY_INSTALLS": "1",
    "UV_OFFLINE": "1",
    "PIP_NO_INDEX": "1",
}


class PreflightError(RuntimeError):
    pass


def assert_lab_env(env: Mapping[str, str], lab_root: Path) -> dict[str, Any]:
    lab = Path(lab_root).resolve()
    problems: list[str] = []
    resolved: dict[str, str] = {}
    for key in REQUIRED_PATH_FIELDS:
        value = env.get(key)
        if not value:
            problems.append(f"{key}: missing")
            continue
        p = Path(value).expanduser()
        try:
            rp = p.resolve(strict=False)
        except OSError as exc:
            problems.append(f"{key}: cannot resolve ({exc})")
            continue
        resolved[key] = str(rp)
        if not rp.is_relative_to(lab):
            problems.append(f"{key}: resolves outside lab ({rp})")
    for key in FORBIDDEN_FIELDS:
        if env.get(key):
            problems.append(f"{key}: worker/proxy/socket pin present")
    for key, want in REQUIRED_FLAGS.items():
        if env.get(key) != want:
            problems.append(f"{key}: expected {want!r}, got {env.get(key)!r}")
    if problems:
        raise PreflightError("; ".join(problems))
    return {"ok": True, "resolved_root": str(lab), "fields": resolved}


def assert_no_network() -> dict[str, Any]:
    routes = json.loads(subprocess.check_output(["ip", "-j", "route"]))
    links = json.loads(subprocess.check_output(["ip", "-j", "link"]))
    names = [i["ifname"] for i in links]
    problems = []
    if routes:
        problems.append(f"routes present: {routes}")
    if names != ["lo"]:
        problems.append(f"interfaces: {names}")
    if problems:
        raise PreflightError("; ".join(problems))
    return {"routes": routes, "interfaces": names}

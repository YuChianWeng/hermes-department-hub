"""Hermes plugin entry point: department-led orchestration for Kanban.

``register(ctx)`` performs **no** file, network or subprocess I/O: it only
registers tools and hooks. Every durable resource (the plugin's own SQLite
state, the Kanban CLI adapter, the effect port) is created lazily on the first
tool call, so plugin discovery stays cheap and side-effect free.

The caller's Hermes profile is captured from the public ``ctx.profile_name``
at registration time. Manager-only operations compare it with the configured
department binding; a model's prose claim is never used as identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["register", "build_service", "TOOL_NAMES"]

TOOL_NAMES = (
    "department_status",
    "department_ingest",
    "department_bind",
    "department_submit",
    "department_accept",
    "department_deliver",
    "department_escalate",
)

_SCHEMAS = {
    "department_status": {
        "name": "department_status",
        "description": ("Read-only snapshot of one department: bindings, bound tasks, "
                        "processing receipts, delivery state and any unresolved/unknown "
                        "delivery, including each task identity and stored report JSON (max 64 KiB each)."),
        "parameters": {
            "type": "object",
            "properties": {"department": {"type": "string", "description": "Department id"}},
            "required": ["department"],
        },
    },
    "department_ingest": {
        "name": "department_ingest",
        "description": ("Re-read one Kanban card authoritatively and record its new events "
                        "for the owning department. Duplicate events are idempotent; a "
                        "conflicting payload is rejected. Never trusts a chat claim."),
        "parameters": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Kanban task id"}},
            "required": ["task_id"],
        },
    },
    "department_bind": {
        "name": "department_bind",
        "description": ("Bind a board-verified root card (its assignee must be the "
                        "department manager) and ensure the manager wake subscription. "
                        "Optionally route additional known task ids."),
        "parameters": {
            "type": "object",
            "properties": {
                "department": {"type": "string"},
                "root_task_id": {"type": "string"},
                "task_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["department", "root_task_id"],
        },
    },
    "department_accept": {
        "name": "department_accept",
        "description": ("Manager acceptance of a review verdict. The claim is verified "
                        "against the live board: current revision, current independent "
                        "PASS run, exact producer-recorded artifact SHA "
                        "(department_submit). A failed, stale or foreign verdict is held, "
                        "never accepted. Manager profile only."),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "revision": {"type": "integer", "minimum": 1},
                "verdict_run_id": {"type": "integer", "minimum": 1},
                "artifact_sha256": {"type": "string"},
            },
            "required": ["task_id", "revision", "verdict_run_id", "artifact_sha256"],
        },
    },
    "department_deliver": {
        "name": "department_deliver",
        "description": ("Commit the single immutable final report in plugin SQLite (no board comment), "
                        "or report why it is still held. Requires the manager's prior "
                        "acceptance of this exact revision/run/SHA. Releasing twice never "
                        "sends twice. Manager profile only."),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "revision": {"type": "integer", "minimum": 1},
                "verdict_run_id": {"type": "integer", "minimum": 1},
                "artifact_sha256": {"type": "string"},
            },
            "required": ["task_id", "revision", "verdict_run_id", "artifact_sha256"],
        },
    },
    "department_escalate": {
        "name": "department_escalate",
        "description": ("Escalate a material blocker to the department root card once per "
                        "reason code. Manager-only; the escalation carries no credentials "
                        "or transcript."),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason_code": {"type": "string", "enum": ["blocked", "repair_loop_exhausted", "manager_unreachable"]},
                "detail": {"type": "object", "additionalProperties": False,
                           "properties": {"round": {"type": "integer", "minimum": 0, "maximum": 1000}}},
            },
            "required": ["task_id", "reason_code"],
        },
    },
    "department_submit": {
        "name": "department_submit",
        "description": ("Producer for the artifact identity a PASS is bound to: the plugin "
                        "reads one local regular file, computes its SHA-256 and records "
                        "(path, real path, algorithm, byte length, time) for the current "
                        "revision. A submission that never goes through this tool stays "
                        "artifact_unbound and cannot be delivered. Manager or department "
                        "member only."),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Kanban work task id"},
                "artifact_path": {"type": "string",
                                  "description": "Absolute path to the local artifact file"},
            },
            "required": ["task_id", "artifact_path"],
        },
    },
}


def _settings() -> dict:
    """Read plugin settings lazily (public config surface only)."""
    try:
        from hermes_cli.config import load_config_readonly  # public module
        cfg = load_config_readonly() or {}
    except Exception:
        return {}
    plugins = (cfg.get("plugins") or {}) if isinstance(cfg, dict) else {}
    entries = (plugins.get("entries") or {}) if isinstance(plugins, dict) else {}
    entry = entries.get("department-hub") or {}
    merged = dict(entry.get("settings") or {})
    for key in ("departments", "state_path"):
        if key in entry and key not in merged:
            merged[key] = entry[key]
    return merged


def _state_path(settings: dict) -> Path:
    explicit = settings.get("state_path")
    if isinstance(explicit, str) and explicit.strip():
        return Path(explicit).expanduser()
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home).expanduser() / "department-hub" / "state.db"


def build_service(settings: dict | None = None):
    """Construct the service stack. Called only from tool handlers."""
    from department_lib.board import Board
    from department_lib.config import load_departments
    from department_lib.delivery import Delivery
    from department_lib.kanban_cli import CliKanban
    from department_lib.kanban_effects import CommentEffectPort
    from department_lib.orchestrator import DepartmentService
    from department_lib.store import Store

    settings = _settings() if settings is None else settings
    store = Store(_state_path(settings))
    depts = load_departments({"departments": settings.get("departments") or []})
    for dept in depts.values():
        store.register_department(dept)
    cli = CliKanban()
    # Read-back is bounded to cards this plugin has bound (never a board scan),
    # and an empty candidate set is reported as `unknown`, never `absent`.
    port = CommentEffectPort(cli, candidates=store.all_tasks)
    board = Board(cli)
    return DepartmentService(store, board, Delivery(store, port))


def _claim(params: dict, manager_profile: str):
    from department_lib.acceptance import AcceptanceClaim
    return AcceptanceClaim(
        task_id=str(params["task_id"]),
        revision=int(params["revision"]),
        verdict_run_id=int(params["verdict_run_id"]),
        artifact_sha256=str(params["artifact_sha256"]),
        manager_profile=manager_profile,
    )


def _manager_context(service, task_id: str, caller_profile: str):
    """Return ``(dept, error_payload)``; both None when no binding exists."""
    dept_id = service.store.dept_for_task(task_id)
    dept = service.store.get_department(dept_id) if dept_id else None
    if dept is None:
        return None, {"success": False, "error": "task is not bound to a department"}
    if not caller_profile:
        return dept, {"success": False,
                      "error": "caller profile unavailable; manager binding cannot be verified"}
    if caller_profile != dept.manager_profile:
        return dept, {"success": False,
                      "error": (f"caller profile '{caller_profile}' is not the manager "
                                f"of department '{dept.dept_id}'")}
    return dept, None


def _handle_status(service, params, **_):
    return {"success": True, "status": service.status(str(params["department"]))}


def _handle_ingest(service, params, **_):
    report = service.ingest(str(params["task_id"]))
    bound = bool(report.dept_id)
    return {"success": bound,
            "error": None if bound else "task is not bound to a department (or the board read was unknown)",
            "task_id": report.task_id, "department": report.dept_id,
            "revision": report.revision, "board_status": report.board_status,
            "events": [{"event_id": d.event_id, "status": d.status, "reason": d.reason}
                       for d in report.decisions]}


def _handle_bind(service, params, **_) -> dict:
    task_ids = params.get("task_ids")
    if task_ids is not None and (not isinstance(task_ids, list)
                                or any(not isinstance(t, str) for t in task_ids)):
        return {"success": False, "error": "task_ids must be an array of task ids"}
    try:
        report = service.bind_department(
            str(params["department"]), str(params["root_task_id"]), task_ids)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # board read / routing failure: fail closed
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"success": report.ok, "department": report.dept_id,
            "routing_ok": report.ok, "ensured": list(report.ensured),
            "issues": [{"task_id": i.task_id, "code": i.code, "detail": i.detail}
                       for i in report.issues]}


def _handle_submit(service, params, *, caller_profile="", **_):
    task_id = str(params["task_id"])
    path = str(params["artifact_path"])
    try:
        result = service.submit_artifact(task_id, path, caller_profile=caller_profile)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return result


def _handle_accept(service, params, *, caller_profile="", **_):
    task_id = str(params["task_id"])
    dept, error = _manager_context(service, task_id, caller_profile)
    if error:
        return error
    claim = _claim(params, dept.manager_profile)
    gate = service.accept(claim)
    return {"success": gate.status == "releasable", "status": gate.status,
            # `ready_to_deliver` is the v1 readiness state: the exact acceptance
            # is durably recorded and `department_deliver` would be authorized.
            "ready_to_deliver": gate.status == "releasable",
            "reason": gate.reason, "revision": gate.revision,
            "verdict_run_id": gate.verdict_run_id,
            "artifact_sha256": gate.artifact_sha256, "reviewer": gate.reviewer_profile}


def _handle_deliver(service, params, *, caller_profile="", **_):
    task_id = str(params["task_id"])
    dept, error = _manager_context(service, task_id, caller_profile)
    if error:
        return error
    claim = _claim(params, dept.manager_profile)
    result = service.release(claim)
    return {"success": result.action in ("delivered", "already_delivered"),
            "action": result.action, "reason": result.reason, "key": result.key,
            "delivered_identity": result.delivered_identity}


def _handle_escalate(service, params, *, caller_profile="", **_):
    task_id = str(params["task_id"])
    dept, error = _manager_context(service, task_id, caller_profile)
    if error:
        return error
    detail = params.get("detail")
    from department_lib.delivery import validate_escalation
    try:
        validate_escalation(params["reason_code"], detail)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    result = service.escalate(task_id, params["reason_code"], detail)
    return {"success": result.action in ("delivered", "already_delivered"),
            "action": result.action, "reason": result.reason, "key": result.key,
            "delivered_identity": result.delivered_identity}


_HANDLERS = {
    "department_status": _handle_status,
    "department_ingest": _handle_ingest,
    "department_bind": _handle_bind,
    "department_submit": _handle_submit,
    "department_accept": _handle_accept,
    "department_deliver": _handle_deliver,
    "department_escalate": _handle_escalate,
}


def _make_handler(name: str, caller_profile: str = ""):
    def handler(params, **kwargs):
        try:
            service = build_service()
            payload = _HANDLERS[name](service, params or {},
                                      caller_profile=caller_profile, **kwargs)
        except Exception as exc:  # never crash the agent loop
            payload = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(payload, ensure_ascii=False, default=str)
    return handler


def register(ctx) -> None:
    """Register the department tools. No I/O here (discovery must stay cheap)."""
    try:
        caller_profile = str(getattr(ctx, "profile_name", "") or "")
    except Exception:
        caller_profile = ""

    for name in TOOL_NAMES:
        ctx.register_tool(
            name=name,
            toolset="department_hub",
            schema=_SCHEMAS[name],
            handler=_make_handler(name, caller_profile),
        )

    def _on_task_event(task_id=None, **_kwargs):
        # Observer-only hook: enqueue nothing durable here (no I/O at hook time).
        return None

    for hook in ("kanban_task_completed", "kanban_task_blocked"):
        try:
            ctx.register_hook(hook, _on_task_event)
        except Exception:
            pass

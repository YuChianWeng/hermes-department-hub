"""Department Hub dashboard backend — mounted at ``/api/plugins/department-hub/``.

Thin FastAPI shell over :mod:`department_lib.dashboard` (the single M1 adapter
layer). Design constraints, all covered by ``tests/test_dashboard_api.py``:

* **Read-only.** Every GET is a bounded read of the Kanban board (read-only
  SQLite URI) plus the plugin's own SQLite state. No route writes the board, a
  profile, ``config.yaml`` or a gateway setting.
* **Authorization is verified, not assumed.** The gateway already gates these
  routes; on top of that every request resolves a principal from the gateway's
  request state. A request that arrives without one on an auth-required gateway
  is refused with 401. Mutations are refused with a stable ``blocked`` code —
  this build has no safe, reversible config/profile mutation path.
* **Minimal data.** No chat ids, platform ids, workspace paths, transcripts,
  comment bodies or event payloads are served.
* **Handlers are sync ``def``** so FastAPI runs them in a worker thread; the
  gateway event loop is never blocked by a board read.
"""
from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

# Make the plugin package importable when this file is loaded standalone (the
# dashboard loader imports it by path, so the package root is not on sys.path).
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

PLUGIN_ID = "department-hub"
API_VERSION = "1.0.0"

router = APIRouter()


# ── settings / service construction ─────────────────────────────────────────

def _settings() -> dict:
    """Read this plugin's settings from the public Hermes config surface."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:
        return {}
    plugins = (cfg.get("plugins") or {}) if isinstance(cfg, dict) else {}
    entries = (plugins.get("entries") or {}) if isinstance(plugins, dict) else {}
    entry = entries.get(PLUGIN_ID) or {}
    merged = dict(entry.get("settings") or {})
    for key in ("departments", "state_path", "board"):
        if key in entry and key not in merged:
            merged[key] = entry[key]
    return merged


def _state_path(settings: dict) -> Path:
    explicit = settings.get("state_path")
    if isinstance(explicit, str) and explicit.strip():
        return Path(explicit).expanduser()
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home).expanduser() / PLUGIN_ID / "state.db"


def build_service(settings: dict | None = None):
    """Construct the read model. Imported lazily so module load stays cheap."""
    from department_lib.config import load_departments
    from department_lib.dashboard import DashboardService
    from department_lib.kanban_db_source import KanbanDbBoard
    from department_lib.store import Store

    settings = _settings() if settings is None else settings
    store = Store(_state_path(settings))
    departments = load_departments({"departments": settings.get("departments") or []})
    for dept in departments.values():
        store.register_department(dept)
    board = KanbanDbBoard(board=settings.get("board") or None)
    return DashboardService(store, board)


# ── request authorization ───────────────────────────────────────────────────

def _principal(request: Request) -> dict:
    """Resolve who is calling, from the gateway's authenticated request state.

    Never from a header, query parameter or body: a caller cannot name itself.
    """
    state = getattr(request, "state", None)
    native = getattr(state, "native_http_principal", None)
    if native:
        return {"kind": "native", "profile_bound": True}
    if getattr(state, "token_authenticated", False):
        return {"kind": "token", "profile_bound": True}
    app_state = getattr(getattr(request, "app", None), "state", None)
    if getattr(app_state, "auth_required", False):
        raise HTTPException(status_code=401, detail="authentication required")
    # Loopback gateway with no token (Hermes' default local mode): the socket is
    # the boundary. Reported honestly instead of pretending a profile identity.
    return {"kind": "loopback", "profile_bound": False}


def _no_store(payload):
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


def _http_error(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _guarded(fn):
    """Uniform, non-leaking error mapping for every route."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from department_lib.config import ConfigError as _ConfigError
        from department_lib.kanban_cli import BoardError as _BoardError
        from department_lib.store import StoreError as _StoreError
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except KeyError as exc:
            raise _http_error(404, "not_found", f"{exc.args[0] if exc.args else 'unknown'}") from None
        except _ConfigError as exc:
            raise _http_error(503, "config_invalid", str(exc)[:300]) from None
        except _BoardError as exc:
            raise _http_error(503, "board_unavailable", f"{exc.kind}: {exc}"[:300]) from None
        except _StoreError as exc:
            raise _http_error(503, "state_unavailable", str(exc)[:300]) from None
        except Exception as exc:  # never leak a traceback or a filesystem path
            raise _http_error(500, "internal_error", type(exc).__name__) from None
    return wrapper


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/health")
@_guarded
def health(request: Request):
    principal = _principal(request)
    try:
        service = build_service()
        board = service.board_health()
        departments = len(service.department_ids())
        state = "ok"
    except Exception as exc:
        board = {"available": False, "code": "unknown", "detail": type(exc).__name__}
        departments = 0
        state = "degraded"
    return _no_store({
        "schema": "department-hub/health/v1",
        "plugin": PLUGIN_ID,
        "api_version": API_VERSION,
        "state": state,
        "read_only": True,
        "caller": principal,
        "kanban": board,
        "departments": departments,
        "limits": {"refresh_seconds": 10, "max_tasks_per_department": 50},
    })


@router.get("/departments")
@_guarded
def list_departments(request: Request):
    _principal(request)
    service = build_service()
    return _no_store(service.list_departments())


@router.get("/departments/{dept_id}")
@_guarded
def get_department(dept_id: str, request: Request):
    _principal(request)
    service = build_service()
    try:
        payload = service.department(dept_id)
    except KeyError:
        raise _http_error(404, "unknown_department", f"department {dept_id!r} is not configured") from None
    return _no_store(payload)


@router.get("/departments/{dept_id}/events")
@_guarded
def get_events(dept_id: str, request: Request, limit: int = Query(20, ge=1, le=50)):
    _principal(request)
    service = build_service()
    try:
        payload = service.events(dept_id, limit)
    except KeyError:
        raise _http_error(404, "unknown_department", f"department {dept_id!r} is not configured") from None
    return _no_store(payload)


@router.post("/departments/plan")
@_guarded
def plan_department(request: Request, body: dict = Body(...)):
    """Validate a department declaration; returns config + steps, writes nothing."""
    _principal(request)
    from department_lib.config import ConfigError
    from department_lib.dashboard import DashboardService
    service = build_service()
    try:
        plan = DashboardService.plan_department(service, body)
    except ConfigError as exc:
        raise _http_error(400, "invalid_department", str(exc)[:300]) from None
    return _no_store(plan)


@router.post("/departments")
@_guarded
def create_department(request: Request, body: dict = Body(default_factory=dict)):
    """Refused by design: creating a department is an operator action."""
    _principal(request)
    from department_lib.dashboard import DashboardService
    refusal = DashboardService.mutation_refusal("create_department")
    if isinstance(body, dict) and isinstance(body.get("id"), str):
        refusal["requested_id"] = body["id"][:64]
    return JSONResponse(status_code=409, content=refusal, headers={"Cache-Control": "no-store"})


@router.patch("/departments/{dept_id}")
@_guarded
def edit_department(dept_id: str, request: Request, body: dict = Body(default_factory=dict)):
    """Refused by design: editing a department is an operator action."""
    _principal(request)
    from department_lib.dashboard import DashboardService
    refusal = DashboardService.mutation_refusal("edit_department")
    refusal["requested_id"] = dept_id[:64]
    return JSONResponse(status_code=409, content=refusal, headers={"Cache-Control": "no-store"})


@router.delete("/departments/{dept_id}")
@_guarded
def delete_department(dept_id: str, request: Request):
    """Refused by design: deletion is disabled by default."""
    _principal(request)
    from department_lib.dashboard import DashboardService
    refusal = DashboardService.mutation_refusal("delete_department")
    refusal["requested_id"] = dept_id[:64]
    return JSONResponse(status_code=409, content=refusal, headers={"Cache-Control": "no-store"})

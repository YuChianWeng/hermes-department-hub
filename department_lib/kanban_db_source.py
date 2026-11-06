"""Read-only Kanban board reads for the management UI.

The desktop backend must show *Kanban* as the source of truth, and it must be
fast enough to refresh on a bounded interval. Shelling out to
``hermes kanban show`` costs a process start per card, so this adapter reads the
board's SQLite file directly:

* the connection URI is ``mode=ro`` — a dashboard refresh can never create,
  migrate or alter a board;
* the Kanban schema is validated first; a missing/renamed table or column
  raises :class:`BoardError` (``unavailable``) and the UI reports ``unknown``
  rather than inventing an empty board;
* no Hermes Python module is imported, so the plugin stays on the public
  catalog surface and a core refactor fails closed instead of breaking
  silently.

The returned :class:`~department_lib.board.BoardTask` has the exact shape M1's
acceptance evidence expects, so the UI reuses M1's verdict parsing instead of
re-implementing it.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .board import BoardRun, BoardTask
from .kanban_cli import BoardError
from .models import BoardEvent

__all__ = ["KanbanDbBoard", "resolve_board_db_path"]

# M1 v1 had no ``source_id`` on BoardEvent; M1 v2 added it as an additive
# field. The adapter stays importable against either shape and only forwards
# the source id when the installed contract can carry it.
try:  # pragma: no cover - the field-set branch is exercised in contract tests
    from dataclasses import fields as _dataclass_fields
    _BOARD_EVENT_FIELDS = {f.name for f in _dataclass_fields(BoardEvent)}
except Exception:  # pragma: no cover
    _BOARD_EVENT_FIELDS = set()

_BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REQUIRED_COLUMNS = {
    "tasks": {"id", "title", "status", "assignee"},
    "task_events": {"id", "task_id", "kind", "created_at", "run_id", "payload"},
    "task_runs": {"id", "task_id", "profile", "status", "outcome", "metadata"},
    "task_links": {"parent_id", "child_id"},
}


def _kanban_home() -> Path:
    """Kanban root: ``HERMES_KANBAN_HOME`` else the shared Hermes root.

    The board is shared across profiles by design, so a profile-scoped
    ``HERMES_HOME`` (``.../profiles/<name>``) is normalized to its parent.
    """
    override = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        path = Path(home).expanduser()
        if path.parent.name == "profiles":
            return path.parent.parent
        return path
    return Path.home() / ".hermes"


def resolve_board_db_path(board: str | None = None) -> Path:
    """Resolve the active board DB from the documented env/pin conventions."""
    pinned = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if pinned:
        return Path(pinned).expanduser()
    slug = (board or os.environ.get("HERMES_KANBAN_BOARD", "")).strip() or None
    home = _kanban_home()
    if slug is None:
        current = home / "kanban" / "current"
        try:
            if current.exists():
                slug = current.read_text(encoding="utf-8-sig").strip() or None
        except OSError:
            slug = None
    if slug is None or slug == "default":
        return home / "kanban.db"
    if not _BOARD_SLUG_RE.match(slug):
        raise BoardError("unavailable", f"invalid board slug {slug!r}")
    return home / "kanban" / "boards" / slug / "kanban.db"


def _json_dict(value: Any) -> dict:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class KanbanDbBoard:
    """A ``Board``-compatible reader over a read-only Kanban SQLite file."""

    def __init__(self, *, board: str | None = None, db_path: str | Path | None = None):
        self.board = board
        self.db_path = Path(db_path) if db_path is not None else None

    # ── plumbing ────────────────────────────────────────────────────────────

    def _path(self) -> Path:
        if self.db_path is not None:
            return self.db_path
        try:
            return resolve_board_db_path(self.board)
        except BoardError:
            raise
        except Exception as exc:
            raise BoardError("unavailable", f"cannot resolve the kanban board: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        path = self._path()
        if not path.exists():
            raise BoardError("unavailable", "kanban board database is not present")
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=3.0)
        except sqlite3.Error as exc:
            raise BoardError("unavailable", f"cannot open the kanban board read-only: {exc}") from exc
        conn.row_factory = sqlite3.Row
        try:
            tables = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = set(_REQUIRED_COLUMNS) - tables
            if missing:
                raise BoardError(
                    "unavailable",
                    f"kanban board database has no task schema (missing {sorted(missing)})")
            for table, required in _REQUIRED_COLUMNS.items():
                columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                absent = required - columns
                if absent:
                    raise BoardError(
                        "unavailable",
                        f"kanban schema drift: {table} lacks {sorted(absent)}")
        except BoardError:
            conn.close()
            raise
        except sqlite3.Error as exc:
            conn.close()
            raise BoardError("unavailable", f"cannot read the kanban schema: {exc}") from exc
        return conn

    # ── reads ───────────────────────────────────────────────────────────────

    def get(self, task_id: str) -> BoardTask:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, status, assignee FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
            if row is None:
                raise BoardError("not_found", f"task {task_id} is not on this board")
            status = row["status"]
            if not isinstance(status, str) or not status:
                raise BoardError("invalid", f"task {task_id} has no status")
            try:
                event_rows = conn.execute(
                    "SELECT id, run_id, kind, payload, created_at FROM task_events"
                    " WHERE task_id = ? ORDER BY id", (str(task_id),)).fetchall()
                run_rows = conn.execute(
                    "SELECT id, profile, status, outcome, summary, metadata FROM task_runs"
                    " WHERE task_id = ? ORDER BY id", (str(task_id),)).fetchall()
                parents = tuple(r["parent_id"] for r in conn.execute(
                    "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id",
                    (str(task_id),)))
                children = tuple(r["child_id"] for r in conn.execute(
                    "SELECT child_id FROM task_links WHERE parent_id = ? ORDER BY child_id",
                    (str(task_id),)))
            except sqlite3.Error as exc:
                raise BoardError("invalid", f"reading topology of {task_id} failed: {exc}") from exc
        finally:
            conn.close()

        events: list[BoardEvent] = []
        for item in event_rows:
            kind = item["kind"]
            created = item["created_at"]
            source_id = item["id"]
            if not isinstance(kind, str) or not kind:
                raise BoardError("invalid", f"event for task {task_id} has no kind")
            if not isinstance(created, int) or isinstance(created, bool):
                raise BoardError("invalid", f"event for task {task_id} has no integer created_at")
            # Mirrors M1 v2's board parser: only string/integer source ids are
            # usable identity. ``bool`` is a subclass of int and is refused.
            if source_id is not None and type(source_id) not in (str, int):
                raise BoardError("invalid", f"event for task {task_id} has an invalid source id")
            payload = {
                "task_id": str(task_id), "kind": kind, "run_id": item["run_id"],
                "created_at": created, "payload": _json_dict(item["payload"]),
            }
            if "source_id" in _BOARD_EVENT_FIELDS:
                payload["source_id"] = source_id
            events.append(BoardEvent(**payload))

        runs: list[BoardRun] = []
        seen_run_ids: set[int] = set()
        for item in run_rows:
            run_id = item["id"]
            if not isinstance(run_id, int) or isinstance(run_id, bool):
                raise BoardError("invalid", f"run for task {task_id} has no integer id")
            # M1 v2 refuses duplicate run ids rather than silently keeping the
            # last one; a collision must never become a fabricated history.
            if run_id in seen_run_ids:
                raise BoardError("invalid", f"duplicate run ids for task {task_id}")
            seen_run_ids.add(run_id)
            runs.append(BoardRun(
                id=run_id, profile=item["profile"], status=item["status"],
                outcome=item["outcome"], summary=item["summary"],
                metadata=_json_dict(item["metadata"]) or None,
            ))

        return BoardTask(
            task_id=str(task_id), status=status, assignee=row["assignee"],
            parents=parents, children=children, comments=(), events=tuple(events),
            runs=tuple(runs),
        )

    def ancestry(self, task_id: str, *, limit: int = 8) -> tuple[str, ...]:
        """Breadth-first ``(task, parents..., ancestors...)`` with a depth cap."""
        order: list[str] = [task_id]
        seen = {task_id}
        frontier = [task_id]
        depth = 0
        while frontier:
            depth += 1
            if depth > limit:
                raise BoardError("invalid", f"ancestry of {task_id} exceeds depth {limit}")
            next_frontier: list[str] = []
            for node in frontier:
                snap = self.get(node)
                for parent in snap.parents:
                    if parent == task_id:
                        raise BoardError("invalid", f"cycle detected in ancestry of {task_id}")
                    if parent in seen:
                        continue
                    seen.add(parent)
                    order.append(parent)
                    next_frontier.append(parent)
            frontier = next_frontier
        return tuple(order)

    # ── health ──────────────────────────────────────────────────────────────

    def titles(self, task_ids: list[str]) -> dict[str, str]:
        """Card titles for the given ids, from the same read-only connection."""
        ids = [str(t) for t in task_ids if isinstance(t, str) and t][:200]
        if not ids:
            return {}
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT id, title FROM tasks WHERE id IN ({placeholders})", ids).fetchall()
        except sqlite3.Error as exc:
            raise BoardError("invalid", f"reading titles failed: {exc}") from exc
        finally:
            conn.close()
        return {row["id"]: row["title"] for row in rows if isinstance(row["title"], str)}

    def subscriptions(self, task_id: str) -> list[dict]:
        """Read-only manager wake subscriptions for one card.

        ``kanban_notify_subs`` stores the same fields M1's public
        ``notify-list`` reads back (platform / chat / notifier profile /
        delivery mode). The values stay inside this adapter and the read
        model; the dashboard payload never publishes chat ids or platforms.
        A board without the table is reported as ``unavailable`` rather than
        an empty subscription set, because \"no rows\" would look like
        \"not subscribed\".
        """
        conn = self._connect()
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kanban_notify_subs'"
            ).fetchone()
            if table is None:
                raise BoardError("unavailable", "kanban board has no notify subscription table")
            rows = conn.execute(
                "SELECT platform, chat_id, notifier_profile, delivery_mode"
                " FROM kanban_notify_subs WHERE task_id = ? ORDER BY platform, chat_id",
                (str(task_id),),
            ).fetchall()
        except BoardError:
            raise
        except sqlite3.Error as exc:
            raise BoardError("unavailable", f"cannot read notify subscriptions: {exc}") from exc
        finally:
            conn.close()
        return [{"platform": r["platform"], "chat_id": r["chat_id"],
                 "notifier_profile": r["notifier_profile"],
                 "delivery_mode": r["delivery_mode"]} for r in rows]

    def health(self) -> dict:
        """Cheap readiness probe; never raises and never leaks an absolute path."""
        try:
            path = self._path()
        except BoardError as exc:
            return {"available": False, "code": exc.kind, "detail": str(exc)[:200]}
        if not path.exists():
            return {"available": False, "code": "missing", "detail": "board database not present"}
        try:
            conn = self._connect()
            try:
                count = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
            finally:
                conn.close()
        except BoardError as exc:
            return {"available": False, "code": exc.kind, "detail": str(exc)[:200]}
        return {"available": True, "code": "", "detail": "", "task_count": int(count),
                "board": self.board or "current"}

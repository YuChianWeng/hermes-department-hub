"""Durable plugin-owned state (SQLite/WAL). NOT the Hermes Kanban database.

This module is the plugin's own bookkeeping: department bindings, observed
board events, processing receipts, verdicts, acceptances, review rounds,
deliveries and escalations. The authoritative task state always stays on the
Kanban board; nothing here replaces it.

Design notes
------------
* Every identity column is a primary key, so retries cannot create a second
  row for the same logical fact. That is *idempotent handling*, not a claim of
  exactly-once delivery to any external system.
* Domain mutation + receipt are written in one ``BEGIN IMMEDIATE``
  transaction: a crash between them either leaves both or neither.
* A single Store instance serialises writers through one connection. This is
  a trusted-single-writer design; it is not an ACL against other processes.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .models import Department

__all__ = [
    "Store", "StoreError", "EventRow", "DeliveryRow", "VerdictRow", "AcceptanceRow",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS departments (
  dept_id TEXT PRIMARY KEY,
  json TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL REFERENCES departments(dept_id),
  role TEXT NOT NULL CHECK (role IN ('root','work','review')),
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  run_id INTEGER,
  created_at INTEGER NOT NULL,
  payload_digest TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'observed',
  detail TEXT NOT NULL DEFAULT '',
  observed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, created_at);
CREATE TABLE IF NOT EXISTS receipts (
  event_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  decision TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_rounds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rounds_task ON review_rounds(dept_id, task_id);
CREATE TABLE IF NOT EXISTS verdicts (
  verdict_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  run_id INTEGER,
  reviewer_profile TEXT NOT NULL,
  revision INTEGER NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('PASS','REQUEST_CHANGES')),
  note TEXT NOT NULL DEFAULT '',
  event_id TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verdicts_task ON verdicts(dept_id, task_id, revision);
CREATE TABLE IF NOT EXISTS acceptances (
  acceptance_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  verdict_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  manager_profile TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','superseded','revoked')),
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_acceptance_active
  ON acceptances(dept_id, task_id) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS deliveries (
  delivery_key TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  body_json TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE (dept_id, task_id, reason_code)
);
"""

_DELIVERY_STATES = {"pending", "confirmed", "unknown", "dead_letter"}


class StoreError(RuntimeError):
    """Raised when a store invariant or precondition fails."""


@dataclass(frozen=True)
class EventRow:
    event_id: str
    dept_id: str
    task_id: str
    kind: str
    run_id: int | None
    created_at: int
    payload_digest: str
    state: str
    detail: str
    payload: dict


@dataclass(frozen=True)
class DeliveryRow:
    delivery_key: str
    dept_id: str
    task_id: str
    kind: str
    body: dict
    state: str
    detail: str
    attempts: int


@dataclass(frozen=True)
class VerdictRow:
    verdict_id: str
    dept_id: str
    task_id: str
    run_id: int | None
    reviewer_profile: str
    revision: int
    artifact_sha256: str
    verdict: str
    note: str
    event_id: str


@dataclass(frozen=True)
class AcceptanceRow:
    acceptance_id: str
    dept_id: str
    task_id: str
    verdict_id: str
    revision: int
    artifact_sha256: str
    manager_profile: str
    status: str


class Store:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._depth = 0
        try:
            self._conn = sqlite3.connect(str(self._path), isolation_level=None,
                                         check_same_thread=False, timeout=15)
        except sqlite3.Error as exc:  # pragma: no cover - environment failure
            raise StoreError(f"cannot open store {self._path}: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.executescript(_SCHEMA)

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("store is closed")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection
        self._depth += 1
        try:
            if self._depth == 1:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if self._depth == 1:
                conn.execute("COMMIT")
        except BaseException:
            if self._depth == 1:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            self._depth -= 1

    def _now(self) -> float:
        return time.time()

    # ── departments ─────────────────────────────────────────────────────────

    def register_department(self, dept: Department) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO departments (dept_id, json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(dept_id) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
                (dept.dept_id, json.dumps(dept.as_dict(), ensure_ascii=False, sort_keys=True),
                 self._now()),
            )

    def get_department(self, dept_id: str) -> Department | None:
        row = self.connection.execute(
            "SELECT json FROM departments WHERE dept_id = ?", (dept_id,)).fetchone()
        if row is None:
            return None
        return Department.from_mapping(json.loads(row["json"]))

    def departments(self) -> list[str]:
        return [r["dept_id"] for r in self.connection.execute(
            "SELECT dept_id FROM departments ORDER BY dept_id")]

    def department_for_manager(self, profile: str) -> Department | None:
        """Resolve the department whose manager binding is *profile*."""
        for dept_id in self.departments():
            dept = self.get_department(dept_id)
            if dept is not None and dept.manager_profile == profile:
                return dept
        return None

    # ── task ownership ──────────────────────────────────────────────────────

    def bind_task(self, dept_id: str, task_id: str, *, role: str) -> None:
        if role not in ("root", "work", "review"):
            raise StoreError(f"invalid role '{role}'")
        if self.get_department(dept_id) is None:
            raise StoreError(f"unknown department '{dept_id}'")
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT dept_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if existing is not None:
                if existing["dept_id"] != dept_id:
                    raise StoreError(
                        f"task {task_id} is already bound to department '{existing['dept_id']}'")
                return
            conn.execute(
                "INSERT INTO tasks (task_id, dept_id, role, created_at) VALUES (?,?,?,?)",
                (task_id, dept_id, role, self._now()),
            )

    def dept_for_task(self, task_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT dept_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row["dept_id"] if row else None

    def tasks_for_dept(self, dept_id: str) -> list[tuple[str, str]]:
        return [(r["task_id"], r["role"]) for r in self.connection.execute(
            "SELECT task_id, role FROM tasks WHERE dept_id = ? ORDER BY created_at", (dept_id,))]

    def all_tasks(self) -> list[str]:
        """Every bound task id, for bounded effect read-back (never a board scan)."""
        return [r["task_id"] for r in self.connection.execute(
            "SELECT task_id FROM tasks ORDER BY created_at")]

    def root_task(self, dept_id: str) -> str | None:
        """The department's root/report card, if one is bound."""
        row = self.connection.execute(
            "SELECT task_id FROM tasks WHERE dept_id = ? AND role = 'root'"
            " ORDER BY created_at LIMIT 1", (dept_id,)).fetchone()
        return row["task_id"] if row else None

    def tasks_by_role(self, dept_id: str, role: str) -> list[str]:
        return [r["task_id"] for r in self.connection.execute(
            "SELECT task_id FROM tasks WHERE dept_id = ? AND role = ? ORDER BY created_at",
            (dept_id, role))]

    # ── events & receipts ───────────────────────────────────────────────────

    def record_event(self, event, dept_id: str) -> str:
        """Insert or classify one observed board event.

        Returns ``new`` | ``duplicate`` | ``conflict``. A conflict (same board
        coordinates, different payload) never overwrites the stored payload.
        """
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT payload_digest, state FROM events WHERE event_id = ?",
                (event.event_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO events (event_id, dept_id, task_id, kind, run_id, created_at,"
                    " payload_digest, payload_json, state, detail, observed_at)"
                    " VALUES (?,?,?,?,?,?,?,?, 'observed', '', ?)",
                    (event.event_id, dept_id, event.task_id, event.kind, event.run_id,
                     event.created_at, event.payload_digest,
                     json.dumps(event.payload, ensure_ascii=False, sort_keys=True), self._now()),
                )
                return "new"
            if row["payload_digest"] == event.payload_digest:
                return "duplicate"
            conn.execute(
                "UPDATE events SET state = 'conflict', detail = ? WHERE event_id = ?",
                (f"payload changed under identical coordinates: stored={row['payload_digest']} "
                 f"incoming={event.payload_digest}", event.event_id),
            )
            return "conflict"

    def events_for_task(self, task_id: str) -> list[EventRow]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY created_at, kind", (task_id,))
        return [EventRow(
            event_id=r["event_id"], dept_id=r["dept_id"], task_id=r["task_id"], kind=r["kind"],
            run_id=r["run_id"], created_at=r["created_at"], payload_digest=r["payload_digest"],
            state=r["state"], detail=r["detail"], payload=json.loads(r["payload_json"]),
        ) for r in rows]

    def record_receipt(self, event_id: str, dept_id: str, task_id: str, kind: str,
                       decision: str, detail: str = "") -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO receipts (event_id, dept_id, task_id, kind, decision, detail,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (event_id, dept_id, task_id, kind, decision, detail, self._now()),
            )
            return cur.rowcount == 1

    def has_receipt(self, event_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM receipts WHERE event_id = ?", (event_id,)).fetchone() is not None

    def receipt_count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) AS n FROM receipts").fetchone()["n"]

    # ── review rounds ───────────────────────────────────────────────────────

    def add_round(self, dept_id: str, task_id: str, reason: str) -> int:
        if self.get_department(dept_id) is None:
            raise StoreError(f"unknown department '{dept_id}'")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO review_rounds (dept_id, task_id, reason, created_at) VALUES (?,?,?,?)",
                (dept_id, task_id, reason, self._now()),
            )
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM review_rounds WHERE dept_id = ? AND task_id = ?",
                (dept_id, task_id)).fetchone()
            return int(row["n"])

    def rounds(self, dept_id: str, task_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM review_rounds WHERE dept_id = ? AND task_id = ?",
            (dept_id, task_id)).fetchone()
        return int(row["n"])

    # ── verdicts / acceptances ──────────────────────────────────────────────

    def put_verdict(self, *, verdict_id: str, dept_id: str, task_id: str, run_id: int | None,
                    reviewer_profile: str, revision: int, artifact_sha256: str, verdict: str,
                    note: str, event_id: str) -> bool:
        if verdict not in ("PASS", "REQUEST_CHANGES"):
            raise StoreError(f"invalid verdict '{verdict}'")
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO verdicts (verdict_id, dept_id, task_id, run_id,"
                " reviewer_profile, revision, artifact_sha256, verdict, note, event_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (verdict_id, dept_id, task_id, run_id, reviewer_profile, revision,
                 artifact_sha256, verdict, note, event_id, self._now()),
            )
            return cur.rowcount == 1

    def current_verdict(self, dept_id: str, task_id: str, revision: int | None = None):
        sql = ("SELECT * FROM verdicts WHERE dept_id = ? AND task_id = ?")
        params: list[Any] = [dept_id, task_id]
        if revision is not None:
            sql += " AND revision = ?"
            params.append(revision)
        sql += " ORDER BY revision DESC, created_at DESC, rowid DESC LIMIT 1"
        row = self.connection.execute(sql, params).fetchone()
        return self._verdict(row) if row else None

    @staticmethod
    def _verdict(row) -> VerdictRow:
        return VerdictRow(
            verdict_id=row["verdict_id"], dept_id=row["dept_id"], task_id=row["task_id"],
            run_id=row["run_id"], reviewer_profile=row["reviewer_profile"],
            revision=row["revision"], artifact_sha256=row["artifact_sha256"],
            verdict=row["verdict"], note=row["note"], event_id=row["event_id"],
        )

    def put_acceptance(self, *, acceptance_id: str, dept_id: str, task_id: str, verdict_id: str,
                       revision: int, artifact_sha256: str, manager_profile: str) -> AcceptanceRow:
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM acceptances WHERE acceptance_id = ?",
                (acceptance_id,)).fetchone()
            if existing is not None and existing["status"] == "active":
                return self._acceptance(existing)
            conn.execute(
                "UPDATE acceptances SET status = 'superseded' WHERE dept_id = ? AND task_id = ?"
                " AND status = 'active' AND acceptance_id != ?",
                (dept_id, task_id, acceptance_id))
            conn.execute(
                "INSERT OR IGNORE INTO acceptances (acceptance_id, dept_id, task_id, verdict_id,"
                " revision, artifact_sha256, manager_profile, status, created_at)"
                " VALUES (?,?,?,?,?,?,?, 'active', ?)",
                (acceptance_id, dept_id, task_id, verdict_id, revision, artifact_sha256,
                 manager_profile, self._now()),
            )
            row = conn.execute(
                "SELECT * FROM acceptances WHERE dept_id = ? AND task_id = ? AND status = 'active'",
                (dept_id, task_id)).fetchone()
        return self._acceptance(row)

    @staticmethod
    def _acceptance(row) -> AcceptanceRow:
        return AcceptanceRow(
            acceptance_id=row["acceptance_id"], dept_id=row["dept_id"], task_id=row["task_id"],
            verdict_id=row["verdict_id"], revision=row["revision"],
            artifact_sha256=row["artifact_sha256"], manager_profile=row["manager_profile"],
            status=row["status"],
        )

    def active_acceptance(self, dept_id: str, task_id: str) -> AcceptanceRow | None:
        row = self.connection.execute(
            "SELECT * FROM acceptances WHERE dept_id = ? AND task_id = ? AND status = 'active'",
            (dept_id, task_id)).fetchone()
        return self._acceptance(row) if row else None

    def supersede_acceptance(self, dept_id: str, task_id: str, *, status: str = "superseded") -> int:
        if status not in ("superseded", "revoked"):
            raise StoreError(f"invalid acceptance status '{status}'")
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE acceptances SET status = ? WHERE dept_id = ? AND task_id = ?"
                " AND status = 'active'", (status, dept_id, task_id))
            return cur.rowcount

    # ── deliveries ─────────────────────────────────────────────────────────

    def put_delivery(self, key: str, dept_id: str, task_id: str, kind: str, body: dict) -> DeliveryRow:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO deliveries (delivery_key, dept_id, task_id, kind, body_json,"
                " state, detail, attempts, created_at, updated_at)"
                " VALUES (?,?,?,?,?, 'pending', '', 0, ?, ?)",
                (key, dept_id, task_id, kind, json.dumps(body, ensure_ascii=False, sort_keys=True),
                 self._now(), self._now()),
            )
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_key = ?", (key,)).fetchone()
        return self._delivery(row)

    @staticmethod
    def _delivery(row) -> DeliveryRow:
        return DeliveryRow(
            delivery_key=row["delivery_key"], dept_id=row["dept_id"], task_id=row["task_id"],
            kind=row["kind"], body=json.loads(row["body_json"]), state=row["state"],
            detail=row["detail"], attempts=row["attempts"],
        )

    def get_delivery(self, key: str) -> DeliveryRow | None:
        row = self.connection.execute(
            "SELECT * FROM deliveries WHERE delivery_key = ?", (key,)).fetchone()
        return self._delivery(row) if row else None

    def deliveries_for_dept(self, dept_id: str) -> list[DeliveryRow]:
        rows = self.connection.execute(
            "SELECT * FROM deliveries WHERE dept_id = ? ORDER BY created_at", (dept_id,))
        return [self._delivery(r) for r in rows]

    def set_delivery_state(self, key: str, state: str, detail: str = "") -> None:
        if state not in _DELIVERY_STATES:
            raise StoreError(f"invalid delivery state '{state}'")
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE deliveries SET state = ?, detail = ?, attempts = attempts + 1,"
                " updated_at = ? WHERE delivery_key = ?",
                (state, detail, self._now(), key),
            )
            if cur.rowcount == 0:
                raise StoreError(f"unknown delivery '{key}'")

    def delivery_count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) AS n FROM deliveries").fetchone()["n"]

    # ── escalations ────────────────────────────────────────────────────────

    def add_escalation(self, dept_id: str, task_id: str, reason_code: str, detail: dict) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO escalations (dept_id, task_id, reason_code, detail_json,"
                " created_at) VALUES (?,?,?,?,?)",
                (dept_id, task_id, reason_code,
                 json.dumps(detail, ensure_ascii=False, sort_keys=True), self._now()),
            )
            return cur.rowcount == 1

    def escalation_count(self, dept_id: str, task_id: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) AS n FROM escalations WHERE dept_id = ? AND task_id = ?",
            (dept_id, task_id)).fetchone()["n"]

    def escalations_for_task(self, dept_id: str, task_id: str) -> list[dict]:
        return [{"reason_code": r["reason_code"], "detail": json.loads(r["detail_json"])}
                for r in self.connection.execute(
                    "SELECT reason_code, detail_json FROM escalations WHERE dept_id = ? AND task_id = ?"
                    " ORDER BY created_at", (dept_id, task_id))]

    # ── transparency ───────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        def grouped(table: str, column: str) -> dict[str, int]:
            return {r[column]: r["n"] for r in self.connection.execute(
                f"SELECT {column} AS {column}, COUNT(*) AS n FROM {table} GROUP BY {column}")}
        return {
            "departments": self.connection.execute(
                "SELECT COUNT(*) AS n FROM departments").fetchone()["n"],
            "tasks": self.connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"],
            "events": grouped("events", "state"),
            "receipts": grouped("receipts", "decision"),
            "deliveries": grouped("deliveries", "state"),
            "escalations": self.connection.execute(
                "SELECT COUNT(*) AS n FROM escalations").fetchone()["n"],
        }

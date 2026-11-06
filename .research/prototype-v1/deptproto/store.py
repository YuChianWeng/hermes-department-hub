"""Durable prototype store: SQLite in WAL mode, explicit transactions.

This database belongs to the prototype only. It is not the Hermes Kanban
database and no production SQLite file is ever opened for writing.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS departments (
  dept_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  manager_profile TEXT NOT NULL,
  report_task_id TEXT NOT NULL,
  manager_root_task_id TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS work_items (
  item_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  board_task_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('setup','work','review','report')),
  owner_profile TEXT NOT NULL,
  parents_json TEXT NOT NULL DEFAULT '[]',
  current_rev INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE (dept_id, board_task_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  rev INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  path TEXT NOT NULL,
  producer TEXT,
  created_at REAL NOT NULL,
  UNIQUE (item_id, rev)
);
CREATE TABLE IF NOT EXISTS review_requests (
  request_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  rev INTEGER NOT NULL,
  reviewer_profile TEXT NOT NULL,
  review_task_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at REAL NOT NULL,
  UNIQUE (item_id, rev)
);
CREATE TABLE IF NOT EXISTS review_runs (
  run_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  item_id TEXT NOT NULL,
  reviewer_profile TEXT NOT NULL,
  review_task_id TEXT NOT NULL,
  artifact_sha TEXT NOT NULL,
  started_at REAL NOT NULL,
  completed_at REAL
);
CREATE TABLE IF NOT EXISTS verdicts (
  verdict_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('PASS','REQUEST_CHANGES')),
  artifact_sha TEXT NOT NULL,
  reviewer_profile TEXT NOT NULL,
  decided_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS acceptances (
  acceptance_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  item_id TEXT NOT NULL UNIQUE,
  verdict_id TEXT NOT NULL,
  artifact_sha TEXT NOT NULL,
  accepted_by TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  dept_id TEXT NOT NULL,
  item_id TEXT,
  kind TEXT NOT NULL,
  seq_no INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  admitted_at REAL NOT NULL,
  processed_at REAL,
  disposition TEXT NOT NULL DEFAULT 'admitted',
  reject_reason TEXT
);
CREATE TABLE IF NOT EXISTS event_rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  dept_id TEXT,
  item_id TEXT,
  reason TEXT NOT NULL,
  detail TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS board_events (
  external_event_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  subscription_profile TEXT,
  admitted_at REAL NOT NULL,
  processed_at REAL,
  receipt_key TEXT
);
CREATE TABLE IF NOT EXISTS deliveries (
  delivery_id TEXT PRIMARY KEY,
  dept_id TEXT NOT NULL,
  report_task_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'final',
  operation_key TEXT NOT NULL UNIQUE,
  payload_sha TEXT NOT NULL,
  marker TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
  delivery_id TEXT NOT NULL,
  operation_key TEXT NOT NULL UNIQUE,
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
  receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id TEXT NOT NULL,
  operation_key TEXT NOT NULL UNIQUE,
  external_ref TEXT,
  evidence_json TEXT,
  observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id TEXT,
  item_id TEXT,
  kind TEXT NOT NULL,
  detail TEXT,
  created_at REAL NOT NULL,
  resolved_at REAL
);
CREATE TABLE IF NOT EXISTS transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  reason TEXT,
  actor TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  detail TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_item_seq ON events(item_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(processed_at);
CREATE INDEX IF NOT EXISTS idx_transitions_item ON transitions(item_id);
CREATE INDEX IF NOT EXISTS idx_alerts_dept ON alerts(dept_id, kind);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path), timeout=60, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        with self.tx() as cur:
            cur.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','1')")

    @classmethod
    def open(cls, path: Path) -> "Store":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()
        finally:
            cur.close()

    # ---------------- config ----------------
    def insert_department(self, dept_id: str, name: str, manager_profile: str,
                          report_task_id: str, manager_root_task_id: Optional[str]) -> dict:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO departments(dept_id,name,manager_profile,report_task_id,"
                "manager_root_task_id,created_at) VALUES(?,?,?,?,?,?)",
                (dept_id, name, manager_profile, report_task_id,
                 manager_root_task_id, time.time()))
        return self.get_department(dept_id)

    def get_department(self, dept_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM departments WHERE dept_id=?", (dept_id,)).fetchone()
        return dict(row) if row else None

    def insert_work_item(self, item_id: str, dept_id: str, board_task_id: str, role: str,
                         owner_profile: str, parents: list[str]) -> dict:
        now = time.time()
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO work_items(item_id,dept_id,board_task_id,role,owner_profile,"
                "parents_json,current_rev,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,0,'draft',?,?)",
                (item_id, dept_id, board_task_id, role, owner_profile,
                 json.dumps(parents), now, now))
        return self.get_item(item_id)

    def get_item(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM work_items WHERE item_id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def item_status(self, item_id: str) -> Optional[str]:
        item = self.get_item(item_id)
        return item["status"] if item else None

    def item_by_task(self, dept_id: str, board_task_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM work_items WHERE dept_id=? AND board_task_id=?",
            (dept_id, board_task_id)).fetchone()
        return dict(row) if row else None

    def items_for(self, dept_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM work_items WHERE dept_id=? ORDER BY created_at", (dept_id,))]

    def set_item_status(self, item_id: str, to_status: str, reason: str, actor: str) -> None:
        with self.tx() as cur:
            row = cur.execute("SELECT status FROM work_items WHERE item_id=?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            cur.execute("UPDATE work_items SET status=?, updated_at=? WHERE item_id=?",
                        (to_status, time.time(), item_id))
            cur.execute("INSERT INTO transitions(item_id,from_status,to_status,reason,actor,at) "
                        "VALUES(?,?,?,?,?,?)",
                        (item_id, row["status"], to_status, reason, actor, time.time()))

    def transitions_for(self, item_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM transitions WHERE item_id=? ORDER BY id", (item_id,))]

    # ---------------- artifacts ----------------
    def next_rev(self, item_id: str) -> int:
        row = self.conn.execute("SELECT current_rev FROM work_items WHERE item_id=?",
                                (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return int(row["current_rev"]) + 1

    def insert_artifact(self, artifact_id: str, item_id: str, rev: int, sha256: str,
                        path: str, producer: str) -> dict:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO artifacts(artifact_id,item_id,rev,sha256,path,producer,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (artifact_id, item_id, rev, sha256, path, producer, time.time()))
            cur.execute("UPDATE work_items SET current_rev=?, updated_at=? WHERE item_id=?",
                        (rev, time.time(), item_id))
        return self.latest_artifact(item_id)

    def artifacts_for(self, item_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM artifacts WHERE item_id=? ORDER BY rev", (item_id,))]

    def latest_artifact(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE item_id=? ORDER BY rev DESC LIMIT 1",
            (item_id,)).fetchone()
        return dict(row) if row else None

    # ---------------- review ----------------
    def create_review_request(self, request_id: str, item_id: str, artifact_id: str,
                              rev: int, reviewer_profile: str, review_task_id: str) -> dict:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO review_requests(request_id,item_id,artifact_id,rev,"
                "reviewer_profile,review_task_id,status,created_at) "
                "VALUES(?,?,?,?,?,?,'open',?)",
                (request_id, item_id, artifact_id, rev, reviewer_profile,
                 review_task_id, time.time()))
        return self.request_by_id(request_id)

    def request_by_id(self, request_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM review_requests WHERE request_id=?",
                                (request_id,)).fetchone()
        return dict(row) if row else None

    def latest_request(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM review_requests WHERE item_id=? ORDER BY rev DESC LIMIT 1",
            (item_id,)).fetchone()
        return dict(row) if row else None

    def set_request_status(self, request_id: str, status: str) -> None:
        with self.tx() as cur:
            cur.execute("UPDATE review_requests SET status=? WHERE request_id=?",
                        (status, request_id))

    def create_review_run(self, run_id: str, request: dict) -> dict:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO review_runs(run_id,request_id,item_id,reviewer_profile,"
                "review_task_id,artifact_sha,started_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, request["request_id"], request["item_id"],
                 request["reviewer_profile"], request["review_task_id"],
                 request["artifact_sha"] if "artifact_sha" in request else
                 self._request_sha(request["artifact_id"]), time.time()))
        return self.run_by_id(run_id)

    def _request_sha(self, artifact_id: str) -> str:
        row = self.conn.execute("SELECT sha256 FROM artifacts WHERE artifact_id=?",
                                (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return row["sha256"]

    def run_by_id(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM review_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM review_runs WHERE item_id=? ORDER BY started_at DESC LIMIT 1",
            (item_id,)).fetchone()
        return dict(row) if row else None

    def run_by_request(self, request_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM review_runs WHERE request_id=?",
                                (request_id,)).fetchone()
        return dict(row) if row else None

    def verdict_for_run(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM verdicts WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def add_verdict(self, verdict_id: str, run_id: str, request_id: str, item_id: str,
                    verdict: str, artifact_sha: str, reviewer_profile: str) -> dict:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO verdicts(verdict_id,run_id,request_id,item_id,verdict,"
                "artifact_sha,reviewer_profile,decided_at) VALUES(?,?,?,?,?,?,?,?)",
                (verdict_id, run_id, request_id, item_id, verdict, artifact_sha,
                 reviewer_profile, time.time()))
            cur.execute("UPDATE review_requests SET status=? WHERE request_id=?",
                        (verdict, request_id))
            cur.execute("UPDATE review_runs SET completed_at=? WHERE run_id=?",
                        (time.time(), run_id))
        return self.verdict_by_id(verdict_id)

    def verdict_by_id(self, verdict_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM verdicts WHERE verdict_id=?",
                                (verdict_id,)).fetchone()
        return dict(row) if row else None

    def latest_verdict(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT v.* FROM verdicts v JOIN review_requests r ON r.request_id=v.request_id "
            "WHERE v.item_id=? ORDER BY r.rev DESC LIMIT 1", (item_id,)).fetchone()
        return dict(row) if row else None

    def latest_pass(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT v.* FROM verdicts v JOIN review_requests r ON r.request_id=v.request_id "
            "WHERE v.item_id=? AND v.verdict='PASS' ORDER BY r.rev DESC LIMIT 1",
            (item_id,)).fetchone()
        return dict(row) if row else None

    def verdicts_for(self, item_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM verdicts WHERE item_id=? ORDER BY decided_at", (item_id,))]

    def insert_acceptance(self, acceptance_id: str, dept_id: str, item_id: str,
                          verdict_id: str, artifact_sha: str, accepted_by: str) -> dict:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO acceptances(acceptance_id,dept_id,item_id,verdict_id,"
                "artifact_sha,accepted_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (acceptance_id, dept_id, item_id, verdict_id, artifact_sha,
                 accepted_by, time.time()))
        return self.acceptance_for(item_id)

    def acceptance_for(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM acceptances WHERE item_id=?",
                                (item_id,)).fetchone()
        return dict(row) if row else None

    # ---------------- events ----------------
    def admit_event(self, event) -> Any:
        from .events import Result
        existing = self.conn.execute("SELECT * FROM events WHERE event_id=?",
                                     (event.event_id,)).fetchone()
        if existing:
            self.record_event_rejection(event.event_id, event.dept_id, event.item_id,
                                        "duplicate_event",
                                        {"disposition": existing["disposition"]})
            return Result("rejected", "duplicate_event")
        expected = 1
        if event.item_id:
            row = self.conn.execute("SELECT COALESCE(MAX(seq_no),0) AS m FROM events "
                                    "WHERE item_id=?", (event.item_id,)).fetchone()
            expected = int(row["m"]) + 1
            if event.seq_no != expected:
                self.record_event_rejection(event.event_id, event.dept_id, event.item_id,
                                            "out_of_order",
                                            {"expected": expected, "got": event.seq_no})
                return Result("rejected", "out_of_order",
                              {"expected": expected, "got": event.seq_no})
        try:
            with self.tx() as cur:
                cur.execute(
                    "INSERT INTO events(event_id,dept_id,item_id,kind,seq_no,payload_json,"
                    "admitted_at,disposition) VALUES(?,?,?,?,?,?,?,'admitted')",
                    (event.event_id, event.dept_id, event.item_id, event.kind,
                     event.seq_no, json.dumps(event.payload, sort_keys=True), time.time()))
        except sqlite3.IntegrityError:
            self.record_event_rejection(event.event_id, event.dept_id, event.item_id,
                                        "duplicate_event", {})
            return Result("rejected", "duplicate_event")
        return Result("admitted")

    def record_event_rejection(self, event_id: str, dept_id: Optional[str],
                               item_id: Optional[str], reason: str, detail: dict) -> None:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO event_rejections(event_id,dept_id,item_id,reason,detail,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (event_id, dept_id, item_id, reason, json.dumps(detail, sort_keys=True),
                 time.time()))

    def event_row(self, event_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM events WHERE event_id=?",
                                (event_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        return out

    def pending_events(self, dept_id: Optional[str] = None) -> list[dict]:
        q = "SELECT * FROM events WHERE processed_at IS NULL"
        args: tuple = ()
        if dept_id:
            q += " AND dept_id=?"
            args = (dept_id,)
        q += " ORDER BY event_seq"
        rows = []
        for r in self.conn.execute(q, args):
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            rows.append(d)
        return rows

    def mark_event_processed(self, event_id: str, disposition: str,
                             reject_reason: Optional[str] = None) -> None:
        with self.tx() as cur:
            cur.execute("UPDATE events SET processed_at=?, disposition=?, reject_reason=? "
                        "WHERE event_id=?", (time.time(), disposition, reject_reason, event_id))

    def rejections_for(self, dept_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM event_rejections WHERE dept_id=? ORDER BY id", (dept_id,))]

    # ---------------- board events ----------------
    def admit_board_event(self, external_event_id: str, dept_id: str, task_id: str,
                          kind: str, subscription_profile: Optional[str]) -> bool:
        try:
            with self.tx() as cur:
                cur.execute(
                    "INSERT INTO board_events(external_event_id,dept_id,task_id,kind,"
                    "subscription_profile,admitted_at) VALUES(?,?,?,?,?,?)",
                    (external_event_id, dept_id, task_id, kind,
                     subscription_profile, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False

    def pending_board_events(self, dept_id: Optional[str] = None) -> list[dict]:
        q = "SELECT * FROM board_events WHERE processed_at IS NULL"
        args: tuple = ()
        if dept_id:
            q += " AND dept_id=?"
            args = (dept_id,)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY admitted_at", args)]

    def board_event_processed(self, external_event_id: str) -> bool:
        row = self.conn.execute("SELECT processed_at FROM board_events WHERE external_event_id=?",
                                (external_event_id,)).fetchone()
        return bool(row and row["processed_at"])

    def mark_board_event_processed(self, external_event_id: str, receipt_key: str) -> None:
        with self.tx() as cur:
            cur.execute("UPDATE board_events SET processed_at=?, receipt_key=? "
                        "WHERE external_event_id=?",
                        (time.time(), receipt_key, external_event_id))

    # ---------------- delivery ----------------
    def create_delivery(self, delivery_id: str, dept_id: str, report_task_id: str,
                        operation_key: str, payload_sha: str, marker: str,
                        body: str) -> dict:
        now = time.time()
        try:
            with self.tx() as cur:
                cur.execute(
                    "INSERT INTO deliveries(delivery_id,dept_id,report_task_id,kind,"
                    "operation_key,payload_sha,marker,status,attempts,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                    (delivery_id, dept_id, report_task_id, "final", operation_key,
                     payload_sha, marker, "intent", now, now))
                cur.execute(
                    "INSERT INTO outbox(delivery_id,operation_key,body,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (delivery_id, operation_key, body, "pending", now, now))
        except sqlite3.IntegrityError:
            pass
        return self.delivery_by_key(operation_key)

    def delivery_by_key(self, operation_key: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM deliveries WHERE operation_key=?",
                                (operation_key,)).fetchone()
        return dict(row) if row else None

    def latest_delivery(self, dept_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM deliveries WHERE dept_id=? ORDER BY created_at DESC LIMIT 1",
            (dept_id,)).fetchone()
        return dict(row) if row else None

    def deliveries_for(self, dept_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM deliveries WHERE dept_id=? ORDER BY created_at", (dept_id,))]

    def outbox_for(self, operation_key: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM outbox WHERE operation_key=?",
                                (operation_key,)).fetchone()
        return dict(row) if row else None

    def set_delivery_status(self, delivery_id: str, status: str) -> None:
        with self.tx() as cur:
            cur.execute("UPDATE deliveries SET status=?, updated_at=? WHERE delivery_id=?",
                        (status, time.time(), delivery_id))

    def increment_attempt(self, delivery_id: str) -> int:
        with self.tx() as cur:
            cur.execute("UPDATE deliveries SET attempts=attempts+1, updated_at=? "
                        "WHERE delivery_id=?", (time.time(), delivery_id))
            row = cur.execute("SELECT attempts FROM deliveries WHERE delivery_id=?",
                              (delivery_id,)).fetchone()
            return int(row["attempts"])

    def set_outbox_status(self, operation_key: str, status: str) -> None:
        with self.tx() as cur:
            cur.execute("UPDATE outbox SET status=?, updated_at=? WHERE operation_key=?",
                        (status, time.time(), operation_key))

    def add_receipt(self, dept_id: str, operation_key: str, external_ref: str,
                    evidence: dict) -> None:
        try:
            with self.tx() as cur:
                cur.execute(
                    "INSERT INTO receipts(dept_id,operation_key,external_ref,evidence_json,"
                    "observed_at) VALUES(?,?,?,?,?)",
                    (dept_id, operation_key, external_ref,
                     json.dumps(evidence, sort_keys=True), time.time()))
        except sqlite3.IntegrityError:
            pass

    def has_receipt(self, operation_key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM receipts WHERE operation_key=?",
                                 (operation_key,)).fetchone() is not None

    def receipts_for(self, dept_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM receipts WHERE dept_id=? ORDER BY receipt_id", (dept_id,))]

    def add_alert(self, dept_id: Optional[str], item_id: Optional[str], kind: str,
                  detail: dict) -> None:
        with self.tx() as cur:
            cur.execute("INSERT INTO alerts(dept_id,item_id,kind,detail,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (dept_id, item_id, kind, json.dumps(detail, sort_keys=True),
                         time.time()))

    def alerts_for(self, dept_id: str, kind: Optional[str] = None,
                   unresolved_only: bool = False) -> list[dict]:
        q = "SELECT * FROM alerts WHERE dept_id=?"
        args: list = [dept_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if unresolved_only:
            q += " AND resolved_at IS NULL"
        return [dict(r) for r in self.conn.execute(q + " ORDER BY alert_id", args)]

    def resolve_alerts(self, dept_id: str, kind: str) -> None:
        with self.tx() as cur:
            cur.execute("UPDATE alerts SET resolved_at=? WHERE dept_id=? AND kind=? "
                        "AND resolved_at IS NULL", (time.time(), dept_id, kind))

    def record_gate_rejection(self, dept_id: str, reason: str, detail: dict) -> None:
        with self.tx() as cur:
            cur.execute("INSERT INTO gate_rejections(dept_id,reason,detail,created_at) "
                        "VALUES(?,?,?,?)",
                        (dept_id, reason, json.dumps(detail, sort_keys=True), time.time()))

    def gate_rejections_for(self, dept_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM gate_rejections WHERE dept_id=? ORDER BY id", (dept_id,))]

"""CLI used by crash-recovery tests and by the report harness.

Only the prototype DB and prototype journal are written. In the live lab the
Kanban board is accessed through public CLI read commands plus one final
comment, never through SQLite.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from .board import JsonBoard
from .crash import crashpoint
from .effects import FileJournalPort
from .events import Event
from .orchestrator import Orchestrator
from .store import Store

DEPT = "dept-eng"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@contextmanager
def writer_lock(directory: Path):
    """One trusted writer at a time (OS file lock, not an ACL)."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "dept.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _open(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    store = Store.open(directory / "dept.db")
    board = JsonBoard(directory / "board.json")
    port = FileJournalPort(directory / "effects.jsonl")
    return store, board, port, Orchestrator(store, board=board, effect_port=port)


def _seed_tasks(board: JsonBoard) -> None:
    board.add_task("t_report")
    board.add_task("t_setup")
    board.add_task("t_work", parents=["t_setup"])


def _seed_accepted(directory: Path) -> None:
    store, board, port, orch = _open(directory)
    _seed_tasks(board)
    orch.register_department(DEPT, "Engineering", "manager", "t_report",
                             manager_root_task_id="t_root")
    item = orch.register_work_item(DEPT, "t_work", "work", "manager",
                                   parents=["t_setup"])
    art = directory / "artifacts"
    art.mkdir(exist_ok=True)
    p1 = art / "rev1.txt"
    p1.write_text("synthetic artifact rev1\n")
    orch.ingest_event(Event("seed-1", DEPT, item.item_id, "artifact.published", 1,
                            {"rev": 1, "sha256": sha(p1.read_text()),
                             "path": str(p1), "producer": "synthetic-worker"}))
    orch.ingest_event(Event("seed-2", DEPT, item.item_id, "review.requested", 2,
                            {"reviewer_profile": "reviewer",
                             "review_task_id": "t_review"}))
    orch.ingest_event(Event("seed-3", DEPT, item.item_id, "review.verdict", 3,
                            {"verdict": "PASS", "reviewer_profile": "reviewer",
                             "review_task_id": "t_review",
                             "artifact_sha": sha(p1.read_text())}))
    verdict = store.latest_pass(item.item_id)
    orch.ingest_event(Event("seed-4", DEPT, item.item_id, "manager.acceptance", 4,
                            {"accepted_by": "manager",
                             "item_id": item.item_id,
                             "verdict_id": verdict["verdict_id"],
                             "artifact_sha": verdict["artifact_sha"],
                             "rev": 1}))
    store.close()


def _status(directory: Path, dept: str) -> dict:
    store, board, port, orch = _open(directory)
    dept_row = store.get_department(dept)
    items = store.items_for(dept) if dept_row else []
    item = next((i for i in items if i["role"] == "work"), None)
    delivery = store.latest_delivery(dept) if dept_row else None
    journal = []
    jp = directory / "effects.jsonl"
    if jp.exists():
        journal = [json.loads(x) for x in jp.read_text().splitlines() if x.strip()]
    all_receipts = store.receipts_for(dept) if dept_row else []
    out = {
        "dept": dept,
        "item_status": item["status"] if item else None,
        "item_id": item["item_id"] if item else None,
        "artifacts": len(store.artifacts_for(item["item_id"])) if item else 0,
        "artifact": store.latest_artifact(item["item_id"]) if item else None,
        "verdicts": len(store.verdicts_for(item["item_id"])) if item else 0,
        "acceptances": len(store.acceptances_for(item["item_id"])) if item else 0,
        "pending_events": len(store.pending_events(dept)) if dept_row else 0,
        "delivery": delivery,
        "receipts": len(all_receipts),
        "delivery_receipts": len([r for r in all_receipts
                                  if r["operation_key"].startswith("final:")]),
        "event_receipts": len([r for r in all_receipts
                               if r["operation_key"].startswith("event:")]),
        "alerts": store.alerts_for(dept) if dept_row else [],
        "gate_rejections": store.gate_rejections_for(dept) if dept_row else [],
        "journal": journal,
    }
    store.close()
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="deptproto")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--dir", required=True)

    p_deliver = sub.add_parser("deliver")
    p_deliver.add_argument("--dir", required=True)
    p_deliver.add_argument("--dept", default=DEPT)
    p_deliver.add_argument("--crash-at")

    p_recover = sub.add_parser("recover")
    p_recover.add_argument("--dir", required=True)
    p_recover.add_argument("--dept", default=DEPT)

    p_status = sub.add_parser("status")
    p_status.add_argument("--dir", required=True)
    p_status.add_argument("--dept", default=DEPT)

    p_ing = sub.add_parser("ingest-crash")
    p_ing.add_argument("--dir", required=True)
    p_ing.add_argument("--crash-at", default="after_admit_before_process")

    p_rev = sub.add_parser("recover-events")
    p_rev.add_argument("--dir", required=True)
    p_rev.add_argument("--dept", default=DEPT)

    p_vc = sub.add_parser("verdict-crash")
    p_vc.add_argument("--dir", required=True)
    p_vc.add_argument("--crash-at", required=True,
                      choices=["after_verdict_insert",
                               "after_mutation_before_receipt",
                               "after_receipt_before_processed",
                               "after_processed"])

    p_pub = sub.add_parser("publish")
    p_pub.add_argument("--dir", required=True)
    p_pub.add_argument("--text", required=True)
    p_pub.add_argument("--dept", default=DEPT)

    args = parser.parse_args(argv)
    directory = Path(args.dir)

    if args.cmd == "seed":
        with writer_lock(directory):
            _seed_accepted(directory)
        print(json.dumps({"seeded": True, "dir": str(directory)}))
        return 0

    if args.cmd == "deliver":
        if args.crash_at:
            import os
            os.environ["DEPT_CRASH_AT"] = args.crash_at
            os.environ.setdefault("DEPT_CRASH_EVIDENCE",
                                  str(directory / "crash-evidence.json"))
        with writer_lock(directory):
            store, board, port, orch = _open(directory)
            result = orch.deliver_final(args.dept)
            store.close()
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0

    if args.cmd == "recover":
        with writer_lock(directory):
            store, board, port, orch = _open(directory)
            result = orch.recover(args.dept)
            store.close()
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0

    if args.cmd == "status":
        print(json.dumps(_status(directory, args.dept), sort_keys=True, ensure_ascii=False))
        return 0

    if args.cmd == "ingest-crash":
        import os
        os.environ["DEPT_CRASH_AT"] = args.crash_at
        os.environ.setdefault("DEPT_CRASH_EVIDENCE",
                              str(directory / "crash-evidence.json"))
        with writer_lock(directory):
            store, board, port, orch = _open(directory)
            _seed_tasks(board)
            orch.register_department(DEPT, "Engineering", "manager", "t_report",
                                     manager_root_task_id="t_root")
            item = orch.register_work_item(DEPT, "t_work", "work", "manager",
                                           parents=["t_setup"])
            art = directory / "artifacts"
            art.mkdir(exist_ok=True)
            p1 = art / "rev1.txt"
            p1.write_text("synthetic artifact rev1\n")
            store.admit_event(Event("crash-1", DEPT, item.item_id,
                                    "artifact.published", 1,
                                    {"rev": 1, "sha256": sha(p1.read_text()),
                                     "path": str(p1), "producer": "synthetic-worker"}))
            crashpoint("after_admit_before_process")
            store.close()
        return 0

    if args.cmd == "verdict-crash":
        import os
        with writer_lock(directory):
            store, board, port, orch = _open(directory)
            _seed_tasks(board)
            orch.register_department(DEPT, "Engineering", "manager", "t_report",
                                     manager_root_task_id="t_root")
            item = orch.register_work_item(DEPT, "t_work", "work", "manager",
                                           parents=["t_setup"])
            art = directory / "artifacts"
            art.mkdir(exist_ok=True)
            p1 = art / "rev1.txt"
            p1.write_text("synthetic artifact rev1\n")
            draft = sha(p1.read_text())
            orch.ingest_event(Event("vc-1", DEPT, item.item_id, "artifact.published", 1,
                                    {"rev": 1, "sha256": draft, "path": str(p1),
                                     "producer": "synthetic-worker"}))
            orch.ingest_event(Event("vc-2", DEPT, item.item_id, "review.requested", 2,
                                    {"reviewer_profile": "reviewer",
                                     "review_task_id": "t_review"}))
            os.environ["DEPT_CRASH_AT"] = args.crash_at
            os.environ.setdefault("DEPT_CRASH_EVIDENCE",
                                  str(directory / "crash-evidence.json"))
            orch.ingest_event(Event("vc-3", DEPT, item.item_id, "review.verdict", 3,
                                    {"verdict": "PASS",
                                     "reviewer_profile": "reviewer",
                                     "review_task_id": "t_review",
                                     "artifact_sha": draft}))
            store.close()
        return 0

    if args.cmd == "publish":
        with writer_lock(directory):
            store, board, port, orch = _open(directory)
            item = next((i for i in store.items_for(args.dept)
                         if i["role"] == "work"), None)
            if item is None:
                print(json.dumps({"error": "no work item"}))
                return 2
            rev = store.next_rev(item["item_id"])
            art = directory / "artifacts"
            art.mkdir(exist_ok=True)
            path = art / f"rev{rev}.txt"
            path.write_text(args.text)
            row = store.conn.execute(
                "SELECT COALESCE(MAX(seq_no),0) AS m FROM events WHERE item_id=?",
                (item["item_id"],)).fetchone()
            seq = int(row["m"]) + 1
            res = orch.ingest_event(Event(f"pub-{rev}", args.dept, item["item_id"],
                                          "artifact.published", seq,
                                          {"rev": rev, "sha256": sha(args.text),
                                           "path": str(path),
                                           "producer": "synthetic-worker"}))
            store.close()
        print(json.dumps(res.to_dict(), sort_keys=True))
        return 0

    if args.cmd == "recover-events":
        with writer_lock(directory):
            store, board, port, orch = _open(directory)
            n = orch.process_pending(args.dept)
            store.close()
        print(json.dumps({"processed": n}))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

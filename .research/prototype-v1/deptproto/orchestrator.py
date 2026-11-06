"""Prototype orchestrator: controlled state machine, acceptance gate, delivery.

Trust model (explicit): ONE trusted writer (this process / this store). It is
not an ACL. The Hermes profile boundary is not a security boundary and the
shared Kanban board is readable by other profiles; this prototype only proves
what one trusted writer can enforce over a known DAG.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from .crash import crashpoint
from .effects import EffectPort, body_for
from .events import Event, Result
from .store import Store

MAX_ATTEMPTS = 3


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Orchestrator:
    def __init__(self, store: Store, board, effect_port: EffectPort,
                 writer_id: str = "trusted-single-writer",
                 session_profiles: Optional[dict[str, str]] = None):
        self.store = store
        self.board = board
        self.effect_port = effect_port
        self.writer_id = writer_id
        self.session_profiles = dict(session_profiles or {})

    # ---------------- configuration ----------------
    def register_department(self, dept_id: str, name: str, manager_profile: str,
                            report_task_id: str,
                            manager_root_task_id: Optional[str] = None):
        existing = self.store.get_department(dept_id)
        if existing:
            return type("Dept", (), existing)()
        row = self.store.insert_department(dept_id, name, manager_profile,
                                           report_task_id, manager_root_task_id)
        return type("Dept", (), row)()

    def register_work_item(self, dept_id: str, board_task_id: str, role: str,
                           owner_profile: str, parents: Optional[list[str]] = None):
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise KeyError(f"unknown department {dept_id}")
        parents = list(parents or [])
        if role == "work":
            if not parents:
                raise ValueError("wrong_parent_edge: work item needs a setup dependency")
            if dept.get("manager_root_task_id") and dept["manager_root_task_id"] in parents:
                raise ValueError(
                    "wrong_parent_edge: a pending manager root card cannot be the "
                    "parent of work children (parent is a dependency, not membership)")
        existing = self.store.item_by_task(dept_id, board_task_id)
        if existing:
            return type("Item", (), existing)()
        item_id = "itm-" + _h(f"{dept_id}|{board_task_id}")[:12]
        row = self.store.insert_work_item(item_id, dept_id, board_task_id, role,
                                          owner_profile, parents)
        return type("Item", (), row)()

    # ---------------- event admission ----------------
    def ingest_event(self, event: Event) -> Result:
        admitted = self.store.admit_event(event)
        if not admitted.accepted:
            return admitted
        crashpoint("after_admit_before_process")
        row = self.store.event_row(event.event_id)
        return self._process_row(row)

    def process_pending(self, dept_id: Optional[str] = None) -> int:
        count = 0
        for row in self.store.pending_events(dept_id):
            self._process_row(row)
            count += 1
        for be in self.store.pending_board_events(dept_id):
            self._process_board_event(be)
            count += 1
        return count

    def _process_row(self, row: dict) -> Result:
        if row.get("processed_at"):
            return Result("applied", None, {"already": True})
        event_id = row["event_id"]
        if self.store.has_receipt(f"event:{event_id}"):
            self.store.mark_event_processed(event_id, "applied")
            return Result("applied", None, {"idempotent": True})
        handler = getattr(self, "_on_" + row["kind"].replace(".", "_"), None)
        if handler is None:
            self.store.record_event_rejection(event_id, row["dept_id"], row.get("item_id"),
                                              "unknown_kind", {"kind": row["kind"]})
            self.store.mark_event_processed(event_id, "rejected", "unknown_kind")
            return Result("rejected", "unknown_kind")
        try:
            res = handler(row)
        except Exception as exc:  # fail closed: visible, not silently retried
            self.store.record_event_rejection(event_id, row["dept_id"], row.get("item_id"),
                                              "apply_error", {"error": repr(exc)})
            self.store.mark_event_processed(event_id, "rejected", "apply_error")
            self.store.add_alert(row["dept_id"], row.get("item_id"), "apply_error",
                                 {"event_id": event_id, "error": repr(exc)})
            return Result("rejected", "apply_error", {"error": repr(exc)})
        if res.disposition != "applied":
            self.store.record_event_rejection(event_id, row["dept_id"], row.get("item_id"),
                                              res.reason or "rejected", res.detail)
            self.store.mark_event_processed(event_id, "rejected", res.reason)
            self.store.add_alert(row["dept_id"], row.get("item_id"),
                                 "event_rejected",
                                 {"event_id": event_id, "reason": res.reason,
                                  "detail": res.detail})
            return res
        self.store.add_receipt(row["dept_id"], f"event:{event_id}", f"event:{event_id}",
                               {"kind": row["kind"], "seq_no": row["seq_no"]})
        self.store.mark_event_processed(event_id, "applied")
        return res

    # ---------------- domain handlers ----------------
    def _on_artifact_published(self, row: dict) -> Result:
        p = row["payload"]
        item = self.store.get_item(row["item_id"])
        if item is None:
            return Result("rejected", "unknown_item")
        rev = int(p["rev"])
        sha = p["sha256"]
        path = Path(p["path"])
        if not path.is_file():
            return Result("rejected", "artifact_missing", {"path": str(path)})
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != sha:
            return Result("rejected", "artifact_mismatch",
                          {"declared": sha, "actual": actual})
        current = self.store.latest_artifact(item["item_id"])
        if current and current["rev"] == rev and current["sha256"] == sha:
            return Result("applied", None, {"already": True})
        if rev != item["current_rev"] + 1:
            return Result("rejected", "wrong_revision",
                          {"expected": item["current_rev"] + 1, "got": rev})
        artifact_id = "art-" + _h(f"{item['item_id']}|{rev}|{sha}")[:12]
        self.store.insert_artifact(artifact_id, item["item_id"], rev, sha,
                                   str(path), p.get("producer", "unknown"))
        target = "artifact_ready" if rev == 1 else "rework_ready"
        self.store.set_item_status(item["item_id"], target,
                                   f"artifact rev {rev} published", self.writer_id)
        return Result("applied", None, {"rev": rev, "sha256": sha})

    def _on_review_requested(self, row: dict) -> Result:
        p = row["payload"]
        item = self.store.get_item(row["item_id"])
        if item is None:
            return Result("rejected", "unknown_item")
        dept = self.store.get_department(item["dept_id"])
        latest = self.store.latest_artifact(item["item_id"])
        if latest is None:
            return Result("rejected", "no_artifact")
        review_task = p.get("review_task_id") or ""
        if not review_task:
            return Result("rejected", "missing_review_task")
        if review_task == dept["report_task_id"]:
            return Result("rejected", "wrong_report_edge",
                          {"review_task_id": review_task})
        reviewer = p.get("reviewer_profile") or ""
        if not reviewer:
            return Result("rejected", "wrong_owner", {"reviewer_profile": reviewer})
        if reviewer == item["owner_profile"]:
            return Result("rejected", "wrong_owner",
                          {"reviewer_profile": reviewer, "owner": item["owner_profile"]})
        existing = self.store.latest_request(item["item_id"])
        if existing and existing["rev"] == latest["rev"]:
            return Result("rejected", "duplicate_review", {"rev": latest["rev"]})
        request_id = "req-" + _h(f"{item['item_id']}|{latest['rev']}")[:12]
        req = self.store.create_review_request(
            request_id, item["item_id"], latest["artifact_id"], latest["rev"],
            reviewer, review_task)
        run_id = "run-" + _h(request_id)[:12]
        self.store.create_review_run(run_id, req)
        self.store.set_item_status(item["item_id"], "in_review",
                                   f"review requested for rev {latest['rev']}",
                                   self.writer_id)
        return Result("applied", None, {"request_id": request_id, "rev": latest["rev"]})

    def _on_review_verdict(self, row: dict) -> Result:
        p = row["payload"]
        item = self.store.get_item(row["item_id"])
        if item is None:
            return Result("rejected", "unknown_item")
        request = self.store.latest_request(item["item_id"])
        if request is None:
            return Result("rejected", "out_of_order", {"why": "no review request"})
        if p.get("review_task_id") != request["review_task_id"]:
            return Result("rejected", "wrong_owner",
                          {"expected_task": request["review_task_id"],
                           "got": p.get("review_task_id")})
        if p.get("reviewer_profile") != request["reviewer_profile"]:
            return Result("rejected", "wrong_owner",
                          {"expected": request["reviewer_profile"],
                           "got": p.get("reviewer_profile")})
        run = self.store.run_by_request(request["request_id"])
        existing = self.store.verdict_for_run(run["run_id"])
        if existing:
            return Result("applied", None, {"already": True,
                                            "verdict_id": existing["verdict_id"]})
        if request["status"] != "open":
            return Result("rejected", "duplicate_verdict",
                          {"request_status": request["status"]})
        if request["rev"] != item["current_rev"]:
            return Result("rejected", "stale_verdict",
                          {"request_rev": request["rev"],
                           "current_rev": item["current_rev"]})
        if p.get("artifact_sha") is not None and p.get("artifact_sha") != run["artifact_sha"]:
            return Result("rejected", "wrong_artifact",
                          {"expected": run["artifact_sha"],
                           "got": p.get("artifact_sha")})
        verdict = p.get("verdict")
        if verdict not in ("PASS", "REQUEST_CHANGES"):
            return Result("rejected", "unknown_verdict", {"verdict": verdict})
        verdict_id = "vrd-" + _h(f"{run['run_id']}|{verdict}")[:12]
        self.store.add_verdict(verdict_id, run["run_id"], request["request_id"],
                               item["item_id"], verdict, run["artifact_sha"],
                               request["reviewer_profile"])
        self.store.set_item_status(
            item["item_id"],
            "approved" if verdict == "PASS" else "changes_requested",
            f"verdict {verdict} for rev {request['rev']}", request["reviewer_profile"])
        return Result("applied", None, {"verdict_id": verdict_id, "verdict": verdict})

    def _on_manager_acceptance(self, row: dict) -> Result:
        p = row["payload"]
        item = self.store.get_item(row["item_id"])
        if item is None:
            return Result("rejected", "unknown_item")
        dept = self.store.get_department(item["dept_id"])
        if p.get("accepted_by") != dept["manager_profile"]:
            return Result("rejected", "wrong_owner",
                          {"expected": dept["manager_profile"],
                           "got": p.get("accepted_by")})
        if self.store.acceptance_for(item["item_id"]):
            return Result("rejected", "duplicate_acceptance")
        verdict = (self.store.verdict_by_id(p["verdict_id"])
                   if p.get("verdict_id") else self.store.latest_pass(item["item_id"]))
        if verdict is None:
            return Result("rejected", "wrong_acceptance", {"why": "no PASS verdict"})
        if verdict["verdict"] != "PASS":
            return Result("rejected", "wrong_acceptance",
                          {"why": "verdict is not PASS",
                           "verdict": verdict["verdict"]})
        latest = self.store.latest_artifact(item["item_id"])
        if latest is None or verdict["artifact_sha"] != latest["sha256"]:
            return Result("rejected", "stale_pass",
                          {"verdict_sha": verdict["artifact_sha"],
                           "latest_sha": latest["sha256"] if latest else None})
        request = self.store.request_by_id(verdict["request_id"])
        if request["rev"] != item["current_rev"]:
            return Result("rejected", "stale_pass",
                          {"verdict_rev": request["rev"],
                           "current_rev": item["current_rev"]})
        if item["status"] != "approved":
            return Result("rejected", "wrong_acceptance",
                          {"item_status": item["status"]})
        acceptance_id = "acc-" + _h(item["item_id"])[:12]
        self.store.insert_acceptance(acceptance_id, dept["dept_id"], item["item_id"],
                                     verdict["verdict_id"], verdict["artifact_sha"],
                                     p["accepted_by"])
        self.store.set_item_status(item["item_id"], "accepted",
                                   f"manager accepted {verdict['verdict_id']}",
                                   p["accepted_by"])
        return Result("applied", None, {"acceptance_id": acceptance_id})

    # ---------------- acceptance gate ----------------
    def _gate(self, dept: dict) -> tuple[bool, Optional[str], dict]:
        report_task = dept["report_task_id"]
        items = [i for i in self.store.items_for(dept["dept_id"]) if i["role"] != "report"]
        work_ids = [i["board_task_id"] for i in items]
        if report_task in work_ids:
            return False, "wrong_report_edge", {"report": report_task, "reason": "same task"}
        snap = self.board.task(report_task)
        if snap is None:
            return False, "report_missing", {"report": report_task}
        edges = set(snap.get("parents") or []) | set(snap.get("children") or [])
        bad = edges & set(work_ids)
        if bad:
            return False, "wrong_report_edge", {
                "report": report_task, "linked_work_tasks": sorted(bad)}
        work_items = [i for i in items if i["role"] == "work"]
        if not work_items:
            return False, "not_approved", {"why": "no work item"}
        for item in work_items:
            ok, reason, detail = self._gate_item(item)
            if not ok:
                return False, reason, detail
        return True, None, {"items": [self._report_facts(i) for i in work_items]}

    def _gate_item(self, item: dict) -> tuple[bool, Optional[str], dict]:
        acceptance = self.store.acceptance_for(item["item_id"])
        latest = self.store.latest_artifact(item["item_id"])
        if acceptance is None:
            verdict = self.store.latest_verdict(item["item_id"])
            if verdict and verdict["verdict"] == "PASS":
                return False, "no_acceptance", {"verdict_id": verdict["verdict_id"]}
            if item["status"] == "changes_requested":
                return False, "not_approved", {"status": item["status"]}
            return False, "not_approved", {"status": item["status"]}
        if latest is None:
            return False, "not_approved", {"why": "no artifact"}
        if acceptance["artifact_sha"] != latest["sha256"]:
            return False, "stale_pass", {"accepted_sha": acceptance["artifact_sha"],
                                         "latest_sha": latest["sha256"]}
        verdict = self.store.verdict_by_id(acceptance["verdict_id"])
        if verdict is None or verdict["verdict"] != "PASS":
            return False, "wrong_artifact", {"verdict": verdict and verdict["verdict"]}
        if verdict["artifact_sha"] != latest["sha256"]:
            return False, "wrong_artifact", {"verdict_sha": verdict["artifact_sha"],
                                             "latest_sha": latest["sha256"]}
        request = self.store.request_by_id(verdict["request_id"])
        if request["rev"] != latest["rev"]:
            return False, "stale_pass", {"verdict_rev": request["rev"],
                                         "latest_rev": latest["rev"]}
        if item["status"] not in ("accepted", "reported"):
            return False, "not_approved", {"status": item["status"]}
        return True, None, self._report_facts(item)

    def _report_facts(self, item: dict) -> dict:
        acceptance = self.store.acceptance_for(item["item_id"])
        verdict = self.store.verdict_by_id(acceptance["verdict_id"])
        request = self.store.request_by_id(verdict["request_id"])
        latest = self.store.latest_artifact(item["item_id"])
        return {
            "item_id": item["item_id"],
            "board_task_id": item["board_task_id"],
            "acceptance_id": acceptance["acceptance_id"],
            "verdict_id": verdict["verdict_id"],
            "evidence_sha256": latest["sha256"],
            "artifact_sha256": latest["sha256"],
            "review_rev": request["rev"],
            "reviewer_profile": verdict["reviewer_profile"],
        }

    # ---------------- delivery ----------------
    def deliver_final(self, dept_id: str) -> Result:
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise KeyError(dept_id)
        ok, reason, detail = self._gate(dept)
        latest_delivery = self.store.latest_delivery(dept_id)
        if latest_delivery and latest_delivery["status"] == "confirmed":
            return Result("already_delivered", None, {"delivery_id":
                                                      latest_delivery["delivery_id"]})
        if not ok:
            self.store.record_gate_rejection(dept_id, reason or "held", detail)
            return Result("held", reason, detail)
        if latest_delivery:
            if latest_delivery["status"] == "dead_letter":
                return Result("dead_letter", "max_attempts",
                              {"delivery_id": latest_delivery["delivery_id"]})
            return self._drive_delivery(latest_delivery)
        report = detail["items"][0]
        operation_key = f"final:{dept_id}:{report['acceptance_id']}"
        marker = f"[[deptproto-final:{_h(operation_key)[:16]}]]"
        delivery_id = "dlv-" + _h(operation_key)[:12]
        body = body_for(dept_id, delivery_id, report, marker)
        delivery = self.store.create_delivery(
            delivery_id, dept_id, dept["report_task_id"], operation_key,
            report["artifact_sha256"], marker, body)
        crashpoint("after_intent")
        return self._drive_delivery(delivery)

    def _drive_delivery(self, d: dict) -> Result:
        delivery_id = d["delivery_id"]
        dept_id = d["dept_id"]
        op_key = d["operation_key"]
        current = self.store.delivery_by_key(op_key)
        if current["status"] == "confirmed":
            return Result("already_delivered", None, {"delivery_id": delivery_id})
        if current["status"] == "dead_letter":
            return Result("dead_letter", "max_attempts", {"delivery_id": delivery_id})
        outbox = self.store.outbox_for(op_key)
        body = outbox["body"]
        marker = current["marker"]
        crashpoint("before_effect")
        ref = self.effect_port.find(marker)
        if ref is None:
            self.store.increment_attempt(delivery_id)
            self.store.set_delivery_status(delivery_id, "pending")
            try:
                self.effect_port.apply(marker, body)
            except Exception as exc:
                self.store.add_alert(dept_id, None, "effect_apply_error",
                                     {"delivery_id": delivery_id, "error": repr(exc)})
            crashpoint("after_effect_before_receipt")
            ref = self.effect_port.find(marker)
        if ref is None:
            self.store.set_delivery_status(delivery_id, "effect_unknown")
            self.store.set_outbox_status(op_key, "unknown")
            self.store.add_alert(dept_id, None, "unknown_effect",
                                 {"delivery_id": delivery_id,
                                  "why": "effect not readable after apply"})
            return Result("unknown", "effect_unknown", {"delivery_id": delivery_id})
        if not self.store.has_receipt(op_key):
            self.store.add_receipt(dept_id, op_key, json.dumps(ref, sort_keys=True), ref)
        crashpoint("after_receipt_before_confirm")
        self.store.set_delivery_status(delivery_id, "confirmed")
        self.store.set_outbox_status(op_key, "delivered")
        for item in self.store.items_for(dept_id):
            if item["role"] == "work" and item["status"] == "accepted":
                self.store.set_item_status(item["item_id"], "reported",
                                           "single final delivered", self.writer_id)
        self.store.resolve_alerts(dept_id, "unknown_effect")
        crashpoint("after_confirm")
        return Result("confirmed", None, {"delivery_id": delivery_id})

    def recover(self, dept_id: str) -> Result:
        d = self.store.latest_delivery(dept_id)
        if d is None:
            return Result("held", "no_delivery")
        if d["status"] == "confirmed":
            return Result("already_delivered", None, {"delivery_id": d["delivery_id"]})
        if d["status"] == "dead_letter":
            return Result("dead_letter", "max_attempts", {"delivery_id": d["delivery_id"]})
        outbox = self.store.outbox_for(d["operation_key"])
        ref = self.effect_port.find(d["marker"])
        if ref is not None:
            if not self.store.has_receipt(d["operation_key"]):
                self.store.add_receipt(dept_id, d["operation_key"],
                                       json.dumps(ref, sort_keys=True), ref)
            crashpoint("after_receipt_before_confirm")
            self.store.set_delivery_status(d["delivery_id"], "confirmed")
            self.store.set_outbox_status(d["operation_key"], "delivered")
            for item in self.store.items_for(dept_id):
                if item["role"] == "work" and item["status"] == "accepted":
                    self.store.set_item_status(item["item_id"], "reported",
                                               "final reconciled after restart",
                                               self.writer_id)
            self.store.resolve_alerts(dept_id, "unknown_effect")
            return Result("confirmed", None, {"delivery_id": d["delivery_id"],
                                              "reconciled": True})
        attempt = self.store.increment_attempt(d["delivery_id"])
        if attempt > MAX_ATTEMPTS:
            return self._dead_letter(d, "max_attempts")
        try:
            self.effect_port.apply(d["marker"], outbox["body"])
        except Exception as exc:
            self.store.add_alert(dept_id, None, "effect_apply_error",
                                 {"delivery_id": d["delivery_id"], "error": repr(exc)})
        ref = self.effect_port.find(d["marker"])
        if ref is None:
            if attempt >= MAX_ATTEMPTS:
                return self._dead_letter(d, "unreadable_effect")
            self.store.set_delivery_status(d["delivery_id"], "effect_unknown")
            self.store.set_outbox_status(d["operation_key"], "unknown")
            self.store.add_alert(dept_id, None, "unknown_effect",
                                 {"delivery_id": d["delivery_id"],
                                  "attempt": attempt})
            return Result("unknown", "effect_unknown", {"delivery_id": d["delivery_id"],
                                                        "attempt": attempt})
        if not self.store.has_receipt(d["operation_key"]):
            self.store.add_receipt(dept_id, d["operation_key"],
                                   json.dumps(ref, sort_keys=True), ref)
        self.store.set_delivery_status(d["delivery_id"], "confirmed")
        self.store.set_outbox_status(d["operation_key"], "delivered")
        for item in self.store.items_for(dept_id):
            if item["role"] == "work" and item["status"] == "accepted":
                self.store.set_item_status(item["item_id"], "reported",
                                           "final reconciled after restart", self.writer_id)
        self.store.resolve_alerts(dept_id, "unknown_effect")
        return Result("confirmed", None, {"delivery_id": d["delivery_id"],
                                          "reconciled": True})

    def _dead_letter(self, d: dict, why: str) -> Result:
        self.store.set_delivery_status(d["delivery_id"], "dead_letter")
        self.store.set_outbox_status(d["operation_key"], "dead_letter")
        self.store.add_alert(d["dept_id"], None, "delivery_dead_letter",
                             {"delivery_id": d["delivery_id"], "why": why})
        return Result("dead_letter", why, {"delivery_id": d["delivery_id"]})

    # ---------------- board event adapter (live-lab shape) ----------------
    def ingest_board_event(self, dept_id: str, raw: dict) -> Result:
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise KeyError(dept_id)
        task_id = raw["task_id"]
        item = self.store.item_by_task(dept_id, task_id)
        expected_owner = item["owner_profile"] if item else None
        if expected_owner is None:
            self.store.add_alert(dept_id, None, "unknown_task", {"task_id": task_id})
            return Result("dead_letter", "unknown_task", {"task_id": task_id})
        sub = raw.get("subscription")
        if not sub:
            self.store.add_alert(dept_id, None, "missing_subscription",
                                 {"task_id": task_id,
                                  "external_event_id": raw["external_event_id"]})
            return Result("dead_letter", "missing_subscription", {"task_id": task_id})
        if sub.get("notifier_profile") != expected_owner:
            self.store.add_alert(dept_id, None, "wrong_owner",
                                 {"task_id": task_id,
                                  "expected": expected_owner,
                                  "got": sub.get("notifier_profile")})
            return Result("dead_letter", "wrong_owner",
                          {"expected": expected_owner,
                           "got": sub.get("notifier_profile")})
        chat_profile = self.session_profiles.get(sub.get("chat_id"))
        if chat_profile is not None and chat_profile != expected_owner:
            self.store.add_alert(dept_id, None, "wrong_owner",
                                 {"task_id": task_id,
                                  "expected": expected_owner,
                                  "subscription_profile": sub.get("notifier_profile"),
                                  "chat_id_profile": chat_profile})
            return Result("dead_letter", "wrong_owner",
                          {"expected": expected_owner,
                           "chat_id_profile": chat_profile})
        if not self.store.admit_board_event(raw["external_event_id"], dept_id,
                                            task_id, raw.get("kind", "completed"),
                                            sub.get("notifier_profile")):
            return Result("rejected", "duplicate_event")
        return Result("admitted", None, {"external_event_id": raw["external_event_id"]})

    def _process_board_event(self, be: dict) -> Result:
        eid = be["external_event_id"]
        key = f"board-event:{eid}"
        if not self.store.has_receipt(key):
            self.store.add_receipt(be["dept_id"], key, eid,
                                   {"task_id": be["task_id"], "kind": be["kind"]})
        self.store.mark_board_event_processed(eid, key)
        return Result("processed")

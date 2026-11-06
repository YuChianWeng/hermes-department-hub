"""Prototype orchestrator v2: controlled state machine, acceptance gate, delivery.

Trust model (explicit): ONE trusted writer (this process / this store). It is
not an ACL. The Hermes profile boundary is not a security boundary and the
shared Kanban board is readable by other profiles; this prototype only proves
what one trusted writer can enforce over a known DAG.

v2 changes are driven by the six P1 findings in the independent v1 review:
exact artifact/verdict/acceptance identity, a three-state effect read-back,
recovery that re-runs the current gate, fully detached report verification,
unknown-owner fail-closed admission, and one transaction per domain mutation +
state transition + receipt + processed marker.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from .crash import crashpoint
from .effects import (ABSENT, CONFLICT, FOUND, UNKNOWN, EffectPort,
                      body_digest, body_for)
from .events import Event, Result
from .store import Store

MAX_ATTEMPTS = 3
MAX_EVENT_ATTEMPTS = 3


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _BoardReject(Exception):
    def __init__(self, reason: str, detail: Optional[dict] = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


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

    # ---------------- blob snapshot ----------------
    def _blob_path(self, sha: str) -> Path:
        return Path(self.store.path).parent / "blobs" / sha

    def _snapshot_blob(self, sha: str, data: bytes) -> Path:
        blob = self._blob_path(sha)
        blob.parent.mkdir(parents=True, exist_ok=True)
        if blob.exists():
            if _sha_file(blob) != sha:
                raise RuntimeError(f"content_addressed_blob_corrupt:{blob}")
            return blob
        tmp = blob.with_name(f".{sha}.{os.getpid()}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, blob)
        return blob

    def _check_artifact_bytes(self, artifact: dict) -> tuple[Optional[str], Optional[str], dict]:
        path = Path(artifact["path"])
        if not path.is_file():
            return None, "artifact_corrupt", {"why": "missing", "path": str(path)}
        try:
            actual = _sha_file(path)
        except OSError as exc:
            return None, "artifact_corrupt", {"why": "unreadable", "error": repr(exc)}
        if actual != artifact["sha256"]:
            return None, "artifact_corrupt", {"expected": artifact["sha256"],
                                              "actual": actual}
        return actual, None, {}

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
            res = self._process_row(row)
            count += 1
            if res.disposition == "retry":
                # Preserve per-item order: do not process later events on top
                # of a mutation that rolled back.
                return count
        for be in self.store.pending_board_events(dept_id):
            res = self._process_board_event(be)
            count += 1
            if res.disposition == "retry":
                return count
        return count

    def _process_row(self, row: dict) -> Result:
        if row.get("processed_at"):
            return Result("applied", None, {"already": True})
        event_id = row["event_id"]
        if self.store.has_receipt(f"event:{event_id}"):
            with self.store.tx():
                self.store.mark_event_processed(event_id, "applied")
            return Result("applied", None, {"idempotent": True})
        handler = getattr(self, "_on_" + row["kind"].replace(".", "_"), None)
        if handler is None:
            with self.store.tx():
                self.store.record_event_rejection(
                    event_id, row["dept_id"], row.get("item_id"),
                    "unknown_kind", {"kind": row["kind"]})
                self.store.mark_event_processed(event_id, "rejected", "unknown_kind")
            return Result("rejected", "unknown_kind")
        try:
            with self.store.tx():
                res = handler(row)
                if res.disposition != "applied":
                    self.store.record_event_rejection(
                        event_id, row["dept_id"], row.get("item_id"),
                        res.reason or "rejected", res.detail)
                    self.store.mark_event_processed(event_id, "rejected", res.reason)
                    self.store.add_alert(row["dept_id"], row.get("item_id"),
                                         "event_rejected",
                                         {"event_id": event_id, "reason": res.reason,
                                          "detail": res.detail})
                    return res
                crashpoint("after_mutation_before_receipt")
                self.store.add_receipt(
                    row["dept_id"], f"event:{event_id}", f"event:{event_id}",
                    {"kind": row["kind"], "seq_no": row["seq_no"]})
                crashpoint("after_receipt_before_processed")
                self.store.mark_event_processed(event_id, "applied")
            crashpoint("after_processed")
            return res
        except Exception as exc:
            # The whole domain transaction rolled back.  The event remains
            # pending and is visibly counted; it is never partially applied.
            attempts = self.store.record_event_apply_error(
                event_id, row["dept_id"], row.get("item_id"),
                {"error": repr(exc)}, MAX_EVENT_ATTEMPTS)
            self.store.add_alert(row["dept_id"], row.get("item_id"), "apply_error",
                                 {"event_id": event_id, "error": repr(exc),
                                  "attempt": attempts})
            if attempts >= MAX_EVENT_ATTEMPTS:
                return Result("failed", "apply_error",
                              {"error": repr(exc), "attempts": attempts})
            return Result("retry", "apply_error",
                          {"error": repr(exc), "attempts": attempts})

    # ---------------- domain handlers ----------------
    def _on_artifact_published(self, row: dict) -> Result:
        p = row["payload"]
        item = self.store.get_item(row["item_id"])
        if item is None:
            return Result("rejected", "unknown_item")
        try:
            rev = int(p["rev"])
        except (KeyError, TypeError, ValueError):
            return Result("rejected", "invalid_revision", {"rev": p.get("rev")})
        declared = p.get("sha256")
        if not _is_sha256(declared):
            return Result("rejected", "invalid_artifact_sha", {"sha256": declared})
        source = Path(str(p.get("path") or ""))
        if not source.is_file():
            return Result("rejected", "artifact_missing", {"path": str(source)})
        try:
            data = source.read_bytes()
        except OSError as exc:
            return Result("rejected", "artifact_unreadable", {"error": repr(exc)})
        actual = hashlib.sha256(data).hexdigest()
        if actual != declared:
            return Result("rejected", "artifact_mismatch",
                          {"declared": declared, "actual": actual})
        current = self.store.latest_artifact(item["item_id"])
        if current and current["rev"] == rev and current["sha256"] == declared:
            if not self._blob_path(declared).is_file():
                return Result("rejected", "artifact_corrupt",
                              {"sha256": declared, "why": "blob_missing"})
            return Result("applied", None, {"already": True})
        if rev != item["current_rev"] + 1:
            return Result("rejected", "wrong_revision",
                          {"expected": item["current_rev"] + 1, "got": rev})
        blob = self._snapshot_blob(declared, data)
        artifact_id = "art-" + _h(f"{item['item_id']}|{rev}|{declared}")[:12]
        # A new revision invalidates any earlier manager acceptance: the
        # accepted bytes are no longer the current artifact.
        self.store.supersede_acceptances(item["item_id"], "new_artifact_rev")
        self.store.insert_artifact(artifact_id, item["item_id"], rev, declared,
                                   str(blob), p.get("producer", "unknown"),
                                   source_path=str(source))
        target = "artifact_ready" if rev == 1 else "rework_ready"
        self.store.set_item_status(item["item_id"], target,
                                   f"artifact rev {rev} published", self.writer_id)
        crashpoint("after_artifact_insert")
        return Result("applied", None, {"rev": rev, "sha256": declared,
                                        "blob": str(blob)})

    def _on_review_requested(self, row: dict) -> Result:
        p = row["payload"]
        item = self.store.get_item(row["item_id"])
        if item is None:
            return Result("rejected", "unknown_item")
        dept = self.store.get_department(item["dept_id"])
        latest = self.store.latest_artifact(item["item_id"])
        if latest is None:
            return Result("rejected", "no_artifact")
        _, reason, detail = self._check_artifact_bytes(latest)
        if reason:
            return Result("rejected", reason, detail)
        review_task = p.get("review_task_id") or ""
        if not review_task:
            return Result("rejected", "missing_review_task")
        if review_task == dept["report_task_id"]:
            return Result("rejected", "wrong_report_edge",
                          {"review_task_id": review_task})
        if review_task == item["board_task_id"]:
            return Result("rejected", "wrong_review_edge",
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
        if run is None:
            return Result("rejected", "out_of_order", {"why": "no review run"})
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
        latest = self.store.latest_artifact(item["item_id"])
        if latest is None or latest["rev"] != request["rev"]:
            return Result("rejected", "stale_verdict",
                          {"why": "latest artifact differs from review rev"})
        actual, reason, detail = self._check_artifact_bytes(latest)
        if reason:
            return Result("rejected", reason, detail)
        declared = p.get("artifact_sha")
        if not _is_sha256(declared):
            return Result("rejected", "missing_artifact_sha",
                          {"artifact_sha": declared})
        if declared != run["artifact_sha"] or declared != actual:
            return Result("rejected", "wrong_artifact",
                          {"expected": run["artifact_sha"], "actual": actual,
                           "got": declared})
        verdict = p.get("verdict")
        if verdict not in ("PASS", "REQUEST_CHANGES"):
            return Result("rejected", "unknown_verdict", {"verdict": verdict})
        verdict_id = "vrd-" + _h(f"{run['run_id']}|{verdict}")[:12]
        self.store.add_verdict(verdict_id, run["run_id"], request["request_id"],
                               item["item_id"], verdict, actual,
                               request["reviewer_profile"])
        crashpoint("after_verdict_insert")
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
        if p.get("item_id") != item["item_id"]:
            return Result("rejected", "wrong_acceptance",
                          {"why": "item_id missing or different",
                           "expected": item["item_id"], "got": p.get("item_id")})
        if self.store.acceptance_for(item["item_id"]):
            return Result("rejected", "duplicate_acceptance")
        verdict_id = p.get("verdict_id")
        if not isinstance(verdict_id, str) or not verdict_id:
            return Result("rejected", "missing_verdict_id", {"verdict_id": verdict_id})
        verdict = self.store.verdict_by_id(verdict_id)
        if verdict is None:
            return Result("rejected", "wrong_acceptance", {"why": "unknown verdict"})
        if verdict["item_id"] != item["item_id"]:
            return Result("rejected", "wrong_acceptance",
                          {"why": "verdict belongs to another item",
                           "verdict_item": verdict["item_id"],
                           "item_id": item["item_id"]})
        if verdict["verdict"] != "PASS":
            return Result("rejected", "wrong_acceptance",
                          {"why": "verdict is not PASS",
                           "verdict": verdict["verdict"]})
        if verdict["reviewer_profile"] == item["owner_profile"]:
            return Result("rejected", "wrong_owner",
                          {"why": "reviewer is the item owner",
                           "reviewer": verdict["reviewer_profile"]})
        request = self.store.request_by_id(verdict["request_id"])
        if request is None or request["item_id"] != item["item_id"]:
            return Result("rejected", "wrong_acceptance",
                          {"why": "verdict request does not belong to item"})
        run = self.store.run_by_request(request["request_id"])
        if run is None:
            return Result("rejected", "wrong_acceptance", {"why": "no review run"})
        declared_sha = p.get("artifact_sha")
        if not _is_sha256(declared_sha):
            return Result("rejected", "missing_artifact_sha",
                          {"artifact_sha": declared_sha})
        declared_rev = p.get("rev")
        if not isinstance(declared_rev, int) or isinstance(declared_rev, bool):
            return Result("rejected", "missing_review_rev", {"rev": declared_rev})
        latest = self.store.latest_artifact(item["item_id"])
        if latest is None:
            return Result("rejected", "wrong_acceptance", {"why": "no artifact"})
        actual, reason, detail = self._check_artifact_bytes(latest)
        if reason:
            return Result("rejected", reason, detail)
        if declared_sha != verdict["artifact_sha"]:
            return Result("rejected", "wrong_acceptance",
                          {"why": "acceptance sha differs from verdict sha",
                           "acceptance_sha": declared_sha,
                           "verdict_sha": verdict["artifact_sha"]})
        if declared_sha != latest["sha256"] or declared_sha != actual:
            return Result("rejected", "stale_pass",
                          {"accepted_sha": declared_sha,
                           "latest_sha": latest["sha256"], "actual_sha": actual})
        if declared_rev != request["rev"] or declared_rev != latest["rev"]:
            return Result("rejected", "stale_pass",
                          {"accepted_rev": declared_rev,
                           "verdict_rev": request["rev"],
                           "latest_rev": latest["rev"]})
        if item["status"] != "approved":
            return Result("rejected", "wrong_acceptance",
                          {"item_status": item["status"]})
        acceptance_id = "acc-" + _h(
            f"{item['item_id']}|{verdict_id}|{declared_sha}|{declared_rev}")[:12]
        self.store.insert_acceptance(
            acceptance_id, dept["dept_id"], item["item_id"], verdict_id,
            declared_sha, declared_rev, verdict["reviewer_profile"],
            p["accepted_by"])
        crashpoint("after_acceptance_insert")
        self.store.set_item_status(item["item_id"], "accepted",
                                   f"manager accepted {verdict_id}",
                                   p["accepted_by"])
        return Result("applied", None, {"acceptance_id": acceptance_id})

    # ---------------- acceptance gate ----------------
    def _gate(self, dept: dict) -> tuple[bool, Optional[str], dict]:
        report_task = dept["report_task_id"]
        items = [i for i in self.store.items_for(dept["dept_id"]) if i["role"] != "report"]
        work_ids = [i["board_task_id"] for i in items]
        if report_task in work_ids:
            return False, "wrong_report_edge", {"report": report_task,
                                                "reason": "same task"}
        report_state = self.board.task_state(report_task)
        if report_state["status"] == "unknown":
            return False, "board_read_unknown", {"report": report_task,
                                                 "error": report_state.get("error")}
        if report_state["status"] == "missing":
            return False, "report_missing", {"report": report_task}
        report = report_state["task"]
        parents = list(report.get("parents") or [])
        children = list(report.get("children") or [])
        if parents or children:
            return False, "wrong_report_edge", {
                "report": report_task,
                "parents": parents, "children": children,
                "why": "report card must be fully detached",
            }
        work_items = [i for i in items if i["role"] == "work"]
        if not work_items:
            return False, "not_approved", {"why": "no work item"}
        facts = []
        for item in items:
            state = self.board.task_state(item["board_task_id"])
            if state["status"] == "unknown":
                return False, "board_read_unknown", {
                    "task": item["board_task_id"], "error": state.get("error")}
            if state["status"] == "missing":
                return False, "work_task_missing", {"task": item["board_task_id"]}
            registered = sorted(json.loads(item["parents_json"]))
            actual_parents = sorted(state["task"].get("parents") or [])
            if registered != actual_parents:
                return False, "work_parent_mismatch", {
                    "task": item["board_task_id"],
                    "registered": registered, "board": actual_parents}
        for item in work_items:
            ok, reason, fact = self._gate_item(item)
            if not ok:
                return False, reason, fact
            facts.append(fact)
        facts.sort(key=lambda f: f["item_id"])
        fingerprint = _h(json.dumps({
            "dept_id": dept["dept_id"],
            "report_task_id": report_task,
            "report_parents": parents,
            "report_children": children,
            "items": facts,
        }, sort_keys=True))
        return True, None, {"items": facts, "fingerprint": fingerprint,
                            "report": {"task_id": report_task,
                                       "parents": parents, "children": children}}

    def _gate_item(self, item: dict) -> tuple[bool, Optional[str], dict]:
        acceptance = self.store.acceptance_for(item["item_id"])
        latest = self.store.latest_artifact(item["item_id"])
        if acceptance is None:
            verdict = self.store.latest_verdict(item["item_id"])
            if verdict and verdict["verdict"] == "PASS":
                return False, "no_acceptance", {"verdict_id": verdict["verdict_id"]}
            return False, "not_approved", {"status": item["status"]}
        if latest is None:
            return False, "not_approved", {"why": "no artifact"}
        actual, reason, detail = self._check_artifact_bytes(latest)
        if reason:
            return False, reason, detail
        if acceptance["artifact_sha"] != latest["sha256"]:
            return False, "stale_pass", {"accepted_sha": acceptance["artifact_sha"],
                                         "latest_sha": latest["sha256"]}
        if acceptance["artifact_sha"] != actual:
            return False, "artifact_corrupt", {
                "accepted_sha": acceptance["artifact_sha"], "actual_sha": actual}
        if acceptance.get("rev") != latest["rev"]:
            return False, "stale_pass", {"accepted_rev": acceptance.get("rev"),
                                         "latest_rev": latest["rev"]}
        if acceptance.get("status") != "active":
            return False, "stale_pass", {"acceptance_status": acceptance.get("status")}
        verdict = self.store.verdict_by_id(acceptance["verdict_id"])
        if verdict is None or verdict["verdict"] != "PASS":
            return False, "wrong_artifact", {"verdict": verdict and verdict["verdict"]}
        if verdict["item_id"] != item["item_id"]:
            return False, "wrong_artifact", {"why": "verdict item mismatch",
                                             "verdict_item": verdict["item_id"]}
        if verdict["artifact_sha"] != actual:
            return False, "wrong_artifact", {"verdict_sha": verdict["artifact_sha"],
                                             "actual_sha": actual}
        if verdict["reviewer_profile"] == item["owner_profile"]:
            return False, "wrong_owner", {"why": "reviewer is the item owner"}
        request = self.store.request_by_id(verdict["request_id"])
        if request is None or request["item_id"] != item["item_id"]:
            return False, "wrong_artifact", {"why": "request item mismatch"}
        if request["rev"] != latest["rev"]:
            return False, "stale_pass", {"verdict_rev": request["rev"],
                                         "latest_rev": latest["rev"]}
        if request["reviewer_profile"] != verdict["reviewer_profile"]:
            return False, "wrong_owner", {"why": "request reviewer mismatch"}
        if item["status"] not in ("accepted", "reported"):
            return False, "not_approved", {"status": item["status"]}
        return True, None, {
            "item_id": item["item_id"],
            "board_task_id": item["board_task_id"],
            "acceptance_id": acceptance["acceptance_id"],
            "verdict_id": verdict["verdict_id"],
            "evidence_sha256": latest["sha256"],
            "artifact_sha256": latest["sha256"],
            "artifact_actual_sha256": actual,
            "review_rev": request["rev"],
            "reviewer_profile": verdict["reviewer_profile"],
            "acceptance_status": acceptance.get("status"),
        }

    # ---------------- delivery ----------------
    def deliver_final(self, dept_id: str) -> Result:
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise KeyError(dept_id)
        latest_delivery = self.store.latest_delivery(dept_id)
        if latest_delivery and latest_delivery["status"] == "confirmed":
            return Result("already_delivered", None,
                          {"delivery_id": latest_delivery["delivery_id"]})
        if latest_delivery and latest_delivery["status"] == "historical_confirmed":
            return Result("held", "stale_after_effect",
                          {"delivery_id": latest_delivery["delivery_id"]})
        ok, reason, detail = self._gate(dept)
        if not ok:
            self.store.record_gate_rejection(dept_id, reason or "held", detail)
            return Result("held", reason, detail)
        facts = detail["items"]
        operation_key = f"final:{dept_id}:{_h(json.dumps(facts, sort_keys=True))[:16]}"
        if latest_delivery:
            if latest_delivery["status"] == "dead_letter":
                return Result("dead_letter", "max_attempts",
                              {"delivery_id": latest_delivery["delivery_id"]})
            if latest_delivery["operation_key"] != operation_key:
                if latest_delivery["status"] in ("intent", "pending_readback",
                                                 "effect_unknown"):
                    return Result("held", "existing_delivery_pending",
                                  {"delivery_id": latest_delivery["delivery_id"]})
                if latest_delivery["status"] in ("held_stale", "superseded"):
                    pass  # a fresh accepted revision may create a new intent
                else:
                    return self._drive_delivery(latest_delivery)
            else:
                return self._drive_delivery(latest_delivery)
        marker = f"[[deptproto-final:{_h(operation_key)[:16]}]]"
        delivery_id = "dlv-" + _h(operation_key)[:12]
        body = body_for(dept_id, delivery_id, facts[0], marker, items=facts)
        delivery = self.store.create_delivery(
            delivery_id, dept_id, dept["report_task_id"], operation_key,
            facts[0]["artifact_sha256"], marker, body,
            gate_fingerprint=detail["fingerprint"])
        crashpoint("after_intent")
        return self._drive_delivery(delivery)

    def _drive_delivery(self, d: dict) -> Result:
        current = self.store.delivery_by_key(d["operation_key"])
        if current is None:
            return Result("held", "no_delivery_intent")
        delivery_id = current["delivery_id"]
        dept_id = current["dept_id"]
        op_key = current["operation_key"]
        status = current["status"]
        if status == "confirmed":
            return Result("already_delivered", None, {"delivery_id": delivery_id})
        if status == "historical_confirmed":
            return Result("held", "stale_after_effect", {"delivery_id": delivery_id})
        if status == "dead_letter":
            return Result("dead_letter", "max_attempts", {"delivery_id": delivery_id})
        if status in ("held_stale", "superseded"):
            return Result("held", current.get("reject_reason") or "held_stale",
                          {"delivery_id": delivery_id})
        outbox = self.store.outbox_for(op_key)
        if outbox is None:
            return Result("held", "no_outbox", {"delivery_id": delivery_id})
        body = outbox["body"]
        marker = current["marker"]

        # 1. Find out whether the historical effect exists before deciding
        #    anything.  UNKNOWN never authorizes a new send.
        probe = self.effect_port.probe(marker, body)
        if probe.status == FOUND:
            return self._confirm_effect(current, probe, body)
        if probe.status == CONFLICT:
            with self.store.tx():
                self.store.set_delivery_outcome(delivery_id, "effect_unknown",
                                                op_key, "unknown")
                self.store.add_alert(dept_id, None, "effect_conflict",
                                     {"delivery_id": delivery_id,
                                      "detail": probe.detail})
            return Result("unknown", "effect_conflict",
                          {"delivery_id": delivery_id, "detail": probe.detail})
        if probe.status == UNKNOWN:
            # A fresh intent with zero attempts has never called apply (the
            # attempt marker is committed before apply), so one send is safe.
            # Any later unknown state is never re-sent.
            fresh_unattempted = (status == "intent"
                                 and int(current.get("attempts") or 0) == 0)
            if not fresh_unattempted:
                with self.store.tx():
                    self.store.set_delivery_outcome(delivery_id, "effect_unknown",
                                                    op_key, "unknown")
                    self.store.add_alert(dept_id, None, "unknown_effect",
                                         {"delivery_id": delivery_id,
                                          "why": probe.detail.get("why"),
                                          "delivery_status": status})
                return Result("unknown", "effect_unknown",
                              {"delivery_id": delivery_id, "detail": probe.detail})

        # 2. ABSENT is authoritative: only now may a new effect be authorized,
        #    and only after re-running the current gate + exact intent binding.
        gate_ok, reason, detail = self._gate(
            self.store.get_department(dept_id))
        if not gate_ok:
            with self.store.tx():
                self.store.set_delivery_outcome(delivery_id, "held_stale",
                                                op_key, "superseded")
                self.store.record_gate_rejection(dept_id, reason or "held_stale",
                                                 detail)
                self.store.add_alert(dept_id, None, "stale_delivery",
                                     {"delivery_id": delivery_id,
                                      "reason": reason, "detail": detail})
            return Result("held", reason or "held_stale",
                          {"delivery_id": delivery_id})
        if current.get("gate_fingerprint") and \
                detail.get("fingerprint") != current["gate_fingerprint"]:
            with self.store.tx():
                self.store.set_delivery_outcome(delivery_id, "held_stale",
                                                op_key, "superseded")
                self.store.record_gate_rejection(
                    dept_id, "superseded_intent",
                    {"intent": current.get("gate_fingerprint"),
                     "current": detail.get("fingerprint")})
                self.store.add_alert(dept_id, None, "stale_delivery",
                                     {"delivery_id": delivery_id,
                                      "reason": "superseded_intent"})
            return Result("held", "superseded_intent",
                          {"delivery_id": delivery_id})

        attempts = int(current.get("attempts") or 0)
        if attempts >= MAX_ATTEMPTS:
            return self._dead_letter(current, "max_attempts")

        with self.store.tx():
            attempt = self.store.increment_attempt(delivery_id)
            self.store.set_delivery_outcome(delivery_id, "pending_readback",
                                            op_key, "attempting")
        crashpoint("before_effect")
        try:
            self.effect_port.apply(marker, body)
        except Exception as exc:
            self.store.add_alert(dept_id, None, "effect_apply_error",
                                 {"delivery_id": delivery_id,
                                  "error": repr(exc), "attempt": attempt})
        crashpoint("after_effect_before_receipt")
        probe = self.effect_port.probe(marker, body)
        if probe.status == FOUND:
            return self._confirm_effect(current, probe, body)
        with self.store.tx():
            self.store.set_delivery_outcome(delivery_id, "effect_unknown",
                                            op_key, "unknown")
            self.store.add_alert(dept_id, None, "unknown_effect",
                                 {"delivery_id": delivery_id,
                                  "why": probe.detail.get("why", probe.status),
                                  "attempt": attempt})
        return Result("unknown", "effect_unknown",
                      {"delivery_id": delivery_id, "attempt": attempt,
                       "probe": probe.status})

    def _confirm_effect(self, d: dict, probe, body: str) -> Result:
        current = self.store.delivery_by_key(d["operation_key"])
        if current is None:
            return Result("held", "no_delivery_intent")
        delivery_id = current["delivery_id"]
        dept_id = current["dept_id"]
        op_key = current["operation_key"]
        if current["status"] == "confirmed" and self.store.has_receipt(op_key):
            return Result("already_delivered", None, {"delivery_id": delivery_id})
        gate_ok, reason, detail = self._gate(self.store.get_department(dept_id))
        fingerprint_ok = bool(
            gate_ok and (not current.get("gate_fingerprint")
                         or detail.get("fingerprint") == current["gate_fingerprint"]))
        evidence = {
            "probe": probe.ref,
            "probe_detail": probe.detail,
            "body_sha256": body_digest(body),
            "gate_fingerprint": detail.get("fingerprint") if gate_ok else None,
            "gate_reason": reason,
            "current_status": current["status"],
        }
        with self.store.tx():
            self.store.add_receipt(dept_id, op_key,
                                   json.dumps(probe.ref, sort_keys=True), evidence)
            if fingerprint_ok:
                crashpoint("after_receipt_before_confirm")
                self.store.set_delivery_outcome(delivery_id, "confirmed",
                                                op_key, "delivered")
                for item in self.store.items_for(dept_id):
                    if item["role"] == "work" and item["status"] == "accepted":
                        self.store.set_item_status(item["item_id"], "reported",
                                                   "single final delivered",
                                                   self.writer_id)
                self.store.resolve_alerts(dept_id, "unknown_effect")
            else:
                self.store.set_delivery_outcome(delivery_id,
                                                "historical_confirmed",
                                                op_key, "delivered_historical")
                self.store.add_alert(dept_id, None, "stale_effect_after_send",
                                     {"delivery_id": delivery_id,
                                      "reason": reason or "superseded_intent",
                                      "detail": detail})
                self.store.resolve_alerts(dept_id, "unknown_effect")
        if not fingerprint_ok:
            return Result("held", "stale_after_effect",
                          {"delivery_id": delivery_id,
                           "reason": reason or "superseded_intent"})
        crashpoint("after_confirm")
        return Result("confirmed", None, {"delivery_id": delivery_id})

    def recover(self, dept_id: str) -> Result:
        d = self.store.latest_delivery(dept_id)
        if d is None:
            return Result("held", "no_delivery")
        if d["status"] == "confirmed":
            return Result("already_delivered", None, {"delivery_id": d["delivery_id"]})
        if d["status"] == "historical_confirmed":
            return Result("held", "stale_after_effect",
                          {"delivery_id": d["delivery_id"]})
        if d["status"] == "dead_letter":
            return Result("dead_letter", "max_attempts",
                          {"delivery_id": d["delivery_id"]})
        if d["status"] in ("held_stale", "superseded"):
            return Result("held", "held_stale", {"delivery_id": d["delivery_id"]})
        return self._drive_delivery(d)

    def _dead_letter(self, d: dict, why: str) -> Result:
        with self.store.tx():
            self.store.set_delivery_outcome(d["delivery_id"], "dead_letter",
                                            d["operation_key"], "dead_letter")
            self.store.add_alert(d["dept_id"], None, "delivery_dead_letter",
                                 {"delivery_id": d["delivery_id"], "why": why,
                                  "effect_absent_authoritative": True})
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
                                 {"task_id": task_id, "expected": expected_owner,
                                  "got": sub.get("notifier_profile")})
            return Result("dead_letter", "wrong_owner",
                          {"expected": expected_owner,
                           "got": sub.get("notifier_profile")})
        chat_id = sub.get("chat_id")
        if not chat_id:
            self.store.add_alert(dept_id, None, "unknown_owner",
                                 {"task_id": task_id, "why": "missing chat_id"})
            return Result("dead_letter", "unknown_owner",
                          {"task_id": task_id, "why": "missing chat_id"})
        chat_profile = self.session_profiles.get(chat_id)
        if chat_profile is None:
            self.store.add_alert(dept_id, None, "unknown_owner",
                                 {"task_id": task_id, "chat_id": chat_id,
                                  "why": "unmapped session"})
            return Result("dead_letter", "unknown_owner",
                          {"task_id": task_id, "chat_id": chat_id,
                           "why": "unmapped session"})
        if chat_profile != expected_owner:
            self.store.add_alert(dept_id, None, "wrong_owner",
                                 {"task_id": task_id, "expected": expected_owner,
                                  "chat_id_profile": chat_profile})
            return Result("dead_letter", "wrong_owner",
                          {"expected": expected_owner,
                           "chat_id_profile": chat_profile})
        if not self.store.admit_board_event(
                raw["external_event_id"], dept_id, task_id,
                raw.get("kind", "completed"), sub.get("notifier_profile"), chat_id):
            return Result("rejected", "duplicate_event")
        return Result("admitted", None, {"external_event_id": raw["external_event_id"]})

    def _process_board_event(self, be: dict) -> Result:
        eid = be["external_event_id"]
        row = self.store.board_event_row(eid)
        if row is None:
            return Result("held", "unknown_board_event")
        if row.get("processed_at"):
            return Result("processed", None, {"already": True})
        key = f"board-event:{eid}"
        try:
            with self.store.tx():
                row = self.store.board_event_row(eid)
                if row.get("processed_at"):
                    return Result("processed", None, {"already": True})
                item = self.store.item_by_task(row["dept_id"], row["task_id"])
                if item is None:
                    raise _BoardReject("unknown_task", {"task_id": row["task_id"]})
                mapped = self.session_profiles.get(row.get("chat_id"))
                if mapped is None:
                    raise _BoardReject("unknown_owner",
                                       {"chat_id": row.get("chat_id"),
                                        "why": "session no longer mapped"})
                if mapped != item["owner_profile"]:
                    raise _BoardReject("wrong_owner",
                                       {"expected": item["owner_profile"],
                                        "chat_id_profile": mapped})
                if row.get("subscription_profile") != item["owner_profile"]:
                    raise _BoardReject("wrong_owner",
                                       {"expected": item["owner_profile"],
                                        "subscription": row.get("subscription_profile")})
                if row["kind"] != "completed":
                    raise _BoardReject("unsupported_kind", {"kind": row["kind"]})
                latest = self.store.latest_artifact(item["item_id"])
                if latest is None:
                    raise _BoardReject("no_artifact", {"item_id": item["item_id"]})
                actual, reason, detail = self._check_artifact_bytes(latest)
                if reason:
                    raise _BoardReject(reason, detail)
                observation_id = "obs-" + _h(f"{eid}|{item['item_id']}")[:12]
                self.store.insert_board_observation(
                    observation_id, row["dept_id"], eid, row["task_id"],
                    item["item_id"], row["kind"], latest["rev"], actual)
                evidence = {"observation_id": observation_id,
                            "task_id": row["task_id"],
                            "item_id": item["item_id"],
                            "rev": latest["rev"],
                            "artifact_sha": actual,
                            "kind": row["kind"]}
                self.store.add_receipt(row["dept_id"], key, eid, evidence)
                self.store.mark_board_event_processed(eid, key)
                return Result("processed", None, {"observation_id": observation_id})
        except _BoardReject as exc:
            self.store.mark_board_event_failed(eid, exc.reason)
            self.store.add_alert(row["dept_id"], row["task_id"], exc.reason,
                                 {"external_event_id": eid, "detail": exc.detail})
            return Result("dead_letter", exc.reason, exc.detail)
        except Exception as exc:
            attempts = self.store.bump_board_event_attempt(eid)
            self.store.add_alert(row["dept_id"], row["task_id"], "board_event_error",
                                 {"external_event_id": eid, "error": repr(exc),
                                  "attempt": attempts})
            if attempts >= MAX_EVENT_ATTEMPTS:
                self.store.mark_board_event_failed(eid, "apply_error")
                return Result("failed", "apply_error", {"error": repr(exc)})
            return Result("retry", "apply_error", {"error": repr(exc)})

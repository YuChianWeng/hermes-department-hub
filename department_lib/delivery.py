"""Plugin-owned immutable final reports (v5); remote escalation intents.

Final delivery is one local SQLite transaction, never an EffectPort append.
The remote intent/readback machinery remains ONLY for escalation and deprecated
pre-v5 intent recovery. It is not an exactly-once remote-delivery guarantee.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .models import digest

__all__ = ["Delivery", "DeliveryResult", "EffectPort", "final_key", "escalation_key"]


def validate_escalation(reason_code, detail):
    if reason_code not in ("blocked", "repair_loop_exhausted", "manager_unreachable"):
        raise ValueError("escalation_reason_invalid")
    if detail is None:
        return
    if not isinstance(detail, dict) or set(detail) - {"round"}:
        raise ValueError("escalation_detail_invalid")
    if "round" in detail and (type(detail["round"]) is not int or not 0 <= detail["round"] <= 1000):
        raise ValueError("escalation_detail_invalid")


def final_key(dept_id: str, task_id: str) -> str:
    return digest({"kind": "final", "dept": dept_id, "task": task_id})


def escalation_key(dept_id: str, task_id: str, reason_code: str) -> str:
    return digest({"kind": "escalation", "dept": dept_id, "task": task_id,
                   "reason": reason_code})


@runtime_checkable
class EffectPort(Protocol):
    """Remote escalation destination; never used for v5 final reports."""

    def append(self, key: str, body: dict) -> None:
        ...

    def find(self, key: str) -> str:
        """Return ``found``, ``absent`` (authoritative complete read) or ``unknown``."""
        ...


@dataclass(frozen=True)
class DeliveryResult:
    action: str          # delivered | already_delivered | held | unknown | dead_letter
    reason: str = ""
    key: str = ""
    delivered_identity: dict | None = None


class Delivery:
    def __init__(self, store, port: EffectPort, *, max_attempts: int = 3):
        self.store = store
        self.port = port
        self.max_attempts = max(1, int(max_attempts))

    # ── internals ───────────────────────────────────────────────────────────

    def _probe(self, key: str) -> str:
        try:
            answer = self.port.find(key)
        except Exception:
            return "unknown"
        return answer if answer in ("found", "absent", "unknown") else "unknown"

    def _apply(self, key: str, body: dict) -> DeliveryResult:
        attempts = self.store.get_delivery(key).attempts if self.store.get_delivery(key) else 0
        if attempts >= self.max_attempts:
            self.store.set_delivery_state(key, "dead_letter", "retry budget exhausted")
            return DeliveryResult("dead_letter", "retry_budget_exhausted", key)
        try:
            self.port.append(key, body)
        except Exception as exc:
            self.store.set_delivery_state(key, "unknown", f"effect raised: {exc}"[:400])
            return DeliveryResult("unknown", "effect_raised", key)
        answer = self._probe(key)
        if answer == "found":
            self.store.set_delivery_state(key, "confirmed", "read-back found")
            return DeliveryResult("delivered", "", key)
        self.store.set_delivery_state(
            key, "unknown", f"read-back after append: {answer}")
        return DeliveryResult("unknown", "readback_not_found", key)

    def _body_final(self, dept, task, gate, root_task_id: str | None = None) -> dict:
        return {
            "schema": "department-hub/final/v1",
            "department": dept.dept_id,
            "task_id": task.task_id,
            # Root is context only: v5 final delivery never writes a comment.
            "root_task_id": root_task_id,
            "status": "accepted",
            "revision": gate.revision,
            "verdict": {
                "kind": "PASS",
                "run_id": gate.verdict_run_id,
                "reviewer": gate.reviewer_profile,
                "artifact_sha256": gate.artifact_sha256,
            },
            "acceptance": {"manager": dept.manager_profile},
            "unresolved_risks": [],
        }

    # ── public API ──────────────────────────────────────────────────────────

    @staticmethod
    def identity(body):
        verdict = body.get("verdict") or {}
        return {"task_id": body.get("task_id"), "revision": body.get("revision"),
                "verdict_run_id": verdict.get("run_id"),
                "artifact_sha256": verdict.get("artifact_sha256")}

    def _local_hold(self, dept, task, gate):
        """All authoritative local fences, inside the INSERT writer transaction.

        Scope is local SQLite only: department/policy, ownership binding,
        acceptance + PASS verdict, review generation, rejection, durable
        quarantine (``review_quarantines`` for the current generation **and** a
        sticky ``events.state='conflict'`` for the task) and producer identity.
        The event-conflict check mirrors ``DepartmentService.authorize()``'s
        ``_snapshot_quarantined`` so the direct library boundary cannot deliver
        around a durable conflict. Board I/O is never performed here: live-board
        ownership is the service's responsibility, not this boundary's.

        This also protects direct library callers; a fabricated GateResult is
        never a substitute for an active accepted verdict.
        """
        current = self.store.get_department(dept.dept_id)
        if current is None or not current.active:
            return "inactive_department"
        if current.as_dict() != dept.as_dict():
            return "department_changed"
        if self.store.dept_for_task(task.task_id) != dept.dept_id:
            return "ownership_changed"
        acceptance = self.store.active_acceptance(dept.dept_id, task.task_id)
        if acceptance is None:
            return "acceptance_missing"
        verdict = self.store.connection.execute(
            "SELECT * FROM verdicts WHERE verdict_id=?", (acceptance.verdict_id,)).fetchone()
        if (acceptance.revision != gate.revision
                or acceptance.artifact_sha256 != gate.artifact_sha256
                or acceptance.manager_profile != dept.manager_profile
                or verdict is None or verdict["run_id"] != gate.verdict_run_id
                or verdict["task_id"] != task.task_id or verdict["dept_id"] != dept.dept_id
                or verdict["revision"] != gate.revision or verdict["verdict"] != "PASS"
                or verdict["reviewer_profile"] != gate.reviewer_profile
                or verdict["reviewer_profile"] not in dept.reviewers
                or verdict["artifact_sha256"] != gate.artifact_sha256):
            return "acceptance_stale"
        generation = self.store.connection.execute(
            "SELECT request_run_id FROM review_generations WHERE task_id=?", (task.task_id,)).fetchone()
        if generation is None or generation[0] != verdict["request_run_id"]:
            return "review_generation_stale"
        if self.store.connection.execute(
                "SELECT 1 FROM review_rejections WHERE task_id=? AND request_run_id=?",
                (task.task_id, generation[0])).fetchone():
            return "review_rejected"
        if self.store.connection.execute(
                "SELECT 1 FROM review_quarantines WHERE task_id=? AND request_run_id=?",
                (task.task_id, generation[0])).fetchone():
            return "event_quarantined"
        # A payload conflict under identical board coordinates is durable and
        # sticky (`ingest` sets events.state='conflict', it does not write a
        # review_quarantines row). The service folds this in via
        # `_snapshot_quarantined`; the direct boundary must not deliver around it.
        if self.store.connection.execute(
                "SELECT 1 FROM events WHERE task_id=? AND state='conflict'",
                (task.task_id,)).fetchone():
            return "event_quarantined"
        # Reuse the frozen producer resolver: a replayed pre-request rejection
        # may bump the displayed revision without invalidating the same artifact.
        from .acceptance import evidence_from_board
        evidence = evidence_from_board(dept, task, artifact_lookup=self.store.artifact_identity)
        if evidence.artifact_sha256 != gate.artifact_sha256:
            return "artifact_mismatch"
        return ""

    def release_final(self, dept, task, gate, *, root_task_id=None,
                      authorize=None) -> DeliveryResult:
        """v5: commit the report itself, never a board comment/remote intent.

        BEGIN IMMEDIATE linearizes independent connections. Rejected
        authorization rolls back even its advisory observation writes. Existing
        immutable reports are compared before current authorization: they are
        historical facts, not permission to deliver a new identity.
        """
        if self.store.connection.in_transaction:
            return DeliveryResult("held", "effect_transaction_nested")
        key = final_key(dept.dept_id, task.task_id)
        body = self._body_final(dept, task, gate, root_task_id)
        class Held(Exception):
            def __init__(self, result): self.result = result
        try:
            with self.store.transaction() as conn:
                existing = conn.execute(
                    "SELECT * FROM deliveries WHERE task_id=? AND kind='final'",
                    (task.task_id,)).fetchone()
                if existing is not None:
                    existing = self.store._delivery(existing)
                    identity = self.identity(existing.body)
                    if identity != self.identity(body):
                        return DeliveryResult("held", "delivery_identity_conflict",
                                              existing.delivery_key, identity)
                    if existing.state == "confirmed":
                        return DeliveryResult("already_delivered", "", existing.delivery_key, identity)
                    if existing.state == "dead_letter":
                        return DeliveryResult("dead_letter", existing.detail or "dead_letter",
                                              existing.delivery_key, identity)
                    # Deprecated pre-v5 remote intent: never resend/convert it.
                    return DeliveryResult("unknown", "prior_effect_unknown", existing.delivery_key, identity)
                if gate.status != "releasable":
                    raise Held(DeliveryResult("held", gate.reason or "gate_not_releasable"))
                if authorize is not None:
                    rejected = authorize()
                    if rejected is not None: raise Held(rejected)
                reason = self._local_hold(dept, task, gate)
                if reason: raise Held(DeliveryResult("held", reason))
                generation = conn.execute(
                    "SELECT request_run_id FROM review_generations WHERE task_id=?",
                    (task.task_id,)).fetchone()[0]
                accepted_verdict = conn.execute(
                    "SELECT v.note FROM verdicts v JOIN acceptances a ON a.verdict_id=v.verdict_id "
                    "WHERE a.task_id=? AND a.dept_id=? AND a.status='active'",
                    (task.task_id, dept.dept_id)).fetchone()
                body.update(schema="department-hub/final/v2", generation=generation,
                            delivered_at=time.time(),
                            summary=accepted_verdict["note"][:4096] or "Accepted report",
                            report_kind="accepted_artifact_identity")
                # Reports are structured acceptance reports, NOT a claim that
                # arbitrary artifact bytes have been copied or reviewer-attested.
                encoded = json.dumps(body, sort_keys=True, ensure_ascii=False)
                if len(encoded.encode("utf-8")) > 65536:
                    raise Held(DeliveryResult("held", "report_too_large"))
                stamp = self.store._now()
                conn.execute(
                    "INSERT INTO deliveries (delivery_key,dept_id,task_id,kind,body_json,"
                    "state,detail,attempts,created_at,updated_at) VALUES (?,?,?,'final',?,"
                    "'confirmed','',1,?,?)", (key,dept.dept_id,task.task_id,encoded,stamp,stamp))
            return DeliveryResult("delivered", "", key, self.identity(body))
        except Held as exc:
            return exc.result

    def reconcile(self, dept_id: str, task_id: str) -> DeliveryResult:
        """Read local v5 reports; deprecated remote intents retain readback-only recovery."""
        rows = [d for d in self.store.deliveries_for_dept(dept_id)
                if d.task_id == task_id and d.kind == "final"]
        if not rows:
            return DeliveryResult("held", "no_delivery")
        row = rows[0]
        if row.state == "confirmed":
            return DeliveryResult("already_delivered", "", row.delivery_key)
        if row.state == "dead_letter":
            return DeliveryResult("dead_letter", row.detail or "dead_letter", row.delivery_key)
        answer = self._probe(row.delivery_key)
        if answer == "found":
            self.store.set_delivery_state(row.delivery_key, "confirmed", "reconciled: found")
            return DeliveryResult("already_delivered", "", row.delivery_key)
        if answer == "unknown":
            return DeliveryResult("unknown", "readback_unavailable", row.delivery_key)
        # Absence now cannot prove a suspended sender will not append later.
        return DeliveryResult("unknown", "in_flight_unknown", row.delivery_key)

    def escalate(self, dept, task_id: str, reason_code: str, detail: dict | None = None,
                 *, root_task_id: str | None = None, authorize=None) -> DeliveryResult:
        validate_escalation(reason_code, detail)
        if self.store.connection.in_transaction:
            return DeliveryResult("held", "effect_transaction_nested")
        if authorize is None:
            with self.store.transaction():
                result = self._escalate(dept, task_id, reason_code, detail,
                                        root_task_id=root_task_id)
            if result.action == "_claimed":
                return self._apply(result.key, result.delivered_identity["body"])
            return result
        # Same two-phase shape as the final handoff: authorization and the unique
        # intent commit together before any effect, and the policy is re-read
        # inside the transaction that performs it. An escalation comments on the
        # root card, so it is an outbound board write like a final delivery.
        with self.store.transaction():
            rejected = authorize()
            if rejected is not None:
                return rejected
            result = self._escalate(dept, task_id, reason_code, detail,
                                    root_task_id=root_task_id)
            if result.action != "_claimed":
                return result
            key, body = result.key, result.delivered_identity["body"]
        with self.store.transaction():
            rejected = authorize()
            if rejected is not None:
                # Nothing has been sent: the claim is provably pre-effect, so it
                # is abandoned rather than dead-lettered (the department may
                # become active again and escalate later).
                self.store.abandon_delivery(key)
                return rejected
            return self._apply(key, body)

    def _escalate(self, dept, task_id, reason_code, detail, *, root_task_id):
        key = escalation_key(dept.dept_id, task_id, reason_code)
        existing = self.store.get_delivery(key)
        body = {
            "schema": "department-hub/escalation/v1",
            "department": dept.dept_id,
            "task_id": task_id,
            "root_task_id": root_task_id,
            "reason_code": reason_code,
            "detail": detail or {},
            "status": "blocked",
        }
        if existing is not None and existing.body != body:
            return DeliveryResult("held", "delivery_identity_conflict", key,
                                  {"body": existing.body, "state": existing.state})
        recorded = self.store.escalations_for_task(dept.dept_id, task_id)
        prior = next((r for r in recorded if r["reason_code"] == reason_code), None)
        if prior is not None and prior["detail"] != (detail or {}):
            return DeliveryResult("held", "escalation_identity_conflict", key,
                                  {"body": existing.body if existing else None,
                                   "recorded": prior, "state": existing.state if existing else "unknown"})
        self.store.add_escalation(dept.dept_id, task_id, reason_code, detail or {})
        if existing is not None and existing.state == "confirmed":
            return DeliveryResult("already_delivered", "", key,
                                  {"body": existing.body, "state": existing.state})
        if existing is None:
            if self.store.claim_delivery(key, dept.dept_id, task_id, "escalation", body):
                return DeliveryResult("_claimed", "", key, {"body": body})
            return DeliveryResult("unknown", "in_flight_unknown", key)
        if existing.state == "unknown":
            answer = self._probe(key)
            if answer == "found":
                self.store.set_delivery_state(key, "confirmed", "reconciled: found")
                return DeliveryResult("already_delivered", "", key)
            return DeliveryResult("unknown", "prior_effect_unknown", key)
        if existing.state == "pending":
            answer = self._probe(key)
            if answer == "found":
                self.store.set_delivery_state(key, "confirmed", "reconciled: found")
                return DeliveryResult("already_delivered", "", key)
            if answer == "unknown":
                return DeliveryResult("unknown", "readback_unavailable", key)
            return DeliveryResult("unknown", "in_flight_unknown", key)
        return DeliveryResult(existing.state, existing.detail, key)

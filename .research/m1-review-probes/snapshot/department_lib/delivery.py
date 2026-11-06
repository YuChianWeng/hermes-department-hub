"""Final handoff and escalation delivery.

Guarantees this module *does* make:

* at most one effective final handoff per (department, task) — enforced by a
  unique delivery key plus a read-back before any new send;
* an unknown outcome is never blindly resent by the normal release path; only
  an explicit :meth:`Delivery.reconcile` may retry, and only after an
  authoritative read says the effect is absent;
* a delivery that is already confirmed is reported as such, never re-sent.

It does **not** claim exactly-once to an external system: the port is an
arbitrary adapter, and a crash between the effect and the local commit is
resolved by read-back, not by a distributed transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .models import digest

__all__ = ["Delivery", "DeliveryResult", "EffectPort", "final_key", "escalation_key"]


def final_key(dept_id: str, task_id: str) -> str:
    return digest({"kind": "final", "dept": dept_id, "task": task_id})


def escalation_key(dept_id: str, task_id: str, reason_code: str) -> str:
    return digest({"kind": "escalation", "dept": dept_id, "task": task_id,
                   "reason": reason_code})


@runtime_checkable
class EffectPort(Protocol):
    """Where a handoff actually lands (a Kanban comment, a chat message, ...)."""

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
            # The comment lands on the root/report card when the department has
            # one bound; the body still identifies the exact accepted work task.
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

    def release_final(self, dept, task, gate, *, root_task_id: str | None = None) -> DeliveryResult:
        if gate.status != "releasable":
            return DeliveryResult("held", gate.reason or "gate_not_releasable")
        key = final_key(dept.dept_id, task.task_id)
        body = self._body_final(dept, task, gate, root_task_id)

        existing = self.store.get_delivery(key)
        if existing is None:
            self.store.put_delivery(key, dept.dept_id, task.task_id, "final", body)
            return self._apply(key, body)
        if existing.state == "confirmed":
            return DeliveryResult("already_delivered", "", key)
        if existing.state == "dead_letter":
            return DeliveryResult("dead_letter", existing.detail or "dead_letter", key)
        if existing.state == "unknown":
            # Never resend from the release path; only explicit reconciliation
            # may retry, and only on an authoritative "absent".
            answer = self._probe(key)
            if answer == "found":
                self.store.set_delivery_state(key, "confirmed", "reconciled: found")
                return DeliveryResult("already_delivered", "", key)
            return DeliveryResult("unknown", "prior_effect_unknown", key)
        # pending: the effect may already have landed before a crash
        answer = self._probe(key)
        if answer == "found":
            self.store.set_delivery_state(key, "confirmed", "reconciled: found")
            return DeliveryResult("already_delivered", "", key)
        if answer == "unknown":
            self.store.set_delivery_state(key, "unknown", "read-back unavailable")
            return DeliveryResult("unknown", "readback_unavailable", key)
        return self._apply(key, body)

    def reconcile(self, dept_id: str, task_id: str) -> DeliveryResult:
        """Restart path: resolve one task's final delivery from the effect port."""
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
        return self._apply(row.delivery_key, row.body)

    def escalate(self, dept, task_id: str, reason_code: str, detail: dict | None = None,
                 *, root_task_id: str | None = None) -> DeliveryResult:
        key = escalation_key(dept.dept_id, task_id, reason_code)
        created = self.store.add_escalation(dept.dept_id, task_id, reason_code, detail or {})
        existing = self.store.get_delivery(key)
        if not created and existing is not None and existing.state == "confirmed":
            return DeliveryResult("already_delivered", "", key)
        body = {
            "schema": "department-hub/escalation/v1",
            "department": dept.dept_id,
            "task_id": task_id,
            "root_task_id": root_task_id,
            "reason_code": reason_code,
            "detail": detail or {},
            "status": "blocked",
        }
        if existing is None:
            self.store.put_delivery(key, dept.dept_id, task_id, "escalation", body)
            return self._apply(key, body)
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
            return self._apply(key, body)
        return DeliveryResult(existing.state, existing.detail, key)

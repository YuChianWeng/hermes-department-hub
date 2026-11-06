"""Department service: the one place that ties board, store and gate together.

Everything here is driven by an authoritative board read. A manager (or any
model) may *claim* a verdict or an acceptance; the service verifies it against
the board before a single effect happens.
"""
from __future__ import annotations

from dataclasses import dataclass

from .acceptance import AcceptanceClaim, evidence_from_board, evaluate
from .board import Board, BoardError
from .delivery import Delivery
from .models import IngestDecision
from .routing import Router
from .store import Store

__all__ = ["DepartmentService", "IngestReport"]


@dataclass(frozen=True)
class IngestReport:
    task_id: str
    dept_id: str
    decisions: tuple[IngestDecision, ...]
    revision: int
    board_status: str

    @property
    def applied(self) -> int:
        return sum(1 for d in self.decisions if d.status == "applied")


class DepartmentService:
    def __init__(self, store: Store, board: Board, delivery: Delivery,
                 router: Router | None = None):
        self.store = store
        self.board = board
        self.delivery = delivery
        self.router = router or Router(getattr(board, "cli", None))

    # ── configuration ───────────────────────────────────────────────────────

    def sync_departments(self, departments: dict) -> None:
        for dept in departments.values():
            self.store.register_department(dept)

    def bind_root(self, dept_id: str, root_task_id: str) -> None:
        if self.store.get_department(dept_id) is None:
            raise ValueError(f"unknown department '{dept_id}'")
        self.store.bind_task(dept_id, root_task_id, role="root")

    def bind_department(self, dept_id: str, root_task_id: str,
                        task_ids: list[str] | None = None):
        """Bind a board-verified root to a department and ensure manager wake.

        The root must be assigned to the department's manager *on the board*:
        that is authoritative ownership, not a caller's claim.
        """
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise ValueError(f"unknown department '{dept_id}'")
        snap = self.board.get(root_task_id)
        if snap.assignee != dept.manager_profile:
            raise ValueError(
                f"root {root_task_id} is assigned to {snap.assignee!r}, not the department "
                f"manager {dept.manager_profile!r}")
        self.store.bind_task(dept_id, root_task_id, role="root")
        targets: list[str] = [root_task_id]
        for task_id in task_ids or []:
            if task_id not in targets:
                targets.append(task_id)
        owners = {tid: self.store.dept_for_task(tid) for tid in targets}
        return self.router.ensure(dept, targets, task_departments=owners)

    # ── ingestion ───────────────────────────────────────────────────────────

    def _resolve_dept(self, task_id: str, snap) -> str | None:
        dept_id = self.store.dept_for_task(task_id)
        if dept_id is not None:
            return dept_id
        # walk the authoritative ancestry to a bound root
        try:
            chain = self.board.ancestry(task_id)
        except BoardError:
            return None
        for node in chain[1:]:
            owner = self.store.dept_for_task(node)
            if owner is not None:
                self.store.bind_task(owner, task_id, role="work")
                return owner
        return None

    def ingest(self, task_id: str) -> IngestReport:
        """Read the board, record every new event, and apply domain effects.

        Duplicate events produce no second receipt and no second effect.
        A conflicting payload under identical board coordinates is rejected.
        """
        try:
            snap = self.board.get(task_id)
        except BoardError as exc:
            return IngestReport(task_id, "", (), 0, f"unknown: {exc.kind}")
        dept_id = self._resolve_dept(task_id, snap)
        if dept_id is None:
            return IngestReport(task_id, "", (), 0, snap.status)
        dept = self.store.get_department(dept_id)
        decisions: list[IngestDecision] = []
        revision = evidence_from_board(dept, snap).revision if dept else 0

        for event in sorted(snap.events, key=lambda e: (e.created_at, e.run_id or 0)):
            # Event observation + domain effect + receipt commit together. If a
            # crash lands between the old separate steps, a duplicate without a
            # receipt is re-applied once instead of being silently dropped.
            with self.store.transaction():
                state = self.store.record_event(event, dept_id)
                if state == "duplicate":
                    if self.store.has_receipt(event.event_id):
                        decisions.append(IngestDecision(event.event_id, "duplicate"))
                        continue
                    applied = self._apply_event(dept, snap, event)
                    decisions.append(IngestDecision(
                        event.event_id, "applied" if applied else "rejected",
                        "" if applied else "recovered event has no domain effect",
                        receipt_written=applied,
                    ))
                    continue
                if state == "conflict":
                    decisions.append(IngestDecision(
                        event.event_id, "conflict", "payload changed under identical coordinates"))
                    continue
                applied = self._apply_event(dept, snap, event)
                decisions.append(IngestDecision(
                    event.event_id, "applied" if applied else "rejected",
                    "" if applied else "event has no domain effect",
                    receipt_written=applied,
                ))
        return IngestReport(task_id, dept_id, tuple(decisions), revision, snap.status)

    def _apply_event(self, dept, snap, event) -> bool:
        """Apply one new event in a single transaction with its receipt."""
        if dept is None:
            return False
        if event.kind == "changes_requested":
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO review_rounds (dept_id, task_id, reason, created_at)"
                    " VALUES (?,?,?,?)",
                    (dept.dept_id, snap.task_id,
                     str((event.payload or {}).get("reason") or "changes requested"),
                     float(event.created_at)),
                )
                conn.execute(
                    "UPDATE acceptances SET status = 'superseded' WHERE dept_id = ? AND"
                    " task_id = ? AND status = 'active'", (dept.dept_id, snap.task_id))
                conn.execute(
                    "INSERT OR IGNORE INTO receipts (event_id, dept_id, task_id, kind, decision,"
                    " detail, created_at) VALUES (?,?,?,?,?,?,?)",
                    (event.event_id, dept.dept_id, snap.task_id, event.kind, "applied",
                     "review round recorded", float(event.created_at)))
            return True
        if event.kind in ("completed", "blocked", "review_requested"):
            return self.store.record_receipt(
                event.event_id, dept.dept_id, snap.task_id, event.kind, "applied", "")
        return False

    # ── acceptance / delivery ───────────────────────────────────────────────

    def accept(self, claim: AcceptanceClaim):
        dept_id = self.store.dept_for_task(claim.task_id)
        if dept_id is None:
            raise ValueError(f"task {claim.task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        if claim.manager_profile != dept.manager_profile:
            from .acceptance import GateResult
            return GateResult("held", "manager_mismatch", 0, None, None, None)
        snap = self.board.get(claim.task_id)
        gate = evaluate(dept=dept, task=snap, claim=claim)
        if gate.status != "releasable":
            return gate
        evidence = evidence_from_board(dept, snap)
        verdict_id = f"verdict:{dept_id}:{claim.task_id}:{evidence.revision}:{gate.verdict_run_id}"
        self.store.put_verdict(
            verdict_id=verdict_id,
            dept_id=dept_id, task_id=claim.task_id, run_id=gate.verdict_run_id,
            reviewer_profile=gate.reviewer_profile or "", revision=evidence.revision,
            artifact_sha256=gate.artifact_sha256 or "", verdict="PASS",
            note=evidence.verdict.note if evidence.verdict else "",
            event_id=f"run:{gate.verdict_run_id}",
        )
        self.store.put_acceptance(
            acceptance_id=f"accept:{dept_id}:{claim.task_id}:{evidence.revision}:{gate.verdict_run_id}",
            dept_id=dept_id, task_id=claim.task_id, verdict_id=verdict_id,
            revision=evidence.revision, artifact_sha256=gate.artifact_sha256 or "",
            manager_profile=claim.manager_profile,
        )
        return gate

    def release(self, claim: AcceptanceClaim):
        dept_id = self.store.dept_for_task(claim.task_id)
        if dept_id is None:
            raise ValueError(f"task {claim.task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        snap = self.board.get(claim.task_id)
        gate = evaluate(dept=dept, task=snap, claim=claim)
        if gate.status != "releasable":
            return self.delivery.release_final(dept, snap, gate)

        # Releasing requires the explicit acceptance step, bound to this exact
        # revision/run/SHA — a fresh board PASS alone does not release.
        from .delivery import DeliveryResult
        expected = f"verdict:{dept_id}:{claim.task_id}:{gate.revision}:{gate.verdict_run_id}"
        acceptance = self.store.active_acceptance(dept_id, claim.task_id)
        if acceptance is None:
            return DeliveryResult("held", "acceptance_missing")
        if (acceptance.revision != gate.revision
                or acceptance.artifact_sha256 != gate.artifact_sha256
                or acceptance.manager_profile != claim.manager_profile
                or acceptance.verdict_id != expected):
            return DeliveryResult("held", "acceptance_stale")
        return self.delivery.release_final(
            dept, snap, gate, root_task_id=self._root_for_task(dept_id, claim.task_id))

    def _root_for_task(self, dept_id: str, task_id: str) -> str | None:
        """The bound root of this task's own tree, not merely the oldest one."""
        roots = set(self.store.tasks_by_role(dept_id, "root"))
        try:
            chain = self.board.ancestry(task_id)
        except BoardError:
            return self.store.root_task(dept_id)
        for node in reversed(chain):
            if node in roots:
                return node
        return self.store.root_task(dept_id)

    def escalate(self, task_id: str, reason_code: str, detail: dict | None = None):
        dept_id = self.store.dept_for_task(task_id)
        if dept_id is None:
            raise ValueError(f"task {task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        return self.delivery.escalate(
            dept, task_id, reason_code, detail or {},
            root_task_id=self._root_for_task(dept_id, task_id))

    def route(self, dept_id: str, task_ids: list[str], *, repair: bool = False):
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise ValueError(f"unknown department '{dept_id}'")
        owners = {tid: self.store.dept_for_task(tid) for tid in task_ids}
        return self.router.ensure(dept, task_ids, repair=repair, task_departments=owners)

    # ── transparency ────────────────────────────────────────────────────────

    def status(self, dept_id: str) -> dict:
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise ValueError(f"unknown department '{dept_id}'")
        tasks = self.store.tasks_for_dept(dept_id)
        return {
            "department": dept.as_dict(),
            "tasks": [{"task_id": t, "role": r} for t, r in tasks],
            "store": self.store.snapshot(),
            "deliveries": [
                {"key": d.delivery_key, "task_id": d.task_id, "kind": d.kind,
                 "state": d.state, "detail": d.detail}
                for d in self.store.deliveries_for_dept(dept_id)
            ],
        }

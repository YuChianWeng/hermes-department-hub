"""Department service: the one place that ties board, store and gate together.

Everything here is driven by an authoritative board read. A manager (or any
model) may *claim* a verdict or an acceptance; the service verifies it against
the board before a single effect happens.
"""
from __future__ import annotations

from dataclasses import dataclass

from .acceptance import (
    AcceptanceClaim, evidence_from_board, evaluate, strongest_hold,
)
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

    def _evidence(self, dept, snap):
        """Board evidence with this department's produced artifact identity.

        Every caller inside the service uses this, so the gate, the dashboard
        and the durable ledgers all resolve the artifact the same way: the
        producer record for the exact current revision, never a board claim.
        """
        return evidence_from_board(dept, snap, artifact_lookup=self.store.artifact_identity)

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
        # Explicit task_ids declare membership; dependencies alone never do.
        for tid in targets[1:]:
            current = self.board.get(tid)
            if current.assignee not in (*dept.members, *dept.reviewers, dept.manager_profile):
                continue
            if self.store.dept_for_task(tid) not in (None, dept_id):
                continue
            try:
                graph = self._topology(tid, current)
                graph.update(self._topology(root_task_id, snap))
            except BoardError:
                continue
            roots = [n for n in graph if any(
                n in self.store.tasks_by_role(d, "root") for d in self.store.departments())]
            if roots and roots != [root_task_id]:
                continue
            self.store.bind_membership(dept_id, tid, root_task_id, current.parents, ancestry=graph)
        owners = {tid: self._resolve_dept(tid, self.board.get(tid)) for tid in targets}
        return self.router.ensure(dept, targets, task_departments=owners)

    # ── ingestion ───────────────────────────────────────────────────────────

    def _topology(self, task_id, snap=None):
        """Read every edge, including intermediate nodes; detect all cycles.

        Sorted adjacency is the binding fingerprint, not only the reached root.
        Bounded traversal failure is UNKNOWN, never a detached tree.
        """
        graph, visiting = {}, set()
        def visit(tid, depth):
            if tid in visiting or depth > 64 or len(graph) >= 1024:
                raise BoardError("invalid", "ancestry cycle or bound exceeded")
            if tid in graph:
                return
            visiting.add(tid)
            node = snap if tid == task_id and snap is not None else self.board.get(tid)
            graph[tid] = sorted(node.parents)
            for parent in node.parents:
                visit(parent, depth + 1)
            visiting.remove(tid)
        visit(task_id, 0)
        return graph

    def _ownership(self, task_id, snap):
        dept_id = self.store.dept_for_task(task_id)
        if dept_id is None:
            return None, "ownership_unknown"
        dept = self.store.get_department(dept_id)
        try:
            graph = self._topology(task_id, snap)
            membership = self.store.membership(task_id)
            if membership:
                root_id, parents = membership
                graph.update(self._topology(root_id))
                old_graph = self.store.membership_ancestry(task_id)
                if old_graph is None:
                    return None, "ownership_unknown"  # old DB needs explicit rebind/migration
                if graph != old_graph or tuple(sorted(snap.parents)) != parents:
                    return None, "ownership_changed"
                roots = [(d, n) for d in self.store.departments()
                         for n in self.store.tasks_by_role(d, "root") if n in graph]
                if roots and roots != [(dept_id, root_id)]:
                    return None, "ownership_changed"
                root = self.board.get(root_id)
                if (root_id not in self.store.tasks_by_role(dept_id, "root")
                        or root.assignee != dept.manager_profile):
                    return None, "ownership_changed"
                return dept_id, ""
            roots = [(d, n) for d in self.store.departments()
                     for n in self.store.tasks_by_role(d, "root") if n in graph]
            if len(roots) > 1 or any(d != dept_id for d, n in roots):
                return None, "ownership_changed"
            if not roots and self.store.tasks_by_role(dept_id, "root"):
                return None, "ownership_changed"
            if roots and self.board.get(roots[0][1]).assignee != dept.manager_profile:
                return None, "ownership_changed"
        except BoardError:
            return None, "ownership_unknown"
        return dept_id, ""

    def _resolve_dept(self, task_id: str, snap) -> str | None:
        return self._ownership(task_id, snap)[0]

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
            return IngestReport(task_id, "", (), 0, "held: ownership_unverified")
        dept = self.store.get_department(dept_id)
        decisions: list[IngestDecision] = []
        revision = self._evidence(dept, snap).revision if dept else 0

        # Quarantine ambiguous coordinates before any effect is applied.
        groups = {}
        for event in snap.events:
            groups.setdefault(event.event_id, []).append(event)
        ambiguous = {key for key, rows in groups.items()
                     if len({(e.task_id, e.kind, e.run_id, e.created_at, e.payload_digest)
                             for e in rows}) > 1}
        with self.store.transaction():
            for key in ambiguous:
                for event in groups[key]:
                    self.store.record_event(event, dept_id)

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
                    self.store.connection.execute(
                        "UPDATE events SET state=? WHERE event_id=? AND state != 'conflict'",
                        ("applied" if applied else "rejected", event.event_id))
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
                self.store.connection.execute(
                    "UPDATE events SET state = ? WHERE event_id = ? AND state != 'conflict'",
                    ("applied" if applied else "rejected", event.event_id))
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
                if self._authorized_rejection(dept, snap, event):
                    request = self._request_before(snap, event)
                    self._observe_generation(snap.task_id, request.run_id)
                    conn.execute("INSERT OR IGNORE INTO review_rejections VALUES (?,?,?,?)",
                                 (snap.task_id, request.run_id, event.run_id, event.event_id))
                    conn.execute(
                        "UPDATE acceptances SET status='superseded' WHERE dept_id=? AND task_id=?"
                        " AND status='active' AND verdict_id IN (SELECT verdict_id FROM verdicts"
                        " WHERE run_id<=? AND request_run_id=? AND reviewer_profile=?)",
                        (dept.dept_id, snap.task_id, event.run_id, request.run_id,
                         snap.run_by_id(event.run_id).profile))
                conn.execute(
                    "INSERT OR IGNORE INTO receipts (event_id, dept_id, task_id, kind, decision,"
                    " detail, created_at) VALUES (?,?,?,?,?,?,?)",
                    (event.event_id, dept.dept_id, snap.task_id, event.kind, "applied",
                     "review round recorded", float(event.created_at)))
            return True
        if event.kind in ("completed", "blocked", "review_requested"):
            if event.kind == "review_requested" and event.run_id is not None:
                self._observe_generation(snap.task_id, event.run_id)
            return self.store.record_receipt(
                event.event_id, dept.dept_id, snap.task_id, event.kind, "applied", "")
        return False

    # ── acceptance / delivery ───────────────────────────────────────────────

    def _observe_generation(self, task_id, request_run_id):
        self.store.connection.execute(
            "INSERT INTO review_generations VALUES (?,?) ON CONFLICT(task_id) DO UPDATE"
            " SET request_run_id=MAX(review_generations.request_run_id,excluded.request_run_id)",
            (task_id, request_run_id))

    def _authority_hold(self, dept, snap, *, observe=True):
        """Monotonic local evidence survives incomplete/stale board snapshots."""
        request_id = self._evidence(dept, snap).review_request_run_id
        seen = self.store.connection.execute(
            "SELECT request_run_id FROM review_generations WHERE task_id=?", (snap.task_id,)).fetchone()
        if seen and (request_id is None or request_id < seen[0]):
            return "review_generation_stale"
        if request_id is not None:
            if self.store.connection.execute(
                    "SELECT 1 FROM review_rejections WHERE task_id=? AND request_run_id=?",
                    (snap.task_id, request_id)).fetchone():
                return "review_rejected"
            if observe:
                self._observe_generation(snap.task_id, request_id)
        return ""

    @staticmethod
    def _request_before(snap, event):
        requests = [e for e in snap.events if e.kind == "review_requested"
                    and e.created_at <= event.created_at and e.run_id is not None
                    and event.run_id is not None and e.run_id < event.run_id]
        return max(requests, key=lambda e: (e.created_at, e.run_id or 0)) if requests else None

    @classmethod
    def _authorized_rejection(cls, dept, snap, event):
        run = snap.run_by_id(event.run_id)
        request = cls._request_before(snap, event)
        claims = [e for e in snap.events if e.kind == "claimed" and e.run_id == event.run_id]
        return (run is not None and request is not None and run.profile in dept.reviewers
                and request.run_id is not None and run.id > request.run_id
                and run.profile == request.payload.get("reviewer")
                and run.outcome == "changes_requested"
                and len(claims) == 1 and claims[0].created_at >= request.created_at
                and claims[0].payload.get("source_status") == "review")

    def _snapshot_quarantined(self, dept_id, snap, *, persist=True):
        """Current source IDs plus durable conflicts; timestamps are not fences.

        Unknown historic conflict ownership is conservatively sticky. Never
        infer that an old stored timestamp means a reused ID is safe now.
        """
        groups = {}
        for event in snap.events:
            groups.setdefault(event.event_id, []).append(event)
        ambiguous = {key for key, rows in groups.items() if len({
            (e.task_id, e.kind, e.run_id, e.created_at, e.payload_digest) for e in rows}) > 1}
        for key, rows in groups.items():
            stored = self.store.connection.execute(
                "SELECT * FROM events WHERE event_id=?", (key,)).fetchone()
            if stored and (stored["state"] == "conflict" or any(
                    (stored["task_id"], stored["dept_id"], stored["kind"], stored["run_id"],
                     stored["created_at"], stored["payload_digest"]) !=
                    (e.task_id, dept_id, e.kind, e.run_id, e.created_at, e.payload_digest)
                    for e in rows)):
                ambiguous.add(key)
        if persist:
            for key in ambiguous:
                for event in groups[key]:
                    self.store.record_event(event, dept_id)
        # A durable conflict with unknown generation cannot be cleared merely
        # by requesting another review. This intentionally sacrifices liveness.
        conflict = bool(ambiguous or any(e.state == "conflict"
                                        for e in self.store.events_for_task(snap.task_id)))
        request_id = self._evidence(self.store.get_department(dept_id), snap).review_request_run_id
        generation = request_id if request_id is not None else -1
        if conflict and persist:
            self.store.connection.execute(
                "INSERT OR IGNORE INTO review_quarantines VALUES (?,?,?)",
                (snap.task_id, generation, "source_identity_collision"))
        return conflict or self.store.connection.execute(
            "SELECT 1 FROM review_quarantines WHERE task_id=? AND request_run_id=?",
            (snap.task_id, generation)).fetchone() is not None

    def accept(self, claim: AcceptanceClaim):
        with self.store.transaction():
            return self._accept(claim)

    def _accept(self, claim: AcceptanceClaim):
        dept_id = self.store.dept_for_task(claim.task_id)
        if dept_id is None:
            raise ValueError(f"task {claim.task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        if claim.manager_profile != dept.manager_profile:
            from .acceptance import GateResult
            return GateResult("held", "manager_mismatch", 0, None, None, None)
        snap = self.board.get(claim.task_id)
        owner, reason = self._ownership(claim.task_id, snap)
        if owner != dept_id:
            from .acceptance import GateResult
            gate = GateResult("held", reason, 0, None, None, None)
        else:
            gate = evaluate(dept=dept, task=snap, claim=claim,
                            artifact_lookup=self.store.artifact_identity)
            # One reason for one board state: accept merges the gate's hold with
            # the durable-authority hold by strength instead of overwriting it.
            # v3 overwrote `repair_loop_exhausted` with `review_rejected` here
            # while `department_deliver` kept the stronger one.
            authority = self._authority_hold(dept, snap)
            reason = strongest_hold(gate.reason, authority)
            if reason:
                from .acceptance import GateResult
                return GateResult("held", reason, gate.revision,
                                  gate.verdict_run_id, gate.artifact_sha256, gate.reviewer_profile)
        if gate.status != "releasable":
            return gate
        if self._snapshot_quarantined(dept_id, snap):
            from .acceptance import GateResult
            return GateResult("held", "event_quarantined", gate.revision,
                              gate.verdict_run_id, gate.artifact_sha256, gate.reviewer_profile)
        evidence = self._evidence(dept, snap)
        verdict_id = f"verdict:{dept_id}:{claim.task_id}:{evidence.revision}:{gate.verdict_run_id}"
        self.store.put_verdict(
            verdict_id=verdict_id,
            dept_id=dept_id, task_id=claim.task_id, run_id=gate.verdict_run_id,
            reviewer_profile=gate.reviewer_profile or "", revision=evidence.revision,
            artifact_sha256=gate.artifact_sha256 or "", verdict="PASS",
            note=evidence.verdict.note if evidence.verdict else "",
            event_id=f"run:{gate.verdict_run_id}",
            request_run_id=evidence.review_request_run_id,
        )
        from .store import StoreError
        try:
            self.store.put_acceptance(
            acceptance_id=f"accept:{dept_id}:{claim.task_id}:{evidence.revision}:{gate.verdict_run_id}",
            dept_id=dept_id, task_id=claim.task_id, verdict_id=verdict_id,
            revision=evidence.revision, artifact_sha256=gate.artifact_sha256 or "",
            manager_profile=claim.manager_profile,
            )
        except StoreError:
            from .acceptance import GateResult
            return GateResult("held", "acceptance_not_active", gate.revision,
                              gate.verdict_run_id, gate.artifact_sha256, gate.reviewer_profile)
        return gate

    def release(self, claim: AcceptanceClaim):
        from .delivery import DeliveryResult
        dept_id = self.store.dept_for_task(claim.task_id)
        if dept_id is None:
            raise ValueError(f"task {claim.task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        existing = self.store.connection.execute(
            "SELECT * FROM deliveries WHERE task_id=? AND kind='final'", (claim.task_id,)).fetchone()
        if existing is not None:
            row = self.store._delivery(existing)
            identity = self.delivery.identity(row.body)
            incoming = {"task_id": claim.task_id, "revision": claim.revision,
                        "verdict_run_id": claim.verdict_run_id,
                        "artifact_sha256": claim.artifact_sha256}
            if identity != incoming:
                return DeliveryResult("held", "delivery_identity_conflict", row.delivery_key, identity)
            if row.state == "confirmed":
                return DeliveryResult("already_delivered", "", row.delivery_key, identity)
            if row.state == "dead_letter":
                return DeliveryResult("dead_letter", row.detail or "dead_letter", row.delivery_key, identity)
            return DeliveryResult("unknown", "prior_effect_unknown", row.delivery_key, identity)
        try:
            snap = self.board.get(claim.task_id)
        except BoardError:
            return DeliveryResult("held", "board_unknown")
        owner, reason = self._ownership(claim.task_id, snap)
        if owner != dept_id:
            return DeliveryResult("held", reason)
        gate = evaluate(dept=dept, task=snap, claim=claim,
                        artifact_lookup=self.store.artifact_identity)
        # One board snapshot, one reason: `_accept` merges the gate hold with the
        # durable-authority hold through `strongest_hold`, so the delivery path
        # must not answer a *weaker* reason for the identical state (v4 could say
        # `verdict_conflict` here while accept said `review_rejected`). When the
        # gate is releasable but local evidence still holds, this is the same
        # hold `authorize()` would return, only resolved before the effect.
        authority = self._authority_hold(dept, snap, observe=False)
        reason = strongest_hold(gate.reason, authority)
        if reason:
            return DeliveryResult("held", reason)
        if gate.status != "releasable":
            return DeliveryResult("held", gate.reason)
        request_id = self._evidence(dept, snap).review_request_run_id
        root = self._root_for_task(dept_id, claim.task_id)
        if root is None and self.store.tasks_by_role(dept_id, "root"):
            return DeliveryResult("held", "ownership_unknown")
        expected = f"verdict:{dept_id}:{claim.task_id}:{gate.revision}:{gate.verdict_run_id}"

        def authorize():
            # Called INSIDE BEGIN IMMEDIATE before the final report INSERT.
            # The local ingest writer fence lasts through report commit; advisory
            # preflight reads cannot authorize a stale accepted identity.
            try:
                current = self.board.get(claim.task_id)
                current_dept = self.store.get_department(dept_id)
                if current_dept is None or not current_dept.active:
                    return DeliveryResult("held", "inactive_department")
                if current_dept.as_dict() != dept.as_dict():
                    return DeliveryResult("held", "department_changed")
                owner, reason = self._ownership(claim.task_id, current)
                if owner != dept_id:
                    return DeliveryResult("held", reason)
                reason = self._authority_hold(current_dept, current, observe=False)
                if reason:
                    return DeliveryResult("held", reason)
                if self._snapshot_quarantined(dept_id, current):
                    return DeliveryResult("held", "event_quarantined")
                fresh = evaluate(dept=current_dept, task=current, claim=claim,
                                 artifact_lookup=self.store.artifact_identity)
                if fresh.status != "releasable":
                    return DeliveryResult("held", fresh.reason)
                if (fresh != gate or self._evidence(dept, current).review_request_run_id != request_id):
                    return DeliveryResult("held", "acceptance_stale")
                if self._root_for_task(dept_id, claim.task_id) != root:
                    return DeliveryResult("held", "ownership_changed")
            except BoardError:
                return DeliveryResult("held", "board_unknown")
            acceptance = self.store.active_acceptance(dept_id, claim.task_id)
            if acceptance is None:
                return DeliveryResult("held", "acceptance_missing")
            if (acceptance.revision != gate.revision
                    or acceptance.artifact_sha256 != gate.artifact_sha256
                    or acceptance.manager_profile != claim.manager_profile
                    or acceptance.verdict_id != expected):
                return DeliveryResult("held", "acceptance_stale")
            return None

        return self.delivery.release_final(dept, snap, gate, root_task_id=root, authorize=authorize)

    def _root_for_task(self, dept_id: str, task_id: str) -> str | None:
        """The bound root of this task's own tree, not merely the oldest one."""
        membership = self.store.membership(task_id)
        if membership:
            try:
                if self._resolve_dept(task_id, self.board.get(task_id)) == dept_id:
                    return membership[0]
            except BoardError:
                pass
            return None
        roots = set(self.store.tasks_by_role(dept_id, "root"))
        try:
            chain = self._topology(task_id)
        except BoardError:
            return None
        matches = roots.intersection(chain)
        return next(iter(matches)) if len(matches) == 1 else None

    def escalate(self, task_id: str, reason_code: str, detail: dict | None = None):
        from .delivery import DeliveryResult, validate_escalation
        validate_escalation(reason_code, detail)
        dept_id = self.store.dept_for_task(task_id)
        if dept_id is None:
            raise ValueError(f"task {task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        # An inactive department is out of scope for every effect, escalation
        # included: commenting on its root card is an outbound board write.
        if not dept.active:
            return DeliveryResult("held", "inactive_department")
        root = self._root_for_task(dept_id, task_id)
        if root is None or self._resolve_dept(task_id, self.board.get(task_id)) != dept_id:
            return DeliveryResult("held", "ownership_unverified")

        def authorize():
            # Re-read policy and ownership inside the delivery transaction so a
            # deactivation during the effect window holds instead of commenting.
            try:
                current_dept = self.store.get_department(dept_id)
                if current_dept is None or not current_dept.active:
                    return DeliveryResult("held", "inactive_department")
                if current_dept.as_dict() != dept.as_dict():
                    return DeliveryResult("held", "department_changed")
                snap = self.board.get(task_id)
                if self._resolve_dept(task_id, snap) != dept_id:
                    return DeliveryResult("held", "ownership_unverified")
                if self._root_for_task(dept_id, task_id) != root:
                    return DeliveryResult("held", "ownership_changed")
            except BoardError:
                return DeliveryResult("held", "board_unknown")
            return None

        return self.delivery.escalate(
            dept, task_id, reason_code, detail or {},
            root_task_id=root, authorize=authorize)

    # ── artifact producer ───────────────────────────────────────────────────

    def submit_artifact(self, task_id: str, artifact_path: str, *,
                        caller_profile: str = "") -> dict:
        """Compute and durably record this revision's artifact identity.

        This is the producer the gate requires: the plugin reads the local file,
        hashes it and stores ``(path, real_path, algorithm, size, time)``. A
        submission that never comes through here keeps no record, so the gate
        stays ``artifact_unbound``.
        """
        from .artifacts import ArtifactError, hash_artifact
        from .store import StoreError

        def held(reason: str, detail: str = "") -> dict:
            return {"success": False, "action": "held", "reason": reason,
                    "detail": detail[:400]}

        dept_id = self.store.dept_for_task(task_id)
        if dept_id is None:
            raise ValueError(f"task {task_id} is not bound to a department")
        dept = self.store.get_department(dept_id)
        if not dept.active:
            return held("inactive_department")
        if not caller_profile:
            return held("caller_unverified",
                        "caller profile unavailable; the producer identity cannot be attributed")
        if caller_profile != dept.manager_profile and caller_profile not in dept.members:
            return held("caller_not_permitted",
                        f"'{caller_profile}' is neither the manager nor a member of '{dept_id}'")
        try:
            snap = self.board.get(task_id)
        except BoardError as exc:
            return held("board_unknown", f"{exc.kind}")
        owner, reason = self._ownership(task_id, snap)
        if owner != dept_id:
            return held(reason or "ownership_unverified")
        revision = self._evidence(dept, snap).revision
        try:
            sha256, real_path, size = hash_artifact(artifact_path)
        except ArtifactError as exc:
            return held(exc.code, exc.detail)
        try:
            identity, created = self.store.put_artifact(
                task_id=task_id, revision=revision, sha256=sha256, algorithm="sha256",
                path=artifact_path, real_path=real_path, size_bytes=size,
                producer_profile=caller_profile)
        except StoreError as exc:
            return held("artifact_identity_conflict", str(exc))
        return {"success": True, "action": "recorded" if created else "already_recorded",
                "task_id": task_id, "revision": revision, "artifact": identity.as_dict()}

    def route(self, dept_id: str, task_ids: list[str], *, repair: bool = False):
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise ValueError(f"unknown department '{dept_id}'")
        owners = {}
        for tid in task_ids:
            try:
                owners[tid] = self._resolve_dept(tid, self.board.get(tid))
            except BoardError:
                owners[tid] = None
        return self.router.ensure(dept, task_ids, repair=repair, task_departments=owners)

    # ── transparency ────────────────────────────────────────────────────────

    def status(self, dept_id: str) -> dict:
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise ValueError(f"unknown department '{dept_id}'")
        tasks = self.store.tasks_for_dept(dept_id)
        return {
            "department": dept.as_dict(),
            "tasks": [{"task_id": t, "role": r, "delivery": self._delivery_status(dept, t)}
                      for t, r in tasks],
            "store": self.store.snapshot(),
            "deliveries": [
                {"key": d.delivery_key, "task_id": d.task_id, "kind": d.kind,
                 "state": d.state, "detail": d.detail,
                 "identity": self.delivery.identity(d.body) if d.kind == "final" else None,
                 "report": d.body if d.kind == "final" and d.body.get("schema") == "department-hub/final/v2" else None}
                for d in self.store.deliveries_for_dept(dept_id)
            ],
        }

    def _delivery_status(self, dept, task_id):
        from .delivery import final_key
        row = self.store.get_delivery(final_key(dept.dept_id, task_id))
        if row is None:
            # Mirror release_final's lookup: the durable final row stays
            # authoritative even if a legacy/hand-written row used a
            # non-canonical delivery key (otherwise status and release disagree).
            found = self.store.connection.execute(
                "SELECT * FROM deliveries WHERE task_id=? AND kind='final'",
                (task_id,)).fetchone()
            row = self.store._delivery(found) if found is not None else None
        if row is not None:
            action = {"confirmed": "delivered", "dead_letter": "dead_letter"}.get(row.state, "unknown")
            return {"action": action, "reason": row.detail if action == "dead_letter" else
                    ("prior_effect_unknown" if action == "unknown" else ""),
                    "identity": self.delivery.identity(row.body),
                    "report": row.body if row.body.get("schema") == "department-hub/final/v2" else None}
        try:
            snap = self.board.get(task_id)
            from .acceptance import AcceptanceClaim
            evidence = self._evidence(dept, snap)
            claim = AcceptanceClaim(task_id, evidence.revision,
                                    evidence.verdict.run_id if evidence.verdict else 0,
                                    evidence.artifact_sha256 or "", dept.manager_profile)
            owner, reason = self._ownership(task_id, snap)
            if owner != dept.dept_id:
                return {"action": "held", "reason": reason, "identity": None, "report": None}
            gate = evaluate(dept=dept, task=snap, claim=claim,
                            artifact_lookup=self.store.artifact_identity)
            reason = strongest_hold(gate.reason, self._authority_hold(dept, snap, observe=False))
            if not reason and self._snapshot_quarantined(dept.dept_id, snap, persist=False):
                reason = "event_quarantined"
            reason = reason or self.delivery._local_hold(dept, snap, gate)
            return {"action": "held", "reason": reason or "not_delivered",
                    "identity": None, "report": None}
        except BoardError:
            return {"action": "unknown", "reason": "board_unknown", "identity": None, "report": None}

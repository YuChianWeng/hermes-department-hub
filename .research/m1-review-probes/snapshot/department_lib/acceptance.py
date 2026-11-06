"""Acceptance gate: implementation ``done`` is not product acceptance.

The gate is computed from an authoritative board snapshot, never from a model
message. A PASS only releases when it is the *current* independent review of
the *current* revision and artifact, the manager accepts that exact evidence,
the repair loop is still within its bound, and no conflicting verdict exists.

Anything the plugin cannot establish is ``held`` — never "assume ok".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .board import BoardTask

__all__ = [
    "AcceptanceClaim", "GateResult", "ReviewEvidence", "VerdictEvidence",
    "evidence_from_board", "evaluate", "is_sha256",
]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_OUTCOMES = ("completed", "changes_requested")


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA_RE.match(value))


@dataclass(frozen=True)
class AcceptanceClaim:
    """What the manager claims to accept. Verified, never trusted."""

    task_id: str
    revision: int
    verdict_run_id: int
    artifact_sha256: str
    manager_profile: str


@dataclass(frozen=True)
class VerdictEvidence:
    kind: str                 # PASS | REQUEST_CHANGES
    run_id: int
    reviewer_profile: str
    artifact_sha256: str | None
    note: str


@dataclass(frozen=True)
class ReviewEvidence:
    task_id: str
    status: str
    assignee: str | None
    revision: int
    review_request_run_id: int | None
    requested_reviewer: str | None
    verdict: VerdictEvidence | None
    artifact_sha256: str | None
    conflict: bool
    repair_rounds: int
    implementer_profile: str | None = None


@dataclass(frozen=True)
class GateResult:
    status: str               # releasable | held
    reason: str               # stable reason code ("" when releasable)
    revision: int
    verdict_run_id: int | None
    artifact_sha256: str | None
    reviewer_profile: str | None


def _latest(items, key):
    return max(items, key=key) if items else None


def evidence_from_board(dept, task: BoardTask) -> ReviewEvidence:
    changes = sorted((e for e in task.events if e.kind == "changes_requested"),
                     key=lambda e: (e.created_at, e.run_id or 0))
    repair_rounds = len(changes)
    revision = repair_rounds + 1

    requests = [e for e in task.events if e.kind == "review_requested"]
    request = _latest(requests, key=lambda e: (e.created_at, e.run_id or 0))
    request_run = request.run_id if request else None
    requested_reviewer = None
    implementer: str | None = None
    if request is not None:
        raw = request.payload.get("reviewer") if isinstance(request.payload, dict) else None
        requested_reviewer = raw if isinstance(raw, str) and raw.strip() else None
        raw_impl = request.payload.get("implementer") if isinstance(request.payload, dict) else None
        if isinstance(raw_impl, str) and raw_impl.strip():
            implementer = raw_impl.strip()
        elif request_run is not None:
            run = task.run_by_id(request_run)
            if run is not None and run.profile:
                implementer = run.profile
    if implementer is None:
        # Same-card review moves `assignee` to the reviewer, so it is only a
        # last-resort fallback when the board did not publish an implementer.
        implementer = task.assignee

    verdict: VerdictEvidence | None = None
    conflict = False
    if request_run is not None:
        candidates = [r for r in task.runs
                      if r.id > request_run and r.outcome in _TERMINAL_OUTCOMES]
        latest = _latest(candidates, key=lambda r: r.id)
        if latest is not None:
            meta = latest.metadata or {}
            sha = meta.get("artifact_sha256")
            verdict = VerdictEvidence(
                kind="PASS" if latest.outcome == "completed" else "REQUEST_CHANGES",
                run_id=latest.id,
                reviewer_profile=latest.profile or "",
                artifact_sha256=sha if is_sha256(sha) else None,
                note=str(meta.get("note") or "")[:400],
            )
            conflict = len({r.outcome for r in candidates}) > 1

    return ReviewEvidence(
        task_id=task.task_id,
        status=task.status,
        assignee=task.assignee,
        revision=revision,
        review_request_run_id=request_run,
        requested_reviewer=requested_reviewer,
        verdict=verdict,
        artifact_sha256=verdict.artifact_sha256 if verdict else None,
        conflict=conflict,
        repair_rounds=repair_rounds,
        implementer_profile=implementer,
    )


def _held(reason: str, evidence: ReviewEvidence | None, **over) -> GateResult:
    verdict = evidence.verdict if evidence else None
    return GateResult(
        status="held",
        reason=reason,
        revision=over.get("revision", evidence.revision if evidence else 0),
        verdict_run_id=verdict.run_id if verdict else None,
        artifact_sha256=evidence.artifact_sha256 if evidence else None,
        reviewer_profile=verdict.reviewer_profile if verdict else None,
    )


def evaluate(*, dept, task: BoardTask, claim: AcceptanceClaim,
             implementation_profile: str | None = None) -> GateResult:
    """Return ``releasable`` only for an exact, current, independent acceptance."""
    if not dept.active:
        return _held("inactive_department", None)

    evidence = evidence_from_board(dept, task)
    verdict = evidence.verdict

    if claim.manager_profile != dept.manager_profile:
        return _held("manager_mismatch", evidence)

    # A conflicting verdict is a safety condition: it is checked before any
    # identity match so a duplicate PASS cannot wash it away.
    if evidence.conflict:
        return _held("verdict_conflict", evidence)

    if claim.revision != evidence.revision:
        return _held("revision_mismatch", evidence)

    if verdict is None:
        return _held("no_pass_verdict", evidence)

    if evidence.repair_rounds > dept.max_repair_rounds:
        return _held("repair_loop_exhausted", evidence)

    if verdict.kind != "PASS":
        return _held("review_rejected", evidence)

    if claim.verdict_run_id != verdict.run_id:
        return _held("stale_verdict", evidence)

    if evidence.status != "done":
        return _held("verdict_not_final", evidence)

    implementer = (implementation_profile or evidence.implementer_profile
                   or evidence.assignee)
    if verdict.reviewer_profile == implementer:
        return _held("reviewer_not_independent", evidence)
    if verdict.reviewer_profile not in dept.reviewers:
        return _held("reviewer_not_independent", evidence)
    if evidence.requested_reviewer and verdict.reviewer_profile != evidence.requested_reviewer:
        return _held("reviewer_not_requested", evidence)

    # The execution side is bound too: a card implemented by a profile the
    # department never declared as a member is not that department's work.
    if implementer is None or implementer not in dept.members:
        return _held("implementer_not_member", evidence)

    if verdict.artifact_sha256 is None:
        return _held("artifact_unbound", evidence)
    if claim.artifact_sha256 != verdict.artifact_sha256:
        return _held("artifact_mismatch", evidence)

    return GateResult(
        status="releasable",
        reason="",
        revision=evidence.revision,
        verdict_run_id=verdict.run_id,
        artifact_sha256=verdict.artifact_sha256,
        reviewer_profile=verdict.reviewer_profile,
    )

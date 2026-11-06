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
from .models import normalize_source_id

__all__ = [
    "AcceptanceClaim", "GateResult", "ReviewEvidence", "VerdictEvidence",
    "evidence_from_board", "evaluate", "is_sha256", "strongest_hold",
    "leading_positive_prose", "normalize_source_id",
]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_OUTCOMES = ("completed", "changes_requested")

#: Negatives recognised in reviewer prose, independent of any structured
#: verdict. None of these may ever be laundered into a PASS by a `completed`
#: outcome, and the set is deliberately *not* narrowed by transport: v3 only
#: checked `REQUEST_CHANGES|REJECT` when `metadata.verdict` was absent, which
#: turned `not approved`, `changes required`, `changes_requested`,
#: `REQUEST CHANGES` and Chinese rejections into PASS/ready.
_NEGATIVE_PROSE_RE = re.compile(
    r"REQUEST[_ ]CHANGES|CHANGES[_ ]REQUESTED|CHANGES[_ ]REQUIRED|NOT[ _]APPROVED|REJECT"
    r"|未通過|不通過|駁回|退回|拒[絕决]|需修改",
    re.I,
)

#: The only free-text positives v4 accepts, anchored at the start of the first
#: line: `PASS` or `REVIEW: PASS`, optionally followed by punctuation or prose
#: that contains no negative token. `looks good`, `all checks passed`, Chinese
#: 「通過」 and every other friendly sentence are NOT authority — they hold as
#: `review_unproven`. A structured ``metadata.verdict="PASS"`` remains the
#: alternative positive transport.
_POSITIVE_PROSE_RE = re.compile(r"^(REVIEW\s*:\s*)?PASS(?=$|[^A-Za-z0-9_])")


def leading_positive_prose(summary: object) -> bool:
    """True only for an explicit positive token at the start of the summary."""
    if not isinstance(summary, str):
        return False
    first = summary.strip().splitlines()[0] if summary.strip() else ""
    return bool(_POSITIVE_PROSE_RE.match(first.upper()))


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA_RE.fullmatch(value))


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
    provenance_reason: str = ""
    #: A `completed` run with no explicit positive token (and no structured
    #: PASS) is unevidenced prose: it holds, it never grants authority.
    prose_unproven: bool = False
    #: A `changes_requested` event in the current generation that cannot be
    #: bound to an authorized rejection run. v3 counted it as a repair round
    #: and then ignored its veto; v4 holds instead.
    unbound_rejection: bool = False
    #: The board still declares an artifact SHA (on the submission, the review
    #: run or the request payload) that disagrees with the producer record.
    #: A declaration can tighten the gate, never replace the producer.
    declaration_conflict: bool = False


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


def _authorized_rejection(dept, task: BoardTask, event, request) -> bool:
    """True only for a rejection this department can attribute to a reviewer.

    A `changes_requested` event that the plugin cannot bind to an authorized
    reviewer run is not a repair round with no consequence: it is an
    un-attributable veto, and the gate must hold instead of delivering the
    previous generation's PASS (v3 counted the round and ignored the veto).
    """
    if event.run_id is None:
        return False
    run = task.run_by_id(event.run_id)
    if run is None or run.outcome != "changes_requested":
        return False
    if request is None or request.run_id is None or run.id <= request.run_id:
        return False
    if run.profile not in dept.reviewers:
        return False
    requested = request.payload.get("reviewer") if isinstance(request.payload, dict) else None
    if isinstance(requested, str) and requested.strip() and run.profile != requested.strip():
        return False
    claims = [e for e in task.events if e.kind == "claimed" and e.run_id == run.id]
    return (len(claims) == 1 and claims[0].created_at >= request.created_at
            and claims[0].payload.get("source_status") == "review")


def _request_before(task: BoardTask, event):
    """The latest review request that precedes this rejection event."""
    requests = [e for e in task.events if e.kind == "review_requested"
                and e.created_at <= event.created_at
                and e.run_id is not None and event.run_id is not None
                and e.run_id < event.run_id]
    return _latest(requests, key=lambda e: (e.created_at, e.run_id or 0))


def evidence_from_board(dept, task: BoardTask, *, artifact_lookup=None) -> ReviewEvidence:
    changes = sorted((e for e in task.events if e.kind == "changes_requested"),
                     key=lambda e: (e.created_at, e.run_id or 0))
    repair_rounds = len({e.event_id for e in changes})
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
    prose_unproven = False
    unbound_rejection = False
    declaration_conflict = False
    request_conflict = bool(request and len({
        (e.created_at, e.payload_digest) for e in requests if e.run_id == request_run}) > 1)
    conflict = request_conflict
    submission = task.run_by_id(request_run)
    # The only artifact authority in v4 is a producer-computed identity for this
    # exact revision. A board that merely *claims* a SHA without a producer
    # record stays artifact_unbound (fail-closed, never widened).
    produced = artifact_lookup(task.task_id, revision) if artifact_lookup is not None else None
    if produced is None and artifact_lookup is not None and request is not None:
        # A revision bump that no authorized rejection in this generation caused
        # (a replayed pre-request event, for example) does not change what was
        # submitted: the identity recorded for the last repair round still
        # describes the same bytes. A real repair round must re-submit.
        repaired = any(_authorized_rejection(dept, task, e, _request_before(task, e))
                       for e in changes)
        if not repaired:
            for earlier in range(revision - 1, 0, -1):
                produced = artifact_lookup(task.task_id, earlier)
                if produced is not None:
                    break
    artifact = produced.sha256 if produced is not None and is_sha256(
        getattr(produced, "sha256", None)) else None
    provenance_reason = ""
    if request is not None and request_run is not None:
        # A rejection event that claims this generation but cannot be bound to a
        # run after the request is not a repair round with no consequence: it is
        # an unauthorised veto. v3 counted it and delivered the old PASS.
        for event in changes:
            if event.created_at < request.created_at:
                continue  # belongs to an older generation
            if not _authorized_rejection(dept, task, event, request):
                unbound_rejection = True
                break
        candidates = [r for r in task.runs if r.id > request_run]

        def claimed_in_request(run):
            # A claim is bound by both run ID and request generation. Never reuse
            # a claim before this request (even if the artifact SHA is the same).
            claims = [e for e in task.events if e.kind == "claimed" and e.run_id == run.id]
            return (len(claims) == 1 and claims[0].created_at >= request.created_at
                    and claims[0].payload.get("source_status") == "review")

        authorized = [r for r in candidates if claimed_in_request(r)
                      and r.profile == requested_reviewer and r.profile in dept.reviewers]
        rejected = [r for r in authorized if r.outcome == "changes_requested"]
        # Conflicting terminal outcomes are diagnostic evidence even when an
        # individual claim is unproven. This can only tighten the gate; it
        # cannot manufacture authority for PASS or supersession.
        conflict = conflict or len({r.outcome for r in candidates
                                    if r.outcome in _TERMINAL_OUTCOMES}) > 1
        latest = _latest(candidates, key=lambda r: r.id)
        # Authoritative negative outcome wins for the current request. A new
        # request, not a repeated PASS in the rejected generation, clears it.
        chosen = _latest(rejected, key=lambda r: r.id) or latest
        if latest is not None and claimed_in_request(latest):
            if latest.profile == implementer or latest.profile not in dept.reviewers:
                provenance_reason = "reviewer_not_independent"
            elif latest.profile != requested_reviewer:
                provenance_reason = "reviewer_not_requested"
        valid_submission = (submission is not None and submission.outcome == "review_requested"
                            and submission.profile == implementer)
        if chosen is not None:
            meta = chosen.metadata or {}
            structured = meta.get("verdict")
            summary = chosen.summary or ""
            if chosen in rejected:
                # Real CLI request_changes writes free text and no verdict/SHA.
                # Neither missing identity nor contradictory prose erases REJECT.
                kind = "REQUEST_CHANGES"
            else:
                kind = "PASS" if chosen.outcome == "completed" else None
                if structured is not None:
                    # Optional structured transport is an additional constraint,
                    # never the sole source of positive or negative authority.
                    conflict = conflict or structured != "PASS"
                elif (kind == "PASS" and chosen in authorized
                        and not leading_positive_prose(summary)):
                    # No structured verdict and no explicit `PASS`/`REVIEW: PASS`
                    # token: friendly prose is not evidence. Held, never PASS.
                    prose_unproven = True
                # Explicit negative tokens cannot be laundered by completed, and
                # they are recognised whether or not a structured verdict exists.
                conflict = conflict or bool(_NEGATIVE_PROSE_RE.search(summary))
            # A board declaration of the artifact identity can only tighten the
            # gate: when the producer recorded a different SHA, the submission is
            # contradictory and must not be delivered.
            declared_shas = [
                meta.get("artifact_sha256"),
                (submission.metadata or {}).get("artifact_sha256") if submission else None,
                request.payload.get("artifact_sha256") if isinstance(request.payload, dict) else None,
            ]
            # With no producer record there is no identity to contradict: the
            # hold is `artifact_unbound`, and a board claim can never fill it.
            if artifact is not None and any(
                    sha is not None and sha != artifact for sha in declared_shas):
                declaration_conflict = True
            sha = artifact if kind == "PASS" else None
            if (chosen in authorized and (valid_submission or kind == "REQUEST_CHANGES")
                    and kind is not None):
                verdict = VerdictEvidence(kind, chosen.id, chosen.profile or "",
                                          sha if is_sha256(sha) else None,
                                          str(meta.get("note") or "")[:400])
            # Contradictory late negative event on a completed run is not a new
            # successful revision. Keep it held even before ingest.
            if kind == "PASS" and any(e.kind == "changes_requested"
                    and e.run_id is not None and e.run_id >= chosen.id
                    and e.created_at >= request.created_at
                    for e in task.events):
                conflict = True
            if verdict and verdict.kind == "PASS" and not is_sha256(artifact):
                artifact = None

    return ReviewEvidence(
        task_id=task.task_id,
        status=task.status,
        assignee=task.assignee,
        revision=revision,
        review_request_run_id=request_run,
        requested_reviewer=requested_reviewer,
        verdict=verdict,
        artifact_sha256=artifact if is_sha256(artifact) else None,
        conflict=conflict,
        repair_rounds=repair_rounds,
        implementer_profile=implementer,
        provenance_reason=provenance_reason,
        prose_unproven=prose_unproven,
        unbound_rejection=unbound_rejection,
        declaration_conflict=declaration_conflict,
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


#: Relative strength of the holds that can meet in one acceptance decision.
#: `_accept` merges the gate's reason with the durable-authority reason through
#: :func:`strongest_hold`, so `department_accept` and `department_deliver` can
#: never report different reasons for the same board state. An exhausted repair
#: budget outranks a plain rejection; a durable rejection outranks a generic
#: conflict (v3 behaviour preserved). Codes absent from the table rank lowest,
#: which means they never *weaken* an existing hold.
_HOLD_STRENGTH = {
    "": 0,
    "verdict_conflict": 50,
    "review_rejected": 55,
    "review_generation_stale": 55,
    "repair_loop_exhausted": 60,
}


def strongest_hold(*reasons: str) -> str:
    """Return the strongest non-empty hold; ties keep the earlier reason."""
    best = ""
    for reason in reasons:
        if not reason:
            continue
        if not best or _HOLD_STRENGTH.get(reason, 0) > _HOLD_STRENGTH.get(best, 0):
            best = reason
    return best


def evaluate(*, dept, task: BoardTask, claim: AcceptanceClaim,
             implementation_profile: str | None = None,
             artifact_lookup=None) -> GateResult:
    """Return ``releasable`` only for an exact, current, independent acceptance."""
    if not dept.active:
        return _held("inactive_department", None)

    evidence = evidence_from_board(dept, task, artifact_lookup=artifact_lookup)
    verdict = evidence.verdict

    if claim.manager_profile != dept.manager_profile:
        return _held("manager_mismatch", evidence)

    # Authoritative rejection is never downgraded to an absent verdict or a
    # generic conflict. Repair exhaustion is an explicit stronger hold.
    if verdict is not None and verdict.kind == "REQUEST_CHANGES":
        if evidence.repair_rounds > dept.max_repair_rounds:
            return _held("repair_loop_exhausted", evidence)
        return _held("review_rejected", evidence)
    if evidence.provenance_reason:
        return _held(evidence.provenance_reason, evidence)

    # An un-attributable rejection in this generation vetoes release: the gate
    # cannot prove it is not a real reviewer rejection, so it must not deliver.
    if evidence.unbound_rejection:
        return _held("rejection_unbound", evidence)

    # A conflicting verdict is a safety condition: it is checked before any
    # identity match so a duplicate PASS cannot wash it away.
    if evidence.conflict:
        return _held("verdict_conflict", evidence)

    # Free text without an explicit positive token is not evidence in either
    # direction; it holds instead of granting authority.
    if evidence.prose_unproven:
        return _held("review_unproven", evidence)

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

    # The board's own declaration of the artifact identity disagreed with the
    # producer record: two different byte-histories, so neither may be delivered.
    if evidence.declaration_conflict:
        return _held("artifact_declaration_conflict", evidence)

    if verdict.artifact_sha256 is None or evidence.artifact_sha256 is None:
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

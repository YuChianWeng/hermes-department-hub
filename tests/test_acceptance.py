"""Tests for the acceptance gate: no failed/stale/foreign review may release."""
from __future__ import annotations

import pytest

from department_lib.acceptance import (
    AcceptanceClaim,
    GateResult,
    ReviewEvidence,
    evaluate,
    evidence_from_board,
)
from department_lib.board import BoardRun, BoardTask
from department_lib.models import BoardEvent, Department


SHA = "a" * 64
OTHER_SHA = "b" * 64


def _dept(**over) -> Department:
    raw = {
        "id": "dev", "name": "Development",
        "manager": {"profile": "dept-manager", "platform": "api_server", "chat_id": "api-dev-mgr"},
        "members": ["worker-a"], "reviewers": ["reviewer-a"], "max_repair_rounds": 2,
    }
    raw.update(over)
    return Department.from_mapping(raw)


def _run(id_, profile, outcome, sha=SHA, status="done", summary="REVIEW: PASS"):
    return BoardRun(id=id_, profile=profile, status=status, outcome=outcome,
                    summary=summary, metadata={"artifact_sha256": sha})


def _task(*, status="done", assignee="worker-a", events=(), runs=()) -> BoardTask:
    return BoardTask(task_id="t_w1", status=status, assignee=assignee,
                     parents=("t_root",), children=(), comments=(),
                     events=tuple(events), runs=tuple(runs))


class _Arts:
    """Callable artifact lookup mirroring Store.artifact_identity."""

    def __init__(self, sha=SHA, revision=1):
        self.sha, self.revision = sha, revision

    def __call__(self, task_id, revision):
        if revision != self.revision:
            return None
        from types import SimpleNamespace
        return SimpleNamespace(sha256=self.sha)


_ARTS = _Arts()
_NOTHING = _Arts(revision=99)


def _request(run_id=5, at=100, reviewer="reviewer-a"):
    return BoardEvent("t_w1", "review_requested", run_id, at, {"reviewer": reviewer})


def _changes(run_id=6, at=150, reviewer="reviewer-a"):
    return BoardEvent("t_w1", "changes_requested", run_id, at, {"reviewer": reviewer,
                                                               "reason": "fix it"})


def _pass_evidence(**over):
    base = dict(
        dept=_dept(), task=_task(events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
                                 runs=[_run(5, "worker-a", "review_requested"),
                                       _run(6, "reviewer-a", "completed")]),
        claim=AcceptanceClaim(task_id="t_w1", revision=1, verdict_run_id=6,
                              artifact_sha256=SHA, manager_profile="dept-manager"),
        implementation_profile="worker-a",
        artifact_lookup=_ARTS,
    )
    base.update(over)
    return base


# ── evidence derivation ─────────────────────────────────────────────────────

def test_evidence_derives_revision_and_current_verdict():
    ev = evidence_from_board(_dept(), _task(
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes(), _request(run_id=7, at=200),
                BoardEvent("t_w1", "claimed", 8, 201, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "changes_requested"),
              _run(7, "worker-a", "review_requested"),
              _run(8, "reviewer-a", "completed")],
    ), artifact_lookup=_Arts(revision=2))
    assert isinstance(ev, ReviewEvidence)
    assert ev.revision == 2                       # one changes_requested so far
    assert ev.review_request_run_id == 7
    assert ev.verdict is not None and ev.verdict.kind == "PASS"
    assert ev.verdict.run_id == 8
    assert ev.artifact_sha256 == SHA


def test_evidence_without_review_request_has_no_verdict():
    ev = evidence_from_board(_dept(), _task(status="done", events=[], runs=[]))
    assert ev.review_request_run_id is None
    assert ev.verdict is None


def test_evidence_ignores_a_run_that_predates_the_review_request():
    ev = evidence_from_board(_dept(), _task(
        events=[_request(run_id=5, at=100)],
        runs=[_run(4, "reviewer-a", "completed"),      # older than the request
              _run(5, "worker-a", "review_requested")],
    ))
    assert ev.verdict is None


def test_evidence_marks_changes_requested_as_rejected():
    ev = evidence_from_board(_dept(), _task(
        status="changes_requested",
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes()],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "changes_requested")],
    ))
    assert ev.verdict is not None and ev.verdict.kind == "REQUEST_CHANGES"
    assert ev.revision == 2


def test_evidence_flags_a_late_conflicting_verdict_as_conflict():
    ev = evidence_from_board(_dept(), _task(
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes(run_id=7, at=160), BoardEvent("t_w1", "claimed", 7, 151, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed"),
              _run(7, "reviewer-a", "changes_requested")],
    ))
    assert ev.conflict is True


def test_board_artifact_declaration_can_only_tighten_the_producer():
    """v4: the producer record is the identity; a disagreeing board claim holds."""
    ev = evidence_from_board(_dept(), _task(
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed", sha=OTHER_SHA)]), artifact_lookup=_ARTS)
    assert ev.artifact_sha256 == SHA          # producer record, not the claim
    assert ev.declaration_conflict is True
    declared = _task(
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed", sha=OTHER_SHA)])
    held = evaluate(**_pass_evidence(task=declared))
    assert held.status == "held" and held.reason == "artifact_declaration_conflict"


# ── gate ────────────────────────────────────────────────────────────────────

def test_fresh_independent_pass_with_exact_identity_is_releasable():
    result = evaluate(**_pass_evidence())
    assert isinstance(result, GateResult)
    assert result.status == "releasable", result.reason
    assert result.revision == 1
    assert result.verdict_run_id == 6
    assert result.artifact_sha256 == SHA
    assert result.reviewer_profile == "reviewer-a"


def test_done_card_without_review_is_held():
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(), task=_task(events=[], runs=[]),
                      claim=_pass_evidence()["claim"], implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "no_pass_verdict"


def test_rejected_review_is_never_releasable():
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(), task=_task(
        status="changes_requested",
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes()],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "changes_requested")],
    ), claim=AcceptanceClaim("t_w1", 2, 6, SHA, "dept-manager"),
        implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "review_rejected"


def test_stale_pass_from_an_older_revision_is_held():
    # revision 1 PASSED, then an authorized rejection forced revision 2
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(), task=_task(
        status="changes_requested",
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes(),
                _request(run_id=7, at=200)],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "changes_requested", summary="REVIEW: REQUEST_CHANGES"),
              _run(7, "worker-a", "review_requested")],
    ), claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"),
        implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "revision_mismatch"


def test_claim_referencing_a_different_run_is_held():
    result = evaluate(**_pass_evidence(
        claim=AcceptanceClaim("t_w1", 1, 99, SHA, "dept-manager")))
    assert result.status == "held"
    assert result.reason == "stale_verdict"


def test_unbound_artifact_is_held():
    """No producer record: a board SHA claim alone never grants identity (P2-4)."""
    result = evaluate(artifact_lookup=_NOTHING, dept=_dept(), task=_task(
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed")],
    ), claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"),
        implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "artifact_unbound"


def test_artifact_mismatch_is_held():
    result = evaluate(**_pass_evidence(
        claim=AcceptanceClaim("t_w1", 1, 6, OTHER_SHA, "dept-manager")))
    assert result.status == "held"
    assert result.reason == "artifact_mismatch"


def test_non_hex_artifact_claim_is_held():
    result = evaluate(**_pass_evidence(
        claim=AcceptanceClaim("t_w1", 1, 6, "not-a-sha", "dept-manager")))
    assert result.status == "held"
    assert result.reason in ("artifact_mismatch", "artifact_unbound")


def test_reviewer_who_is_the_implementer_is_not_independent():
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(),
                      task=_task(assignee="reviewer-a",
                                 events=[_request(reviewer="reviewer-a"),
                                         BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
                                 runs=[_run(5, "reviewer-a", "review_requested"),
                                       _run(6, "reviewer-a", "completed")]),
                      claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"),
                      implementation_profile="reviewer-a")
    assert result.status == "held"
    assert result.reason == "reviewer_not_independent"


def test_reviewer_outside_the_department_binding_is_not_independent():
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(), task=_task(
        events=[_request(reviewer="someone-else"),
                BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "someone-else", "completed")],
    ), claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"),
        implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "reviewer_not_independent"


def test_acceptance_by_a_non_manager_is_held():
    result = evaluate(**_pass_evidence(
        claim=AcceptanceClaim("t_w1", 1, 6, SHA, "worker-a")))
    assert result.status == "held"
    assert result.reason == "manager_mismatch"


def test_inactive_department_holds_everything():
    result = evaluate(**_pass_evidence(dept=_dept(active=False)))
    assert result.status == "held"
    assert result.reason == "inactive_department"


def test_repair_loop_limit_blocks_release():
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(max_repair_rounds=1),
                      task=_task(status="changes_requested",
                                 events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes(), _changes(run_id=7, at=170),
                                         BoardEvent("t_w1", "claimed", 7, 151, {"source_status": "review"})],
                                 runs=[_run(5, "worker-a", "review_requested"),
                                       _run(6, "reviewer-a", "changes_requested"),
                                       _run(7, "reviewer-a", "changes_requested")]),
                      claim=AcceptanceClaim("t_w1", 3, 7, SHA, "dept-manager"),
                      implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "repair_loop_exhausted"


def test_conflicting_verdict_claim_blocks_release():
    result = evaluate(**_pass_evidence(task=_task(
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"}), _changes(run_id=7, at=160), BoardEvent("t_w1", "claimed", 7, 151, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed"),
              _run(7, "reviewer-a", "changes_requested")])))
    assert result.status == "held"
    assert result.reason == "review_rejected"


def test_claimed_reviewer_must_match_the_requested_reviewer_when_declared():
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(reviewers=["reviewer-a", "reviewer-b"]),
                      task=_task(events=[_request(reviewer="reviewer-b"),
                                         BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
                                 runs=[_run(5, "worker-a", "review_requested"),
                                       _run(6, "reviewer-a", "completed")]),
                      claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"),
                      implementation_profile="worker-a")
    assert result.status == "held"
    assert result.reason == "reviewer_not_requested"


def test_pass_on_a_card_still_in_review_is_not_final():
    result = evaluate(**_pass_evidence(task=_task(
        status="review",
        events=[_request(), BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed")])))
    assert result.status == "held"
    assert result.reason == "verdict_not_final"


def test_gate_result_is_immutable_and_carries_evidence_identity():
    result = evaluate(**_pass_evidence())
    with pytest.raises(Exception):
        result.status = "held"  # frozen dataclass


def test_evidence_uses_published_implementer_when_assignee_moved_to_reviewer():
    """Same-card review sets `assignee` to the reviewer; the event names the implementer."""
    task = _task(
        assignee="reviewer-a",
        events=[BoardEvent("t_w1", "review_requested", 5, 100,
                           {"reviewer": "reviewer-a", "implementer": "worker-a"}),
                BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "worker-a", "review_requested"),
              _run(6, "reviewer-a", "completed")],
    )
    evidence = evidence_from_board(_dept(), task)
    assert evidence.implementer_profile == "worker-a"
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(), task=task,
                      claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"))
    assert result.status == "releasable", result.reason


def test_implementer_outside_the_department_binding_is_held():
    task = _task(
        assignee="outsider",
        events=[BoardEvent("t_w1", "review_requested", 5, 100,
                           {"reviewer": "reviewer-a", "implementer": "outsider"}),
                BoardEvent("t_w1", "claimed", 6, 101, {"source_status": "review"})],
        runs=[_run(5, "outsider", "review_requested"),
              _run(6, "reviewer-a", "completed")],
    )
    result = evaluate(artifact_lookup=_ARTS, dept=_dept(), task=task,
                      claim=AcceptanceClaim("t_w1", 1, 6, SHA, "dept-manager"))
    assert result.status == "held"
    assert result.reason == "implementer_not_member"

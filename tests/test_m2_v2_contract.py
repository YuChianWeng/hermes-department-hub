"""M2 ↔ M1 contract tests.

Originally the v2 alignment suite (``t_255f16e5``), then updated in
``t_c410652a`` to v4.1 and in ``t_68287415`` to the **v5** contract
``department-hub/m1-contract/v5``. Test names from earlier rounds are kept so
the coverage stays auditable; assertions that encoded superseded semantics are
called out inline.

What is pinned here:

* the read model asks M1's own ``evaluate()`` instead of re-deriving a PASS, so
  ``review_unproven`` / ``rejection_unbound`` / ``artifact_declaration_conflict``
  surface exactly as ``department_accept`` would answer;
* every held / rejected / conflict / unbound review state is explicit and is
  never rendered as a pass, acceptance or delivery;
* the read-model code vocabulary is exactly the v5 set (``effect_fencing_unavailable``
  removed, ``report_too_large`` added, ``effect_raised`` unknown);
* manager status is three separate facts (subscription / session owner / wake),
  never one green light, and ``ensured`` is never "the manager woke";
* explicit membership and ``ownership_changed`` drive a rebind warning;
* delivery readiness projects what ``department_deliver`` would answer:
  unknown is preserved (never downgraded), a second generation is
  ``held/delivery_identity_conflict`` even when the gate is releasable, and
  only a confirmed plugin row is "delivered";
* the delivered report is projected from ``deliveries.body_json`` (summary /
  delivered_at / schema / generation / canonical four-field identity), is
  bounded at 65,536 bytes and 4,096 summary characters, and stays readable when
  the board is unavailable or a later generation is rejected;
* the read-only board adapter preserves ``events[].id`` as ``source_id`` and
  refuses duplicate run ids;
* M2 exposes no escalation or delivery mutation entry point.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from department_lib.acceptance import AcceptanceClaim, evaluate
from department_lib.dashboard import (
    ACCEPT_HELD_CODES,
    CONTRACT_VERSION,
    DEAD_LETTER_CODES,
    DELIVER_EXTRA_HELD_CODES,
    NOT_M1_CODES,
    REPORT_MAX_BYTES,
    REPORT_SUMMARY_MAX_CHARS,
    STATUS_ONLY_REASONS,
    UNKNOWN_CODES,
    DashboardService,
)
from department_lib.delivery import final_key
from department_lib.kanban_cli import BoardError
from department_lib.kanban_db_source import KanbanDbBoard
from department_lib.store import Store
from tests.support import SHA, SHA2, FakeBoard, dept, event, produce, run, task

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "state.db")
    department = dept()
    store.register_department(department)
    store.bind_task(department.dept_id, "t1", role="root")
    return store, department


def service(store, board, **kw):
    return DashboardService(store, board, **kw)


def row_for(detail: dict, task_id: str = "t1") -> dict:
    return next(row for row in detail["department"]["tasks"] if row["task_id"] == task_id)


def _review(request_run: int = 5, verdict_run: int = 6, reviewer: str = "reviewer-a",
            implementer: str = "worker-a"):
    return [
        event("review_requested", 200, request_run, reviewer=reviewer,
              implementer=implementer),
        # v4.1 binds a verdict/rejection to exactly one review-lane claim in the
        # current generation; without it the rejection is ``rejection_unbound``.
        event("claimed", 250, verdict_run, source_status="review"),
    ]


def passing_task(status: str = "done", *, summary: str | None = None,
                 structured: str | None = "PASS", artifact_sha: str | None = SHA,
                 parents=()):
    meta = {}
    if structured is not None:
        meta["verdict"] = structured
    if artifact_sha is not None:
        meta["artifact_sha256"] = artifact_sha
    return task(
        "t1", status,
        # The bound root card belongs to the manager; the implementer comes from
        # the review request payload, so ownership stays verifiable here.
        assignee="manager-a",
        parents=parents,
        events=_review() + [event("completed", 300, 6)],
        runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
              run(6, "reviewer-a", "completed", outcome="completed",
                  summary=summary, **meta)],
    )


def rejected_task(rounds: int = 1):
    """A task whose *current* generation ends in an authorised rejection.

    v4.1 ignores rejections from an older generation, so each extra round needs
    its own request; the final generation must end with the rejection for the
    replay to expose it (a trailing request means "awaiting review").
    """
    events = [event("review_requested", 200, 5, reviewer="reviewer-a",
                    implementer="worker-a")]
    runs = [run(5, "worker-a", "review_requested", outcome="review_requested")]
    at, request_run, verdict_run = 200, 5, 5
    for _ in range(rounds):
        verdict_run += 1
        at += 100
        events += [event("claimed", at - 50, verdict_run, source_status="review"),
                   event("changes_requested", at, verdict_run, reason="not acceptable")]
        runs.append(run(verdict_run, "reviewer-a", "review", outcome="changes_requested"))
        if _ < rounds - 1:
            request_run = verdict_run + 1
            at += 100
            events.append(event("review_requested", at, request_run,
                                reviewer="reviewer-a", implementer="worker-a"))
            runs.append(run(request_run, "worker-a", "review_requested",
                            outcome="review_requested"))
            verdict_run = request_run
    return task("t1", "ready", assignee="manager-a", events=events, runs=runs)


# ── 1. acceptance / held states ─────────────────────────────────────────────

def test_no_pass_verdict_is_held_and_not_accepted(env):
    store, _department = env
    pending_review = task(
        "t1", "ready", assignee="manager-a",
        events=[event("review_requested", 200, 5, reviewer="reviewer-a",
                      implementer="worker-a")],
        runs=[run(5, "worker-a", "review_requested", outcome="review_requested")],
    )
    detail = service(store, FakeBoard({"t1": pending_review})).department("dev")
    row = row_for(detail)
    assert row["review"]["state"] == "held"
    assert row["review"]["held"] is True
    assert row["review"]["held_code"] == "no_pass_verdict"
    assert row["review"]["verdict"] == "NONE"
    assert row["review"]["ready_to_deliver"] is False
    assert row["acceptance"] is None
    assert row["final_report"] is None
    assert row["delivery_readiness"]["state"] == "held"
    assert row["delivery_readiness"]["code"] == "no_pass_verdict"
    assert detail["department"]["work"]["review_held"] == 1
    assert detail["department"]["work"]["held_codes"] == {"no_pass_verdict": 1}


def test_rejected_review_is_held_as_review_rejected(env):
    store, _department = env
    detail = service(store, FakeBoard({"t1": rejected_task()})).department("dev")
    row = row_for(detail)
    # v4.1 authority is the *run outcome* (changes_requested) bound to the
    # requested reviewer's review-lane claim — not a structured verdict.
    assert row["review"]["verdict"] == "REQUEST_CHANGES"
    assert row["review"]["state"] == "rejected"
    assert row["review"]["held"] is True
    assert row["review"]["held_code"] == "review_rejected"
    assert row["review"]["revision"] == 2
    assert row["acceptance"] is None
    assert row["final_report"] is None


def test_conflicting_verdicts_are_held_as_conflict(env):
    store, _department = env
    # A ``completed`` run whose structured verdict says otherwise is a conflict,
    # not a PASS: the structured transport can only tighten the gate.
    conflicting = task(
        "t1", "done", assignee="worker-a",
        events=_review() + [event("completed", 300, 6)],
        runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
              run(6, "reviewer-a", "completed", outcome="completed",
                  verdict="REQUEST_CHANGES", artifact_sha256=SHA)],
    )
    produce(store, sha=SHA)
    detail = service(store, FakeBoard({"t1": conflicting})).department("dev")
    row = row_for(detail)
    assert row["review"]["verdict_conflict"] is True
    assert row["review"]["state"] == "held"
    assert row["review"]["held_code"] == "verdict_conflict"
    assert row["review"]["held"] is True


def test_pass_without_reviewer_artifact_sha_is_artifact_unbound(env):
    store, _department = env
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    row = row_for(detail)
    assert row["review"]["verdict"] == "PASS"
    # No producer record means no identity at all: a board claim can never fill
    # it (v4 producer-only contract).
    assert row["review"]["artifact_sha256"] is None
    assert row["review"]["held"] is True
    assert row["review"]["held_code"] == "artifact_unbound"


def test_reviewer_submission_sha_mismatch_is_artifact_declaration_conflict(env):
    """v2 called this ``artifact_mismatch``; v4.1 supersedes that reading.

    A board-declared SHA that disagrees with the producer record is a
    *declaration* conflict — the declaration can tighten, never replace.
    """
    store, _department = env
    produce(store, sha=SHA)
    detail = service(store, FakeBoard({"t1": passing_task(artifact_sha=SHA2)})).department("dev")
    row = row_for(detail)
    assert row["review"]["declaration_conflict"] is True
    assert row["review"]["artifact_sha256"] == SHA  # producer value, not board's
    assert row["review"]["held"] is True
    assert row["review"]["held_code"] == "artifact_declaration_conflict"


def test_artifact_mismatch_is_claim_dependent_not_a_board_state(env):
    """``artifact_mismatch`` describes a manager passing the wrong claim.

    The read model synthesises the claim from the board, so this code can never
    be a *board* state; what the board can say is ``artifact_unbound`` (no
    producer record) or ``artifact_declaration_conflict`` (contradictory
    declaration). Pinning this prevents a future read model from inventing it.
    """
    store, department = env
    produce(store, sha=SHA)
    snap = passing_task(artifact_sha=SHA)
    gate = evaluate(
        dept=department, task=snap,
        claim=AcceptanceClaim(task_id="t1", revision=1, verdict_run_id=6,
                              artifact_sha256=SHA2, manager_profile="manager-a"),
        artifact_lookup=store.artifact_identity)
    assert gate.status == "held"
    assert gate.reason == "artifact_mismatch"
    row = row_for(service(store, FakeBoard({"t1": snap})).department("dev"))
    assert row["review"]["ready_to_deliver"] is True  # the board evidence is fine
    assert row["review"]["held"] is False


def test_held_review_never_shows_a_stale_acceptance_as_current(env):
    store, _department = env
    store.put_verdict(verdict_id="v1", dept_id="dev", task_id="t1", run_id=6,
                      reviewer_profile="reviewer-a", revision=1, artifact_sha256=SHA,
                      verdict="PASS", note="", event_id="run:6")
    store.put_acceptance(acceptance_id="a1", dept_id="dev", task_id="t1", verdict_id="v1",
                         revision=1, artifact_sha256=SHA, manager_profile="manager-a")
    detail = service(store, FakeBoard({"t1": rejected_task()})).department("dev")
    row = row_for(detail)
    assert row["review"]["revision"] == 2
    assert row["review"]["held"] is True
    assert row["acceptance"]["revision"] == 1
    assert row["acceptance"]["current"] is False
    assert row["delivery_readiness"]["state"] == "held"
    assert row["delivery_readiness"]["code"] == "review_rejected"


# ── 2. manager status: three independent facts ──────────────────────────────

def test_manager_status_is_three_facts_not_one_green_light(env):
    store, _department = env
    board = FakeBoard(
        {"t1": passing_task()},
        subscriptions={"t1": [
            {"platform": "api_server", "chat_id": "chat-secret-123",
             "notifier_profile": "manager-a", "delivery_mode": "wake"},
            {"platform": "local", "chat_id": "other-chat",
             "notifier_profile": "default", "delivery_mode": "notify"},
        ]},
    )
    detail = service(store, board).department("dev")["department"]
    manager = detail["manager_status"]
    assert manager["subscription"]["state"] == "subscribed"
    assert manager["subscription"]["matching"] == 1
    assert manager["subscription"]["foreign"] == 1
    assert manager["subscription"]["wake_mode"] is True
    assert manager["owner"]["state"] == "unverified"
    assert manager["owner"]["code"] == "session_owner_unverified"
    assert manager["wake"]["state"] == "unconfirmed"
    assert manager["wake"]["code"] == "ensured_not_awake"
    assert manager["verified"] is False
    # ``wake_mode`` (the routing subscription) is not evidence of a wake.
    assert "chat-secret-123" not in json.dumps(detail)


def test_manager_status_missing_subscription_is_not_green(env):
    store, _department = env
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    manager = detail["department"]["manager_status"]
    assert manager["subscription"]["state"] == "missing"
    assert manager["owner"]["state"] == "unverified"
    assert manager["wake"]["state"] == "unconfirmed"
    assert manager["verified"] is False


def test_manager_status_never_claims_a_wake_or_a_verified_owner(env):
    """``ensured`` must never render as "the manager is awake"."""
    store, _department = env
    payload = json.dumps(service(store, FakeBoard({"t1": passing_task()})).department("dev"))
    assert '"state": "confirmed"' not in payload
    assert "manager_awake" not in payload
    assert "ensured_not_awake" in payload


# ── 3. explicit membership / rebind ────────────────────────────────────────

def test_explicit_membership_verified_when_topology_matches(env):
    store, _department = env
    store.bind_membership("dev", "t2", "t1", ("t1",))
    board = FakeBoard({
        "t1": task("t1", "done", assignee="manager-a", events=[event("created", 100)]),
        "t2": task("t2", "done", assignee="worker-a", parents=("t1",),
                   events=[event("created", 100)]),
    })
    detail = service(store, board).department("dev")
    assert row_for(detail, "t2")["binding"]["state"] == "verified"
    assert detail["department"]["membership"]["state"] == "verified"
    assert detail["department"]["membership"]["explicit_bindings"] == 1
    assert detail["department"]["membership"]["rebind_required"] is False


def test_moved_membership_requires_rebind(env):
    store, _department = env
    store.bind_membership("dev", "t2", "t1", ("t1",))
    board = FakeBoard({
        "t1": task("t1", "done", assignee="manager-a", events=[event("created", 100)]),
        "t2": task("t2", "done", assignee="worker-a", parents=("t_ops",),
                   events=[event("created", 100)]),
    })
    detail = service(store, board).department("dev")
    binding = row_for(detail, "t2")["binding"]
    # v4.1 vocabulary: the same fact delivery reports as ``ownership_changed``.
    assert binding["state"] == "ownership_changed"
    assert binding["rebind_required"] is True
    assert binding["recorded_parents"] == ["t1"]
    assert binding["current_parents"] == ["t_ops"]
    assert detail["department"]["membership"]["state"] == "ownership_changed"
    assert detail["department"]["membership"]["rebind_required"] is True
    readiness = row_for(detail, "t2")["delivery_readiness"]
    assert readiness["state"] == "held"
    assert readiness["code"] == "ownership_changed"


# ── 4. delivery identity / unknown ─────────────────────────────────────────

def _delivery_body(revision: int = 1, run_id: int = 6, sha: str = SHA,
                   schema: str = "department-hub/final/v1") -> dict:
    """A legacy (pre-v5) final body — used for pending/unknown/dead-letter rows."""
    return {
        "schema": schema,
        "department": "dev",
        "task_id": "t1",
        "root_task_id": "t1",
        "status": "accepted",
        "revision": revision,
        "verdict": {"kind": "PASS", "run_id": run_id, "reviewer": "reviewer-a",
                    "artifact_sha256": sha},
        "acceptance": {"manager": "manager-a"},
        "unresolved_risks": [],
    }


def _v2_body(*, task_id: str = "t1", revision: int = 1, run_id: int = 6, sha: str = SHA,
             generation: int = 5, delivered_at: float | None = None,
             summary: str = "Accepted report", **extra) -> dict:
    body = {
        "schema": "department-hub/final/v2",
        "department": "dev",
        "task_id": task_id,
        "root_task_id": "t1",
        "status": "accepted",
        "revision": revision,
        "generation": generation,
        "verdict": {"kind": "PASS", "run_id": run_id, "reviewer": "reviewer-a",
                    "artifact_sha256": sha},
        "acceptance": {"manager": "manager-a"},
        "unresolved_risks": [],
        "delivered_at": time.time() - 30 if delivered_at is None else delivered_at,
        "summary": summary,
        "report_kind": "accepted_artifact_identity",
    }
    body.update(extra)
    return body


def _put_confirmed_report(store, body: dict | None = None, *,
                          task_id: str = "t1") -> str:
    """Insert a v5 committed report exactly as the final writer does.

    ``Store.put_delivery`` starts in ``pending`` and the v2 immutability triggers
    forbid promoting it, so a confirmed row is inserted directly (state
    ``confirmed``) — the same shape ``Delivery.release_final`` commits.
    """
    body = body or _v2_body(task_id=task_id)
    key = final_key("dev", task_id)
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False)
    stamp = store._now()
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO deliveries (delivery_key,dept_id,task_id,kind,body_json,"
            "state,detail,attempts,created_at,updated_at)"
            " VALUES (?,?,?,'final',?,'confirmed','',1,?,?)",
            (key, "dev", task_id, encoded, stamp, stamp))
    return key


def test_unknown_delivery_is_preserved_not_downgraded_to_pending(env):
    store, _department = env
    key = final_key("dev", "t1")
    store.put_delivery(key, "dev", "t1", "final", _delivery_body())
    store.set_delivery_state(key, "unknown", "in_flight_unknown")
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    final = row_for(detail)["final_report"]
    assert final["state"] == "unknown"
    assert final["action"] == "unknown"
    assert final["code"] == "in_flight_unknown"
    assert final["delivered_identity"] == {
        "task_id": "t1", "revision": 1, "verdict_run_id": 6, "artifact_sha256": SHA}
    # A legacy v1 body is never exposed as a v5 report.
    assert final["report"] is None
    assert final["report_state"] == "legacy"
    assert detail["department"]["final_report"]["state"] == "unknown"
    assert detail["department"]["final_report"]["unknown_count"] == 1
    readiness = row_for(detail)["delivery_readiness"]
    assert readiness["state"] == "unknown"
    assert readiness["ready"] is False


def test_delivery_identity_conflict_exposes_actual_identity(env):
    """Second generation: gate releasable, but delivery is held for identity."""
    store, _department = env
    _put_confirmed_report(store, _v2_body(revision=1, run_id=4, sha=SHA))
    produce(store, revision=1, sha=SHA)
    produce(store, revision=2, sha=SHA2)
    # Revision 2 = one authorised rejection + a fresh PASS at run 9.
    second = task(
        "t1", "done", assignee="worker-a",
        events=[
            event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
            event("claimed", 250, 6, source_status="review"),
            event("changes_requested", 300, 6, reason="redo"),
            event("review_requested", 400, 7, reviewer="reviewer-a", implementer="worker-a"),
            event("claimed", 450, 9, source_status="review"),
            event("completed", 500, 9),
        ],
        runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
              run(6, "reviewer-a", "review", outcome="changes_requested"),
              run(7, "worker-a", "review_requested", outcome="review_requested"),
              run(9, "reviewer-a", "completed", outcome="completed",
                  verdict="PASS", artifact_sha256=SHA2)],
    )
    detail = service(store, FakeBoard({"t1": second})).department("dev")
    row = row_for(detail)
    assert row["review"]["ready_to_deliver"] is True
    assert row["final_report"]["identity_conflict"] is True
    assert row["final_report"]["delivered_identity"] == {
        "task_id": "t1", "revision": 1, "verdict_run_id": 4, "artifact_sha256": SHA}
    readiness = row["delivery_readiness"]
    assert readiness["state"] == "held"
    assert readiness["code"] == "delivery_identity_conflict"
    assert readiness["ready"] is False
    # Current and stored identities are separate, canonical, and both present.
    assert readiness["current_identity"] == {
        "task_id": "t1", "revision": 2, "verdict_run_id": 9, "artifact_sha256": SHA2}
    assert readiness["stored_identity"] == {
        "task_id": "t1", "revision": 1, "verdict_run_id": 4, "artifact_sha256": SHA}
    assert detail["department"]["final_report"]["identity_conflicts"] == 1
    # The stored report itself is still readable (history is not hidden).
    assert row["final_report"]["report"]["summary"] == "Accepted report"
    assert detail["department"]["final_report"]["history"][0]["task_id"] == "t1"


def test_pending_delivery_is_never_ready_or_delivered(env):
    store, _department = env
    key = final_key("dev", "t1")
    store.put_delivery(key, "dev", "t1", "final", _delivery_body())
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    row = row_for(detail)
    assert row["final_report"]["state"] == "pending"
    assert row["final_report"]["action"] == "in_flight"
    assert row["final_report"]["report"] is None
    assert row["delivery_readiness"]["state"] == "unknown"
    assert row["delivery_readiness"]["code"] == "in_flight_unknown"
    assert detail["department"]["final_report"]["state"] == "pending"
    assert detail["department"]["final_report"]["history"] == []


def test_effect_raised_is_unknown_not_dead_letter(env):
    """v5 classifies a raised remote effect as unknown, not dead-letter."""
    store, _department = env
    key = final_key("dev", "t1")
    store.put_delivery(key, "dev", "t1", "final", _delivery_body())
    store.set_delivery_state(key, "dead_letter", "effect_raised")
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    row = row_for(detail)
    assert row["final_report"]["action"] == "unknown"
    assert row["final_report"]["code"] == "effect_raised"
    assert row["delivery_readiness"]["state"] == "unknown"
    assert row["delivery_readiness"]["code"] == "effect_raised"
    assert "effect_raised" in UNKNOWN_CODES
    assert "effect_raised" not in DEAD_LETTER_CODES


def test_retry_budget_exhausted_stays_dead_letter(env):
    store, _department = env
    key = final_key("dev", "t1")
    store.put_delivery(key, "dev", "t1", "final", _delivery_body())
    store.set_delivery_state(key, "dead_letter", "retry_budget_exhausted")
    row = row_for(service(store, FakeBoard({"t1": passing_task()})).department("dev"))
    assert row["final_report"]["action"] == "dead_letter"
    assert row["final_report"]["code"] == "retry_budget_exhausted"
    assert row["delivery_readiness"]["state"] == "dead_letter"
    assert row["delivery_readiness"]["code"] == "retry_budget_exhausted"


def test_dead_letter_and_unknown_codes_surface_verbatim(env):
    """Compatibility coverage name from the v2/v4.1 suite.

    Both classes still surface verbatim; v5 only reclassifies ``effect_raised``
    as unknown and leaves ``retry_budget_exhausted`` as the dead letter.
    """
    store, _department = env
    key = final_key("dev", "t1")
    store.put_delivery(key, "dev", "t1", "final", _delivery_body())
    store.set_delivery_state(key, "dead_letter", "effect_raised")
    row = row_for(service(store, FakeBoard({"t1": passing_task()})).department("dev"))
    assert row["final_report"]["code"] == "effect_raised"
    assert row["delivery_readiness"]["code"] == "effect_raised"
    assert row["delivery_readiness"]["state"] == "unknown"
    assert set(DEAD_LETTER_CODES) == {"retry_budget_exhausted"}


# ── 5. frozen vocabulary / contract version ────────────────────────────────

FROZEN_ACCEPT = {
    "inactive_department", "manager_mismatch", "repair_loop_exhausted",
    "review_rejected", "reviewer_not_independent", "reviewer_not_requested",
    "implementer_not_member", "rejection_unbound", "verdict_conflict",
    "review_unproven", "revision_mismatch", "no_pass_verdict", "stale_verdict",
    "verdict_not_final", "artifact_declaration_conflict", "artifact_unbound",
    "artifact_mismatch", "review_generation_stale", "event_quarantined",
    "acceptance_not_active",
}
FROZEN_DELIVER_EXTRA = {
    "acceptance_missing", "acceptance_stale", "delivery_identity_conflict",
    "department_changed", "effect_transaction_nested", "ownership_changed",
    "ownership_unknown", "ownership_unverified", "board_unknown",
    "escalation_identity_conflict", "no_delivery", "report_too_large",
}


def test_frozen_code_sets_match_the_frozen_contract():
    assert len(ACCEPT_HELD_CODES) == 20 and set(ACCEPT_HELD_CODES) == FROZEN_ACCEPT
    assert len(DELIVER_EXTRA_HELD_CODES) == 12
    assert set(DELIVER_EXTRA_HELD_CODES) == FROZEN_DELIVER_EXTRA
    assert set(UNKNOWN_CODES) == {"prior_effect_unknown", "in_flight_unknown",
                                  "readback_unavailable", "readback_not_found",
                                  "effect_raised"}
    assert set(DEAD_LETTER_CODES) == {"retry_budget_exhausted"}
    assert set(NOT_M1_CODES) == {"not_attempted", "event_at", "board_unavailable",
                                 "department_unreadable", "department_missing"}
    assert set(STATUS_ONLY_REASONS) == {"not_delivered"}
    everything = set(ACCEPT_HELD_CODES) | set(DELIVER_EXTRA_HELD_CODES)
    assert "effect_fencing_unavailable" not in everything
    assert "report_too_large" in everything
    assert not everything & set(NOT_M1_CODES)
    # ``not_delivered`` is status-only: never a held/release action or code.
    assert not (set(STATUS_ONLY_REASONS) & everything)
    assert not (set(STATUS_ONLY_REASONS)
                & (set(UNKNOWN_CODES) | set(DEAD_LETTER_CODES)))
    # ``escalation_detail_conflict`` (named in an older M2 card) is NOT a code;
    # the real ones are escalation_identity_conflict / escalation_detail_invalid.
    assert "escalation_detail_conflict" not in everything
    assert REPORT_MAX_BYTES == 65536 and REPORT_SUMMARY_MAX_CHARS == 4096


def test_frozen_code_sets_agree_with_the_contract_document():
    doc = (ROOT / "docs" / "m1-v5-contract.md").read_text(encoding="utf-8")
    section = doc.split("## 6.", 1)[1].split("## 7.", 1)[0]
    # Codes appear as ``code`` or inside compound spans such as
    # ``dead_letter/retry_budget_exhausted`` and ``**unknown/effect_raised**``;
    # tokenise the code spans so compound forms still count.
    chunks = re.findall(r"`([^`]+)`", section) + re.findall(r"\*\*([^*]+)\*\*", section)
    tokens = set(re.findall(r"[a-z][a-z0-9_]{3,}", " ".join(chunks)))
    assert set(ACCEPT_HELD_CODES) <= tokens
    assert set(DELIVER_EXTRA_HELD_CODES) <= tokens
    assert set(UNKNOWN_CODES) <= tokens
    assert set(DEAD_LETTER_CODES) <= tokens


def test_dashboard_payload_declares_the_frozen_contract_version(env):
    store, _department = env
    svc = service(store, FakeBoard({"t1": passing_task()}))
    listing = svc.list_departments()
    detail = svc.department("dev")
    assert listing["contract"]["version"] == CONTRACT_VERSION
    assert detail["contract"]["version"] == CONTRACT_VERSION
    assert CONTRACT_VERSION == "department-hub/m1-contract/v5"
    assert listing["read_only"] is True and detail["read_only"] is True


def test_read_model_never_emits_a_non_m1_reason_code(tmp_path):
    store = Store(tmp_path / "state.db")
    store.register_department(dept())
    board = FakeBoard({})
    for index, snap in enumerate([passing_task(), rejected_task(), rejected_task(3)]):
        snap = task(f"t{index}", snap.status, assignee=snap.assignee,
                    events=snap.events, runs=snap.runs)
        board.tasks[snap.task_id] = snap
        store.bind_task("dev", snap.task_id, role="work")
    payload = service(store, board).department("dev")
    if True:
        for row in payload["department"]["tasks"]:
            code = row["review"].get("held_code")
            if code is not None:
                assert code in ACCEPT_HELD_CODES, code
            readiness_code = (row.get("delivery_readiness") or {}).get("code")
            if readiness_code is not None:
                allowed = (set(ACCEPT_HELD_CODES) | set(DELIVER_EXTRA_HELD_CODES)
                           | set(UNKNOWN_CODES) | set(DEAD_LETTER_CODES))
                assert readiness_code in allowed, readiness_code


def test_reading_the_department_with_the_gate_probe_writes_nothing(env):
    """``evaluate()`` is a pure read; the read model must stay mutation-free."""
    store, _department = env
    produce(store, sha=SHA)
    board = FakeBoard({"t1": passing_task()})
    before = store.snapshot()
    service(store, board).department("dev")
    service(store, board).list_departments()
    assert store.snapshot() == before


def test_read_model_matches_a_real_m1_accept_and_deliver(tmp_path):
    """End-to-end against M1's own service: local deliver → dashboard/API report."""
    from department_lib.board import Board
    from department_lib.delivery import Delivery
    from department_lib.orchestrator import DepartmentService
    from department_lib.routing import Router
    from tests.test_m1_v2_regressions import CLI, D, Port, claim, payload

    sha = "a" * 64
    store = Store(tmp_path / "state.db")
    store.register_department(D)
    store.bind_task("dev", "root", role="root")
    store.bind_task("dev", "work", role="work")
    store.put_artifact(task_id="work", revision=1, sha256=sha, algorithm="sha256",
                       path="/synthetic/work", real_path="/synthetic/work",
                       size_bytes=0, producer_profile="worker", computed_at=0.0)
    root = payload("root", "manager")
    root["events"], root["runs"] = [], []
    work = payload()
    work["parents"] = ["root"]
    cli = CLI({"work": work, "root": root})
    board = Board(cli)
    port = Port()
    m1 = DepartmentService(store, board, Delivery(store, port), Router(cli))

    assert m1.accept(claim(sha)).status == "releasable"
    dash = DashboardService(store, board)
    row = row_for(dash.department("dev"), "work")
    assert row["review"]["ready_to_deliver"] is True
    assert row["review"]["held"] is False
    assert row["acceptance"]["current"] is True
    assert row["delivery_readiness"]["state"] == "ready"
    assert row["delivery_readiness"]["code"] is None
    assert row["final_report"] is None

    assert m1.release(claim(sha)).action == "delivered"
    dash.invalidate()
    detail = dash.department("dev")
    row = row_for(detail, "work")
    assert row["final_report"]["action"] == "delivered"
    assert row["final_report"]["identity_conflict"] is False
    assert row["final_report"]["delivered_identity"] == {
        "task_id": "work", "revision": 1, "verdict_run_id": 6, "artifact_sha256": sha}
    assert row["delivery_readiness"]["state"] == "already_delivered"
    # v5: the report is committed to the plugin DB; nothing is appended to the
    # board and no final comment is written.
    assert port.appends == 0 and port.entries == []

    report = row["final_report"]["report"]
    assert report is not None
    assert report["schema"] == "department-hub/final/v2"
    assert isinstance(report["generation"], int)
    assert isinstance(report["summary"], str) and report["summary"]
    assert report["summary_chars"] == len(report["summary"])
    assert report["delivered_at"] is not None
    assert report["identity"] == {
        "task_id": "work", "revision": 1, "verdict_run_id": 6, "artifact_sha256": sha}
    assert row["final_report"]["delivered_at"] == report["delivered_at"]
    history = detail["department"]["final_report"]["history"]
    assert [item["task_id"] for item in history] == ["work"]
    assert history[0]["report"]["summary"] == report["summary"]

    # Same identity retry: already_delivered, still no append and no new row.
    assert m1.release(claim(sha)).action == "already_delivered"
    assert port.appends == 0
    dash.invalidate()
    assert [item["task_id"] for item in
            dash.department("dev")["department"]["final_report"]["history"]] == ["work"]


# ── 5b. v5 plugin-persisted report projection ─────────────────────────────

def test_report_projection_comes_from_the_plugin_body_json(env):
    """The report is the committed v2 body: summary/time/schema/generation/identity."""
    store, _department = env
    delivered_at = time.time() - 120
    body = _v2_body(task_id="t1", revision=3, run_id=44, sha=SHA,
                    generation=7, delivered_at=delivered_at,
                    summary="Accepted report — reviewer note, plain text")
    _put_confirmed_report(store, body)
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    final = row_for(detail)["final_report"]
    report = final["report"]
    assert final["action"] == "delivered"
    assert final["delivered_at"] == delivered_at
    assert final["delivered_at_iso"] == time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(delivered_at))
    assert report["schema"] == "department-hub/final/v2"
    assert report["generation"] == 7
    assert report["delivered_at"] == delivered_at
    assert report["summary"] == "Accepted report — reviewer note, plain text"
    assert report["identity"] == {"task_id": "t1", "revision": 3,
                                  "verdict_run_id": 44, "artifact_sha256": SHA}
    assert final["report_state"] == "ok" and final["report_code"] is None
    assert report["size_bytes"] == len(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    # The department-level history is the same row, independent of task reads.
    history = detail["department"]["final_report"]["history"]
    assert history == [{
        "task_id": "t1",
        "identity": report["identity"],
        "report": report,
        "report_state": "ok",
        "report_code": None,
        "delivered_at": delivered_at,
        "delivered_at_iso": final["delivered_at_iso"],
        "summary": report["summary"],
        "report_schema": "department-hub/final/v2",
        "report_generation": 7,
        "report_bytes": report["size_bytes"],
    }]


def test_report_stays_readable_when_the_board_is_unavailable(env):
    """History is read from the plugin DB, not from the board."""
    store, _department = env
    _put_confirmed_report(store, _v2_body(summary="still readable offline", generation=2))
    detail = service(store, FakeBoard({}, fail={"t1": "unavailable"})).department("dev")
    row = row_for(detail)
    assert row["unknown"]["code"] == "unavailable"
    assert row.get("final_report") is None  # the card itself is unreadable
    history = detail["department"]["final_report"]["history"]
    assert [item["task_id"] for item in history] == ["t1"]
    assert history[0]["summary"] == "still readable offline"
    assert history[0]["report"]["generation"] == 2
    assert history[0]["identity"]["revision"] == 1


def test_report_stays_readable_after_a_later_rejection(env):
    """A new review generation rejects, but the committed report is history."""
    store, _department = env
    _put_confirmed_report(store, _v2_body(revision=1, run_id=6, sha=SHA,
                                          summary="first generation accepted"))
    produce(store, revision=1, sha=SHA)
    detail = service(store, FakeBoard({"t1": rejected_task()})).department("dev")
    row = row_for(detail)
    assert row["review"]["state"] == "rejected"
    assert row["final_report"]["report"]["summary"] == "first generation accepted"
    assert row["delivery_readiness"]["code"] == "delivery_identity_conflict"
    assert detail["department"]["final_report"]["history"][0]["summary"] == \
        "first generation accepted"


def test_overlong_summary_shows_report_too_large_and_is_not_echoed(env):
    store, _department = env
    huge = "s" * (REPORT_SUMMARY_MAX_CHARS + 1)
    _put_confirmed_report(store, _v2_body(summary=huge))
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    final = row_for(detail)["final_report"]
    assert final["report"] is None
    assert final["report_state"] == "report_too_large"
    assert final["report_code"] == "report_too_large"
    # Identity and delivery time remain visible; the oversized text does not.
    assert final["delivered_identity"]["revision"] == 1
    assert final["delivered_at"] is not None
    assert huge not in json.dumps(detail)
    assert detail["department"]["final_report"]["report_too_large"] == 1
    assert final["report_state"] == "report_too_large"
    history = detail["department"]["final_report"]["history"]
    assert history and history[0]["report"] is None
    assert history[0]["report_code"] == "report_too_large"


def test_over_byte_bound_report_is_not_rendered(env):
    """A foreign/legacy DB row above 65,536 bytes is flagged, never served."""
    store, _department = env
    # Simulate a row written outside the v5 writer (the trigger was added with
    # the schema, so a pre-existing foreign file could lack it).
    store.connection.execute("DROP TRIGGER IF EXISTS bounded_final_insert")
    huge = "x" * (REPORT_MAX_BYTES + 512)
    _put_confirmed_report(store, _v2_body(summary="small summary", unresolved_risks=[huge]))
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    final = row_for(detail)["final_report"]
    assert final["report"] is None
    assert final["report_code"] == "report_too_large"
    assert final["report_bytes"] is not None and final["report_bytes"] > REPORT_MAX_BYTES
    assert huge not in json.dumps(detail)


def test_report_projection_reads_no_files_and_no_board_comments(env, monkeypatch):
    """The only source is ``deliveries.body_json``."""
    from dataclasses import replace

    store, _department = env
    _put_confirmed_report(store, _v2_body(summary="from the plugin database"))
    snap = replace(passing_task(),
                   comments=({"body": "BOARD COMMENT must not be read"},))
    monkeypatch.setattr("builtins.open",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("dashboard opened a file")))
    payload = service(store, FakeBoard({"t1": snap})).department("dev")
    monkeypatch.undo()
    encoded = json.dumps(payload)
    assert "from the plugin database" in encoded
    assert "BOARD COMMENT must not be read" not in encoded


def test_not_delivered_is_status_only_never_a_release_action(env):
    store, _department = env
    produce(store, sha=SHA)
    store.put_verdict(verdict_id="v1", dept_id="dev", task_id="t1", run_id=6,
                      reviewer_profile="reviewer-a", revision=1, artifact_sha256=SHA,
                      verdict="PASS", note="", event_id="run:6")
    store.put_acceptance(acceptance_id="a1", dept_id="dev", task_id="t1", verdict_id="v1",
                         revision=1, artifact_sha256=SHA, manager_profile="manager-a")
    detail = service(store, FakeBoard({"t1": passing_task()})).department("dev")
    row = row_for(detail)
    assert row["delivery_readiness"]["state"] == "ready"
    assert row["delivery_readiness"]["code"] is None
    assert row["final_report"] is None
    # ``not_delivered`` is a status-only explanation; it is not one of the
    # mutation/held codes and never appears as a delivery readiness code.
    assert set(STATUS_ONLY_REASONS) == {"not_delivered"}
    allowed = (set(ACCEPT_HELD_CODES) | set(DELIVER_EXTRA_HELD_CODES)
               | set(UNKNOWN_CODES) | set(DEAD_LETTER_CODES))
    assert not (set(STATUS_ONLY_REASONS) & allowed)
    assert row["delivery_readiness"]["code"] not in STATUS_ONLY_REASONS


def test_reading_a_persisted_report_writes_nothing(env):
    store, _department = env
    _put_confirmed_report(store, _v2_body())
    svc = service(store, FakeBoard({"t1": passing_task()}))
    before = store.snapshot()
    svc.department("dev")
    svc.list_departments()
    after = store.snapshot()
    assert after == before
    assert after["deliveries"] == {"confirmed": 1}


# ── 6. read-only board adapter: source_id / duplicate runs ─────────────────

def _raw_board(path: Path, *, events=(), runs=(), subscriptions=None,
               event_id_type: str = "INTEGER", runs_pk: bool = True) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, assignee TEXT)")
    conn.execute(
        f"CREATE TABLE task_events (id {event_id_type}, task_id TEXT, kind TEXT,"
        " created_at INTEGER, run_id INTEGER, payload TEXT)")
    conn.execute(
        "CREATE TABLE task_runs (id INTEGER%s, task_id TEXT, profile TEXT, status TEXT,"
        " outcome TEXT, summary TEXT, metadata TEXT)" % (" PRIMARY KEY" if runs_pk else ""))
    conn.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    if subscriptions is not None:
        conn.execute(
            "CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT,"
            " notifier_profile TEXT, delivery_mode TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t1','Card','done','worker-a')")
    conn.executemany("INSERT INTO task_events VALUES (?,?,?,?,?,?)", events)
    conn.executemany("INSERT INTO task_runs VALUES (?,?,?,?,?,?,?)", runs)
    for row in subscriptions or ():
        conn.execute("INSERT INTO kanban_notify_subs VALUES (?,?,?,?,?)", row)
    conn.commit()
    conn.close()
    return path


def test_board_adapter_preserves_event_source_id(tmp_path):
    path = _raw_board(
        tmp_path / "board.db",
        events=[(10, "t1", "created", 100, None, "{}"),
                (11, "t1", "completed", 200, 1, "{}")],
        runs=[(1, "t1", "worker-a", "completed", "completed", None, "{}")],
    )
    snap = KanbanDbBoard(db_path=path).get("t1")
    assert [e.source_id for e in snap.events] == [10, 11]
    assert snap.events[0].event_id != snap.events[1].event_id


def test_board_adapter_refuses_duplicate_run_ids(tmp_path):
    path = _raw_board(
        tmp_path / "board.db",
        runs=[(1, "t1", "worker-a", "completed", "completed", None, "{}"),
              (1, "t1", "reviewer-a", "review", "completed", None, "{}")],
        runs_pk=False,
    )
    with pytest.raises(BoardError) as err:
        KanbanDbBoard(db_path=path).get("t1")
    assert err.value.kind == "invalid"
    assert "duplicate run" in str(err.value)


def test_board_adapter_refuses_invalid_event_source_id(tmp_path):
    path = _raw_board(
        tmp_path / "board.db",
        events=[(1.5, "t1", "created", 100, None, "{}")],
        event_id_type="REAL",
    )
    with pytest.raises(BoardError) as err:
        KanbanDbBoard(db_path=path).get("t1")
    assert err.value.kind == "invalid"
    assert "source id" in str(err.value)


def test_board_adapter_reads_subscriptions_without_leaking(tmp_path):
    path = _raw_board(
        tmp_path / "board.db",
        events=[(1, "t1", "created", 100, None, "{}")],
        subscriptions=[("t1", "api_server", "chat-secret-123", "manager-a", "wake")],
    )
    rows = KanbanDbBoard(db_path=path).subscriptions("t1")
    assert rows == [{"platform": "api_server", "chat_id": "chat-secret-123",
                     "notifier_profile": "manager-a", "delivery_mode": "wake"}]


# ── API surface ────────────────────────────────────────────────────────────

def _load_plugin_api():
    path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("department_hub_contract_api", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client(module):
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/department-hub")
    return TestClient(app)


def test_api_exposes_held_review_and_not_success(tmp_path, monkeypatch):
    module = _load_plugin_api()
    store = Store(tmp_path / "state.db")
    store.register_department(dept())
    store.bind_task("dev", "t1", role="root")
    svc = DashboardService(store, FakeBoard({"t1": rejected_task()}))
    monkeypatch.setattr(module, "build_service", lambda settings=None: svc)
    body = _client(module).get("/api/plugins/department-hub/departments/dev").json()
    row = body["department"]["tasks"][0]
    assert row["review"]["held"] is True
    assert row["review"]["held_code"] == "review_rejected"
    assert row["acceptance"] is None
    assert row["final_report"] is None
    assert row["delivery_readiness"]["state"] == "held"
    assert body["department"]["manager_status"]["owner"]["state"] == "unverified"
    assert body["department"]["manager_status"]["wake"]["state"] == "unconfirmed"
    assert body["contract"]["version"] == CONTRACT_VERSION


def _held_task(code: str):
    """Build a board state that makes M1's own gate return ``code``."""
    if code == "inactive_department":
        return passing_task(), {"active": False, "produce": True}
    if code == "no_pass_verdict":
        return task(
            "t1", "ready", assignee="worker-a",
            events=[event("review_requested", 200, 5, reviewer="reviewer-a",
                          implementer="worker-a")],
            runs=[run(5, "worker-a", "review_requested", outcome="review_requested")],
        ), {}
    if code == "review_rejected":
        return rejected_task(), {}
    if code == "repair_loop_exhausted":
        return rejected_task(3), {}
    if code == "verdict_conflict":
        return task(
            "t1", "done", assignee="worker-a",
            events=_review() + [event("completed", 300, 6)],
            runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
                  run(6, "reviewer-a", "completed", outcome="completed",
                      verdict="REQUEST_CHANGES", artifact_sha256=SHA)],
        ), {"produce": True}
    if code == "review_unproven":
        return passing_task(summary="looks good", structured=None), {"produce": True}
    if code == "rejection_unbound":
        return task(
            "t1", "ready", assignee="worker-a",
            events=[event("review_requested", 200, 5, reviewer="reviewer-a",
                          implementer="worker-a"),
                    event("changes_requested", 300, 99, reason="unbound")],
            runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
                  run(9, "reviewer-a", "review", outcome="changes_requested")],
        ), {}
    if code == "reviewer_not_independent":
        return task(
            "t1", "done", assignee="worker-a",
            events=[event("review_requested", 200, 5, reviewer="reviewer-a",
                          implementer="worker-a"),
                    event("claimed", 250, 6, source_status="review"),
                    event("completed", 300, 6)],
            runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
                  run(6, "worker-a", "completed", outcome="completed", verdict="PASS",
                      artifact_sha256=SHA)],
        ), {"produce": True}
    if code == "reviewer_not_requested":
        return task(
            "t1", "done", assignee="worker-a",
            events=[event("review_requested", 200, 5, reviewer="reviewer-a",
                          implementer="worker-a"),
                    event("claimed", 250, 6, source_status="review"),
                    event("completed", 300, 6)],
            runs=[run(5, "worker-a", "review_requested", outcome="review_requested"),
                  run(6, "reviewer-b", "completed", outcome="completed", verdict="PASS",
                      artifact_sha256=SHA)],
        ), {"produce": True, "reviewers": ["reviewer-a", "reviewer-b"]}
    if code == "implementer_not_member":
        return task(
            "t1", "done", assignee="worker-z",
            events=[event("review_requested", 200, 5, reviewer="reviewer-a",
                          implementer="worker-z"),
                    event("claimed", 250, 6, source_status="review"),
                    event("completed", 300, 6)],
            runs=[run(5, "worker-z", "review_requested", outcome="review_requested"),
                  run(6, "reviewer-a", "completed", outcome="completed", verdict="PASS",
                      artifact_sha256=SHA)],
        ), {"produce": True, "producer": "worker-z"}
    if code == "verdict_not_final":
        return passing_task(status="review"), {"produce": True}
    if code == "artifact_unbound":
        return passing_task(), {}
    if code == "artifact_declaration_conflict":
        return passing_task(artifact_sha=SHA2), {"produce": True}
    raise AssertionError(code)


READ_MODEL_REACHABLE = [
    "inactive_department", "no_pass_verdict", "review_rejected",
    "repair_loop_exhausted", "verdict_conflict", "review_unproven",
    "rejection_unbound", "reviewer_not_independent", "reviewer_not_requested",
    "implementer_not_member", "verdict_not_final", "artifact_unbound",
    "artifact_declaration_conflict",
]


@pytest.mark.parametrize("code", READ_MODEL_REACHABLE)
def test_api_surfaces_every_held_code(tmp_path, monkeypatch, code):
    module = _load_plugin_api()
    store = Store(tmp_path / "state.db")
    snap, options = _held_task(code)
    department = dept(reviewers=options.get("reviewers") or ["reviewer-a"],
                      active=options.get("active", True))
    store.register_department(department)
    store.bind_task("dev", "t1", role="root")
    if options.get("produce"):
        produce(store, sha=SHA, profile=options.get("producer", "worker-a"))
    svc = DashboardService(store, FakeBoard({"t1": snap}))
    monkeypatch.setattr(module, "build_service", lambda settings=None: svc)
    body = _client(module).get("/api/plugins/department-hub/departments/dev").json()
    row = body["department"]["tasks"][0]
    assert row["review"]["held"] is True, row["review"]
    assert row["review"]["held_code"] == code
    assert row["review"]["state"] in ("held", "rejected")
    assert row["review"]["ready_to_deliver"] is False
    assert row["acceptance"] is None
    assert row["final_report"] is None
    assert body["department"]["final_report"]["state"] == "none"
    # Held is never displayed as a delivered/ready handoff.
    assert row["delivery_readiness"]["ready"] is False
    assert row["delivery_readiness"]["state"] in ("held", "unknown")


def test_api_exposes_the_persisted_final_report(tmp_path, monkeypatch):
    """The API projects the same plugin report the Desktop page renders."""
    module = _load_plugin_api()
    store = Store(tmp_path / "state.db")
    store.register_department(dept())
    store.bind_task("dev", "t1", role="root")
    _put_confirmed_report(store, _v2_body(summary="api report — plain text",
                                          generation=4, revision=2, run_id=9))
    svc = DashboardService(store, FakeBoard({"t1": passing_task()}))
    monkeypatch.setattr(module, "build_service", lambda settings=None: svc)
    body = _client(module).get("/api/plugins/department-hub/departments/dev").json()
    row = body["department"]["tasks"][0]
    assert row["final_report"]["action"] == "delivered"
    assert row["final_report"]["report"]["summary"] == "api report — plain text"
    assert row["final_report"]["report"]["generation"] == 4
    assert row["final_report"]["delivered_identity"] == {
        "task_id": "t1", "revision": 2, "verdict_run_id": 9, "artifact_sha256": SHA}
    assert body["department"]["final_report"]["history"][0]["summary"] == \
        "api report — plain text"
    assert body["contract"]["version"] == "department-hub/m1-contract/v5"
    # Read-only: the two reads wrote nothing.
    assert store.snapshot()["deliveries"] == {"confirmed": 1}


def test_api_ready_to_deliver_needs_an_active_acceptance(tmp_path, monkeypatch):
    module = _load_plugin_api()
    store = Store(tmp_path / "state.db")
    department = dept()
    store.register_department(department)
    store.bind_task("dev", "t_root", role="root")
    store.bind_membership("dev", "t1", "t_root", ("t_root",))
    produce(store, sha=SHA)
    board = FakeBoard({
        "t_root": task("t_root", "done", assignee="manager-a",
                       events=[event("created", 100)]),
        "t1": passing_task(parents=("t_root",)),
    })
    svc = DashboardService(store, board)
    monkeypatch.setattr(module, "build_service", lambda settings=None: svc)
    client = _client(module)

    body = client.get("/api/plugins/department-hub/departments/dev").json()
    row = row_for({"department": body["department"]}, "t1")
    assert row["review"]["ready_to_deliver"] is True
    assert row["delivery_readiness"]["state"] == "held"
    assert row["delivery_readiness"]["code"] == "acceptance_missing"

    store.put_verdict(verdict_id="v1", dept_id="dev", task_id="t1", run_id=6,
                      reviewer_profile="reviewer-a", revision=1, artifact_sha256=SHA,
                      verdict="PASS", note="", event_id="run:6")
    store.put_acceptance(acceptance_id="a1", dept_id="dev", task_id="t1", verdict_id="v1",
                         revision=1, artifact_sha256=SHA, manager_profile="manager-a")
    svc.invalidate()
    body = client.get("/api/plugins/department-hub/departments/dev").json()
    row = row_for({"department": body["department"]}, "t1")
    assert row["acceptance"]["current"] is True
    assert row["delivery_readiness"]["state"] == "ready"
    assert row["delivery_readiness"]["code"] is None
    # Readiness is not delivery: no confirmed row exists.
    assert row["final_report"] is None
    assert "delivered" not in row["delivery_readiness"]["state"]


def test_api_has_no_escalation_or_delivery_mutation_route():
    module = _load_plugin_api()
    paths = [getattr(route, "path", "") for route in module.router.routes]
    assert not any("escalat" in path for path in paths)
    assert not any("deliver" in path for path in paths)
    mutation_paths = {(route.path, method) for route in module.router.routes
                      for method in getattr(route, "methods", set())
                      if method not in ("GET", "HEAD", "OPTIONS")}
    assert mutation_paths == {
        ("/departments/plan", "POST"),
        ("/departments", "POST"),
        ("/departments/{dept_id}", "PATCH"),
        ("/departments/{dept_id}", "DELETE"),
    }

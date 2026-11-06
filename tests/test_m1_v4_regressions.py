"""M1 v4/v4.1 regressions for the review blockers.

Covers, one test group per finding:
* P1-A  free-text negatives / non-token positives never grant authority;
* P1-B  a `changes_requested` the plugin cannot attribute vetoes (it is not a
        repair round with no consequence);
* P2-1  accept and deliver report the *same* strongest hold;
* P2-4  a real producer (`department_submit`) makes `ready_to_deliver`
        reachable while everything else stays `artifact_unbound`;
* P2-2  an inactive department cannot escalate, including mid-effect-window;
* P2-3  JSON scalar source ids are one identity, not two;
* P2-5  (v4.1) `department_submit` must precede the acceptance, and a post-hoc
        submit never retroactively authorises one;
* P3    (v4.1) release merges the durable-authority hold like accept does, and
        a delivered task has no second deliverable generation.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace

import pytest

from department_lib.acceptance import AcceptanceClaim, leading_positive_prose
from department_lib.board import Board
from department_lib.delivery import Delivery, DeliveryResult
from department_lib.models import BoardEvent, normalize_source_id
from department_lib.orchestrator import DepartmentService
from department_lib.routing import Router
from department_lib.store import Store
from tests.test_m1_v2_regressions import (  # noqa: F401  (fixtures/shared harness)
    A, CLI, D, Port, claim, payload, setup,
)


def _with_root(st, cli):
    cli.tasks["root"] = payload("root", "manager")
    cli.tasks["root"]["events"] = []
    cli.tasks["root"]["runs"] = []
    cli.tasks["work"]["parents"] = ["root"]
    st.bind_task("dev", "root", role="root")


def _service(tmp_path, *, produce_sha=A, name="s.db"):
    st = Store(tmp_path / name)
    st.register_department(D)
    st.bind_task("dev", "work", role="work")
    if produce_sha is not None:
        st.put_artifact(task_id="work", revision=1, sha256=produce_sha, algorithm="sha256",
                        path="/synthetic/work", real_path="/synthetic/work", size_bytes=0,
                        producer_profile="worker", computed_at=0.0)
    cli = CLI({"work": payload()})
    port = Port()
    svc = DepartmentService(st, Board(cli), Delivery(st, port), Router(cli))
    return st, cli, svc, port


def _run(cli, index=1):
    return cli.tasks["work"]["runs"][index]


def _add_authorized_rejection(cli, *, run_id=7, at=110, request_run=5, label="fix it"):
    cli.tasks["work"]["events"].append(
        {"id": 10 + run_id, "kind": "claimed", "created_at": at - 1, "run_id": run_id,
         "payload": {"source_status": "review"}})
    cli.tasks["work"]["events"].append(
        {"id": 100 + run_id, "kind": "changes_requested", "created_at": at, "run_id": run_id,
         "payload": {"reviewer": "reviewer", "reason": label}})
    cli.tasks["work"]["runs"].append(
        {"id": run_id, "profile": "reviewer", "outcome": "changes_requested", "status": "done",
         "summary": "REVIEW: REQUEST_CHANGES", "metadata": {}})


# ── P1-A: free text ─────────────────────────────────────────────────────────

NEGATIVE_PROSE = [
    "not approved",
    "changes required",
    "changes requested",
    "REQUEST CHANGES",
    "changes_requested",
    "複審未通過：6 項 P1 未修",
    "不通過，請重修",
    "M1 v3 審查未通過",
    "REQUEST_CHANGES",
    "REJECT: unsafe",
    "駁回：P1 未修",
    "審查未通過，退回重修",
]


@pytest.mark.parametrize("summary", NEGATIVE_PROSE)
def test_negative_prose_without_structured_verdict_never_delivers(tmp_path, summary):
    st, cli, svc, port = _service(tmp_path)
    run = _run(cli)
    run["metadata"].pop("verdict")
    run["summary"] = summary
    gate = svc.accept(claim())
    assert gate.status == "held", (summary, gate.reason)
    assert gate.reason in ("verdict_conflict", "review_unproven")
    released = svc.release(claim())
    assert released.action == "held" and port.appends == 0
    assert st.delivery_count() == 0 and st.active_acceptance("dev", "work") is None


@pytest.mark.parametrize("summary", ["REVIEW: PASS", "PASS", "review: pass",
                                     "REVIEW: PASS — details in the report"])
def test_explicit_positive_token_still_delivers_exactly_once(tmp_path, summary):
    st, cli, svc, port = _service(tmp_path)
    run = _run(cli)
    run["metadata"].pop("verdict")
    run["summary"] = summary
    gate = svc.accept(claim())
    assert gate.status == "releasable", gate.reason
    assert svc.release(claim()).action == "delivered"
    assert svc.release(claim()).action == "already_delivered"
    assert port.appends == 0


@pytest.mark.parametrize("summary", ["looks good", "all checks passed", "LGTM", "已通過"])
def test_friendly_prose_is_not_authority(tmp_path, summary):
    st, cli, svc, port = _service(tmp_path)
    run = _run(cli)
    run["metadata"].pop("verdict")
    run["summary"] = summary
    gate = svc.accept(claim())
    assert gate.status == "held" and gate.reason == "review_unproven"
    assert svc.release(claim()).action == "held" and port.appends == 0


@pytest.mark.parametrize("summary", ["REVIEW: PASS; REQUEST CHANGES",
                                     "PASS but changes required",
                                     "REVIEW: PASS — 未通過的部分見下"])
def test_a_summary_with_both_directions_cannot_pass(tmp_path, summary):
    st, cli, svc, port = _service(tmp_path)
    run = _run(cli)
    run["metadata"].pop("verdict")
    run["summary"] = summary
    gate = svc.accept(claim())
    assert gate.status == "held" and gate.reason == "verdict_conflict"
    assert svc.release(claim()).action == "held" and port.appends == 0


def test_structured_pass_cannot_launder_a_chinese_negative(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    _run(cli)["summary"] = "駁回：P1 未修"      # metadata still says verdict=PASS
    gate = svc.accept(claim())
    assert gate.status == "held" and gate.reason == "verdict_conflict"
    assert svc.release(claim()).action == "held" and port.appends == 0


def test_leading_positive_prose_is_anchored_to_a_token():
    assert leading_positive_prose("REVIEW: PASS") is True
    assert leading_positive_prose("PASS — fine") is True
    assert leading_positive_prose("passed") is False
    assert leading_positive_prose("looks good") is False
    assert leading_positive_prose(None) is False


# ── P1-B: unattributable rejection ──────────────────────────────────────────

def test_null_run_rejection_vetoes_instead_of_counting_and_ignoring(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == "releasable"
    cli.tasks["work"]["events"].append(
        {"id": 4, "kind": "changes_requested", "created_at": 110, "run_id": None,
         "payload": {"reason": "reject without a run"}})
    bumped = replace(claim(), revision=2)
    gate = svc.accept(bumped)
    assert gate.status == "held" and gate.reason == "rejection_unbound"
    assert svc.release(bumped).action == "held" and port.appends == 0
    # the old generation PASS cannot be used under the bumped revision either
    assert svc.release(claim()).action == "held" and port.appends == 0
    assert st.delivery_count() == 0


def test_rejection_bound_to_a_non_reviewer_run_is_unattributable(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    cli.tasks["work"]["events"].append(
        {"id": 4, "kind": "changes_requested", "created_at": 110, "run_id": 7,
         "payload": {"reason": "worker wrote this"}})
    cli.tasks["work"]["runs"].append(
        {"id": 7, "profile": "worker", "outcome": "changes_requested", "status": "done",
         "metadata": {}})
    gate = svc.accept(replace(claim(), revision=2))
    assert gate.status == "held" and gate.reason == "rejection_unbound"
    assert svc.release(replace(claim(), revision=2)).action == "held"


def test_authorized_rejection_still_vetoes_the_previous_pass(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    _add_authorized_rejection(cli, run_id=7, at=110)
    bumped = replace(claim(), revision=2, verdict_run_id=7)
    gate = svc.accept(bumped)
    assert gate.status == "held" and gate.reason == "review_rejected"
    assert svc.release(bumped).action == "held" and port.appends == 0
    assert svc.release(claim()).action == "held" and port.appends == 0


def test_old_generation_rejection_neither_unbinds_the_artifact_nor_vetoes(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    cli.tasks["work"]["events"].append(
        {"id": 4, "kind": "changes_requested", "created_at": 50, "run_id": 6,
         "payload": {"reason": "replayed before the request"}})
    aged = replace(claim(), revision=2)
    gate = svc.accept(aged)
    assert gate.status == "releasable", gate.reason
    assert svc.release(aged).action == "delivered" and port.appends == 0


# ── P2-1: one hold reason for one board state ───────────────────────────────

def test_accept_and_deliver_report_the_same_exhausted_repair_hold(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    for index, run_id in enumerate((6, 7, 8)):
        if index == 0:
            cli.tasks["work"]["events"] = [
                e for e in cli.tasks["work"]["events"] if not (
                    e["kind"] == "changes_requested") ]
            cli.tasks["work"]["runs"] = [
                r for r in cli.tasks["work"]["runs"] if r["id"] == 5]
        _add_authorized_rejection(cli, run_id=run_id, at=110 + index * 10)
    assert cli.tasks["work"]["runs"][0]["id"] == 5
    svc.ingest("work")                       # durable rejection authority
    exhausted = replace(claim(), revision=4, verdict_run_id=8)
    accepted = svc.accept(exhausted)
    released = svc.release(exhausted)
    assert accepted.status == "held"
    assert accepted.reason == "repair_loop_exhausted"
    assert released.action == "held"
    assert released.reason == accepted.reason
    assert port.appends == 0


# ── P2-4: artifact producer ─────────────────────────────────────────────────

def test_submission_without_the_producer_stays_artifact_unbound(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    # The board still claims a SHA on both runs; a claim is not a producer.
    gate = svc.accept(claim())
    assert gate.status == "held" and gate.reason == "artifact_unbound"
    assert svc.release(claim()).action == "held" and port.appends == 0
    assert svc.submit_artifact("work", "relative.bin", caller_profile="worker")["reason"] \
        == "artifact_path_invalid"


def test_producer_records_path_algorithm_bytes_and_time(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    data = b"department artifact v1\n"
    path = tmp_path / "artifact.bin"
    path.write_bytes(data)
    result = svc.submit_artifact("work", str(path), caller_profile="worker")
    assert result["success"] is True and result["action"] == "recorded"
    assert result["revision"] == 1
    art = result["artifact"]
    assert art["sha256"] == hashlib.sha256(data).hexdigest()
    assert art["algorithm"] == "sha256"
    assert art["size_bytes"] == len(data)
    assert art["path"] == str(path)
    assert art["real_path"] == str(path.resolve())
    assert art["producer_profile"] == "worker"
    assert isinstance(art["computed_at"], float) and art["computed_at"] > 0
    again = svc.submit_artifact("work", str(path), caller_profile="worker")
    assert again["action"] == "already_recorded"
    assert again["artifact"]["sha256"] == art["sha256"]


def test_producer_refuses_inputs_it_cannot_attribute_or_read(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    missing = tmp_path / "nope.bin"
    assert svc.submit_artifact("work", str(missing), caller_profile="worker")["reason"] \
        == "artifact_missing"
    assert svc.submit_artifact("work", str(tmp_path), caller_profile="worker")["reason"] \
        == "artifact_not_regular"
    directory = tmp_path / "file.bin"
    directory.write_bytes(b"x")
    assert svc.submit_artifact("work", str(directory), caller_profile="outsider")["reason"] \
        == "caller_not_permitted"
    assert svc.submit_artifact("work", str(directory), caller_profile="")["reason"] \
        == "caller_unverified"
    with pytest.raises(ValueError):
        svc.submit_artifact("not-bound", str(directory), caller_profile="worker")
    assert port.appends == 0


def test_a_second_different_artifact_for_the_same_revision_is_refused(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    first = tmp_path / "first.bin"
    first.write_bytes(b"first")
    second = tmp_path / "second.bin"
    second.write_bytes(b"second")
    assert svc.submit_artifact("work", str(first), caller_profile="worker")["success"]
    refused = svc.submit_artifact("work", str(second), caller_profile="worker")
    assert refused["success"] is False and refused["reason"] == "artifact_identity_conflict"
    identity = st.artifact_identity("work", 1)
    assert identity.sha256 == hashlib.sha256(b"first").hexdigest()


def test_end_to_end_submit_review_ready_and_manual_delivery(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    for run in cli.tasks["work"]["runs"]:
        run["metadata"].pop("artifact_sha256", None)
        run["metadata"].pop("verdict", None)
    _run(cli)["summary"] = "REVIEW: PASS"
    data = b"# deliverable\n"
    path = tmp_path / "deliverable.md"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()

    assert svc.submit_artifact("work", str(path), caller_profile="worker")["success"]
    gate = svc.accept(claim(sha))           # durable acceptance == ready_to_deliver
    assert gate.status == "releasable" and gate.artifact_sha256 == sha

    first = svc.release(claim(sha))          # the human-triggered handoff
    assert first.action == "delivered" and port.appends == 0
    body = st.deliveries_for_dept('dev')[0].body
    assert body["verdict"]["artifact_sha256"] == sha
    assert body["status"] == "accepted"

    again = svc.release(claim(sha))
    assert again.action == "already_delivered" and port.appends == 0


def test_board_declaration_disagreeing_with_the_producer_holds(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    _run(cli)["summary"] = "REVIEW: PASS"
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"real bytes")
    assert svc.submit_artifact("work", str(path), caller_profile="worker")["success"]
    # payload() still declares A on the submission and on the review run
    gate = svc.accept(claim(hashlib.sha256(b"real bytes").hexdigest()))
    assert gate.status == "held" and gate.reason == "artifact_declaration_conflict"
    assert svc.release(claim(hashlib.sha256(b"real bytes").hexdigest())).action == "held"
    assert port.appends == 0


def test_a_repair_round_requires_a_new_producer_record(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    for run in cli.tasks["work"]["runs"]:
        run["metadata"].pop("artifact_sha256", None)
    path_v1 = tmp_path / "v1.bin"
    path_v1.write_bytes(b"v1")
    sha_v1 = hashlib.sha256(b"v1").hexdigest()
    assert svc.submit_artifact("work", str(path_v1), caller_profile="worker")["success"]

    _add_authorized_rejection(cli, run_id=7, at=110)
    cli.tasks["work"]["events"].append(
        {"id": 20, "kind": "review_requested", "created_at": 200, "run_id": 8,
         "payload": {"reviewer": "reviewer", "implementer": "worker"}})
    cli.tasks["work"]["events"].append(
        {"id": 21, "kind": "claimed", "created_at": 201, "run_id": 9,
         "payload": {"source_status": "review"}})
    cli.tasks["work"]["runs"].extend([
        {"id": 8, "profile": "worker", "outcome": "review_requested", "status": "done",
         "metadata": {}},
        {"id": 9, "profile": "reviewer", "outcome": "completed", "status": "done",
         "summary": "REVIEW: PASS", "metadata": {}},
    ])
    claim_v2 = AcceptanceClaim("work", 2, 9, sha_v1, "manager")
    held = svc.accept(claim_v2)
    assert held.status == "held" and held.reason == "artifact_unbound"
    assert svc.release(claim_v2).action == "held" and port.appends == 0

    path_v2 = tmp_path / "v2.bin"
    path_v2.write_bytes(b"v2")
    sha_v2 = hashlib.sha256(b"v2").hexdigest()
    assert svc.submit_artifact("work", str(path_v2), caller_profile="worker")["revision"] == 2
    claim_v2 = AcceptanceClaim("work", 2, 9, sha_v2, "manager")
    assert svc.accept(claim_v2).status == "releasable"
    assert svc.release(claim_v2).action == "delivered" and port.appends == 0


def test_a_rejection_after_submission_still_blocks_delivery(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    for run in cli.tasks["work"]["runs"]:
        run["metadata"].pop("artifact_sha256", None)
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"bytes")
    sha = hashlib.sha256(b"bytes").hexdigest()
    assert svc.submit_artifact("work", str(path), caller_profile="worker")["success"]
    _add_authorized_rejection(cli, run_id=7, at=110)
    rejected = AcceptanceClaim("work", 2, 7, sha, "manager")
    assert svc.accept(rejected).reason == "review_rejected"
    assert svc.release(rejected).action == "held" and port.appends == 0


# ── P2-2: inactive department escalation ────────────────────────────────────

def test_inactive_department_cannot_escalate(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    _with_root(st, cli)
    st.register_department(replace(D, active=False))
    result = svc.escalate("work", "blocked", {})
    assert result.action == "held" and result.reason == "inactive_department"
    assert port.appends == 0 and st.delivery_count() == 0
    assert st.escalation_count("dev", "work") == 0


def test_escalation_reauthorizes_inside_the_effect_transaction(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    _with_root(st, cli)
    calls = []

    def authorize():
        calls.append(st.connection.in_transaction)
        if len(calls) == 2:
            return DeliveryResult("held", "inactive_department")
        return None

    result = svc.delivery.escalate(D, "work", "blocked", {}, root_task_id="work",
                                   authorize=authorize)
    assert result.action == "held" and result.reason == "inactive_department"
    assert calls == [True, True]
    assert port.appends == 0 and st.delivery_count() == 0


def test_deactivation_between_claim_and_effect_is_held(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    _with_root(st, cli)
    calls = {"n": 0}
    original = st.get_department

    def counting(dept_id):
        calls["n"] += 1
        if calls["n"] == 3:                  # the phase-2 re-authorization
            st.register_department(replace(D, active=False))
        return original(dept_id)

    st.get_department = counting
    result = svc.escalate("work", "blocked", {})
    assert calls["n"] >= 3
    assert result.action == "held" and result.reason == "inactive_department"
    assert port.appends == 0 and st.delivery_count() == 0


# ── P2-3: JSON scalar source identity ───────────────────────────────────────

def test_scalar_source_id_types_are_one_identity(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    numeric = BoardEvent("work", "changes_requested", 6, 103, {"reason": "x"}, source_id=9)
    textual = replace(numeric, source_id="9")
    assert numeric.event_id == textual.event_id
    assert st.record_event(numeric, "dev") == "new"
    assert st.record_event(textual, "dev") == "duplicate"
    assert len(st.events_for_task("work")) == 1
    conflicting = replace(numeric, source_id="9", payload={"reason": "different"})
    assert st.record_event(conflicting, "dev") == "conflict"


def test_source_id_normalisation_keeps_booleans_distinct():
    assert normalize_source_id(9) == normalize_source_id("9")
    assert normalize_source_id(True) != normalize_source_id(1)
    assert normalize_source_id(True) != normalize_source_id("True")


def test_int_and_string_source_id_do_not_double_count_a_repair_round(setup):
    st, cli, svc, port = setup
    cli.tasks["work"]["events"].extend([
        {"id": 9, "kind": "changes_requested", "created_at": 110, "run_id": 6,
         "payload": {"reason": "x"}},
        {"id": "9", "kind": "changes_requested", "created_at": 110, "run_id": 6,
         "payload": {"reason": "x"}},
    ])
    report = svc.ingest("work")
    statuses = [d.status for d in report.decisions]
    assert statuses.count("duplicate") == 1 and statuses.count("conflict") == 0
    assert st.rounds("dev", "work") == 1
    assert report.revision == 2


# ── P2-5: producer ordering contract (v4.1) ─────────────────────────────────

def _review_ready(tmp_path):
    """A PASS review with no board-declared artifact SHA anywhere."""
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    for run in cli.tasks["work"]["runs"]:
        run["metadata"].pop("artifact_sha256", None)
        run["metadata"].pop("verdict", None)
    _run(cli)["summary"] = "REVIEW: PASS"
    return st, cli, svc, port


def _advance_to_revision_two(cli):
    """Authorised rejection, new review request and a new PASS (revision 2)."""
    _add_authorized_rejection(cli, run_id=7, at=110)
    cli.tasks["work"]["events"].append(
        {"id": 20, "kind": "review_requested", "created_at": 200, "run_id": 8,
         "payload": {"reviewer": "reviewer", "implementer": "worker"}})
    cli.tasks["work"]["events"].append(
        {"id": 21, "kind": "claimed", "created_at": 201, "run_id": 9,
         "payload": {"source_status": "review"}})
    cli.tasks["work"]["runs"].extend([
        {"id": 8, "profile": "worker", "outcome": "review_requested", "status": "done",
         "metadata": {}},
        {"id": 9, "profile": "reviewer", "outcome": "completed", "status": "done",
         "summary": "REVIEW: PASS", "metadata": {}},
    ])


def test_accept_before_the_producer_stays_unbound_and_is_not_retroactive(tmp_path):
    """`department_submit` must be recorded before `department_accept`.

    A board declaration is not identity, so the accept that runs first holds
    `artifact_unbound` and records nothing. A later submit does not retroactively
    authorise that acceptance: `department_deliver` needs a *recorded* acceptance
    and holds `acceptance_missing` until the manager accepts again.
    """
    st, cli, svc, port = _review_ready(tmp_path)
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"post-hoc bytes\n")
    sha = hashlib.sha256(b"post-hoc bytes\n").hexdigest()

    early = svc.accept(claim(sha))
    assert early.status == "held" and early.reason == "artifact_unbound"
    assert st.active_acceptance("dev", "work") is None

    submitted = svc.submit_artifact("work", str(path), caller_profile="worker")
    assert submitted["success"] and submitted["artifact"]["sha256"] == sha

    posthoc = svc.release(claim(sha))
    assert posthoc.action == "held" and posthoc.reason == "acceptance_missing"
    assert port.appends == 0 and st.delivery_count() == 0

    fresh = svc.accept(claim(sha))              # the required, explicit new accept
    assert fresh.status == "releasable" and fresh.artifact_sha256 == sha
    assert svc.release(claim(sha)).action == "delivered" and port.appends == 0


def test_a_submit_after_the_review_is_allowed_by_contract(tmp_path):
    """P2-5 decision (ii): submit may follow the review — a documented limitation.

    Pins the *contract*, not a safety property: these bytes were chosen after the
    reviewer's PASS and the reviewer never saw them, yet the delivered handoff
    asserts the worker-declared SHA next to the reviewer's run. Nothing in M1
    claims the reviewer attested those bytes (contract §7/§8).
    """
    st, cli, svc, port = _review_ready(tmp_path)
    assert _run(cli)["outcome"] == "completed", "the review is already finished"
    path = tmp_path / "late.bin"
    path.write_bytes(b"bytes the reviewer never saw\n")
    sha = hashlib.sha256(b"bytes the reviewer never saw\n").hexdigest()

    submitted = svc.submit_artifact("work", str(path), caller_profile="worker")
    assert submitted["success"] and submitted["artifact"]["sha256"] == sha
    assert svc.accept(claim(sha)).status == "releasable"
    assert svc.release(claim(sha)).action == "delivered"
    body = st.deliveries_for_dept('dev')[0].body
    assert body["verdict"]["artifact_sha256"] == sha
    assert body["verdict"]["reviewer"] == "reviewer"


# ── P3-3: accept and release agree on the strongest hold ────────────────────

def test_accept_and_release_report_the_same_strongest_hold(tmp_path, monkeypatch):
    """A weaker gate hold must not hide a stronger durable hold on release."""
    st, cli, svc, port = _service(tmp_path)
    run = _run(cli)
    run["metadata"].pop("verdict")
    run["summary"] = "looks good"                 # gate hold: review_unproven (0)
    monkeypatch.setattr(DepartmentService, "_authority_hold",
                        lambda self, dept, snap, **kw: "review_rejected")   # stronger (55)
    accepted = svc.accept(claim())
    released = svc.release(claim())
    assert accepted.status == "held" and accepted.reason == "review_rejected"
    assert released.action == "held"
    assert released.reason == accepted.reason
    assert port.appends == 0 and st.delivery_count() == 0


# ── P3-4: one final handoff — the second generation is not deliverable ──────

def test_a_delivered_task_has_no_second_deliverable_generation(tmp_path):
    """After delivery, a new generation may be `releasable` but never delivered."""
    st, cli, svc, port = _review_ready(tmp_path)
    path_v1 = tmp_path / "v1.bin"
    path_v1.write_bytes(b"v1")
    sha1 = hashlib.sha256(b"v1").hexdigest()
    assert svc.submit_artifact("work", str(path_v1), caller_profile="worker")["success"]
    assert svc.accept(claim(sha1)).status == "releasable"
    assert svc.release(claim(sha1)).action == "delivered"
    assert port.appends == 0

    _advance_to_revision_two(cli)
    path_v2 = tmp_path / "v2.bin"
    path_v2.write_bytes(b"v2")
    sha2 = hashlib.sha256(b"v2").hexdigest()
    assert svc.submit_artifact("work", str(path_v2), caller_profile="worker")["revision"] == 2
    second = AcceptanceClaim("work", 2, 9, sha2, "manager")
    assert svc.accept(second).status == "releasable"     # readiness is real...
    again = svc.release(second)
    assert again.action == "held"                         # ...but not a second handoff
    assert again.reason == "delivery_identity_conflict"
    assert port.appends == 0

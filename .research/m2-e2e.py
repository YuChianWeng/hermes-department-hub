#!/usr/bin/env python3
"""M2 isolated end-to-end run: dispatch → manager event → review fail → fix →
independent PASS → manager accept → single final handoff → M2 read-back.

Real components, isolated state only:

* real ``hermes kanban`` CLI for board reads/writes and the manager wake
  subscription (documented commands; no gateway, no model, no credentials);
* real ``hermes_cli.kanban_db`` transitions for the dispatcher-owned steps
  (claim / review-claim / complete), because there is no CLI verb for
  "claim a review task" — this is the same API the dispatcher itself calls;
* real M1 ``DepartmentService`` (Board CLI adapter, durable Store, gate);
* real M2 ``DashboardService`` + read-only ``KanbanDbBoard``.

v5 semantics (``department-hub/m1-contract/v5``): artifact identity is produced
only by ``department_submit`` (a worker-declared SHA-256 of a real local file);
the reviewer never declares bytes; ``department_deliver`` commits one immutable
report in the plugin SQLite (``deliveries.body_json``) and writes **zero**
final board comments. ``department_bind`` answers ``session_owner_unverified``
by design, so this harness never treats ``report.ok`` as the bind verdict.

No production profile, board, gateway, notification or FreshTape is touched:
``HERMES_HOME`` + ``HERMES_KANBAN_HOME`` point into this task's scratch and the
delegated-child fence marker is removed for this isolated subprocess only.

Run:
  PYTHONPATH=/home/ubuntu/.hermes/hermes-agent:. \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python .research/m2-e2e.py <scratch-root>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SCRATCH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT / ".research" / "m2-e2e-run"
HOME = SCRATCH / "home"
KANBAN_HOME = SCRATCH / "kanban-home"
HERMES_CLI = os.environ.get("HERMES_CLI") or "/home/ubuntu/.hermes/hermes-agent/venv/bin/hermes"
AGENT_ROOT = Path(os.environ.get("HERMES_AGENT_ROOT", "/home/ubuntu/.hermes/hermes-agent"))
# A caller-declared digest that never went through ``department_submit``. v5
# must refuse to treat it as identity (``artifact_unbound``), so it is
# deliberately *not* the produced SHA.
UNBOUND_SHA = "a" * 64

failures: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    line = f"{'ok  ' if condition else 'FAIL'} {name}" + (f" :: {extra}" if extra else "")
    print(line, flush=True)
    if not condition:
        failures.append(name)


def clean_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("HERMES_KANBAN_") or key in (
            "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_TASK_ID", "HERMES_RUN_ID",
            "HERMES_SESSION_ID",
        ):
            env.pop(key, None)
    env.update({
        "HERMES_HOME": str(HOME),
        "HERMES_KANBAN_HOME": str(KANBAN_HOME),
        "HERMES_CLI": HERMES_CLI,
        "PYTHONPATH": f"{AGENT_ROOT}:{PROJECT}" + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""),
    })
    return env


ENV = clean_env()


def cli(*args: str, expect_json: bool = False):
    result = subprocess.run([HERMES_CLI, *args], capture_output=True, text=True,
                            env=ENV, timeout=180)
    if result.returncode != 0:
        raise SystemExit(f"CLI FAILED {' '.join(args)} rc={result.returncode}\n"
                         f"stdout={result.stdout}\nstderr={result.stderr}")
    if expect_json:
        return json.loads(result.stdout)
    return result


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    HOME.mkdir(parents=True)
    KANBAN_HOME.mkdir(parents=True)

    # The worker that runs this script inherits the dispatcher's production
    # ``HERMES_KANBAN_*`` pins. Strip them from the *actual* process env (not
    # just the CLI subprocess env) before importing/connecting: the E2E must
    # never resolve to the production board.
    for key in list(os.environ):
        if key.startswith("HERMES_KANBAN_"):
            os.environ.pop(key, None)
    for key, value in ENV.items():
        os.environ[key] = value
    os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)

    # Import after the isolated env is in place.
    sys.path.insert(0, str(AGENT_ROOT))
    sys.path.insert(0, str(PROJECT))
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect

    from department_lib.acceptance import AcceptanceClaim, evidence_from_board
    from department_lib.board import Board
    from department_lib.config import load_departments
    from department_lib.dashboard import DashboardService
    from department_lib.delivery import Delivery
    from department_lib.kanban_cli import CliKanban
    from department_lib.kanban_db_source import KanbanDbBoard
    from department_lib.kanban_effects import CommentEffectPort
    from department_lib.orchestrator import DepartmentService
    from department_lib.store import Store

    print(f"scratch: {SCRATCH}", flush=True)
    resolved = kb.kanban_db_path().resolve()
    check("in-process kanban path is inside the isolated scratch",
          str(resolved).startswith(str(SCRATCH.resolve())), str(resolved))
    if not str(resolved).startswith(str(SCRATCH.resolve())):
        print("RESULT: FAIL (refusing to touch a non-isolated board)", flush=True)
        return 2
    init = cli("kanban", "init")
    check("isolated kanban init", "initialized" in init.stdout.lower() or "kanban" in init.stdout.lower())

    root = cli("kanban", "create", "E2E root goal", "--assignee", "dept-manager",
               "--json", expect_json=True)
    child = cli("kanban", "create", "E2E work item", "--assignee", "worker-a",
                "--json", expect_json=True)
    root_id, child_id = root["id"], child["id"]
    check("board assigned root to the manager", root["assignee"] == "dept-manager", root_id)
    check("board assigned the work item to a member", child["assignee"] == "worker-a", child_id)

    settings = {
        "state_path": str(HOME / "department-hub" / "state.db"),
        "departments": [{
            "id": "dev",
            "name": "Development",
            "manager": {"profile": "dept-manager", "platform": "api_server",
                        "chat_id": "e2e-manager"},
            "members": ["worker-a"],
            "reviewers": ["reviewer-a"],
            "max_repair_rounds": 2,
            "active": True,
        }],
    }
    store = Store(Path(settings["state_path"]))
    depts = load_departments({"departments": settings["departments"]})
    dept = depts["dev"]
    store.register_department(dept)

    cli_port = CliKanban(env=ENV, timeout=180)
    board = Board(cli_port)
    port = CommentEffectPort(cli_port, candidates=store.all_tasks)
    svc = DepartmentService(store, board, Delivery(store, port))

    # ── bind: board-verified root + manager wake subscription ───────────────
    report = svc.bind_department("dev", root_id, [child_id])
    # ``session_owner_unverified`` is the documented v5 answer: ownership is
    # bound, but the public notify list cannot attest the runtime session owner.
    # It is *not* a bind failure, so ``report.ok`` must not gate this check.
    check("bind ensured the root and the work item",
          set(report.ensured) >= {root_id, child_id}, f"ensured={report.ensured}")
    check("bind reports session ownership as unverified, not proven awake",
          bool(report.issues)
          and {i.code for i in report.issues} == {"session_owner_unverified"},
          f"issues={[i.code for i in report.issues]}")
    # ``department_bind`` verifies/routes the extra task ids; the durable
    # ownership row is written by M1's resolver once ancestry exists. This E2E
    # graph has no parent link (a production root card stays open while its
    # work runs), so declare the work item's ownership explicitly — the same
    # binding M1's ``_resolve_dept`` writes when it walks a child to its root.
    store.bind_task("dev", child_id, role="work")
    check("work item is durably owned by the department",
          store.dept_for_task(child_id) == "dev")
    check("manager wake subscription ensured", root_id in report.ensured)
    subs = cli_port.notify_list(root_id)
    check("wake subscription read back", any(
        s.get("platform") == "api_server" and s.get("chat_id") == "e2e-manager"
        and s.get("delivery_mode") == "wake" for s in subs), json.dumps(subs)[:200])

    conn = connect()
    try:
        # ── dispatch + implementation v1 ────────────────────────────────────
        claimed = kb.claim_task(conn, child_id, claimer="e2e:worker")
        check("dispatcher claim of the work item", claimed is not None)
        run1 = kb.latest_run(conn, child_id)
        ok = kb.request_review(conn, child_id, summary="implementation v1",
                               reviewer="reviewer-a", expected_run_id=run1.id)
        check("implementer requested review", bool(ok))
        ing = svc.ingest(child_id)
        check("manager event ingest binds child to department", ing.dept_id == "dev", ing.board_status)

        # ── review v1: explicit REQUEST_CHANGES (fail) ──────────────────────
        review1 = kb.claim_review_task(conn, child_id, claimer="e2e:reviewer")
        check("reviewer claimed the review", review1 is not None)
        run_review1 = kb.latest_run(conn, child_id)
        ok, implementer = kb.request_changes(conn, child_id,
                                             reason="widget is not fixed yet",
                                             expected_run_id=run_review1.id)
        check("reviewer returned changes to the implementer", ok, str(implementer))
        ing = svc.ingest(child_id)
        check("changes_requested recorded durably", store.rounds("dev", child_id) == 1)

        snap = board.get(child_id)
        failed = evidence_from_board(dept, snap)
        check("board evidence shows the failed review", failed.verdict.kind == "REQUEST_CHANGES",
              f"run={failed.verdict.run_id} revision={failed.revision}")
        held = svc.accept(AcceptanceClaim(child_id, failed.revision, failed.verdict.run_id,
                                         UNBOUND_SHA, "dept-manager"))
        check("a failed review cannot be accepted", held.status == "held" and held.reason == "review_rejected",
              f"{held.status}/{held.reason}")

        dash_before = DashboardService(store, KanbanDbBoard(db_path=kb.kanban_db_path()))
        before = dash_before.department("dev")["department"]
        before_task = [t for t in before["tasks"] if t["task_id"] == child_id][0]
        check("M2 page shows REQUEST_CHANGES before the fix",
              before_task["review"]["verdict"] == "REQUEST_CHANGES",
              json.dumps(before_task["review"])[:220])
        check("M2 page shows the repair round",
              before_task["review"]["repair_rounds"] == 1)
        check("M2 page shows no final report before acceptance",
              before["final_report"]["state"] == "none", json.dumps(before["final_report"]))

        # ── correction + independent PASS (revision 2) ──────────────────────
        claimed2 = kb.claim_task(conn, child_id, claimer="e2e:worker-2")
        check("implementer re-claimed after repair", claimed2 is not None)
        run2 = kb.latest_run(conn, child_id)
        ok = kb.request_review(conn, child_id, summary="implementation v2",
                               reviewer="reviewer-a", expected_run_id=run2.id)
        check("corrected work re-submitted for review", bool(ok))
        review2 = kb.claim_review_task(conn, child_id, claimer="e2e:reviewer-2")
        check("independent reviewer claimed the re-review", review2 is not None)
        run_review2 = kb.latest_run(conn, child_id)
        # v5: the reviewer proves PASS and nothing else. Artifact identity comes
        # from ``department_submit``, so no ``artifact_sha256`` is declared here.
        ok = kb.complete_task(
            conn, child_id, summary="PASS: verified the corrected widget",
            metadata={"note": "independent re-review"},
            expected_run_id=run_review2.id)
        check("reviewer completed with PASS (no byte attestation)", bool(ok))
    finally:
        conn.close()

    # ── M1 ingest + gate ────────────────────────────────────────────────────
    ing = svc.ingest(child_id)
    check("passing review ingested", ing.revision == 2 and ing.board_status == "done",
          f"revision={ing.revision} status={ing.board_status}")
    snap = board.get(child_id)
    # v5 producer: the worker (a department member) hashes a real local file at
    # the current revision. Neither board metadata nor a reviewer declaration
    # can supply this identity.
    artifact_path = SCRATCH / "work" / "widget-v2.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "# Corrected widget\n\nv2 fixes the review finding.\n", encoding="utf-8")
    unbound = evidence_from_board(dept, snap, artifact_lookup=store.artifact_identity)
    check("before submit the artifact is still unbound",
          unbound.artifact_sha256 is None, str(unbound.artifact_sha256))
    pre_accept = svc.accept(AcceptanceClaim(child_id, unbound.revision,
                                            unbound.verdict.run_id, UNBOUND_SHA,
                                            "dept-manager"))
    check("v5 identity is producer-only: a caller-declared SHA cannot be accepted",
          pre_accept.status == "held" and pre_accept.reason == "artifact_unbound",
          f"{pre_accept.status}/{pre_accept.reason}")
    sub = svc.submit_artifact(child_id, str(artifact_path), caller_profile="worker-a")
    check("department_submit records the artifact identity", bool(sub.get("success")),
          json.dumps(sub)[:220])
    produced = sub["artifact"]["sha256"]
    check("submit bound the identity to the current revision",
          sub["revision"] == unbound.revision, f"revision={sub.get('revision')}")
    check("produced sha is the real file digest, not a declared value",
          produced != UNBOUND_SHA and len(produced) == 64, produced)
    evidence = evidence_from_board(dept, snap, artifact_lookup=store.artifact_identity)
    check("latest verdict is the independent PASS",
          evidence.verdict.kind == "PASS" and evidence.verdict.reviewer_profile == "reviewer-a",
          f"run={evidence.verdict.run_id}")
    check("artifact identity is the produced sha", evidence.artifact_sha256 == produced,
          str(evidence.artifact_sha256))

    claim = AcceptanceClaim(child_id, evidence.revision, evidence.verdict.run_id, produced,
                            "dept-manager")
    pre = svc.release(claim)
    check("delivery is held until the manager explicitly accepts",
          pre.action == "held" and pre.reason == "acceptance_missing",
          f"{pre.action}/{pre.reason}")
    gate = svc.accept(claim)
    check("manager acceptance verified against the live board",
          gate.status == "releasable", f"{gate.status}/{gate.reason}")
    delivered = svc.release(claim)
    check("single final handoff delivered", delivered.action == "delivered", delivered.action)
    again = svc.release(claim)
    check("second release is idempotent (no duplicate handoff)",
          again.action == "already_delivered", again.action)

    root_snap = board.get(root_id)
    check("v5 delivery writes zero final board comments", len(root_snap.comments) == 0,
          f"comments={len(root_snap.comments)}")
    status_view = svc.status("dev")
    finals = [d for d in status_view["deliveries"] if d["kind"] == "final"]
    check("exactly one final report row exists in the plugin DB", len(finals) == 1,
          json.dumps(finals)[:240])
    report_body = (finals[0]["report"] or {}) if finals else {}
    report_identity = (finals[0]["identity"] or {}) if finals else {}
    check("final report carries the v5 plugin-DB schema",
          report_body.get("schema") == "department-hub/final/v2",
          json.dumps(report_body)[:240])
    check("final report identity is the canonical four fields",
          report_identity.get("artifact_sha256") == produced
          and report_identity.get("revision") == claim.revision
          and report_identity.get("verdict_run_id") == evidence.verdict.run_id
          and report_identity.get("task_id") == child_id,
          json.dumps(report_identity)[:240])
    check("final report body names the accepted task",
          child_id in json.dumps(report_body), json.dumps(report_body)[:240])

    # ── M2 backend read-back from the durable state + Kanban ───────────────
    fresh_store = Store(Path(settings["state_path"]))
    fresh_store.register_department(dept)
    dash = DashboardService(fresh_store, KanbanDbBoard(db_path=kb.kanban_db_path()))
    detail = dash.department("dev")
    dept_view = detail["department"]
    task_view = [t for t in dept_view["tasks"] if t["task_id"] == child_id][0]
    check("M2 list view reports work as done", dept_view["work"]["state"] == "done",
          json.dumps(dept_view["work"]))
    check("M2 work rollup ignores the open root container",
          dept_view["work"]["scope"] == "work_tasks"
          and dept_view["work"]["items_considered"] == 1,
          json.dumps(dept_view["work"]))
    check("M2 shows last activity", bool(dept_view["work"]["last_activity_at_iso"]),
          str(dept_view["work"]["last_activity_at_iso"]))
    check("M2 shows the Kanban card status", task_view["status"] == "done")
    check("M2 shows the PASS evidence with run id",
          task_view["review"]["verdict"] == "PASS"
          and task_view["review"]["run_id"] == evidence.verdict.run_id,
          json.dumps(task_view["review"])[:240])
    check("M2 shows the manager acceptance", (task_view["acceptance"] or {}).get("manager") == "dept-manager",
          json.dumps(task_view["acceptance"]))
    check("M2 shows the final report delivered",
          (task_view["final_report"] or {}).get("state") == "confirmed",
          json.dumps(task_view["final_report"]))
    check("M2 department final-report rollup is delivered",
          dept_view["final_report"]["state"] == "delivered"
          and dept_view["final_report"]["count"] == 1,
          json.dumps(dept_view["final_report"]))
    check("M2 reports zero unknown tasks", dept_view["unknown_tasks"] == 0)
    kinds = [e["kind"] for e in detail["events"]]
    check("M2 durable event trail includes review and completion",
          "changes_requested" in kinds and "completed" in kinds, str(kinds))
    check("M2 source is Kanban", detail["source"] == "kanban")
    health = dash.board_health()
    check("M2 board probe is available", health.get("available") is True, json.dumps(health))

    print("\nEVIDENCE " + json.dumps({
        "scratch": str(SCRATCH),
        "root_task": root_id,
        "child_task": child_id,
        "revision": evidence.revision,
        "verdict_run_id": evidence.verdict.run_id,
        "artifact_sha256": produced,
        "artifact_path": str(artifact_path),
        "review_fail_run": failed.verdict.run_id,
        "delivery": delivered.action,
        "delivery_repeat": again.action,
        "root_comments": len(root_snap.comments),
        "report_schema": report_body.get("schema"),
        "report_identity": report_identity,
        "dashboard_work_state": dept_view["work"]["state"],
        "dashboard_final_report": dept_view["final_report"],
        "durable_event_kinds": kinds,
    }, sort_keys=True), flush=True)

    if failures:
        print(f"RESULT: FAIL ({len(failures)} checks)", flush=True)
        return 1
    print("RESULT: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Live isolated stage for the Department Hub research prototype.

Runs INSIDE the unshare sandbox started by scripts/sandbox.sh. Starts a REAL
Hermes gateway with three synthetic profiles (default, manager, reviewer) and a
deterministic synthetic model, then exercises:

  * manager-only work DAG subscriptions + detached default report;
  * real manager / reviewer / default profile wakes and real tool effects;
  * reject -> rework -> fresh independent PASS -> manager acceptance -> one final;
  * missing subscription, wrong owner, wrong report edge (fail closed);
  * a real board effect crash before receipt + restart reconciliation.

This is research evidence only. It is not a P0 PASS, not M1 authorization, and
the model is synthetic (no reasoning, no provider network).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from deptproto.board import CliBoard, JsonBoard
from deptproto.effects import BoardEffectPort, FileJournalPort
from deptproto.events import Event
from deptproto.orchestrator import Orchestrator
from deptproto.preflight import PreflightError, assert_lab_env, assert_no_network
from deptproto.store import Store

LAB = Path(os.environ["PROTO_LAB"])
PROTO = Path(os.environ["PROTO_ROOT"])
SRC = Path("~/.hermes/hermes-agent")
PY = str(SRC / "venv/bin/python")
CLI = str(PROTO / "scripts/hermes_cli.py")
E = LAB / "evidence"
E.mkdir(parents=True, exist_ok=True)
HOME = Path(os.environ["HERMES_HOME"])
MODEL_PORT = 18251
GATEWAY_PORT = 18252
PROCS: list[subprocess.Popen] = []
LEDGER: list[dict] = []


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def save(name: str, value) -> None:
    (E / name).write_text(json.dumps(
        value, indent=2, ensure_ascii=False, sort_keys=True,
        default=lambda o: sorted(o) if isinstance(o, set) else str(o)))


def check(name: str, ok: bool, detail=None) -> None:
    row = {"assertion": name, "pass": bool(ok), "detail": detail}
    LEDGER.append(row)
    save("assertions.json", LEDGER)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if not ok:
        raise AssertionError(name)


def preflight() -> dict:
    out = assert_lab_env(os.environ, LAB)
    out.update({"network": assert_no_network(),
                "mount_ns": os.readlink("/proc/self/ns/mnt"),
                "net_ns": os.readlink("/proc/self/ns/net"),
                "pid_ns": os.readlink("/proc/self/ns/pid"),
                "user_ns": os.readlink("/proc/self/ns/user"),
                "uid": os.getuid(), "pid": os.getpid()})
    return out


def cli(*args: str, expect: int = 0, timeout: int = 90) -> str:
    p = subprocess.run([PY, CLI, *args], cwd=LAB, env=os.environ, text=True,
                       capture_output=True, timeout=timeout)
    with (E / "cli.jsonl").open("a") as fh:
        fh.write(json.dumps({"argv": list(args), "rc": p.returncode,
                             "stdout": p.stdout, "stderr": p.stderr}) + "\n")
    if p.returncode != expect:
        raise RuntimeError(f"cli {args} rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    return p.stdout


def api(profile: str, text: str) -> dict:
    key = {"default": "synthetic-default-key",
           "manager": "synthetic-manager-key",
           "reviewer": "synthetic-reviewer-key"}[profile]
    path = "/v1/chat/completions" if profile == "default" else f"/p/{profile}/v1/chat/completions"
    req = urllib.request.Request(
        f"http://127.0.0.1:{GATEWAY_PORT}{path}",
        data=json.dumps({"model": "proto-synthetic",
                         "messages": [{"role": "user", "content": text}],
                         "stream": False}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            status, headers, body = resp.status, dict(resp.headers), json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status, headers, body = exc.code, dict(exc.headers), exc.read().decode()
    rec = {"profile": profile, "text": text, "status": status,
           "session": headers.get("X-Hermes-Session-Id"), "body": body,
           "synthetic_model": True}
    with (E / "http.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def wait_port(port: int, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if PROCS and PROCS[-1].poll() is not None:
            raise RuntimeError(f"process exited early rc={PROCS[-1].returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return
        except OSError:
            time.sleep(0.4)
    raise TimeoutError(f"port {port} did not open")


def create(title: str, assignee: str, parent: str | None = None) -> str:
    args = ["kanban", "create", title, "--assignee", assignee, "--json"]
    if parent:
        args += ["--parent", parent]
    obj = json.loads(cli(*args))
    return obj.get("id") or obj.get("task_id") or obj["task"]["id"]


def complete(task: str, result: str) -> None:
    cli("kanban", "complete", task, "--force", "--result", result, "--summary", result)


def subs(task: str) -> list[dict]:
    raw = cli("kanban", "notify-list", task, "--json") or "[]"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("subscriptions") or data.get("data") or []
    return list(data or [])


def ensure_sub(task: str, owner: str, session: str) -> bool:
    for row in subs(task):
        if row.get("notifier_profile") == owner and row.get("chat_id") == session:
            return False
    cli("kanban", "notify-subscribe", task, "--platform", "api_server",
        "--chat-id", session, "--notifier-profile", owner, "--delivery-mode", "wake")
    return True


def snapshot(task: str) -> dict:
    return json.loads(cli("kanban", "show", task, "--json"))


def comments(task: str) -> list[dict]:
    return snapshot(task).get("comments") or []


def wait_comment(task: str, author: str, needle: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for c in comments(task):
            if c.get("author") == author and needle in (c.get("body") or ""):
                return c
        time.sleep(1.0)
    raise TimeoutError(f"no {author} comment containing {needle!r} on {task}; "
                       f"comments={comments(task)}")


def read_sessions() -> dict:
    out = {}
    for label, db in [("default", HOME / "state.db"),
                      ("manager", HOME / "profiles/manager/state.db"),
                      ("reviewer", HOME / "profiles/reviewer/state.db")]:
        if not db.exists():
            out[label] = []
            continue
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            out[label] = [dict(r) for r in conn.execute(
                "SELECT id, profile_name, source FROM sessions")]
            msgs = [dict(r) for r in conn.execute(
                "SELECT id, role, CAST(substr(content,1,400) AS TEXT) AS content FROM messages "
                "ORDER BY id")]
            out[label + "_messages"] = msgs
        finally:
            conn.close()
    return out


def write_profile(path: Path, config: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(json.dumps(config, indent=2))
    (path / "SOUL.md").write_text("Synthetic Department Hub lab profile. "
                                  "Kanban tools only; no real inference.")
    (path / ".no-bundled-skills").touch()


def seed_proto(directory: Path, dept: str, work_task: str, report_task: str,
               artifact_text: str = "seeded artifact") -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    store = Store.open(directory / "dept.db")
    board = JsonBoard(directory / "board.json")
    board.add_task(report_task)
    board.add_task("t_setup")
    board.add_task(work_task, parents=["t_setup"])
    port = FileJournalPort(directory / "effects.jsonl")
    orch = Orchestrator(store, board=board, effect_port=port)
    orch.register_department(dept, "Seed", manager_profile="manager",
                             report_task_id=report_task)
    item = orch.register_work_item(dept, work_task, "work", "manager",
                                   parents=["t_setup"])
    art = directory / "artifact.txt"
    art.write_text(artifact_text)
    digest = hashlib.sha256(art.read_bytes()).hexdigest()
    evs = [
        Event("seed-1", dept, item.item_id, "artifact.published", 1,
              {"rev": 1, "sha256": digest, "path": str(art), "producer": "seed"}),
        Event("seed-2", dept, item.item_id, "review.requested", 2,
              {"reviewer_profile": "reviewer", "review_task_id": "t_rev1"}),
        Event("seed-3", dept, item.item_id, "review.verdict", 3,
              {"verdict": "PASS", "reviewer_profile": "reviewer",
               "review_task_id": "t_rev1", "artifact_sha": digest}),
        Event("seed-4", dept, item.item_id, "manager.acceptance", 4,
              {"accepted_by": "manager"}),
    ]
    for ev in evs:
        orch.ingest_event(ev)
    store.close()


def reset_state() -> None:
    """Fresh-portable run: wipe generated lab state, keep the console log."""
    for name in ("cli.jsonl", "http.jsonl", "model.jsonl", "assertions.json"):
        p = E / name
        if p.exists():
            p.unlink()
    for name in ("os-home", "board", "xdg", "runtime", "tmp"):
        shutil.rmtree(LAB / name, ignore_errors=True)
    for d in LAB.glob("proto-*"):
        shutil.rmtree(d, ignore_errors=True)
    for name in ("os-home", "xdg", "runtime", "tmp", "board", "evidence"):
        (LAB / name).mkdir(parents=True, exist_ok=True)


def run() -> None:
    reset_state()
    save("preflight.json", preflight())

    # Fail-before-start negatives: a production DB path or a real XDG runtime
    # path must be rejected before any child process exists.
    for key, bad in [("HERMES_KANBAN_DB", "~/.hermes/kanban.db"),
                     ("XDG_RUNTIME_DIR", "/run/user/1000"),
                     ("HERMES_KANBAN_TASK", "t_leaked")]:
        badenv = dict(os.environ)
        badenv[key] = bad
        try:
            assert_lab_env(badenv, LAB)
        except PreflightError as exc:
            check(f"fail-before-start rejects {key}", True, str(exc))
        else:
            check(f"fail-before-start rejects {key}", False)

    # Isolation facts.
    check("production profiles masked", not Path("~/.hermes/profiles").exists())
    check("production board masked", not Path("~/.hermes/kanban.db").exists())
    check("production runtime socket masked", not Path("/run/user/1000").exists())
    check("obsidian vault masked", not Path("~/ObsidianVault").exists())
    try:
        Path(SRC / ".write-probe").write_text("x")
        source_ro = False
    except OSError:
        source_ro = True
    check("installed source read-only", source_ro)
    try:
        Path("~/.local/share/uv/python/.write-probe").write_text("x")
        python_ro = False
    except OSError:
        python_ro = True
    check("python runtime read-only", python_ro)

    config = {
        "_config_version": 52,
        "model": {"default": "proto-synthetic", "provider": "custom",
                  "base_url": f"http://127.0.0.1:{MODEL_PORT}/v1",
                  "api_key": "synthetic-not-credential", "streaming": False,
                  "context_length": 200000, "max_tokens": 1000},
        "gateway": {"multiplex_profiles": True,
                    "api_server": {"enabled": True, "host": "127.0.0.1",
                                   "port": GATEWAY_PORT,
                                   "key": "synthetic-default-key"}},
        "kanban": {"dispatch_in_gateway": False, "notify_in_gateway": True,
                   "auto_subscribe_on_create": False, "auto_decompose": False,
                   "done_sub_retention_days": 30},
        "plugins": {"enabled": [], "disabled": []},
        "agent": {"max_iterations": 8},
        "display": {"streaming": False},
        "logging": {"level": "INFO"},
        "platform_toolsets": {"api_server": ["kanban"]},
    }
    write_profile(HOME, config)
    write_profile(HOME / "profiles/manager", config)
    write_profile(HOME / "profiles/reviewer", config)
    (HOME / "profiles/manager/.env").write_text("API_SERVER_KEY=synthetic-manager-key\n")
    (HOME / "profiles/reviewer/.env").write_text("API_SERVER_KEY=synthetic-reviewer-key\n")

    for script, log in [("model_server.py", "model-console.log"),
                        ("hermes_cli.py gateway run", "gateway-console.log")]:
        preflight()
        argv = ([PY, str(PROTO / "scripts/model_server.py")] if script.endswith(".py")
                else [PY, CLI, "gateway", "run"])
        handle = (E / log).open("w")
        proc = subprocess.Popen(argv, cwd=LAB, env=os.environ,
                                stdout=handle, stderr=subprocess.STDOUT)
        PROCS.append(proc)
    wait_port(MODEL_PORT, 60)
    wait_port(GATEWAY_PORT, 90)
    save("process-env.json", [
        {"pid": p.pid,
         "cmdline": Path(f"/proc/{p.pid}/cmdline").read_bytes().decode(errors="replace").split("\0"),
         "environment": Path(f"/proc/{p.pid}/environ").read_bytes().decode(errors="replace").split("\0")}
        for p in PROCS])

    d = api("default", "bootstrap-default")
    m = api("manager", "bootstrap-manager")
    r = api("reviewer", "bootstrap-reviewer")
    check("default session created", d["status"] == 200, d)
    check("manager profile session created", m["status"] == 200, m)
    check("reviewer profile session created", r["status"] == 200, r)
    sid_d, sid_m, sid_r = d["session"], m["session"], r["session"]
    check("three distinct profile sessions", len({sid_d, sid_m, sid_r}) == 3,
          {"default": sid_d, "manager": sid_m, "reviewer": sid_r})
    sessions = read_sessions()
    save("session-identities.json", {"sessions": sessions,
                                     "session_ids": {"default": sid_d,
                                                     "manager": sid_m,
                                                     "reviewer": sid_r}})
    profile_names = sorted({s["profile_name"] for s in sessions["manager"]})
    check("manager store is a real manager profile", profile_names == ["manager"],
          profile_names)
    # Wrong-owner credential check uses the default key against manager route.
    req = urllib.request.Request(
        f"http://127.0.0.1:{GATEWAY_PORT}/p/manager/v1/chat/completions",
        data=json.dumps({"model": "proto-synthetic", "messages": [{"role": "user", "content": "x"}], "stream": False}).encode(),
        headers={"Authorization": "Bearer synthetic-default-key",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            wrong_key_status = resp.status
    except urllib.error.HTTPError as exc:
        wrong_key_status = exc.code
    check("wrong profile key rejected", wrong_key_status == 401, wrong_key_status)

    cli("kanban", "init")
    cli("profile", "list")

    # ---------------- topology ----------------
    global sha1
    ART1 = "synthetic work artifact revision 1\n"
    sha1 = sha_text(ART1)
    t_setup = create("setup-dependency-completed-early", "manager")
    ensure_sub(t_setup, "manager", sid_m)
    t_root = create("department-root-not-a-parent", "manager")
    t_work = create("work-artifact", "manager", parent=t_setup)
    inherited = subs(t_work)
    ensure_sub(t_work, "manager", sid_m)
    t_rev1 = create(f"REVIEW REQUEST_CHANGES sha={sha1}", "reviewer",
                    parent=t_setup)
    ensure_sub(t_rev1, "reviewer", sid_r)
    t_report = create("detached-final-report", "default")
    ensure_sub(t_report, "default", sid_d)
    save("topology.json", {
        "setup": t_setup, "root_not_parent": t_root, "work": t_work,
        "review1": t_rev1, "report": t_report,
        "work_subs_after_create": inherited,
        "report_parents": snapshot(t_report).get("parents"),
        "report_children": snapshot(t_report).get("children"),
    })
    save("inheritance-observation.json", {
        "work_subs_after_create": inherited,
        "manager_inherited": any(s.get("notifier_profile") == "manager"
                                 and s.get("chat_id") == sid_m for s in inherited),
        "note": "create --parent inheritance re-observed; explicit ensure_sub is a no-op if present",
    })
    check("report is detached (no parents/children)",
          not snapshot(t_report).get("parents") and not snapshot(t_report).get("children"))

    store = Store.open(LAB / "proto-live" / "dept.db")
    cli_board = CliBoard(PY, CLI, LAB)
    port = BoardEffectPort(cli_board, t_report, author="manager")
    session_map = {sid_d: "default", sid_m: "manager", sid_r: "reviewer"}
    (LAB / "proto-live").mkdir(exist_ok=True)
    (LAB / "proto-live/session-profiles.json").write_text(json.dumps(session_map))
    orch = Orchestrator(store, board=cli_board, effect_port=port,
                        session_profiles=session_map)
    orch.register_department("dept-live", "Engineering", "manager", t_report,
                             manager_root_task_id=t_root)
    item = orch.register_work_item("dept-live", t_work, "work", "manager",
                                   parents=[t_setup])
    save("work-item.json", {"item_id": item.item_id, "board_task_id": t_work,
                            "parents": [t_setup]})

    def ingest(kind: str, seq: int, payload: dict, event_id: str) -> dict:
        res = orch.ingest_event(Event(event_id, "dept-live", item.item_id, kind, seq, payload))
        save(f"event-{event_id}.json", res.to_dict())
        return res.to_dict()

    # ---------------- round 1: reject ----------------
    art_dir = LAB / "proto-live/artifacts"
    art_dir.mkdir(exist_ok=True)
    a1 = art_dir / "work-rev1.txt"
    a1.write_text(ART1)
    assert hashlib.sha256(a1.read_bytes()).hexdigest() == sha1
    ingest("artifact.published", 1,
           {"rev": 1, "sha256": sha1, "path": str(a1), "producer": "synthetic-worker"},
           "live-1")
    ingest("review.requested", 2,
           {"reviewer_profile": "reviewer", "review_task_id": t_rev1}, "live-2")

    complete(t_setup, "SETUP DONE")
    complete(t_work, f"WORK DONE sha={sha1}")
    work_receipt = wait_comment(t_work, "manager", "SYNTHETIC_ROUTE_RECEIPT")
    check("manager wake executed a real tool effect on work child",
          work_receipt.get("author") == "manager", work_receipt)

    complete(t_rev1, f"REVIEW REQUEST_CHANGES sha={sha1}")
    v1_comment = wait_comment(t_rev1, "reviewer", '"prototype": "review-verdict"')
    v1 = json.loads(v1_comment["body"])
    check("reviewer profile wake emitted REQUEST_CHANGES for exact artifact",
          v1["verdict"] == "REQUEST_CHANGES" and v1["artifact_sha"] == sha1, v1)
    res = ingest("review.verdict", 3,
                 {"verdict": "REQUEST_CHANGES", "reviewer_profile": "reviewer",
                  "review_task_id": t_rev1, "artifact_sha": sha1}, "live-3")
    check("prototype accepted REQUEST_CHANGES", res["status"] == "applied", res)
    check("report has no prototype final yet",
          not any("[[deptproto-final:" in (c.get("body") or "")
                  for c in comments(t_report)), comments(t_report))

    # ---------------- rework: fresh PASS ----------------
    a2 = art_dir / "work-rev2.txt"
    a2.write_text("synthetic work artifact revision 2 after rework\n")
    sha2 = hashlib.sha256(a2.read_bytes()).hexdigest()
    ingest("artifact.published", 4,
           {"rev": 2, "sha256": sha2, "path": str(a2), "producer": "synthetic-worker"},
           "live-4")
    t_rev2 = create(f"REVIEW PASS sha={sha2}", "reviewer", parent=t_setup)
    ensure_sub(t_rev2, "reviewer", sid_r)
    ingest("review.requested", 5,
           {"reviewer_profile": "reviewer", "review_task_id": t_rev2}, "live-5")
    complete(t_rev2, f"REVIEW PASS sha={sha2}")
    v2_comment = wait_comment(t_rev2, "reviewer", '"prototype": "review-verdict"')
    v2 = json.loads(v2_comment["body"])
    check("fresh independent PASS references the CURRENT artifact",
          v2["verdict"] == "PASS" and v2["artifact_sha"] == sha2
          and sha2 != sha1, v2)
    res = ingest("review.verdict", 6,
                 {"verdict": "PASS", "reviewer_profile": "reviewer",
                  "review_task_id": t_rev2, "artifact_sha": sha2}, "live-6")
    check("prototype accepted fresh PASS", res["status"] == "applied", res)

    # ---------------- manager acceptance ----------------
    t_accept = create(f"ACCEPT PASS sha={sha2}", "manager", parent=t_setup)
    ensure_sub(t_accept, "manager", sid_m)
    complete(t_accept, f"ACCEPT PASS sha={sha2}")
    acc_comment = wait_comment(t_accept, "manager", '"prototype": "manager-acceptance"')
    acc = json.loads(acc_comment["body"])
    check("manager wake emitted acceptance for exact artifact",
          acc["decision"] == "ACCEPT" and acc["artifact_sha"] == sha2, acc)
    res = ingest("manager.acceptance", 7,
                 {"accepted_by": "manager", "artifact_sha": sha2}, "live-7")
    check("prototype accepted manager acceptance", res["status"] == "applied", res)

    # ---------------- single final ----------------
    out = orch.deliver_final("dept-live")
    save("delivery-main.json", out.to_dict())
    check("single final delivered", out.status == "confirmed", out.to_dict())
    finals = [c for c in comments(t_report) if "[[deptproto-final:" in (c.get("body") or "")]
    check("detached report has exactly one final comment", len(finals) == 1, finals)
    again = orch.deliver_final("dept-live")
    check("duplicate delivery attempt has no second effect",
          again.status == "already_delivered" and
          len([c for c in comments(t_report)
               if "[[deptproto-final:" in (c.get("body") or "")]) == 1, again.to_dict())

    # default report route: completing the report card wakes default once.
    complete(t_report, "FINAL DELIVERED")
    default_receipt = wait_comment(t_report, "default", "SYNTHETIC_ROUTE_RECEIPT")
    check("default receipt after single final report",
          default_receipt.get("author") == "default", default_receipt)
    save("final-report-comments.json", comments(t_report))

    # default must have no child wake in this observation window.
    sessions_after = read_sessions()
    save("session-messages-after.json", sessions_after)
    default_text = "\n".join((m.get("content") or "")
                             for m in sessions_after["default_messages"])
    child_wakes = {t: default_text.count(t) for t in [t_work, t_rev1, t_rev2, t_accept]}
    save("default-child-exposure.json", {
        "counts": child_wakes,
        "note": ("counts are text occurrences in default messages, not proof of zero "
                 "metadata exposure; kanban_show worker_context can carry child ids"),
    })
    work_dag_subs = {t: subs(t) for t in [t_work, t_rev1, t_rev2, t_accept]}
    check("default has no subscription row on any work-DAG card",
          not any(any(s.get("notifier_profile") == "default" for s in rows)
                  for rows in work_dag_subs.values()), work_dag_subs)

    # ---------------- negative: missing subscription ----------------
    t_report_neg = create("negative-report", "default")
    t_nosub = create("no-subscription-control", "manager")
    orch.register_department("dept-neg", "Negatives", "manager", t_report_neg)
    orch.register_work_item("dept-neg", t_nosub, "work", "manager",
                            parents=[t_setup])
    complete(t_nosub, "NOSUB DONE")
    time.sleep(6)
    check("missing-subscription task produced no wake/tool", comments(t_nosub) == [])
    neg = orch.ingest_board_event("dept-neg", {
        "external_event_id": "neg-nosub-1", "task_id": t_nosub,
        "kind": "completed", "subscription": None})
    check("missing subscription is an identifiable dead letter",
          neg.disposition == "dead_letter" and neg.reason == "missing_subscription"
          and bool(orch.store.alerts_for("dept-neg", kind="missing_subscription")),
          neg.to_dict())

    # ---------------- negative: wrong owner ----------------
    t_wrong = create("wrong-owner-control", "manager")
    cli("kanban", "notify-subscribe", t_wrong, "--platform", "api_server",
        "--chat-id", sid_d, "--notifier-profile", "manager", "--delivery-mode", "wake")
    orch.register_work_item("dept-neg", t_wrong, "work", "manager",
                            parents=[t_setup])
    complete(t_wrong, "WRONG OWNER DONE")
    time.sleep(8)
    check("wrong-owner subscription produced no wake/tool", comments(t_wrong) == [],
          {"subscriptions": subs(t_wrong), "comments": comments(t_wrong)})
    neg = orch.ingest_board_event("dept-neg", {
        "external_event_id": "neg-wrong-1", "task_id": t_wrong,
        "kind": "completed",
        "subscription": {"notifier_profile": "manager", "chat_id": sid_d}})
    check("wrong owner (manager owner, default session) is a dead letter",
          neg.disposition == "dead_letter" and neg.reason == "wrong_owner", neg.to_dict())

    # ---------------- negative: wrong report edge ----------------
    t_work2 = create("work-for-bad-report", "manager", parent=t_setup)
    t_badreport = create("bad-report-linked-to-work", "default")
    cli("kanban", "link", t_work2, t_badreport)
    seed_proto(LAB / "proto-bad", "dept-bad", t_work2, t_badreport,
               "seeded bad-report artifact")
    store2 = Store.open(LAB / "proto-bad/dept.db")
    orch2 = Orchestrator(store2, board=cli_board,
                         effect_port=BoardEffectPort(cli_board, t_badreport,
                                                     author="manager"),
                         session_profiles=session_map)
    out2 = orch2.deliver_final("dept-bad")
    bad_comments = comments(t_badreport)
    save("negative-wrong-report-edge.json", {"result": out2.to_dict(),
                                             "report_comments": bad_comments,
                                             "report_edges": snapshot(t_badreport)})
    check("wrong report edge is held fail-closed",
          out2.status == "held" and out2.reason == "wrong_report_edge", out2.to_dict())
    check("wrongly linked report received no final comment", bad_comments == [],
          bad_comments)
    store2.close()

    # ---------------- live crash at effect/receipt boundary ----------------
    t_report_crash = create("crash-report", "default")
    seed_proto(LAB / "proto-crash", "dept-crash", t_work, t_report_crash,
               "seeded crash artifact")
    (LAB / "proto-crash/session-profiles.json").write_text(json.dumps(session_map))
    crash_cmd = [PY, str(PROTO / "scripts/live_deliver.py"), "--dir",
                 str(LAB / "proto-crash"), "--dept", "dept-crash",
                 "--report", t_report_crash,
                 "--crash-at", "after_effect_before_receipt"]
    p = subprocess.run(crash_cmd, cwd=LAB, env=os.environ, text=True,
                       capture_output=True, timeout=120)
    save("crash-run.json", {"rc": p.returncode, "stdout": p.stdout,
                            "stderr": p.stderr})
    check("live delivery crashed at effect/receipt boundary", p.returncode == 137,
          p.returncode)
    after_crash = [c for c in comments(t_report_crash)
                   if "[[deptproto-final:" in (c.get("body") or "")]
    check("real board effect happened once before crash", len(after_crash) == 1,
          after_crash)
    p2 = subprocess.run(crash_cmd[:-2] + ["--recover"], cwd=LAB, env=os.environ,
                        text=True, capture_output=True, timeout=120)
    save("crash-recovery.json", {"rc": p2.returncode, "stdout": p2.stdout,
                                 "stderr": p2.stderr})
    check("restart recovery confirmed the existing effect", p2.returncode == 0
          and json.loads(p2.stdout)["status"] == "confirmed", p2.stdout)
    after_recovery = [c for c in comments(t_report_crash)
                      if "[[deptproto-final:" in (c.get("body") or "")]
    check("recovery did not duplicate the real board effect",
          len(after_recovery) == 1, after_recovery)
    conn = sqlite3.connect(f"file:{LAB / 'proto-crash/dept.db'}?mode=ro", uri=True)
    try:
        receipts = conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE operation_key LIKE 'final:%'").fetchone()[0]
        status = conn.execute("SELECT status FROM deliveries").fetchone()[0]
    finally:
        conn.close()
    check("crash recovery has one receipt and confirmed status",
          receipts == 1 and status == "confirmed", {"receipts": receipts,
                                                    "status": status})

    # ---------------- summary ----------------
    save("result.json", {
        "investigation": "completed",
        "scope": ("isolated research prototype; synthetic model; real gateway/"
                  "profiles/tools; no P0 PASS; no M1 authorization"),
        "synthetic_model": True,
        "assertions": LEDGER,
        "ids": {"default_session": sid_d, "manager_session": sid_m,
                "reviewer_session": sid_r, "setup": t_setup, "root": t_root,
                "work": t_work, "review1": t_rev1, "review2": t_rev2,
                "accept": t_accept, "report": t_report,
                "nosub": t_nosub, "wrong_owner": t_wrong,
                "bad_report": t_badreport, "crash_report": t_report_crash},
        "final_comments": finals,
        "default_child_exposure": child_wakes,
        "disclaimer": ("trusted single writer is not an unbypassable ACL; shared "
                       "board metadata is not confidential; unknown external effects "
                       "are surfaced, never claimed as exactly-once"),
    })


if __name__ == "__main__":
    try:
        run()
    except BaseException as exc:
        save("error.json", {"error": repr(exc), "traceback": traceback.format_exc(),
                            "assertions": LEDGER})
        print(traceback.format_exc(), flush=True)
        raise
    finally:
        for proc in reversed(PROCS):
            if proc.poll() is None:
                proc.terminate()
        for proc in reversed(PROCS):
            try:
                proc.wait(timeout=25)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        listeners = ""
        try:
            listeners = subprocess.check_output(["ss", "-lntup"], text=True)
        except Exception as exc:
            listeners = f"ss unavailable: {exc}"
        save("cleanup.json", {
            "processes": [{"pid": p.pid, "returncode": p.returncode,
                           "reaped": p.poll() is not None} for p in PROCS],
            "listeners": listeners,
        })

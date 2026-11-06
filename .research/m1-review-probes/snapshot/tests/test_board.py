"""Unit tests for the public Kanban CLI adapter and its strict board reader.

The adapter may only use documented CLI surfaces (`hermes kanban show/list/
notify-list/notify-subscribe`). It must fail closed: a missing or malformed
field is UNKNOWN, never "empty/detached".
"""
from __future__ import annotations

import json

import pytest

from department_lib.board import Board, BoardError, BoardTask
from department_lib.kanban_cli import CliKanban, CommandResult


class FakeRunner:
    """Records argv and replays queued CommandResults."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, args, env=None, timeout=None):
        self.calls.append(list(args))
        if not self.results:
            raise AssertionError(f"unexpected CLI call: {args}")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(payload) -> CommandResult:
    return CommandResult(rc=0, stdout=json.dumps(payload), stderr="")


def raw(text: str, rc: int = 0) -> CommandResult:
    return CommandResult(rc=rc, stdout=text, stderr="boom" if rc else "")


def show_payload(**over) -> dict:
    base = {
        "task": {"id": "t_w1", "title": "work", "status": "review", "assignee": "worker-a",
                 "created_at": 100, "completed_at": None},
        "latest_summary": "handoff",
        "parents": ["t_root"],
        "children": [],
        "comments": [{"author": "worker-a", "body": "done", "created_at": 110}],
        "events": [{"kind": "review_requested", "payload": {"reviewer": "reviewer-a"},
                    "created_at": 120, "run_id": 5}],
        "runs": [{"id": 5, "profile": "worker-a", "status": "done",
                  "outcome": "review_requested", "summary": "ready for review",
                  "metadata": None, "started_at": 100, "ended_at": 120}],
    }
    base.update(over)
    return base


# ── CliKanban ───────────────────────────────────────────────────────────────

def test_show_builds_documented_argv_and_returns_json():
    runner = FakeRunner([ok(show_payload())])
    cli = CliKanban(runner=runner)
    data = cli.show("t_w1")
    assert data["task"]["id"] == "t_w1"
    assert runner.calls[0][-4:] == ["kanban", "show", "t_w1", "--json"]


def test_non_zero_exit_is_unavailable_with_stderr():
    cli = CliKanban(runner=FakeRunner([raw("", rc=2)]))
    with pytest.raises(BoardError) as exc:
        cli.show("t_w1")
    assert exc.value.kind == "unavailable"
    assert "boom" in str(exc.value)


def test_non_json_stdout_is_invalid():
    cli = CliKanban(runner=FakeRunner([raw("not json at all")]))
    with pytest.raises(BoardError) as exc:
        cli.show("t_w1")
    assert exc.value.kind == "invalid"


def test_truncated_json_is_invalid():
    cli = CliKanban(runner=FakeRunner([raw('{"task": {"id": "t_w1"')]))
    with pytest.raises(BoardError) as exc:
        cli.show("t_w1")
    assert exc.value.kind == "invalid"


def test_notify_list_parses_rows_and_requires_a_list():
    runner = FakeRunner([
        ok([{"task_id": "t_w1", "platform": "api_server", "chat_id": "api-mgr",
             "thread_id": "", "notifier_profile": "dept-manager", "delivery_mode": "wake"}]),
        ok({"not": "a list"}),
    ])
    cli = CliKanban(runner=runner)
    rows = cli.notify_list("t_w1")
    assert rows[0]["chat_id"] == "api-mgr"
    with pytest.raises(BoardError) as exc:
        cli.notify_list("t_w1")
    assert exc.value.kind == "invalid"


def test_subscribe_and_unsubscribe_argv():
    runner = FakeRunner([CommandResult(0, "Subscribed", ""), CommandResult(0, "", "")])
    cli = CliKanban(runner=runner)
    cli.notify_subscribe("t_w1", platform="api_server", chat_id="api-mgr",
                         delivery_mode="wake", thread_id="th-1")
    arg = runner.calls[0]
    i = arg.index("kanban")
    assert arg[i:i + 3] == ["kanban", "notify-subscribe", "t_w1"]
    assert arg[arg.index("--platform") + 1] == "api_server"
    assert arg[arg.index("--chat-id") + 1] == "api-mgr"
    assert arg[arg.index("--delivery-mode") + 1] == "wake"
    assert arg[arg.index("--thread-id") + 1] == "th-1"
    cli.notify_unsubscribe("t_w1", platform="api_server", chat_id="api-mgr", thread_id="th-1")
    second = runner.calls[1]
    j = second.index("kanban")
    assert second[j:j + 3] == ["kanban", "notify-unsubscribe", "t_w1"]


def test_subscribe_failure_raises_unavailable():
    cli = CliKanban(runner=FakeRunner([raw("", rc=1)]))
    with pytest.raises(BoardError) as exc:
        cli.notify_subscribe("t_w1", platform="api_server", chat_id="api-mgr")
    assert exc.value.kind == "unavailable"


def test_runner_exception_is_unavailable_not_a_crash():
    cli = CliKanban(runner=FakeRunner([RuntimeError("spawn failed")]))
    with pytest.raises(BoardError) as exc:
        cli.show("t_w1")
    assert exc.value.kind == "unavailable"


# ── Board: strict parsing ───────────────────────────────────────────────────

def test_board_parses_task_and_events():
    board = Board(CliKanban(runner=FakeRunner([ok(show_payload())])))
    task = board.get("t_w1")
    assert isinstance(task, BoardTask)
    assert task.task_id == "t_w1"
    assert task.status == "review"
    assert task.assignee == "worker-a"
    assert task.parents == ("t_root",)
    assert task.children == ()
    assert task.events[0].kind == "review_requested"
    assert task.events[0].run_id == 5
    assert task.runs[0].profile == "worker-a"
    assert task.runs[0].outcome == "review_requested"


@pytest.mark.parametrize("field", ["parents", "children", "comments", "events", "runs"])
def test_missing_topology_field_is_invalid_not_empty(field):
    payload = show_payload()
    payload.pop(field)
    board = Board(CliKanban(runner=FakeRunner([ok(payload)])))
    with pytest.raises(BoardError) as exc:
        board.get("t_w1")
    assert exc.value.kind == "invalid"
    assert field in str(exc.value)


@pytest.mark.parametrize("field, bad", [
    ("parents", None), ("parents", "t_root"), ("parents", [1]), ("parents", ["", "t"]),
    ("children", {}), ("children", [None]), ("comments", None), ("comments", "x"),
    ("events", None), ("events", [{"kind": "completed"}]), ("runs", None),
    ("runs", [{"id": "five"}]),
])
def test_bad_topology_field_is_invalid(field, bad):
    payload = show_payload()
    payload[field] = bad
    board = Board(CliKanban(runner=FakeRunner([ok(payload)])))
    with pytest.raises(BoardError):
        board.get("t_w1")


def test_missing_task_object_is_invalid():
    payload = show_payload()
    payload.pop("task")
    board = Board(CliKanban(runner=FakeRunner([ok(payload)])))
    with pytest.raises(BoardError) as exc:
        board.get("t_w1")
    assert exc.value.kind == "invalid"


def test_task_id_mismatch_is_invalid():
    payload = show_payload()
    payload["task"]["id"] = "t_other"
    board = Board(CliKanban(runner=FakeRunner([ok(payload)])))
    with pytest.raises(BoardError) as exc:
        board.get("t_w1")
    assert exc.value.kind == "invalid"


def test_unknown_task_reports_not_found():
    payload = {"error": "task not found"}
    board = Board(CliKanban(runner=FakeRunner([ok(payload)])))
    with pytest.raises(BoardError) as exc:
        board.get("t_nope")
    assert exc.value.kind in ("not_found", "invalid")


# ── Board: ancestry ─────────────────────────────────────────────────────────

def test_ancestry_walks_parents_to_the_root():
    board = Board(CliKanban(runner=FakeRunner([
        ok(show_payload()),
        ok(show_payload(task={"id": "t_root", "title": "root", "status": "running",
                              "assignee": "worker-a", "created_at": 1, "completed_at": None},
                        parents=[])),
    ])))
    assert board.ancestry("t_w1") == ("t_w1", "t_root")


def test_ancestry_raises_when_a_parent_cannot_be_read():
    board = Board(CliKanban(runner=FakeRunner([
        ok(show_payload()),
        BoardError("unavailable", "CLI down"),
    ])))
    with pytest.raises(BoardError):
        board.ancestry("t_w1")


def test_ancestry_detects_cycles_and_depth_overflow():
    # a card that (incorrectly) lists itself as parent
    cyc = show_payload(parents=["t_w1"])
    board = Board(CliKanban(runner=FakeRunner([ok(cyc)])))
    with pytest.raises(BoardError) as exc:
        board.ancestry("t_w1")
    assert "cycle" in str(exc.value).lower()

    # a chain deeper than the limit
    chain = []
    for node, parent in (("t_w1", "t_a"), ("t_a", "t_b"), ("t_b", "t_c")):
        chain.append(ok(show_payload(
            task={"id": node, "title": node, "status": "running", "assignee": "worker-a",
                  "created_at": 1, "completed_at": None},
            parents=[parent])))
    board = Board(CliKanban(runner=FakeRunner(chain)))
    with pytest.raises(BoardError) as exc:
        board.ancestry("t_w1", limit=2)
    assert "depth" in str(exc.value).lower()


def test_is_descendant_of_root():
    board = Board(CliKanban(runner=FakeRunner([
        ok(show_payload()),
        ok(show_payload(task={"id": "t_root", "title": "root", "status": "running",
                              "assignee": "worker-a", "created_at": 1, "completed_at": None},
                        parents=[])),
    ])))
    assert board.is_descendant("t_w1", "t_root") is True


def test_is_descendant_is_false_for_unrelated_task():
    board = Board(CliKanban(runner=FakeRunner([
        ok(show_payload(parents=[])),
    ])))
    assert board.is_descendant("t_w1", "t_root") is False

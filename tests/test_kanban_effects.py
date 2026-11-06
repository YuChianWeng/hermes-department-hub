"""Tests for the comment effect port (bounded read-back, fail-closed)."""
from __future__ import annotations

import pytest

from department_lib.kanban_cli import BoardError
from department_lib.kanban_effects import CommentEffectPort, marker


class FakeCli:
    def __init__(self, cards=None):
        self.cards = {k: list(v) for k, v in (cards or {}).items()}
        self.comments: list[tuple[str, str, str | None]] = []
        self.fail_show = False

    def comment(self, task_id, body, *, author=None):
        if task_id not in self.cards:
            raise BoardError("not_found", task_id)
        self.cards[task_id].append({"body": body, "author": author})
        self.comments.append((task_id, body, author))

    def show(self, task_id):
        if self.fail_show:
            raise BoardError("unavailable", "board down")
        if task_id not in self.cards:
            raise BoardError("not_found", task_id)
        return {"comments": self.cards[task_id]}


FINAL = {"schema": "department-hub/final/v1", "task_id": "t_w1",
         "root_task_id": "t_root", "status": "accepted"}


def test_final_is_appended_to_the_root_card_and_found_by_readback():
    cli = FakeCli({"t_root": [], "t_w1": []})
    port = CommentEffectPort(cli, ["t_root", "t_w1"])
    port.append("k1", FINAL)
    assert [c[0] for c in cli.comments] == ["t_root"]
    assert marker("final", "k1") in cli.comments[0][1]
    assert cli.comments[0][2] == "department-hub"
    assert port.find("k1") == "found"


def test_falls_back_to_the_work_card_without_a_root():
    cli = FakeCli({"t_w1": []})
    port = CommentEffectPort(cli, ["t_w1"])
    body = dict(FINAL)
    body.pop("root_task_id")
    port.append("k1", body)
    assert cli.comments[0][0] == "t_w1"
    assert port.find("k1") == "found"


def test_complete_read_without_marker_is_absent():
    cli = FakeCli({"t_root": [{"body": "unrelated"}], "t_w1": []})
    port = CommentEffectPort(cli, ["t_root", "t_w1"])
    assert port.find("missing") == "absent"


def test_empty_candidate_set_is_unknown_not_absent():
    port = CommentEffectPort(FakeCli({"t_root": []}), [])
    assert port.find("k1") == "unknown"


def test_any_failed_read_is_unknown_not_absent():
    cli = FakeCli({"t_root": [], "t_w1": []})
    cli.fail_show = True
    port = CommentEffectPort(cli, ["t_root"])
    assert port.find("k1") == "unknown"


def test_incomplete_read_is_unknown():
    class Partial:
        def show(self, task_id):
            return {"task": {"id": task_id}}   # no comments key

        def comment(self, *a, **k):
            raise AssertionError("not used")

    port = CommentEffectPort(Partial(), ["t_root"])
    assert port.find("k1") == "unknown"


def test_append_requires_a_task_target():
    port = CommentEffectPort(FakeCli({}), ["t_root"])
    with pytest.raises(ValueError):
        port.append("k1", {"schema": "department-hub/final/v1"})


def test_escalation_marker_roundtrips_on_root():
    cli = FakeCli({"t_root": []})
    port = CommentEffectPort(cli, ["t_root"])
    port.append("esc1", {"schema": "department-hub/escalation/v1",
                         "task_id": "t_w1", "root_task_id": "t_root"})
    assert marker("escalation", "esc1") in cli.comments[0][1]
    assert port.find("esc1") == "found"


def test_port_does_not_advertise_the_removed_upstream_fence():
    """P2-1: v5 finals never reach this port, so the v3 fence flag is gone."""
    assert not hasattr(CommentEffectPort, "requires_upstream_fence")

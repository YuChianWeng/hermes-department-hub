"""Unit tests for manager wake routing (subscription planning + read-back)."""
from __future__ import annotations

import json

import pytest

from department_lib.kanban_cli import CliKanban, CommandResult
from department_lib.models import Department
from department_lib.routing import Router, RoutingIssue

from tests.test_board import FakeRunner, ok


def _dept(**over) -> Department:
    raw = {
        "id": "dev",
        "name": "Development",
        "manager": {"profile": "dept-manager", "platform": "api_server",
                    "chat_id": "api-dev-mgr"},
        "members": ["worker-a"],
        "reviewers": ["reviewer-a"],
    }
    raw.update(over)
    return Department.from_mapping(raw)


def _row(task="t_w1", platform="api_server", chat="api-dev-mgr", mode="wake",
         profile="dept-manager"):
    return {"task_id": task, "platform": platform, "chat_id": chat, "thread_id": "",
            "user_id": None, "user_id_alt": None, "chat_type": "dm",
            "notifier_profile": profile, "delivery_mode": mode,
            "delivery_metadata": {}, "created_at": 1, "last_event_id": 1,
            "last_ping_event_id": 0}


def _router(*results):
    return Router(CliKanban(runner=FakeRunner(list(results))))


def test_ensure_subscribes_missing_manager_wake_and_verifies_by_readback():
    router = _router(ok([]), CommandResult(0, "Subscribed", ""), ok([_row()]))
    report = router.ensure(_dept(), ["t_w1"])
    assert report.ok is True
    assert report.ensured == ("t_w1",)
    assert report.issues == ()
    calls = router.cli._runner.calls
    assert any("notify-subscribe" in c for c in calls)
    assert sum(1 for c in calls if "notify-list" in c) == 2  # before + read-back


def test_existing_correct_subscription_is_left_alone():
    router = _router(ok([_row()]))
    report = router.ensure(_dept(), ["t_w1"])
    assert report.ok is True
    assert report.ensured == ()
    assert not any("notify-subscribe" in c for c in router.cli._runner.calls)


def test_existing_subscription_with_wrong_delivery_mode_is_reported_not_silently_fixed():
    router = _router(ok([_row(mode="notify")]))
    report = router.ensure(_dept(), ["t_w1"])
    assert report.ok is False
    assert [i.code for i in report.issues] == ["mode_mismatch"]
    assert not any("notify-subscribe" in c for c in router.cli._runner.calls)


def test_wrong_delivery_mode_is_repaired_only_when_explicitly_requested():
    router = _router(ok([_row(mode="notify")]), CommandResult(0, "", ""),
                     CommandResult(0, "", ""), ok([_row()]))
    report = router.ensure(_dept(), ["t_w1"], repair=True)
    assert report.ok is True
    assert report.ensured == ("t_w1",)
    assert any("notify-unsubscribe" in c for c in router.cli._runner.calls)


def test_foreign_subscription_is_surfaced_and_never_silently_removed():
    router = _router(ok([_row(), _row(chat="api-default-user", profile="default")]))
    report = router.ensure(_dept(), ["t_w1"])
    assert report.ok is False
    issue = report.issues[0]
    assert isinstance(issue, RoutingIssue)
    assert issue.code == "foreign_subscription"
    assert "api-default-user" in issue.detail
    assert not any("notify-unsubscribe" in c for c in router.cli._runner.calls)


def test_subscribe_without_visible_readback_is_failed_closed():
    router = _router(ok([]), CommandResult(0, "Subscribed", ""), ok([]))
    report = router.ensure(_dept(), ["t_w1"])
    assert report.ok is False
    assert [i.code for i in report.issues] == ["subscribe_unverified"]


def test_read_error_is_reported_as_unknown_not_ok():
    router = _router(CommandResult(2, "", "board locked"))
    report = router.ensure(_dept(), ["t_w1"])
    assert report.ok is False
    assert report.issues[0].code == "board_unknown"
    assert "board locked" in report.issues[0].detail


def test_verify_makes_no_writes():
    router = _router(ok([_row()]))
    report = router.verify(_dept(), ["t_w1"])
    assert report.ok is True
    assert not any("notify-" in c and "list" not in c for c in router.cli._runner.calls)


def test_cross_department_task_is_refused():
    router = _router()
    report = router.ensure(_dept(), ["t_ops_1"], task_departments={"t_ops_1": "ops"})
    assert report.ok is False
    assert report.issues[0].code == "cross_department"
    assert router.cli._runner.calls == []


def test_ensure_is_idempotent_across_two_passes():
    runner = FakeRunner([ok([]), CommandResult(0, "", ""), ok([_row()]), ok([_row()])])
    router = Router(CliKanban(runner=runner))
    first = router.ensure(_dept(), ["t_w1"])
    second = router.ensure(_dept(), ["t_w1"])
    assert first.ensured == ("t_w1",)
    assert second.ensured == ()
    assert second.ok is True

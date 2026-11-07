"""FINDING-5 regression: ``department_bind`` is manager-only and fails closed.

The gate must be decided from the *configured* department (a card is not bound
yet when it is being bound) and it must fire before any board/store write: no
``notify_subscribe`` (the ``kanban_notify_subs`` writer), no membership/binding/
event row.  The refusal cases point at a root card that the board already
assigns to the department manager, so the identity gate is the only thing that
can stop the bind.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from department_lib.board import Board
from department_lib.delivery import Delivery
from department_lib.models import Department
from department_lib.orchestrator import DepartmentService
from department_lib.routing import Router
from department_lib.store import Store
from tests.plugin_loader import load_plugin
from tests.test_m1_v2_regressions import CLI, Port, payload
from tests.test_plugin_registration import FakeCtx

MANAGER = "manager"
OTHER_MANAGER = "manager-ops"


def _dept(dept_id: str = "dev", manager: str = MANAGER, chat: str = "session") -> Department:
    return Department.from_mapping({
        "id": dept_id, "name": dept_id.title(),
        "manager": {"profile": manager, "platform": "api_server", "chat_id": chat},
        "members": ["worker"], "reviewers": ["reviewer"],
    })


class _Stack:
    def __init__(self, tmp_path):
        self.store = Store(tmp_path / "state.db")
        self.store.register_department(_dept())
        self.store.register_department(_dept("ops", OTHER_MANAGER, "ops-session"))
        self.cli = CLI({"root": payload("root", assignee=MANAGER),
                        "root-ops": payload("root-ops", assignee=OTHER_MANAGER)})
        self.port = Port()
        self.service = DepartmentService(
            self.store, Board(self.cli), Delivery(self.store, self.port), Router(self.cli))

    def dump(self) -> str:
        """Full SQL dump of the plugin store: any write changes this string."""
        return "\n".join(self.store.connection.iterdump())

    def subscriptions(self):
        return deepcopy(self.cli.subscriptions), list(self.cli.calls)


@pytest.fixture
def stack(tmp_path):
    harness = _Stack(tmp_path)
    yield harness
    harness.store.close()


def _call(monkeypatch, plugin, harness: _Stack, caller: str, params: dict) -> dict:
    monkeypatch.setattr(plugin, "build_service", lambda: harness.service)
    ctx = FakeCtx(profile_name=caller)
    plugin.register(ctx)
    return json.loads(ctx.tools["department_bind"]["handler"](params))


def test_manager_bind_still_works(monkeypatch, stack):
    plugin = load_plugin()
    payload_ = _call(monkeypatch, plugin, stack, MANAGER,
                     {"department": "dev", "root_task_id": "root"})
    # Positive control: the identity gate does not disable the manager path.
    # (`routing_ok` stays False on purpose: the public notify-list cannot attest
    # runtime session ownership, so `session_owner_unverified` is always reported.)
    assert payload_["department"] == "dev", payload_
    assert payload_["ensured"] == ["root"], payload_
    assert [issue["code"] for issue in payload_["issues"]] == ["session_owner_unverified"], payload_
    assert stack.store.dept_for_task("root") == "dev"
    assert stack.cli.calls, "manager bind must still ensure the wake subscription"


@pytest.mark.parametrize("caller", ["worker", "reviewer", OTHER_MANAGER])
def test_non_manager_caller_is_refused_before_any_write(monkeypatch, stack, caller):
    plugin = load_plugin()
    before_db = stack.dump()
    before_subs = stack.subscriptions()

    payload_ = _call(monkeypatch, plugin, stack, caller,
                     {"department": "dev", "root_task_id": "root"})

    assert payload_["success"] is False, payload_
    assert payload_["error"] == f"caller profile '{caller}' is not the manager of department 'dev'"
    assert stack.dump() == before_db, "refused bind wrote to the plugin store"
    assert stack.subscriptions() == before_subs, "refused bind wrote kanban_notify_subs"
    assert stack.store.dept_for_task("root") is None


def test_empty_caller_fails_closed_before_any_write(monkeypatch, stack):
    plugin = load_plugin()
    before_db = stack.dump()
    before_subs = stack.subscriptions()

    payload_ = _call(monkeypatch, plugin, stack, "",
                     {"department": "dev", "root_task_id": "root"})

    assert payload_["success"] is False, payload_
    assert payload_["error"] == "caller profile unavailable; manager binding cannot be verified"
    assert stack.dump() == before_db, "fail-closed bind wrote to the plugin store"
    assert stack.subscriptions() == before_subs, "fail-closed bind wrote kanban_notify_subs"
    assert stack.store.dept_for_task("root") is None


def test_unknown_department_error_is_unchanged(monkeypatch, stack):
    plugin = load_plugin()
    before_db = stack.dump()
    before_subs = stack.subscriptions()

    payload_ = _call(monkeypatch, plugin, stack, MANAGER,
                     {"department": "nope", "root_task_id": "root"})

    assert payload_ == {"success": False, "error": "unknown department 'nope'"}, payload_
    assert stack.dump() == before_db
    assert stack.subscriptions() == before_subs


def test_gate_uses_the_configured_department_not_a_task_binding(monkeypatch, stack):
    """A task bound to one department cannot be bound by another department's manager."""
    plugin = load_plugin()
    first = _call(monkeypatch, plugin, stack, MANAGER,
                  {"department": "dev", "root_task_id": "root"})
    assert first["ensured"] == ["root"], first
    before_db = stack.dump()

    payload_ = _call(monkeypatch, plugin, stack, OTHER_MANAGER,
                     {"department": "dev", "root_task_id": "root-ops"})

    assert payload_["success"] is False
    assert payload_["error"] == (f"caller profile '{OTHER_MANAGER}' is not the manager "
                                 "of department 'dev'")
    assert stack.dump() == before_db

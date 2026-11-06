"""Plugin registration contract: no I/O at discovery, valid schemas, safe handlers."""
from __future__ import annotations

import json

import pytest

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "department_hub_plugin", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    def __init__(self, profile_name="default"):
        self.tools = {}
        self.hooks = {}
        self.profile_name = profile_name

    def register_tool(self, *, name, toolset, schema, handler):
        assert toolset and schema["name"] == name and callable(handler)
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)


def test_register_does_no_io_and_registers_declared_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin = _load_plugin()
    ctx = FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == set(plugin.TOOL_NAMES)
    # discovery must not create state
    assert not (tmp_path / "department-hub").exists()
    assert all(t["toolset"] == "department_hub" for t in ctx.tools.values())


def test_every_schema_is_json_serialisable_and_has_required_fields():
    plugin = _load_plugin()
    for name in plugin.TOOL_NAMES:
        schema = plugin._SCHEMAS[name]
        json.dumps(schema)
        assert schema["parameters"]["required"]
        assert schema["description"]


def test_handlers_return_json_error_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin = _load_plugin()
    handler = plugin._make_handler("department_ingest")
    # no departments configured -> unbound task error, but never an exception
    payload = json.loads(handler({"task_id": "t_nope"}))
    assert payload["success"] is False
    assert "error" in payload


def test_register_hooks_are_observer_only_callbacks():
    plugin = _load_plugin()
    ctx = FakeCtx()
    plugin.register(ctx)
    for hook in ("kanban_task_completed", "kanban_task_blocked"):
        assert hook in ctx.hooks
        assert all(callable(cb) for cb in ctx.hooks[hook])


def test_plugin_manifest_declares_the_registered_surface():
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    declared = set()
    in_tools = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("provides_tools:"):
            in_tools = True
            continue
        if in_tools:
            if stripped.startswith("- "):
                declared.add(stripped[2:].strip())
            elif stripped and not line.startswith(" "):
                in_tools = False
    plugin = _load_plugin()
    assert declared == set(plugin.TOOL_NAMES)
    assert "name: department-hub" in text


class _Gate:
    status = "releasable"
    reason = ""
    revision = 1
    verdict_run_id = 6
    artifact_sha256 = "d" * 64
    reviewer_profile = "reviewer-a"


class _Service:
    def __init__(self, store):
        self.store = store
        self.claims = []

    def accept(self, claim):
        self.claims.append(claim)
        return _Gate()


def _bound_store(tmp_path):
    from department_lib.models import Department
    from department_lib.store import Store
    store = Store(tmp_path / "plugin.db")
    store.register_department(Department.from_mapping({
        "id": "dev", "name": "Development",
        "manager": {"profile": "dept-manager", "platform": "api_server", "chat_id": "api-dev"},
        "members": ["worker-a"], "reviewers": ["reviewer-a"],
    }))
    store.bind_task("dev", "t_w1", role="work")
    return store


def test_accept_rejects_a_non_manager_caller_profile(tmp_path, monkeypatch):
    plugin = _load_plugin()
    fake = _Service(_bound_store(tmp_path))
    monkeypatch.setattr(plugin, "build_service", lambda: fake)
    ctx = FakeCtx(profile_name="worker-a")
    plugin.register(ctx)
    payload = json.loads(ctx.tools["department_accept"]["handler"]({
        "task_id": "t_w1", "revision": 1, "verdict_run_id": 6,
        "artifact_sha256": "d" * 64}))
    assert payload["success"] is False
    assert "not the manager" in payload["error"]
    assert fake.claims == []


def test_accept_binds_the_registered_manager_profile(tmp_path, monkeypatch):
    plugin = _load_plugin()
    fake = _Service(_bound_store(tmp_path))
    monkeypatch.setattr(plugin, "build_service", lambda: fake)
    ctx = FakeCtx(profile_name="dept-manager")
    plugin.register(ctx)
    payload = json.loads(ctx.tools["department_accept"]["handler"]({
        "task_id": "t_w1", "revision": 1, "verdict_run_id": 6,
        "artifact_sha256": "d" * 64}))
    assert payload["success"] is True
    assert fake.claims[0].manager_profile == "dept-manager"


def test_manager_ops_fail_closed_when_caller_profile_is_unknown(tmp_path, monkeypatch):
    plugin = _load_plugin()
    fake = _Service(_bound_store(tmp_path))
    monkeypatch.setattr(plugin, "build_service", lambda: fake)
    ctx = FakeCtx(profile_name="")
    plugin.register(ctx)
    payload = json.loads(ctx.tools["department_deliver"]["handler"]({
        "task_id": "t_w1", "revision": 1, "verdict_run_id": 6,
        "artifact_sha256": "d" * 64}))
    assert payload["success"] is False
    assert "caller profile unavailable" in payload["error"]

"""FINDING-6 regression: the tool half must work *as installed*.

Evidence level: real Hermes plugin loader (``hermes_cli.plugins.PluginManager``),
fresh CPython process, scratch ``HERMES_HOME``, the real plugin code — and
deliberately **no** plugin root (or its parent) on ``sys.path``, because that is
exactly the accommodation the defect needed.  ``tests/conftest.py`` puts the
plugin root on ``sys.path`` for the in-process unit tests, so a same-process test
can never prove this; the probe must be a separate interpreter.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "loader_probe.py"
AGENT_ROOT = Path(os.path.expanduser("~/.hermes/hermes-agent"))

# What a real install of the tool half contains.  The dashboard/desktop halves are
# irrelevant to the loader contract and are exercised by their own suites.
INSTALL_COPY = ("__init__.py", "department_lib", "plugin.yaml")

OTHER_TOOLS = ("department_ingest", "department_bind", "department_submit",
               "department_accept", "department_deliver", "department_escalate")

CONFIG = """\
plugins:
  enabled: [department-hub]
  entries:
    department-hub:
      settings:
        state_path: {state_path}
        departments:
          - id: dev
            name: Development
            manager:
              profile: dept-manager
              platform: api_server
              chat_id: api-dev
            members: [worker-a]
            reviewers: [reviewer-a]
"""


def _install(plugin_dir: Path) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for name in INSTALL_COPY:
        src, dst = ROOT / name, plugin_dir / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)


def _scratch_home(root: Path, layout: str) -> tuple[Path, Path]:
    """Return ``(hermes_home, plugin_dir)`` for one of the two installed layouts.

    ``launch_home``: ``<root>/plugins/department-hub`` with ``HERMES_HOME=<root>``.
    ``profile``:     ``<root>/profiles/dh-manager/plugins/department-hub`` with
                     ``HERMES_HOME=<root>/profiles/dh-manager`` — i.e. the profile's
                     own home, which is how the gateway serves several profiles.
    """
    if layout == "launch_home":
        hermes_home = root
        plugin_dir = root / "plugins" / "department-hub"
    elif layout == "profile":
        hermes_home = root / "profiles" / "dh-manager"
        plugin_dir = hermes_home / "plugins" / "department-hub"
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(layout)
    hermes_home.mkdir(parents=True, exist_ok=True)
    _install(plugin_dir)
    (hermes_home / "config.yaml").write_text(
        CONFIG.format(state_path=hermes_home / "department-hub" / "state.db"), encoding="utf-8")
    return hermes_home, plugin_dir


def _run_probe(home: Path, hermes_home: Path, args: list[str], *,
               extra_env: dict[str, str] | None = None) -> dict:
    env = {"HOME": str(home), "HERMES_HOME": str(hermes_home), "PATH": "/usr/bin:/bin"}
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-I", str(PROBE), *args],
        env=env, cwd=str(home), capture_output=True, text=True, timeout=600, check=False)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


@pytest.mark.parametrize("layout", ["launch_home", "profile"])
def test_all_tools_reach_their_handlers_under_the_real_loader(tmp_path, layout):
    home = tmp_path / "home"
    hermes_home, plugin_dir = _scratch_home(home, layout)
    result_path = tmp_path / f"{layout}.json"

    proc = _run_probe(home, hermes_home,
                      ["load", str(AGENT_ROOT), str(plugin_dir), str(result_path)])
    assert result_path.exists(), proc
    report = json.loads(result_path.read_text(encoding="utf-8"))
    detail = json.dumps({**proc, "report": report}, indent=2, ensure_ascii=False)

    # The probe itself is honest about the environment it ran in.
    assert report["ok"] is True, detail
    assert report["forbidden_on_path_before"] == [], detail
    assert not any(str(plugin_dir) == str(Path(p).resolve()) for p in report["sys_path_before"]
                   if p), detail

    # The plugin was discovered and loaded as a package by the real loader, with
    # no load error (the bug was invisible at load time and only appeared when a
    # handler ran, so both halves of the proof matter).
    ours = [entry for entry in report["loaded"].values()
            if Path(entry["path"] or ".") == plugin_dir]
    assert ours, detail
    assert ours[0]["enabled"] is True and ours[0]["error"] is None, detail
    assert report["plugin_package"]["loaded"] is True, detail
    assert Path(report["plugin_package"]["file"]).parent == plugin_dir, detail

    # Relative imports: the bundled package lives inside the plugin namespace only,
    # and discovery alone loads none of it (the handlers import it lazily).
    assert report["module_names_at_load"]["department_lib"] == [], detail
    assert not any(n.startswith("hermes_plugins.department_hub.department_lib")
                   for n in report["module_names_at_load"]["hermes_plugins"]), detail
    assert report["module_names"]["department_lib"] == [], detail
    assert ("hermes_plugins.department_hub.department_lib.store"
            in report["module_names"]["hermes_plugins"]), detail

    # department_status is the end-to-end positive against the scratch state.db.
    status = report["tools"]["department_status"]
    assert status["registered"] is True, detail
    assert status["payload"]["success"] is True, detail
    assert status["payload"]["status"]["department"]["id"] == "dev", detail
    assert Path(report["hermes_home"], "department-hub", "state.db").exists(), detail

    # The other six reach their handlers and answer with structured errors — the
    # exact failure mode of FINDING-6 was "ModuleNotFoundError" for all seven.
    for name in OTHER_TOOLS:
        tool = report["tools"][name]
        assert tool["registered"] is True, detail
        assert isinstance(tool["payload"], dict) and "success" in tool["payload"], detail
        assert tool["payload"]["success"] is False, detail
        assert tool["payload"]["error"], detail
        assert "ModuleNotFoundError" not in json.dumps(tool["payload"]), detail
        assert "ImportError" not in json.dumps(tool["payload"]), detail


def test_two_homes_in_one_process_get_isolated_plugin_namespaces(tmp_path):
    """The loader's per-profile namespacing is only usable with relative imports.

    Same slug from two Hermes homes in one process (the gateway multiplex shape):
    the second scope must get ``hermes_plugins.department_hub__home_<digest>`` and
    both must register their tools — with no global top-level ``department_lib``
    that both copies would silently share.
    """
    home = tmp_path / "home"
    home_a, plugin_a = _scratch_home(home, "launch_home")
    home_b, plugin_b = _scratch_home(home, "profile")
    result_path = tmp_path / "multiplex.json"

    proc = _run_probe(home, home_a,
                      ["multiplex", str(AGENT_ROOT), str(plugin_a), str(plugin_b), str(result_path)],
                      extra_env={"HERMES_HOME_B": str(home_b)})
    assert result_path.exists(), proc
    report = json.loads(result_path.read_text(encoding="utf-8"))
    detail = json.dumps({**proc, "report": report}, indent=2, ensure_ascii=False)

    assert report["ok"] is True, detail
    assert report["forbidden_on_path_before"] == [], detail
    assert report["module_names"]["department_lib"] == [], detail

    root_a = report["scopes"]["a"]["module_root"]
    root_b = report["scopes"]["b"]["module_root"]
    assert root_a == "hermes_plugins.department_hub", detail
    assert root_b.startswith("hermes_plugins.department_hub__home_"), detail
    assert root_a != root_b, detail
    # Each copy carries its own bundled library — nothing is shared globally.
    lib_a = report["scopes"]["a"]["bundled_lib_modules"]
    lib_b = report["scopes"]["b"]["bundled_lib_modules"]
    assert f"{root_a}.department_lib.store" in lib_a, detail
    assert f"{root_b}.department_lib.store" in lib_b, detail
    assert not set(lib_a) & set(lib_b), detail
    assert Path(report["scopes"]["a"]["bundled_lib_store_file"]).is_relative_to(plugin_a), detail
    assert Path(report["scopes"]["b"]["bundled_lib_store_file"]).is_relative_to(plugin_b), detail
    for scope in ("a", "b"):
        assert all(report["scopes"][scope]["tools"].values()), detail
        loaded = report["scopes"][scope]["loaded"]
        ours = [e for e in loaded.values()
                if Path(e["path"] or ".").name == "department-hub"]
        assert ours and ours[0]["error"] is None, detail

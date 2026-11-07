"""Load the department-hub plugin through the REAL Hermes plugin loader.

Run as a *fresh* CPython process (never imported by pytest):

    python -I tests/loader_probe.py load      <agent_root> <plugin_dir> <result_json>
    python -I tests/loader_probe.py multiplex <agent_root> <plugin_dir_a> <plugin_dir_b> <result_json>

Only the hermes-agent root (the loader's own dependency) is added to
``sys.path``.  The plugin directory, its parent and its grandparent are
explicitly rejected if they appear on ``sys.path``: the point of this probe is
to prove the tool half works as installed, i.e. through the loader's contract
(``hermes_plugins.<slug>`` package with ``submodule_search_locations`` = the
plugin directory) and with no PYTHONPATH / no plugin-root sys.path entry.

``load`` exercises one installed layout end to end: discovery, registration and
all seven tool handlers.  ``multiplex`` loads the same slug from two Hermes
homes in one process (the gateway's per-profile layout) and reports the two
module namespaces, proving the plugin does not depend on a global top-level
``department_lib`` that all profiles would share.
"""
from __future__ import annotations

import json
import importlib
import os
import sys
import traceback
from pathlib import Path

TOOL_CALLS = {
    # department_status is the end-to-end positive: the configured department in a
    # scratch state.db must produce success=True.
    "department_status": {"department": "dev"},
    # The other six must reach their handler and return a *structured* error
    # (missing param / unbound task / unknown board task), never an ImportError.
    "department_ingest": {"task_id": "t_probe"},
    "department_bind": {"department": "dev", "root_task_id": "t_probe"},
    "department_submit": {"task_id": "t_probe", "artifact_path": "/nonexistent/probe.bin"},
    "department_accept": {"task_id": "t_probe", "revision": 1, "verdict_run_id": 1,
                          "artifact_sha256": "0" * 64},
    "department_deliver": {"task_id": "t_probe", "revision": 1, "verdict_run_id": 1,
                           "artifact_sha256": "0" * 64},
    "department_escalate": {"task_id": "t_probe", "reason_code": "blocked",
                            "detail": {"round": 1}},
}


def _forbidden_on_path(target: Path) -> list[str]:
    """Plugin dir / parent / grandparent entries currently on ``sys.path``."""
    candidates = {target, target.parent, target.parent.parent}
    found = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:  # pragma: no cover - defensive
            continue
        if resolved in candidates:
            found.append(str(resolved))
    return sorted(set(found))


def _module_names() -> dict:
    return {
        "hermes_plugins": sorted(n for n in sys.modules if n.startswith("hermes_plugins")),
        "department_lib": sorted(n for n in sys.modules
                                 if n == "department_lib" or n.startswith("department_lib.")),
    }


def _loaded_plugins(manager) -> dict:
    return {
        key: {
            "name": loaded.manifest.name,
            "path": str(getattr(loaded.manifest, "path", "") or ""),
            "enabled": bool(loaded.enabled),
            "error": loaded.error,
        }
        for key, loaded in manager._plugins.items()
    }


def _write(result_path: Path, report: dict) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def run_load(agent_root: Path, plugin_dir: Path, result_path: Path) -> int:
    home = Path(os.environ["HERMES_HOME"]).resolve()
    report: dict = {
        "mode": "load",
        "agent_root": str(agent_root),
        "plugin_dir": str(plugin_dir),
        "hermes_home": str(home),
        "python": sys.version.split()[0],
        "sys_path_before": list(sys.path),
        "forbidden_on_path_before": _forbidden_on_path(plugin_dir),
        "loaded": {},
        "module_names": {},
        "tools": {},
        "ok": False,
    }
    try:
        sys.path.insert(0, str(agent_root))
        if _forbidden_on_path(plugin_dir):
            raise RuntimeError("probe misconfigured: plugin path on sys.path "
                               f"{_forbidden_on_path(plugin_dir)}")

        from hermes_cli.plugins import PluginManager  # the real loader

        manager = PluginManager(scope_key=str(home))
        manager.discover_and_load()

        report["loaded"] = _loaded_plugins(manager)
        report["module_names_at_load"] = _module_names()
        report["plugin_package"] = {
            "name": "hermes_plugins.department_hub",
            "loaded": "hermes_plugins.department_hub" in sys.modules,
            "file": getattr(sys.modules.get("hermes_plugins.department_hub"), "__file__", ""),
        }

        from tools.registry import registry  # noqa: PLC0415 — after the loader import

        for name, params in TOOL_CALLS.items():
            entry = registry.get_entry(name, scope=manager.scope_key)
            if entry is None:
                report["tools"][name] = {"registered": False, "payload": None}
                continue
            raw = entry.handler(params)
            report["tools"][name] = {
                "registered": True,
                "toolset": getattr(entry, "toolset", None),
                "handler": getattr(getattr(entry, "handler", None), "__qualname__", ""),
                "raw_type": type(raw).__name__,
                "payload": json.loads(raw),
            }
        # The bundled library is imported lazily by the handlers; record the
        # namespace it landed in (must be inside the plugin package, never global).
        report["module_names"] = _module_names()
        report["ok"] = True
    except BaseException:  # noqa: BLE001 — the report must capture any failure
        report["traceback"] = traceback.format_exc()
    finally:
        _write(result_path, report)
    return 0 if report["ok"] else 1


def _package_root_for(plugin_dir: Path) -> dict:
    """Identify the module the loader created for *this* plugin directory.

    The plugin imports its bundled library lazily (inside the tool handlers), so
    the probe imports one bundled submodule explicitly through the *package
    namespace* the loader gave this copy.  That is what proves each copy owns its
    own ``department_lib`` instead of sharing one global top-level module.
    """
    init_file = (plugin_dir / "__init__.py").resolve()
    root = ""
    for name, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            if Path(module_file).resolve() == init_file:
                root = name
                break
        except OSError:  # pragma: no cover - defensive
            continue
    store_file = ""
    if root:
        store_module = importlib.import_module(root + ".department_lib.store")
        store_file = str(Path(store_module.__file__).resolve())
    return {
        "module_root": root,
        "bundled_lib_modules": sorted(n for n in sys.modules
                                      if root and n.startswith(root + ".department_lib")),
        "bundled_lib_store_file": store_file,
    }


def run_multiplex(agent_root: Path, plugin_a: Path, plugin_b: Path, result_path: Path) -> int:
    home_a = Path(os.environ["HERMES_HOME"]).resolve()
    home_b = Path(os.environ["HERMES_HOME_B"]).resolve()
    report: dict = {
        "mode": "multiplex",
        "agent_root": str(agent_root),
        "plugin_a": str(plugin_a),
        "plugin_b": str(plugin_b),
        "home_a": str(home_a),
        "home_b": str(home_b),
        "python": sys.version.split()[0],
        "forbidden_on_path_before": sorted(set(_forbidden_on_path(plugin_a)
                                               + _forbidden_on_path(plugin_b))),
        "scopes": {},
        "module_names": {},
        "ok": False,
    }
    try:
        sys.path.insert(0, str(agent_root))
        from hermes_cli.plugins import PluginManager
        from tools.registry import registry

        managers = {}
        for label, home in (("a", home_a), ("b", home_b)):
            manager = PluginManager(scope_key=str(home))
            manager.discover_and_load()
            managers[label] = manager
            report["scopes"][label] = {
                "scope_key": manager.scope_key,
                "loaded": _loaded_plugins(manager),
                "tools": {name: registry.get_entry(name, scope=manager.scope_key) is not None
                          for name in TOOL_CALLS},
                "plugin_dir": str(plugin_a if label == "a" else plugin_b),
                **_package_root_for(plugin_a if label == "a" else plugin_b),
            }
        report["module_names"] = _module_names()
        report["ok"] = True
    except BaseException:  # noqa: BLE001
        report["traceback"] = traceback.format_exc()
    finally:
        _write(result_path, report)
    return 0 if report["ok"] else 1


def main() -> int:
    mode = sys.argv[1]
    agent_root = Path(sys.argv[2]).resolve()
    if mode == "load":
        return run_load(agent_root, Path(sys.argv[3]).resolve(), Path(sys.argv[4]).resolve())
    if mode == "multiplex":
        return run_multiplex(agent_root, Path(sys.argv[3]).resolve(),
                             Path(sys.argv[4]).resolve(), Path(sys.argv[5]).resolve())
    raise SystemExit(f"unknown probe mode {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main())

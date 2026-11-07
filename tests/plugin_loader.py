"""Load the plugin the way the real Hermes loader does: as a package.

``hermes_cli.plugins_loader._load_directory_module`` imports a directory plugin as
``hermes_plugins.<slug>`` with ``submodule_search_locations=[plugin_dir]``, sets
``__package__``/``__path__`` and *registers the module in ``sys.modules`` before
executing it*, so the plugin's own relative imports resolve inside that namespace.
Tests that only need an in-process handle reuse this helper — it mirrors that
contract exactly.  The as-installed contract itself (fresh process, no plugin
root on ``sys.path``) is proven by ``tests/test_real_loader_contract.py``.

Loading ``__init__.py`` with ``spec_from_file_location(name, path)`` *without* a
package context is what let FINDING-6 hide: relative imports could not resolve,
and a top-level absolute ``import department_lib`` only worked because
``conftest.py`` put the plugin root on ``sys.path`` — which the real loader never
does for the plugin's own imports.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

__all__ = ["PLUGIN_PACKAGE", "PLUGIN_ROOT", "load_plugin"]

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PACKAGE = "hermes_plugins.department_hub"


def load_plugin(module_name: str = PLUGIN_PACKAGE):
    """Import the plugin in package context, caching it like the loader does."""
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    if module_name.startswith("hermes_plugins.") and "hermes_plugins" not in sys.modules:
        # Same namespace-parent construction the real loader uses.
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        namespace.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = namespace
    init_file = PLUGIN_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        module_name, init_file, submodule_search_locations=[str(PLUGIN_ROOT)])
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot create a module spec for {init_file}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(PLUGIN_ROOT)]
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # Don't leave a half-initialized package (or its partially imported
        # relative submodules) behind: the loader evicts on the same boundary.
        for cached_name in [n for n in sys.modules
                            if n == module_name or n.startswith(module_name + ".")]:
            sys.modules.pop(cached_name, None)
        raise
    return module

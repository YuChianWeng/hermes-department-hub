"""Static + SDK-mock checks for the desktop half (not a real Desktop render)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "desktop" / "department-hub" / "plugin.js"

NODE_CANDIDATES = [
    "/home/ubuntu/.nvm/versions/node/v22.22.2/bin/node",
    "/home/ubuntu/.hermes/tools/node-26.12.1-linux-arm64/bin/node",
    shutil.which("node") or "",
]


def node_bin() -> str:
    for candidate in NODE_CANDIDATES:
        if candidate and Path(candidate).exists():
            version = subprocess.run([candidate, "--version"], capture_output=True, text=True)
            major = int(re.sub(r"^v(\d+).*", r"\1", version.stdout.strip() or "0"))
            if major >= 18:
                return candidate
    pytest.skip("no node >= 18 available for ESM checks")


def test_plugin_id_matches_its_folder():
    source = PLUGIN.read_text(encoding="utf-8")
    assert "id: ID" in source
    assert "const ID = 'department-hub'" in source
    assert PLUGIN.parent.name == "department-hub"


def test_only_allowlisted_imports():
    source = PLUGIN.read_text(encoding="utf-8")
    specifiers = set(re.findall(r"from\s+'([^']+)'", source))
    assert specifiers <= {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}, specifiers


def test_no_hardcoded_colours():
    source = PLUGIN.read_text(encoding="utf-8")
    without_comments = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    without_comments = re.sub(r"//[^\n]*", "", without_comments)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", without_comments), "hardcoded hex colour"
    assert "rgb(" not in without_comments


def test_manifest_declares_the_backend():
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "department-hub"
    assert manifest["api"] == "plugin_api.py"
    assert (ROOT / "dashboard" / "plugin_api.py").exists()


def test_node_syntax_check(tmp_path):
    node = node_bin()
    copy = tmp_path / "plugin.mjs"
    copy.write_text(PLUGIN.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sdk_mock_component_logic():
    node = node_bin()
    result = subprocess.run([node, str(ROOT / "tests" / "js" / "run.mjs")],
                            capture_output=True, text=True, timeout=120)
    print(result.stdout)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ALL PASS" in result.stdout


def _run_js(script: str, timeout: int = 300) -> subprocess.CompletedProcess:
    node = node_bin()
    return subprocess.run([node, str(ROOT / "tests" / "js" / script)],
                          capture_output=True, text=True, timeout=timeout)


def test_provenance_decisions_and_pin_scope_matrix():
    """Pure decision branches + the real build() pin-scope mutation matrix.

    The pure half has every input injected, so it must run on any machine. The
    matrix half visibly skips without a Desktop checkout but still exits 0.
    """
    result = _run_js("provenance-pure.mjs")
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ALL PASS" in result.stdout
    assert "pure: drifted source → FAIL" in result.stdout


def test_provenance_guard_is_fail_closed_end_to_end():
    """Spawn the real run.mjs: drifted source must be RED, not a silent skip."""
    result = _run_js("provenance-meta.mjs")
    print(result.stdout)
    print(result.stderr)
    if "SKIP provenance-meta:" in result.stdout:
        pytest.skip(result.stdout.strip().splitlines()[-1])
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ALL PASS" in result.stdout
    assert "meta(ii): drifted source exits non-zero" in result.stdout

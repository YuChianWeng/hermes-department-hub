"""Board adapters.

Only *read* access to Hermes Kanban is used for structure/edge verification.
The only board write in the live adapter is the final report comment, performed
through the public CLI and then read back.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional, Protocol


class BoardPort(Protocol):
    def task(self, task_id: str) -> Optional[dict[str, Any]]: ...
    def post_comment(self, task_id: str, author: str, body: str) -> dict[str, Any]: ...


class FakeBoard:
    """In-memory deterministic board used by unit tests."""

    def __init__(self):
        self.tasks: dict[str, dict[str, Any]] = {}

    def _save(self) -> None:  # overridden by JsonBoard
        pass

    def add_task(self, task_id: str, parents: Optional[list[str]] = None) -> None:
        parents = list(parents or [])
        self.tasks[task_id] = {
            "task_id": task_id,
            "parents": parents,
            "children": [],
            "comments": [],
        }
        for p in parents:
            if p not in self.tasks:
                self.add_task(p)
            if task_id not in self.tasks[p]["children"]:
                self.tasks[p]["children"].append(task_id)
        self._save()

    def link(self, parent: str, child: str) -> None:
        if parent not in self.tasks:
            self.add_task(parent)
        if child not in self.tasks:
            self.add_task(child)
        if child not in self.tasks[parent]["children"]:
            self.tasks[parent]["children"].append(child)
        if parent not in self.tasks[child]["parents"]:
            self.tasks[child]["parents"].append(parent)
        self._save()

    def task(self, task_id: str) -> Optional[dict[str, Any]]:
        t = self.tasks.get(task_id)
        if t is None:
            return None
        return {
            "task_id": t["task_id"],
            "parents": list(t["parents"]),
            "children": list(t["children"]),
            "comments": [dict(c) for c in t["comments"]],
        }

    def post_comment(self, task_id: str, author: str, body: str) -> dict[str, Any]:
        if task_id not in self.tasks:
            self.add_task(task_id)
        c = {"author": author, "body": body}
        self.tasks[task_id]["comments"].append(c)
        self._save()
        return c

    def comments(self, task_id: str) -> list[dict]:
        t = self.tasks.get(task_id) or {"comments": []}
        return list(t["comments"])


class JsonBoard(FakeBoard):
    """FakeBoard persisted to JSON so crash-test subprocesses share board state."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.tasks = data

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.tasks, indent=2, sort_keys=True))
        tmp.replace(self.path)


class CliBoard:
    """Read-only Kanban snapshot adapter over the public CLI (live lab only)."""

    def __init__(self, cli_python: str, hermes_script: str, cwd: Path,
                 env: Optional[dict] = None, timeout: int = 60):
        self.cli_python = cli_python
        self.hermes_script = hermes_script
        self.cwd = str(cwd)
        self.env = env
        self.timeout = timeout

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [self.cli_python, self.hermes_script, *args]
        return subprocess.run(cmd, cwd=self.cwd, env=self.env, text=True,
                              capture_output=True, timeout=self.timeout)

    def task(self, task_id: str) -> Optional[dict[str, Any]]:
        p = self._run("kanban", "show", task_id, "--json")
        if p.returncode != 0:
            return None
        data = json.loads(p.stdout)
        return {
            "task_id": task_id,
            "parents": list(data.get("parents") or []),
            "children": list(data.get("children") or []),
            "comments": [
                {"author": c.get("author"), "body": c.get("body")}
                for c in data.get("comments") or []
            ],
            "status": (data.get("task") or {}).get("status"),
            "raw": data,
        }

    def post_comment(self, task_id: str, author: str, body: str) -> dict[str, Any]:
        p = self._run("kanban", "comment", task_id, body, "--author", author)
        if p.returncode != 0:
            raise RuntimeError(f"kanban comment failed rc={p.returncode}: {p.stderr.strip()}")
        return {"author": author, "body": body, "rc": p.returncode}

    def subscriptions(self, task_id: str) -> list[dict[str, Any]]:
        p = self._run("kanban", "notify-list", task_id, "--json")
        if p.returncode != 0:
            return []
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = data.get("subscriptions") or data.get("data") or []
        return list(data or [])

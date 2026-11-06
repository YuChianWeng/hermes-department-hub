"""Thin adapter over the documented ``hermes kanban`` CLI.

Only public, documented commands are used (create/link/show/list/comment/
notify-*). The plugin never imports Hermes internals and never opens the
Kanban SQLite file: the CLI output is the only board contract it relies on.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

__all__ = ["BoardError", "CliKanban", "CommandResult"]


class BoardError(RuntimeError):
    """A board read/write could not be trusted.

    ``kind`` is one of ``unavailable`` (CLI failed / not installed),
    ``timeout``, ``invalid`` (malformed or incomplete response) or
    ``not_found``. Callers must treat every kind as fail-closed.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class CommandResult:
    rc: int
    stdout: str
    stderr: str


def _default_argv() -> list[str]:
    override = os.environ.get("HERMES_CLI")
    if override:
        return [override]
    found = shutil.which("hermes")
    if found:
        return [found]
    # Last resort: the CLI entry point inside the running Hermes install.
    return ["hermes"]


def _default_runner(argv: Sequence[str], env: Mapping[str, str] | None,
                    timeout: float | None) -> CommandResult:
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
            env=dict(env) if env is not None else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise BoardError("timeout", f"hermes CLI timed out after {timeout}s: {exc}") from exc
    except OSError as exc:
        raise BoardError("unavailable", f"cannot execute hermes CLI: {exc}") from exc
    return CommandResult(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


class CliKanban:
    """Runs documented ``hermes kanban`` subcommands and parses their JSON."""

    def __init__(self, *, argv: Sequence[str] | None = None,
                 env: Mapping[str, str] | None = None, timeout: float = 60,
                 runner: Callable[..., CommandResult] | None = None):
        self.argv = list(argv) if argv else _default_argv()
        self.env = dict(env) if env is not None else None
        self.timeout = timeout
        self._runner = runner or _default_runner

    # ── plumbing ────────────────────────────────────────────────────────────

    def run(self, args: Sequence[str]) -> CommandResult:
        argv = [*self.argv, *args]
        try:
            result = self._runner(argv, self.env, self.timeout)
        except BoardError:
            raise
        except Exception as exc:  # runner injection / spawn failure
            raise BoardError("unavailable", f"kanban CLI call failed: {exc}") from exc
        if not isinstance(result, CommandResult):
            raise BoardError("invalid", "kanban CLI runner returned an unexpected result")
        if result.rc != 0:
            raise BoardError(
                "unavailable",
                f"`hermes {' '.join(args)}` exited {result.rc}: {result.stderr.strip()[:500]}",
            )
        return result

    def _json(self, args: Sequence[str], *, expect: type) -> Any:
        result = self.run(args)
        text = result.stdout.strip()
        if not text:
            raise BoardError("invalid", f"`hermes {' '.join(args)}` produced no JSON output")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BoardError(
                "invalid", f"`hermes {' '.join(args)}` produced malformed JSON: {exc}") from exc
        if not isinstance(data, expect):
            raise BoardError(
                "invalid",
                f"`hermes {' '.join(args)}` returned {type(data).__name__}, expected {expect.__name__}",
            )
        return data

    # ── reads ───────────────────────────────────────────────────────────────

    def show(self, task_id: str) -> dict:
        return self._json(["kanban", "show", str(task_id), "--json"], expect=dict)

    def list(self, *extra: str) -> list:
        return self._json(["kanban", "list", "--json", *extra], expect=list)

    def notify_list(self, task_id: str | None = None) -> list:
        args = ["kanban", "notify-list"]
        if task_id:
            args.append(str(task_id))
        args.append("--json")
        rows = self._json(args, expect=list)
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise BoardError("invalid", f"notify-list row {i} is not an object")
        return rows

    # ── writes ──────────────────────────────────────────────────────────────

    def notify_subscribe(self, task_id: str, *, platform: str, chat_id: str,
                         notifier_profile: str, delivery_mode: str = "wake",
                         thread_id: str | None = None) -> None:
        args = ["kanban", "notify-subscribe", str(task_id), "--platform", platform,
                "--chat-id", chat_id, "--delivery-mode", delivery_mode,
                "--notifier-profile", notifier_profile]
        if thread_id:
            args += ["--thread-id", thread_id]
        self.run(args)

    def notify_unsubscribe(self, task_id: str, *, platform: str, chat_id: str,
                           thread_id: str | None = None) -> None:
        args = ["kanban", "notify-unsubscribe", str(task_id), "--platform", platform,
                "--chat-id", chat_id]
        if thread_id:
            args += ["--thread-id", thread_id]
        self.run(args)

    def comment(self, task_id: str, body: str, *, author: str | None = None) -> None:
        args = ["kanban", "comment", str(task_id), body]
        if author:
            args += ["--author", author]
        self.run(args)

"""Shared fixtures for the M2 read-model tests (fake board, no gateway)."""
from __future__ import annotations

from typing import Iterable

from department_lib.board import BoardRun, BoardTask
from department_lib.config import load_departments
from department_lib.kanban_cli import BoardError
from department_lib.models import BoardEvent


SHA = "a" * 64
SHA2 = "b" * 64


def dept(**over):
    raw = {
        "id": "dev",
        "name": "Development",
        "manager": {"profile": "manager-a", "platform": "api_server", "chat_id": "chat-secret-123"},
        "members": ["worker-a"],
        "reviewers": ["reviewer-a"],
        "max_repair_rounds": 2,
    }
    raw.update(over)
    return load_departments({"departments": [raw]})[raw["id"]]


def event(kind: str, at: int, run_id: int | None = None, **payload) -> BoardEvent:
    return BoardEvent(task_id="t1", kind=kind, run_id=run_id, created_at=at, payload=payload)


def run(run_id: int, profile: str, status: str, outcome: str | None = None,
        summary: str | None = None, **metadata):
    return BoardRun(id=run_id, profile=profile, status=status, outcome=outcome, summary=summary,
                    metadata=metadata or None)


def produce(store, task_id: str = "t1", revision: int = 1, sha: str = SHA,
            profile: str = "worker-a"):
    """Record a synthetic producer identity (v4 gates on this, not board prose).

    No file is read here: unit tests exercise the gate, not the hasher. The
    producer path itself is covered end-to-end by test_m1_v4_regressions.py.
    """
    identity, _ = store.put_artifact(
        task_id=task_id, revision=revision, sha256=sha, algorithm="sha256",
        path=f"/synthetic/{task_id}", real_path=f"/synthetic/{task_id}",
        size_bytes=0, producer_profile=profile, computed_at=0.0)
    return identity


def task(task_id: str = "t1", status: str = "done", *, assignee: str | None = "worker-a",
         events: Iterable[BoardEvent] = (), runs: Iterable[BoardRun] = (),
         parents: Iterable[str] = (), children: Iterable[str] = ()) -> BoardTask:
    return BoardTask(task_id=task_id, status=status, assignee=assignee,
                     parents=tuple(parents), children=tuple(children), comments=(),
                     events=tuple(events), runs=tuple(runs))


class FakeBoard:
    """A board lookalike: real BoardTask shapes, injectable read failures."""

    def __init__(self, tasks: dict[str, BoardTask], *, titles: dict[str, str] | None = None,
                 fail: dict[str, str] | None = None, health: dict | None = None,
                 subscriptions: dict[str, list[dict]] | None = None):
        self.tasks = dict(tasks)
        self._titles = dict(titles or {t: f"Card {t}" for t in tasks})
        self.fail = dict(fail or {})
        self._health = health or {"available": True, "code": "", "detail": "", "task_count": len(tasks)}
        self._subscriptions = {key: [dict(row) for row in rows]
                               for key, rows in (subscriptions or {}).items()}
        self.get_calls = 0

    def get(self, task_id: str) -> BoardTask:
        self.get_calls += 1
        if task_id in self.fail:
            raise BoardError(self.fail[task_id], f"injected failure for {task_id}")
        if task_id not in self.tasks:
            raise BoardError("not_found", f"task {task_id} is not on this board")
        return self.tasks[task_id]

    def titles(self, task_ids: list[str]) -> dict[str, str]:
        return {t: self._titles[t] for t in task_ids if t in self._titles}

    def subscriptions(self, task_id: str) -> list[dict]:
        return [dict(row) for row in self._subscriptions.get(task_id, [])]

    def health(self) -> dict:
        return dict(self._health)

    def ancestry(self, task_id: str, *, limit: int = 8) -> tuple[str, ...]:
        order = [task_id]
        frontier = [task_id]
        while frontier:
            nxt = []
            for node in frontier:
                if node not in self.tasks:
                    raise BoardError("not_found", node)
                for parent in self.tasks[node].parents:
                    if parent not in order:
                        order.append(parent)
                        nxt.append(parent)
            frontier = nxt
        return tuple(order)

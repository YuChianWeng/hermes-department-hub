"""Authoritative board reads with strict validation.

A Kanban response is only usable when every field this module depends on is
present with the expected type. A missing field is UNKNOWN, never "empty":
treating an incomplete response as an empty topology is exactly the fail-open
bug that lets an attached report look detached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .kanban_cli import BoardError

__all__ = ["Board", "BoardTask", "BoardRun"]

_TOPOLOGY_FIELDS = ("parents", "children", "comments", "events", "runs")


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise BoardError("invalid", message)


def _str_list(data: Mapping[str, Any], field: str, task_id: str) -> tuple[str, ...]:
    _require(field in data, f"{field} missing for task {task_id}")
    value = data[field]
    _require(isinstance(value, list), f"{field} for task {task_id} is not a list")
    out: list[str] = []
    for item in value:
        _require(isinstance(item, str) and bool(item.strip()),
                 f"{field} for task {task_id} contains a non-string id")
        out.append(item)
    return tuple(out)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class BoardRun:
    id: int
    profile: str | None
    status: str | None
    outcome: str | None
    summary: str | None
    metadata: dict | None


@dataclass(frozen=True)
class BoardTask:
    task_id: str
    status: str
    assignee: str | None
    parents: tuple[str, ...]
    children: tuple[str, ...]
    comments: tuple[dict, ...]
    events: tuple[Any, ...]        # department_lib.models.BoardEvent
    runs: tuple[BoardRun, ...]

    def run_by_id(self, run_id: int | None) -> BoardRun | None:
        if run_id is None:
            return None
        for run in self.runs:
            if run.id == run_id:
                return run
        return None

    def latest_run(self) -> BoardRun | None:
        return max(self.runs, key=lambda r: r.id) if self.runs else None


class Board:
    """Reads authoritative task snapshots through :class:`CliKanban`."""

    def __init__(self, cli):
        self.cli = cli

    def get(self, task_id: str) -> BoardTask:
        data = self.cli.show(task_id)
        _require(isinstance(data, dict), f"show {task_id} did not return an object")
        if "task" not in data:
            if "error" in data:
                raise BoardError("not_found", f"task {task_id}: {data.get('error')}")
            raise BoardError("invalid", f"show {task_id} returned no 'task' object")
        task = data["task"]
        _require(isinstance(task, dict), f"task {task_id} is not an object")
        _require(isinstance(task.get("id"), str), f"task {task_id} has no string id")
        _require(task["id"] == task_id,
                 f"task id mismatch: requested {task_id}, board returned {task['id']}")
        _require(isinstance(task.get("status"), str) and bool(task["status"]),
                 f"task {task_id} has no status")
        assignee = task.get("assignee")
        _require(assignee is None or isinstance(assignee, str),
                 f"task {task_id} has a non-string assignee")

        for field in _TOPOLOGY_FIELDS:
            _require(field in data, f"{field} missing for task {task_id}")

        parents = _str_list(data, "parents", task_id)
        children = _str_list(data, "children", task_id)

        comments_raw = data["comments"]
        _require(isinstance(comments_raw, list), f"comments for task {task_id} is not a list")
        comments: list[dict] = []
        for item in comments_raw:
            _require(isinstance(item, Mapping), f"comment entry for task {task_id} is not an object")
            comments.append(dict(item))

        events_raw = data["events"]
        _require(isinstance(events_raw, list), f"events for task {task_id} is not a list")
        from .models import BoardEvent  # local import keeps models dependency-free
        events: list[BoardEvent] = []
        for item in events_raw:
            _require(isinstance(item, Mapping), f"event entry for task {task_id} is not an object")
            _require(isinstance(item.get("kind"), str) and bool(item["kind"]),
                     f"event for task {task_id} has no kind")
            _require(_is_int(item.get("created_at")),
                     f"event for task {task_id} has no integer created_at")
            run_id = item.get("run_id")
            _require(run_id is None or _is_int(run_id),
                     f"event for task {task_id} has a non-integer run_id")
            payload = item.get("payload", {})
            _require(payload is None or isinstance(payload, Mapping),
                     f"event payload for task {task_id} is not an object")
            _require(item.get("id") is None or type(item["id"]) in (str, int),
                     f"event for task {task_id} has an invalid source id")
            events.append(BoardEvent(
                task_id=task_id, kind=item["kind"], run_id=run_id,
                created_at=item["created_at"], payload=dict(payload or {}),
                source_id=item.get("id"),
            ))

        runs_raw = data["runs"]
        _require(isinstance(runs_raw, list), f"runs for task {task_id} is not a list")
        runs: list[BoardRun] = []
        for item in runs_raw:
            _require(isinstance(item, Mapping), f"run entry for task {task_id} is not an object")
            _require(_is_int(item.get("id")), f"run for task {task_id} has no integer id")
            _require(item.get("profile") is None or isinstance(item["profile"], str),
                     f"run {item['id']} for task {task_id} has a non-string profile")
            meta = item.get("metadata")
            _require(meta is None or isinstance(meta, Mapping),
                     f"run {item['id']} metadata for task {task_id} is not an object")
            for field in ("status", "outcome", "summary"):
                _require(item.get(field) is None or isinstance(item[field], str),
                         f"run {item['id']} for task {task_id} has invalid {field}")
            runs.append(BoardRun(
                id=item["id"], profile=item.get("profile"), status=item.get("status"),
                outcome=item.get("outcome"), summary=item.get("summary"),
                metadata=dict(meta) if meta else None,
            ))

        _require(len({r.id for r in runs}) == len(runs), f"duplicate run ids for task {task_id}")

        return BoardTask(
            task_id=task_id, status=task["status"], assignee=assignee,
            parents=parents, children=children, comments=tuple(comments),
            events=tuple(events), runs=tuple(runs),
        )

    def ancestry(self, task_id: str, *, limit: int = 8) -> tuple[str, ...]:
        """Return ``(task, parents..., ancestors...)`` in breadth-first order.

        Raises :class:`BoardError` on any unreadable parent, a revisit of the
        start node (cycle), or a chain longer than ``limit``. Callers must not
        treat an error as "no ancestors".
        """
        order: list[str] = [task_id]
        seen = {task_id}
        frontier = [task_id]
        depth = 0
        while frontier:
            depth += 1
            if depth > limit:
                raise BoardError("invalid", f"ancestry of {task_id} exceeds depth {limit}")
            next_frontier: list[str] = []
            for node in frontier:
                snap = self.get(node)
                for parent in snap.parents:
                    if parent == task_id:
                        raise BoardError("invalid", f"cycle detected in ancestry of {task_id}")
                    if parent in seen:
                        continue
                    seen.add(parent)
                    order.append(parent)
                    next_frontier.append(parent)
            frontier = next_frontier
        return tuple(order)

    def is_descendant(self, task_id: str, ancestor_id: str) -> bool:
        chain = self.ancestry(task_id)
        return ancestor_id in chain and ancestor_id != task_id

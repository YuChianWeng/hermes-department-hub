"""Manager wake routing.

A managed child card must wake its own department manager — not the
coordinator, and not another department. Hermes' documented surface for that
is ``hermes kanban notify-subscribe ... --delivery-mode wake`` plus
``notify-list --json`` as the read-back.

Known limitation (reproduced by P0-R run 444): core Kanban *inherits* a
parent's notification subscription onto children created or linked later, so a
coordinator subscribed to a root can be woken by child events. This module
cannot veto that: hooks are observer-only and there is no public no-inherit
switch. It therefore reports a foreign subscription as a conflict and never
claims that routing is an enforced isolation boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .kanban_cli import BoardError

__all__ = ["Router", "RoutingIssue", "RoutingReport"]


@dataclass(frozen=True)
class RoutingIssue:
    task_id: str
    code: str
    detail: str


@dataclass(frozen=True)
class RoutingReport:
    dept_id: str
    ensured: tuple[str, ...]
    issues: tuple[RoutingIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


class Router:
    def __init__(self, cli):
        self.cli = cli

    # ── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _matches(row: Mapping, dept) -> bool:
        return (row.get("platform") == dept.manager_platform
                and row.get("chat_id") == dept.manager_chat_id
                and row.get("notifier_profile") == dept.manager_profile)

    def _inspect(self, dept, task_id: str) -> tuple[list[RoutingIssue], bool]:
        """Return (issues, needs_subscribe) for one task, read-only."""
        rows = self.cli.notify_list(task_id)
        matching = [r for r in rows if self._matches(r, dept)]
        foreign = [r for r in rows if not self._matches(r, dept)]
        issues: list[RoutingIssue] = []
        # Matching chat/subscription does not attest runtime session ownership.
        issues.append(RoutingIssue(task_id, "session_owner_unverified",
                                   "public notify-list cannot attest runtime session ownership"))
        if foreign:
            seen = ", ".join(sorted({f"{r.get('platform')}:{r.get('chat_id')}" for r in foreign}))
            issues.append(RoutingIssue(
                task_id, "foreign_subscription",
                f"non-manager subscription(s) present on {task_id}: {seen}. "
                "Not removed automatically; Kanban inheritance can wake them.",
            ))
        if not matching:
            return issues, True
        if all(r.get("delivery_mode") != "wake" for r in matching):
            issues.append(RoutingIssue(
                task_id, "mode_mismatch",
                f"manager subscription on {task_id} does not use delivery-mode=wake",
            ))
        if len(matching) > 1:
            issues.append(RoutingIssue(
                task_id, "duplicate_subscription",
                f"{len(matching)} manager subscriptions on {task_id}",
            ))
        return issues, False

    # ── public API ──────────────────────────────────────────────────────────

    def ensure(self, dept, task_ids: Iterable[str], *, repair: bool = False,
               task_departments: Mapping[str, str] | None = None) -> RoutingReport:
        ensured: list[str] = []
        issues: list[RoutingIssue] = []
        for task_id in task_ids:
            owner = (task_departments or {}).get(task_id)
            if owner is None:
                issues.append(RoutingIssue(task_id, "ownership_unknown", "explicit membership required"))
                continue
            if owner is not None and owner != dept.dept_id:
                issues.append(RoutingIssue(
                    task_id, "cross_department",
                    f"task {task_id} belongs to department '{owner}', not '{dept.dept_id}'",
                ))
                continue
            try:
                found, needs_subscribe = self._inspect(dept, task_id)
            except BoardError as exc:
                issues.append(RoutingIssue(task_id, "board_unknown", str(exc)))
                continue
            issues.extend(found)
            mode_issue = [i for i in found if i.code == "mode_mismatch"]
            if mode_issue and repair:
                try:
                    self.cli.notify_unsubscribe(
                        task_id, platform=dept.manager_platform, chat_id=dept.manager_chat_id)
                    needs_subscribe = True
                except BoardError as exc:
                    issues.append(RoutingIssue(task_id, "repair_failed", str(exc)))
                    continue
                issues = [i for i in issues if i.code != "mode_mismatch"]
            if not needs_subscribe:
                continue
            try:
                self.cli.notify_subscribe(
                    task_id, platform=dept.manager_platform,
                    chat_id=dept.manager_chat_id, delivery_mode="wake",
                    notifier_profile=dept.manager_profile)
                verified, still_missing = self._inspect(dept, task_id)
            except BoardError as exc:
                issues.append(RoutingIssue(task_id, "subscribe_failed", str(exc)))
                continue
            issues.extend(i for i in verified if i not in issues)
            blocking = [i for i in verified if i.code != "session_owner_unverified"]
            if still_missing or blocking or any(i.code == "board_unknown" for i in verified):
                issues.append(RoutingIssue(
                    task_id, "subscribe_unverified",
                    "manager wake subscription was not visible on read-back",
                ))
                continue
            ensured.append(task_id)
        return RoutingReport(dept.dept_id, tuple(ensured), tuple(issues))

    def verify(self, dept, task_ids: Sequence[str]) -> RoutingReport:
        issues: list[RoutingIssue] = []
        for task_id in task_ids:
            try:
                found, needs_subscribe = self._inspect(dept, task_id)
            except BoardError as exc:
                issues.append(RoutingIssue(task_id, "board_unknown", str(exc)))
                continue
            issues.extend(found)
            if needs_subscribe:
                issues.append(RoutingIssue(
                    task_id, "missing_subscription",
                    f"no manager wake subscription for {task_id}",
                ))
        return RoutingReport(dept.dept_id, (), tuple(issues))

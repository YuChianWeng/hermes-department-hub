"""Read model for the Desktop management page.

This is the **single thin layer** between the M2 user interface and M1's
internals. The FastAPI routes in ``dashboard/plugin_api.py`` and the desktop
half in ``desktop/department-hub/plugin.js`` only know the JSON shapes produced here; if M1-R
changes an internal structure, this module is the only place to realign.

Truth rules enforced here (and asserted in ``tests/test_dashboard.py``):

* Task status/assignee/last activity/review verdict come from an authoritative
  Kanban read. When a card cannot be read the task is reported under
  ``unknown_tasks`` — never as an empty or invented state.
* "Accepted" and "final report delivered" come from M1's durable store
  (acceptance rows / delivery rows). The UI never infers them.
* The final report is the **plugin-owned** row in ``deliveries.body_json``
  (schema ``department-hub/final/v2``): summary / delivered_at / schema /
  generation / canonical identity are projected from that row only — never from
  a file, a board comment or a receipt. Identity is the v5 four-field form
  (``task_id/revision/verdict_run_id/artifact_sha256``), and the stored identity
  is shown separately from the current board identity.
* Nothing is mutated: this module has no write path at all. Department
  creation/editing is returned as a validated *plan* plus an explicit refusal,
  because writing ``config.yaml`` and installing profiles cannot be done
  safely from a browser surface (see :meth:`DashboardService.mutation_refusal`).
* Personal data is minimised: no chat ids, no platform ids, no workspace
  paths, no comment bodies, no run summaries, no event payloads.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .acceptance import AcceptanceClaim, evaluate, evidence_from_board
from .kanban_cli import BoardError
from .models import ConfigError, Department
from .store import StoreError

__all__ = ["DashboardService", "LIMITS", "MUTATION_CODE", "CONTRACT_VERSION"]

#: The M1 contract this read model is aligned to. The version string is the
#: document version, not a wire version (see ``docs/m1-v5-contract.md``).
CONTRACT_VERSION = "department-hub/m1-contract/v5"

#: Frozen reason-code vocabulary (``docs/m1-v5-contract.md`` §6). The read
#: model may only surface codes from these sets; anything else is an M2-internal
#: state (``none`` / ``unknown``) and is labelled as such. Kept here so tests can
#: prove completeness instead of trusting prose.
#:
#: v5 removes ``effect_fencing_unavailable`` (final delivery never appends) and
#: adds the final held ``report_too_large``. ``effect_raised`` is an *unknown*
#: outcome (a remote escalation effect may or may not have happened), never a
#: dead letter.
ACCEPT_HELD_CODES = (
    "inactive_department", "manager_mismatch", "repair_loop_exhausted",
    "review_rejected", "reviewer_not_independent", "reviewer_not_requested",
    "implementer_not_member", "rejection_unbound", "verdict_conflict",
    "review_unproven", "revision_mismatch", "no_pass_verdict", "stale_verdict",
    "verdict_not_final", "artifact_declaration_conflict", "artifact_unbound",
    "artifact_mismatch", "review_generation_stale", "event_quarantined",
    "acceptance_not_active",
)
DELIVER_EXTRA_HELD_CODES = (
    "acceptance_missing", "acceptance_stale", "delivery_identity_conflict",
    "department_changed", "effect_transaction_nested", "ownership_changed",
    "ownership_unknown", "ownership_unverified", "board_unknown",
    "escalation_identity_conflict", "no_delivery", "report_too_large",
)
UNKNOWN_CODES = (
    "prior_effect_unknown", "in_flight_unknown", "readback_unavailable",
    "readback_not_found", "effect_raised",
)
DEAD_LETTER_CODES = ("retry_budget_exhausted",)
#: Status-only reasons: M1's ``department_status`` may say "all checks passed
#: but delivery was not invoked" with ``not_delivered``. It is **not** a release
#: action and must never appear as a mutation/held code in this read model.
STATUS_ONLY_REASONS = ("not_delivered",)
#: Names that look like M1 codes but are not. M2 must never surface them as a
#: held/release state.
NOT_M1_CODES = (
    "not_attempted", "event_at", "board_unavailable",
    "department_unreadable", "department_missing",
)

#: ``deliveries.body_json`` is UTF-8 JSON with these hard bounds (M1 v5 §4).
#: The UI never renders a larger report: it shows ``report_too_large`` instead,
#: so a corrupt/foreign row cannot inflate the bounded dashboard payload.
REPORT_MAX_BYTES = 65536
REPORT_SUMMARY_MAX_CHARS = 4096
REPORT_SCHEMA_V2 = "department-hub/final/v2"

LIMITS = {
    "max_departments": 20,
    "max_tasks_per_department": 50,
    "max_events": 50,
    "max_text": 300,
    "max_report_bytes": REPORT_MAX_BYTES,
    "max_report_summary_chars": REPORT_SUMMARY_MAX_CHARS,
    "snapshot_ttl_seconds": 5,
    "max_age_seconds": 300,
}

MUTATION_CODE = "unsafe_mutation_blocked"

_TERMINAL = ("done", "archived")


def _text(value: Any, limit: int = LIMITS["max_text"]) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _iso(seconds: int | float | None) -> str | None:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(seconds)))


def _review_state(evidence, gate) -> dict:
    """Expose M1's own gate decision instead of re-deriving a PASS.

    v4.1 decides rejection from the *run outcome* (``changes_requested`` bound
    to an authorised review-lane claim), not from a structured verdict, and it
    distinguishes ``review_unproven`` / ``rejection_unbound`` /
    ``artifact_declaration_conflict``. Re-implementing that here would drift, so
    the read model asks :func:`department_lib.acceptance.evaluate` for the exact
    code the manager would get for the evidence that is on the board. The probe
    is pure: it writes nothing and accepts nothing.

    ``gate is None`` (evidence unreadable) is reported as ``unknown`` — an M2
    display state, not an M1 reason code.
    """
    verdict = evidence.verdict if evidence is not None else None
    if gate is None:
        return {
            "state": "unknown",
            "held": True,
            "held_code": None,
            "gate_status": "unknown",
            "ready_to_deliver": False,
        }
    if gate.status == "releasable":
        state, code = "pass", None
    elif verdict is not None and verdict.kind == "REQUEST_CHANGES":
        state, code = "rejected", gate.reason
    elif verdict is None and evidence.review_request_run_id is None:
        # Nothing was ever sent to review: "not reviewed yet" must not be
        # displayed as a failed review.
        state, code = "none", None
    else:
        state, code = "held", gate.reason
    return {
        "state": state,
        "held": state in ("held", "rejected"),
        "held_code": code,
        "gate_status": gate.status,
        "gate_revision": gate.revision,
        "ready_to_deliver": bool(gate.status == "releasable"),
    }


def _gate_probe(dept, snap, evidence, artifact_lookup):
    """What ``department_accept`` would answer for the exact board evidence.

    The claim is synthesised from the evidence itself (current revision, the
    verdict run that is actually on the board, the producer-recorded SHA), so
    claim-dependent codes (``revision_mismatch``, ``stale_verdict``,
    ``artifact_mismatch``) cannot appear here — they describe a manager passing
    the wrong claim, not a board state. ``evaluate`` is pure, so this is a read.
    """
    verdict = evidence.verdict
    claim = AcceptanceClaim(
        task_id=snap.task_id,
        revision=evidence.revision,
        verdict_run_id=verdict.run_id if verdict is not None else 0,
        artifact_sha256=(verdict.artifact_sha256 if verdict is not None else None) or "",
        manager_profile=dept.manager_profile,
    )
    return evaluate(dept=dept, task=snap, claim=claim, artifact_lookup=artifact_lookup)


def _identity(task_id: Any, revision: Any, verdict_run_id: Any,
              artifact_sha256: Any) -> dict:
    """Canonical v5 delivery identity — exactly four fields, never nested.

    v5 compares identities on ``task_id / revision / verdict_run_id /
    artifact_sha256`` only. The keys are always present (values may be ``None``)
    so an equality check can never pass just because a field was dropped.
    """
    return {
        "task_id": _text(task_id, 64),
        "revision": _int(revision),
        "verdict_run_id": _int(verdict_run_id),
        "artifact_sha256": _text(artifact_sha256, 64),
    }


def _delivery_identity(body: Any) -> dict | None:
    """The canonical identity recorded in ``deliveries.body_json``.

    This is a pure read of the stored report body: no file, no board comment.
    """
    if not isinstance(body, dict):
        return None
    verdict = body.get("verdict") if isinstance(body.get("verdict"), dict) else {}
    return _identity(body.get("task_id"), body.get("revision"),
                     verdict.get("run_id"), verdict.get("artifact_sha256"))


def _current_identity(review: dict, task_id: str) -> dict:
    """The identity the *current* board evidence would deliver, same shape."""
    return _identity(task_id, review.get("revision"), review.get("run_id"),
                     review.get("artifact_sha256"))


def _same_identity(delivered: dict | None, current: dict) -> bool:
    """True only when the stored report describes this exact generation."""
    if not delivered:
        return False
    return all(delivered.get(key) == current.get(key)
               for key in ("task_id", "revision", "verdict_run_id", "artifact_sha256"))


def _report_size(body: Any) -> int | None:
    """UTF-8 byte size of the stored report, as M1 bounded it."""
    if not isinstance(body, dict):
        return None
    try:
        return len(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return None


def _report_projection(body: Any, *, state: str | None) -> dict:
    """Project the plugin-persisted final report for display.

    Source of truth is ``deliveries.body_json`` only. A report is served when it
    is a confirmed ``department-hub/final/v2`` body within the v5 bounds; an
    oversized or malformed body is *not* rendered (the UI shows
    ``report_too_large``), and legacy v1 / pending / unknown rows never become a
    "local report".
    """
    out = {"report": None, "report_state": "none", "report_code": None,
           "report_bytes": None, "delivered_at": None, "delivered_at_iso": None,
           "report_schema": None, "report_generation": None, "summary": None}
    if not isinstance(body, dict):
        out["report_state"] = "legacy"
        return out
    delivered_at = body.get("delivered_at")
    if isinstance(delivered_at, (int, float)) and not isinstance(delivered_at, bool):
        out["delivered_at"] = delivered_at
        out["delivered_at_iso"] = _iso(delivered_at)
    schema = body.get("schema")
    if schema != REPORT_SCHEMA_V2:
        # v5 exposes only v2 reports (`report=null` for legacy v1).
        out["report_state"] = "legacy"
        return out
    size = _report_size(body)
    out["report_bytes"] = size
    out["report_schema"] = _text(schema, 80)
    out["report_generation"] = _int(body.get("generation"))
    summary = body.get("summary")
    summary_chars = len(summary) if isinstance(summary, str) else None
    if state != "confirmed":
        # A v2 body on a pending/unknown/dead-letter row can only be a legacy or
        # foreign row: it is not a committed local report.
        out["report_state"] = "not_confirmed"
        return out
    if (size is None or size > REPORT_MAX_BYTES
            or summary_chars is None or summary_chars > REPORT_SUMMARY_MAX_CHARS):
        # Do not echo oversized/foreign content into the bounded payload.
        too_large = bool(
            (size is not None and size > REPORT_MAX_BYTES)
            or (summary_chars is not None and summary_chars > REPORT_SUMMARY_MAX_CHARS))
        out["report_state"] = "report_too_large" if too_large else "legacy"
        out["report_code"] = "report_too_large" if too_large else None
        return out
    out["summary"] = summary
    out["report_state"] = "ok"
    out["report"] = {
        "schema": schema,
        "generation": _int(body.get("generation")),
        "delivered_at": out["delivered_at"],
        "delivered_at_iso": out["delivered_at_iso"],
        "summary": summary,
        "summary_chars": summary_chars,
        "identity": _delivery_identity(body),
        "report_kind": _text(body.get("report_kind"), 80),
        "size_bytes": size,
    }
    return out


def _delivery_readiness(dept, review: dict, binding: dict, acceptance: dict | None,
                        row, delivered_identity: dict | None, task_id: str) -> dict:
    """Project what ``department_deliver`` would answer, without writing.

    Mirrors the observable authorize() preconditions (department active,
    ownership, review gate, durable acceptance). A confirmed row is the only
    source of "already delivered"; a second generation is ``held`` with
    ``delivery_identity_conflict`` and never "ready to deliver again". The
    current (board) identity and the stored (delivered) identity are returned
    side by side so a conflict is visible instead of hidden.
    """
    current = _current_identity(review, task_id)
    base = {"current_identity": current, "stored_identity": delivered_identity,
            "delivered_identity": delivered_identity,
            "source": "derived: M1 policy/gate + durable acceptance + "
                      "plugin-persisted final report rows"}
    if row is not None:
        if row.state == "confirmed":
            if _same_identity(delivered_identity, current):
                return {**base, "state": "already_delivered", "code": None, "ready": False}
            return {**base, "state": "held", "code": "delivery_identity_conflict",
                    "ready": False}
        if row.state == "unknown":
            return {**base, "state": "unknown",
                    "code": _text(row.detail) or "in_flight_unknown", "ready": False}
        if row.state == "dead_letter":
            detail = _text(row.detail) or "retry_budget_exhausted"
            if detail in UNKNOWN_CODES:
                # Legacy pre-v5 row: an effect exception is an unknown outcome,
                # not a dead letter (M1 v5 §6).
                return {**base, "state": "unknown", "code": detail, "ready": False}
            return {**base, "state": "dead_letter", "code": detail, "ready": False}
        # pending: an intent exists but no read-back confirmed it. Never resend,
        # never call it ready, never project it as a local report.
        return {**base, "state": "unknown", "code": "in_flight_unknown", "ready": False}

    if not dept.active:
        code = "inactive_department"
    elif binding.get("state") != "verified":
        code = binding.get("state") or "ownership_unverified"
    elif not review.get("ready_to_deliver"):
        code = review.get("held_code") or "no_pass_verdict"
    elif acceptance is None:
        code = "acceptance_missing"
    elif not acceptance.get("current"):
        code = "acceptance_stale"
    else:
        code = None
    if code is not None:
        return {**base, "state": "held", "code": code, "ready": False}
    return {**base, "state": "ready", "code": None, "ready": True}


@dataclass(frozen=True)
class _Cached:
    expires: float
    payload: dict


class DashboardService:
    """Builds the bounded, cached read snapshots served to the desktop page."""

    def __init__(self, store, board, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = time.time, ttl: float | None = None,
                 limits: dict | None = None):
        self.store = store
        self.board = board
        self._clock = clock
        self._now = now
        self._ttl = float(LIMITS["snapshot_ttl_seconds"] if ttl is None else ttl)
        self._limits = {**LIMITS, **(limits or {})}
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.Lock()

    # ── cache ───────────────────────────────────────────────────────────────

    def _cached(self, key: str, build: Callable[[], dict]) -> tuple[dict, bool]:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and hit.expires > now:
                return hit.payload, True
        payload = build()
        with self._lock:
            self._cache[key] = _Cached(expires=now + self._ttl, payload=payload)
        return payload, False

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    # ── board helpers ───────────────────────────────────────────────────────

    def board_health(self) -> dict:
        probe = getattr(self.board, "health", None)
        if probe is None:
            return {"available": None, "code": "unknown", "detail": "no health probe"}
        try:
            return probe()
        except Exception as exc:  # a probe must never break the page
            return {"available": False, "code": "probe_failed", "detail": str(exc)[:200]}

    def _titles(self, task_ids: list[str]) -> dict[str, str]:
        """Card titles, when the board adapter can provide them (never required)."""
        reader = getattr(self.board, "titles", None)
        if reader is None:
            return {}
        try:
            found = reader(task_ids)
        except Exception:
            return {}
        if not isinstance(found, dict):
            return {}
        return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, str)}

    def _root_ids(self, dept_id: str) -> list[str]:
        getter = getattr(self.store, "tasks_by_role", None)
        if getter is not None:
            return list(getter(dept_id, "root"))
        return [task_id for task_id, role in self.store.tasks_for_dept(dept_id)
                if role == "root"]

    def _membership_of(self, task_id: str):
        """Stored explicit membership, when the M1 store publishes one."""
        getter = getattr(self.store, "membership", None)
        if getter is None:
            return None
        try:
            return getter(task_id)
        except Exception:
            return None

    def _binding_view(self, dept, task_id: str, role: str, snap, root_id: str | None,
                      root_snap) -> dict:
        """How this card is bound to the department, and whether that holds now.

        ``ownership_changed`` is the frozen v4.1 rebind signal: the recorded parent
        topology no longer matches the live board. The read model reports it;
        it never re-binds or guesses a new root.
        """
        if role == "root":
            ok = (task_id == root_id and snap.assignee == dept.manager_profile)
            return {
                "source": "root_binding",
                "state": "verified" if ok else "ownership_unverified",
                "rebind_required": False if ok else True,
                "detail": None if ok else "root card assignee is not the department manager",
            }

        stored = self._membership_of(task_id)
        if stored is not None:
            stored_root, parents = stored
            current = tuple(sorted(snap.parents))
            if current != tuple(parents):
                return {
                    "source": "explicit_membership",
                    "state": "ownership_changed",
                    "rebind_required": True,
                    "stored_root": stored_root,
                    "recorded_parents": list(parents),
                    "current_parents": list(snap.parents),
                    "detail": "parent topology changed; department_bind is required again",
                }
            if root_snap is None:
                return {
                    "source": "explicit_membership",
                    "state": "ownership_unknown",
                    "rebind_required": False,
                    "stored_root": stored_root,
                    "detail": "the bound root card is unreadable",
                }
            if stored_root != root_id or root_snap.assignee != dept.manager_profile:
                return {
                    "source": "explicit_membership",
                    "state": "ownership_unverified",
                    "rebind_required": False,
                    "stored_root": stored_root,
                    "detail": "root ownership or manager assignment no longer matches",
                }
            return {"source": "explicit_membership", "state": "verified",
                    "rebind_required": False, "stored_root": stored_root, "detail": None}

        # Legacy Store.bind_task association (M1 v2 keeps it, but revalidates
        # it against the live topology on every read).
        try:
            chain = self.board.ancestry(task_id)
        except Exception:
            return {
                "source": "legacy_bind_task",
                "state": "ownership_unknown",
                "rebind_required": False,
                "detail": "ancestry is unreadable",
            }
        bound_roots = [node for node in chain
                       if any(node in self._root_ids(d) for d in self.store.departments())]
        if len(bound_roots) > 1:
            return {
                "source": "legacy_bind_task",
                "state": "ownership_unknown",
                "rebind_required": False,
                "detail": "multiple bound roots in ancestry",
            }
        if root_id is None or root_id not in chain:
            return {
                "source": "legacy_bind_task",
                "state": "ownership_unverified",
                "rebind_required": False,
                "detail": "the department root is not an ancestor of this card",
            }
        if root_snap is None or root_snap.assignee != dept.manager_profile:
            return {
                "source": "legacy_bind_task",
                "state": "ownership_unverified",
                "rebind_required": False,
                "detail": "root ownership or manager assignment no longer matches",
            }
        return {"source": "legacy_bind_task", "state": "verified",
                "rebind_required": False, "detail": None}

    def _task_view(self, dept, task_id: str, role: str, *, title: str | None = None,
                   deliveries: list | None = None, root_id: str | None = None,
                   root_snap=None, snap=None, snap_loader=None) -> dict:
        """One task as the UI needs it; unreadable cards are explicit unknowns."""
        try:
            if snap is None:
                snap = snap_loader(task_id) if snap_loader is not None else self.board.get(task_id)
        except BoardError as exc:
            return {"task_id": task_id, "role": role,
                    "binding": {"source": "unknown", "state": "ownership_unknown",
                                "rebind_required": False, "detail": exc.kind},
                    "unknown": {
                "code": exc.kind, "detail": str(exc)[:self._limits["max_text"]]}}
        except Exception as exc:  # adapter failure: fail closed, never guess
            return {"task_id": task_id, "role": role,
                    "binding": {"source": "unknown", "state": "ownership_unknown",
                                "rebind_required": False, "detail": "unavailable"},
                    "unknown": {
                "code": "unavailable", "detail": str(exc)[:self._limits["max_text"]]}}

        events = sorted(snap.events, key=lambda e: (e.created_at, e.run_id or 0))
        last_event = events[-1] if events else None
        blocking = None
        if snap.status == "blocked" or any(e.kind == "blocked" for e in events):
            blocked_events = [e for e in events if e.kind == "blocked"]
            latest = blocked_events[-1] if blocked_events else None
            reason = None
            if latest is not None and isinstance(latest.payload, dict):
                for key in ("reason", "detail", "error", "message"):
                    reason = _text(latest.payload.get(key))
                    if reason:
                        break
            blocking = {
                "recorded": latest is not None,
                "reason": reason,
                "event_at": _int(latest.created_at) if latest is not None else None,
                "is_current": snap.status == "blocked",
            }

        binding = self._binding_view(dept, task_id, role, snap, root_id, root_snap)
        try:
            evidence = evidence_from_board(
                dept, snap, artifact_lookup=self.store.artifact_identity)
            gate = _gate_probe(dept, snap, evidence, self.store.artifact_identity)
            review = {
                "revision": evidence.revision,
                "verdict": evidence.verdict.kind if evidence.verdict else "NONE",
                "run_id": evidence.verdict.run_id if evidence.verdict else None,
                "reviewer": evidence.verdict.reviewer_profile if evidence.verdict else None,
                "artifact_sha256": evidence.artifact_sha256,
                # The only identity authority in v4.1 is the producer record;
                # this hash is worker-declared, not a reviewer byte check.
                "artifact_source": "department_submit (worker-declared SHA-256)",
                "note": (evidence.verdict.note if evidence.verdict else None) or None,
                "verdict_conflict": evidence.conflict,
                "repair_rounds": evidence.repair_rounds,
                "implementer": evidence.implementer_profile,
                "requested_reviewer": evidence.requested_reviewer,
                "review_request_run_id": evidence.review_request_run_id,
                "provenance_reason": evidence.provenance_reason or None,
                "prose_unproven": evidence.prose_unproven,
                "unbound_rejection": evidence.unbound_rejection,
                "declaration_conflict": evidence.declaration_conflict,
            }
            review.update(_review_state(evidence, gate))
        except Exception as exc:  # a malformed board must not fake a verdict
            review = {"verdict": "UNKNOWN", "error": str(exc)[:self._limits["max_text"]],
                      "state": "unknown", "held": True, "held_code": None,
                      "gate_status": "unknown", "ready_to_deliver": False}

        acceptance = None
        delivery = None
        delivered_identity = None
        row = None
        projection = _report_projection(None, state=None)
        try:
            acc = self.store.active_acceptance(dept.dept_id, task_id)
            if acc is not None:
                acceptance = {
                    "revision": acc.revision,
                    "artifact_sha256": acc.artifact_sha256,
                    "manager": acc.manager_profile,
                    # An acceptance of an older revision (or of a different
                    # artifact within the revision) must never make the current
                    # held revision look accepted.
                    "current": bool(
                        acc.revision == review.get("revision")
                        and acc.artifact_sha256 == review.get("artifact_sha256")
                        and acc.manager_profile == dept.manager_profile
                        and review.get("state") != "rejected"),
                }
            rows = [d for d in (deliveries or []) if d.task_id == task_id and d.kind == "final"]
            if rows:
                row = rows[-1]
                body = getattr(row, "body", None)
                delivered_identity = _delivery_identity(body)
                projection = _report_projection(body, state=row.state)
                action = {"confirmed": "delivered", "pending": "in_flight"}.get(
                    row.state, row.state)
                code = (_text(row.detail)
                        if row.state in ("unknown", "dead_letter") else None)
                if code in UNKNOWN_CODES and action == "dead_letter":
                    # Legacy pre-v5 effect exception: unknown, never dead-letter.
                    action = "unknown"
                delivery = {
                    # ``state`` stays the durable storage state (pending /
                    # confirmed / unknown / dead_letter) for compatibility;
                    # ``action`` is the projection onto the frozen delivery
                    # action set the UI speaks.
                    "state": row.state,
                    "action": action,
                    "storage_state": row.state,
                    "key": row.delivery_key,
                    "detail": _text(row.detail),
                    "attempts": row.attempts,
                    "delivered_identity": delivered_identity,
                    "identity_conflict": bool(
                        row.state == "confirmed"
                        and not _same_identity(delivered_identity,
                                               _current_identity(review, task_id))),
                    "code": code,
                    # ── v5 report projection (source: deliveries.body_json) ──
                    "report": projection["report"],
                    "report_state": projection["report_state"],
                    "report_code": projection["report_code"],
                    "report_schema": projection["report_schema"],
                    "report_generation": projection["report_generation"],
                    "report_bytes": projection["report_bytes"],
                    "summary": projection["summary"],
                    "delivered_at": projection["delivered_at"],
                    "delivered_at_iso": projection["delivered_at_iso"],
                }
        except StoreError as exc:
            delivery = {"state": "unknown", "detail": str(exc)[:self._limits["max_text"]],
                        "code": "state_unavailable", "identity_conflict": False,
                        "report": None, "report_state": "unavailable",
                        "report_code": None}

        delivery_readiness = _delivery_readiness(
            dept, review, binding, acceptance, row, delivered_identity, task_id)

        return {
            "task_id": task_id,
            "role": role,
            "title": _text(title),
            "status": snap.status,
            "assignee": snap.assignee,
            "updated_at": _int(last_event.created_at) if last_event is not None else None,
            "updated_at_iso": _iso(last_event.created_at) if last_event is not None else None,
            "last_event": ({"kind": last_event.kind, "at": _int(last_event.created_at)}
                           if last_event is not None else None),
            "blocked": blocking,
            "review": review,
            "binding": binding,
            "acceptance": acceptance,
            "final_report": delivery,
            "delivery_readiness": delivery_readiness,
        }

    def _manager_status(self, dept, root_id: str | None) -> dict:
        """Three independent manager facts, never collapsed into one light.

        * subscription: a read-only look at the board's notify-subscription
          table (the same data ``notify-list`` exposes);
        * owner: public board data cannot attest the runtime session owner, so
          this stays ``unverified`` per the M1 v2 contract;
        * wake: ``ensured`` only means a subscription was read back; it is not
          evidence that the manager woke.
        """
        status = {
            "profile": dept.manager_profile,
            "subscription": {
                "state": "unknown",
                "matching": 0,
                "foreign": 0,
                "wake_mode": False,
                "source": "kanban_notify_subs (read-only)",
                "detail": None,
            },
            "owner": {
                "state": "unverified",
                "code": "session_owner_unverified",
                "detail": "public notify-list cannot attest runtime session ownership",
            },
            "wake": {
                "state": "unconfirmed",
                "code": "ensured_not_awake",
                "detail": "a read-back subscription is not proof that the manager woke",
            },
            # Deliberately false: no single green light may imply all three.
            "verified": False,
        }
        if root_id is None:
            status["subscription"]["state"] = "missing"
            status["subscription"]["detail"] = "no root card is bound"
            return status
        reader = getattr(self.board, "subscriptions", None)
        if reader is None:
            status["subscription"]["detail"] = "board adapter has no subscription reader"
            return status
        try:
            rows = reader(root_id)
        except Exception as exc:
            status["subscription"]["detail"] = type(exc).__name__
            return status
        if not isinstance(rows, list):
            status["subscription"]["detail"] = "subscription read returned no list"
            return status
        matching = [r for r in rows if isinstance(r, dict)
                    and r.get("notifier_profile") == dept.manager_profile
                    and r.get("platform") == dept.manager_platform
                    and r.get("chat_id") == dept.manager_chat_id]
        status["subscription"]["matching"] = len(matching)
        status["subscription"]["foreign"] = max(0, len(rows) - len(matching))
        status["subscription"]["wake_mode"] = bool(matching) and all(
            isinstance(r.get("delivery_mode"), str) and "wake" in r["delivery_mode"]
            for r in matching)
        if not rows:
            status["subscription"]["state"] = "missing"
        elif len(matching) > 1:
            status["subscription"]["state"] = "duplicate"
        elif len(matching) == 1:
            status["subscription"]["state"] = "subscribed"
        else:
            status["subscription"]["state"] = "foreign_only"
        if status["subscription"]["state"] == "subscribed" and not status["subscription"]["wake_mode"]:
            status["subscription"]["detail"] = "matching subscription is not wake-capable"
        return status

    def _membership_summary(self, bindings: list[dict], unknown_count: int) -> dict:
        states = [b.get("state") for b in bindings]
        if any(state == "ownership_changed" for state in states):
            state = "ownership_changed"
        elif any(state == "ownership_unverified" for state in states):
            state = "ownership_unverified"
        elif any(state == "ownership_unknown" for state in states) or unknown_count:
            state = "ownership_unknown"
        elif bindings:
            state = "verified"
        else:
            state = "unbound"
        return {
            "state": state,
            "rebind_required": any(bool(b.get("rebind_required")) for b in bindings),
            "explicit_bindings": sum(1 for b in bindings if b.get("source") == "explicit_membership"),
            "legacy_bindings": sum(1 for b in bindings if b.get("source") == "legacy_bind_task"),
            "source": "store membership bindings / explicit department_bind task_ids",
        }

    def _delivered_reports(self, deliveries: list) -> list[dict]:
        """Historical plugin-persisted reports, independent of the board.

        Built from ``deliveries.body_json`` only: a card whose board read fails
        (or whose later generation is rejected) keeps its committed report
        readable. Oversized rows are listed with ``report_too_large`` instead of
        their content. Bounded by the per-department task cap.
        """
        cap = int(self._limits["max_tasks_per_department"])
        out: list[dict] = []
        for row in deliveries:
            if getattr(row, "kind", None) != "final" or row.state != "confirmed":
                continue
            projection = _report_projection(getattr(row, "body", None), state=row.state)
            if projection["report_state"] not in ("ok", "report_too_large"):
                continue
            out.append({
                "task_id": row.task_id,
                "identity": _delivery_identity(getattr(row, "body", None)),
                "report": projection["report"],
                "report_state": projection["report_state"],
                "report_code": projection["report_code"],
                "delivered_at": projection["delivered_at"],
                "delivered_at_iso": projection["delivered_at_iso"],
                "summary": projection["summary"],
                "report_schema": projection["report_schema"],
                "report_generation": projection["report_generation"],
                "report_bytes": projection["report_bytes"],
            })
            if len(out) >= cap:
                break
        return out

    def _department_view(self, dept, *, include_tasks: bool) -> dict:
        pairs = self.store.tasks_for_dept(dept.dept_id)
        cap = int(self._limits["max_tasks_per_department"])
        visible = pairs[:cap]
        titles = self._titles([task_id for task_id, _ in visible])
        root_id = self.store.root_task(dept.dept_id)
        snaps: dict[str, object] = {}

        def load_snap(tid: str):
            if tid not in snaps:
                snaps[tid] = self.board.get(tid)
            return snaps[tid]

        root_snap = None
        if root_id is not None:
            try:
                root_snap = load_snap(root_id)
            except Exception:
                root_snap = None
        try:
            deliveries = self.store.deliveries_for_dept(dept.dept_id)
        except StoreError:
            deliveries = []
        delivered_reports = self._delivered_reports(deliveries)
        tasks = [self._task_view(dept, task_id, role, title=titles.get(task_id),
                                 deliveries=deliveries, root_id=root_id, root_snap=root_snap,
                                 snap_loader=load_snap)
                 for task_id, role in visible]
        unknowns = [{"task_id": t["task_id"], "code": t["unknown"]["code"],
                     "detail": t["unknown"]["detail"]}
                    for t in tasks if "unknown" in t]
        # The bound root card is the department's report container, not a unit
        # of work: an open root must not make a finished department look
        # "queued". Roll up over work-role cards when any exist; a root-only
        # department still reports the root's own status.
        work_ids = [task_id for task_id, role in visible if role != "root"]
        rollup_ids = work_ids or [task_id for task_id, _ in visible]
        by_id = {t["task_id"]: t for t in tasks}
        readable = [by_id[task_id] for task_id in rollup_ids
                    if task_id in by_id and "unknown" not in by_id[task_id]]

        by_status: dict[str, int] = {}
        for task in readable:
            by_status[task["status"]] = by_status.get(task["status"], 0) + 1

        stamped = [t["updated_at"] for t in readable if t["updated_at"]]
        last_activity = max(stamped) if stamped else None

        if not readable:
            work_state = "unknown"
        elif any(t["status"] == "blocked" for t in readable):
            work_state = "blocked"
        elif any(t["status"] == "review" for t in readable):
            work_state = "review"
        elif any(t["status"] == "running" for t in readable):
            work_state = "running"
        elif all(t["status"] in _TERMINAL for t in readable):
            work_state = "done"
        else:
            work_state = "queued"

        finals = [(t["task_id"], t["final_report"]) for t in readable if t.get("final_report")]
        final_states = [f.get("action") or f.get("state") for _tid, f in finals]
        delivered = [(tid, f) for tid, f in finals if (f.get("action") or f.get("state")) == "delivered"]
        # Unknown/uncertain delivery must never be flattened into "pending" or
        # "delivered": M1 deliberately removed a blind retry path. Precedence is
        # unknown > dead_letter > in_flight > delivered.
        if any(state == "unknown" for state in final_states):
            report_state = "unknown"
            report_code = next((f.get("code") or f.get("detail") for _tid, f in finals
                                if f.get("state") == "unknown"), None)
        elif any(state == "dead_letter" for state in final_states):
            report_state = "dead_letter"
            report_code = next((f.get("code") or "dead_letter" for _tid, f in finals
                                if f.get("state") == "dead_letter"), None)
        elif any(state == "in_flight" for state in final_states):
            # ``pending`` is the durable storage name M1 publishes for a claimed
            # intent that has not been read back; it is not "delivered".
            report_state, report_code = "pending", "in_flight_unknown"
        elif delivered:
            report_state, report_code = "delivered", None
        else:
            report_state, report_code = "none", None
        final_report = {
            "state": report_state,
            "code": report_code,
            "count": len(finals),
            "delivered_count": len(delivered),
            "unknown_count": sum(1 for state in final_states if state == "unknown"),
            "identity_conflicts": sum(1 for _tid, f in finals if f.get("identity_conflict")),
            "reports_available": sum(1 for _tid, f in finals if f.get("report")),
            "report_too_large": sum(1 for _tid, f in finals
                                    if f.get("report_code") == "report_too_large"),
            "latest_task_id": (delivered[-1][0] if delivered else
                               (finals[-1][0] if finals else None)),
            # Historical reports are read from ``deliveries.body_json``, so they
            # stay listed even when the board read for that card failed or a new
            # review generation later rejects. Bounded to the task cap.
            "history": delivered_reports if include_tasks else [],
            "history_count": len(delivered_reports),
        }

        held_codes: dict[str, int] = {}
        delivery_held_codes: dict[str, int] = {}
        for task in readable:
            review = task.get("review") or {}
            if review.get("held"):
                code = review.get("held_code") or "held"
                held_codes[code] = held_codes.get(code, 0) + 1
            readiness = task.get("delivery_readiness") or {}
            if readiness.get("state") == "held":
                code = readiness.get("code") or "held"
                delivery_held_codes[code] = delivery_held_codes.get(code, 0) + 1

        view = {
            "id": dept.dept_id,
            "name": dept.name,
            "active": dept.active,
            "manager": dept.manager_profile,
            "manager_status": self._manager_status(dept, root_id),
            "membership": self._membership_summary(
                [t["binding"] for t in tasks if isinstance(t.get("binding"), dict)],
                len(unknowns)),
            "members": list(dept.members),
            "reviewers": list(dept.reviewers),
            "max_repair_rounds": dept.max_repair_rounds,
            "root_task_id": root_id,
            "task_count": len(pairs),
            "tasks_truncated": len(pairs) > len(visible),
            "work": {
                "state": work_state,
                "state_source": "derived from Kanban task statuses",
                "scope": "work_tasks" if work_ids else "all_tasks",
                "items_considered": len(readable),
                "by_status": by_status,
                "blocked": by_status.get("blocked", 0),
                "awaiting_review": by_status.get("review", 0),
                "review_held": sum(held_codes.values()),
                "held_codes": held_codes,
                "delivery_held": sum(delivery_held_codes.values()),
                "delivery_held_codes": delivery_held_codes,
                "last_activity_at": last_activity,
                "last_activity_at_iso": _iso(last_activity),
            },
            "final_report": final_report,
            "unknown_tasks": len(unknowns),
        }
        if include_tasks:
            view["tasks"] = tasks
            view["unknown_task_details"] = unknowns
        return view

    def department_ids(self) -> list[str]:
        cap = int(self._limits["max_departments"])
        return self.store.departments()[:cap]

    # ── public views ────────────────────────────────────────────────────────

    def list_departments(self) -> dict:
        def build() -> dict:
            ids = self.department_ids()
            departments: list[dict] = []
            degraded: list[dict] = []
            for dept_id in ids:
                dept = self.store.get_department(dept_id)
                if dept is None:
                    degraded.append({"code": "department_missing", "department": dept_id})
                    continue
                try:
                    departments.append(self._department_view(dept, include_tasks=False))
                except Exception as exc:
                    degraded.append({"code": "department_unreadable", "department": dept_id,
                                     "detail": str(exc)[:self._limits["max_text"]]})
            health = self.board_health()
            if health.get("available") is False:
                degraded.append({"code": "board_unavailable", "detail": health.get("detail", "")})
            return {
                "schema": "department-hub/dashboard/v1",
                "contract": {"version": CONTRACT_VERSION},
                "source": "kanban",
                "read_only": True,
                "generated_at": int(self._now()),
                "generated_at_iso": _iso(self._now()),
                "limits": {k: self._limits[k] for k in
                           ("max_departments", "max_tasks_per_department", "snapshot_ttl_seconds")},
                "board": health,
                "departments": departments,
                "degraded": degraded,
            }
        return self._cached("departments", build)[0]

    def department(self, dept_id: str) -> dict:
        dept = self.store.get_department(dept_id)
        if dept is None:
            raise KeyError(dept_id)

        def build() -> dict:
            view = self._department_view(dept, include_tasks=True)
            return {
                "schema": "department-hub/department/v1",
                "contract": {"version": CONTRACT_VERSION},
                "source": "kanban",
                "read_only": True,
                "generated_at": int(self._now()),
                "generated_at_iso": _iso(self._now()),
                "board": self.board_health(),
                "department": view,
                "events": self._durable_events(dept.dept_id, 20),
                "store": self.store.snapshot(),
            }
        return self._cached(f"department:{dept_id}", build)[0]

    def _durable_events(self, dept_id: str, limit: int) -> list[dict]:
        cap = max(1, min(int(limit), int(self._limits["max_events"])))
        rows: list[dict] = []
        try:
            conn = self.store.connection
            for row in conn.execute(
                "SELECT event_id, task_id, kind, run_id, created_at, state, detail FROM events"
                " WHERE dept_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (dept_id, cap),
            ):
                rows.append({
                    "event_id": row["event_id"], "task_id": row["task_id"],
                    "kind": row["kind"], "run_id": row["run_id"],
                    "at": _int(row["created_at"]), "state": row["state"],
                    "detail": _text(row["detail"]),
                })
        except Exception:
            return []
        return rows

    def events(self, dept_id: str, limit: int = 20) -> dict:
        if self.store.get_department(dept_id) is None:
            raise KeyError(dept_id)
        cap = max(1, min(int(limit), int(self._limits["max_events"])))
        return {"schema": "department-hub/events/v1", "department": dept_id,
                "limit": cap, "events": self._durable_events(dept_id, cap)}

    # ── department creation / editing ───────────────────────────────────────

    def plan_department(self, payload: dict) -> dict:
        """Validate a department declaration and return an operator plan.

        Validation is real (the same :class:`Department` constructor M1 uses);
        writing is deliberately *not* offered here.
        """
        if not isinstance(payload, dict):
            raise ConfigError("request body must be an object")
        candidate = {
            "id": payload.get("id"),
            "name": payload.get("name"),
            "manager": payload.get("manager") if isinstance(payload.get("manager"), dict)
            else {"profile": payload.get("manager_profile"),
                  "platform": payload.get("manager_platform") or "api_server",
                  "chat_id": payload.get("manager_chat_id") or "operator-supplied"},
            "members": payload.get("members"),
            "reviewers": payload.get("reviewers"),
            "max_repair_rounds": payload.get("max_repair_rounds", 2),
            "active": payload.get("active", True),
        }
        dept = Department.from_mapping(candidate)
        return {
            "schema": "department-hub/plan/v1",
            "valid": True,
            "department": {
                "id": dept.dept_id,
                "name": dept.name,
                "manager": dept.manager_profile,
                "members": list(dept.members),
                "reviewers": list(dept.reviewers),
                "max_repair_rounds": dept.max_repair_rounds,
                "active": dept.active,
            },
            "config_snippet": self._config_snippet(candidate),
            "operator_steps": self.operator_steps(dept.dept_id),
            "note": ("validated only; nothing was written. The desktop surface cannot "
                     "edit config.yaml or install profiles."),
        }

    @staticmethod
    def _config_snippet(candidate: dict) -> str:
        import json
        body = {
            "id": candidate["id"],
            "name": candidate["name"],
            "manager": {
                "profile": candidate["manager"]["profile"],
                "platform": candidate["manager"].get("platform", "api_server"),
                "chat_id": "<operator-supplied chat id>",
            },
            "members": candidate["members"],
            "reviewers": candidate["reviewers"],
            "max_repair_rounds": candidate.get("max_repair_rounds", 2),
        }
        return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def operator_steps(dept_id: str) -> list[str]:
        return [
            "Edit config.yaml on the Hermes host: plugins.entries.department-hub.settings.departments",
            "Add the department object shown in config_snippet and set the real manager chat_id",
            "Confirm the manager/member/reviewer profiles already exist; M2 does not create profiles",
            "Restart the gateway so the plugin config is re-read, then verify with department_status",
            f"Bind a board-verified root card assigned to the manager: department_bind department={dept_id}",
        ]

    @staticmethod
    def mutation_refusal(action: str) -> dict:
        return {
            "schema": "department-hub/refusal/v1",
            "blocked": True,
            "code": MUTATION_CODE,
            "action": action,
            "reason": ("Creating, editing or deleting a department changes config.yaml and requires "
                       "profile installation. This build has no safe, reversible path for that from "
                       "the desktop surface, and profile binding is not a security boundary."),
            "what_you_can_do": [
                "Validate the declaration with the read-only plan endpoint",
                "Apply the printed config snippet as the Hermes operator",
                "Use department_status / department_bind to verify the live binding",
            ],
            "not_attempted": ["config write", "profile install", "gateway restart", "board write"],
        }

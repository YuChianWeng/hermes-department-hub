# M1-v2 contract tightening / caller migration

> **Historical (v2). Superseded by [`m1-v5-contract.md`](m1-v5-contract.md).**
> v5 final delivery is a plugin-owned immutable report, not a board comment; any old final effect/fencing description below is deprecated.
> Anything below that makes a board `metadata.artifact_sha256` the artifact
> identity is v2-only; v4 accepts identity **only** from `department_submit`.
> Kept for migration history, not as an operator instruction.

This is an isolated, not-installed implementation candidate. Independent review
is still required. M2 files are preserved, not declared accepted by this change.

## Public contract changes

| Path / entry | Field / contract | Error / held result | Old caller behavior |
|---|---|---|---|
| `department_lib/acceptance.py:evidence_from_board` | Worker request run must have `outcome=review_requested`, the published implementer profile, and `metadata.artifact_sha256`; request must name a configured reviewer | `no_pass_verdict` / `artifact_unbound` | Missing provenance no longer implies a PASS |
| same | Current run after the latest request must be that reviewer, have one `claimed` event at/after the request with `payload.source_status=review`, and terminate with an explicit verdict | `no_pass_verdict` | A completed worker/ready-lane run is not a review |
| same | `metadata.verdict` exactly `PASS` or `REQUEST_CHANGES`; alternative whole summary tokens: `PASS`, `REVIEW: PASS`, `REQUEST_CHANGES`, `REVIEW: REQUEST_CHANGES`. Structured PASS summary must be empty or exactly a canonical PASS token | `verdict_conflict` / `review_rejected` / `no_pass_verdict` | Free-text positive summaries alone no longer pass; structured PASS plus noncanonical prose is held conservatively |
| same / `is_sha256` | Worker submission SHA, reviewer SHA, optional request payload SHA, and manager claim must agree; exact 64 lowercase hex, no whitespace | `artifact_unbound` / `artifact_mismatch` | Reviewer-only SHA is insufficient; newline/uppercase/space rejected |
| `department_lib/kanban_cli.py:notify_subscribe` | Required keyword `notifier_profile`, emitted as `--notifier-profile` | Python missing-keyword `TypeError` for direct old library calls | Direct callers must supply target profile; public department tools supply it automatically |
| `department_lib/routing.py:Router.ensure` | Require a verified `task_departments[task_id]`; read subscription profile, session-target `chat_id`, platform, and mode; no public runtime-session attestation | `ownership_unknown`, `session_owner_unverified`, `foreign_subscription`, `mode_mismatch`, `subscribe_unverified` | Correct subscription can be listed in `ensured`, but `ok=False` remains; never infer actual manager wake |
| `department_lib/orchestrator.py:bind_department` / `department_bind` | `task_ids` explicitly declare membership independently of dependency edges. Store root plus parent-topology fingerprint; verify board reads, registered roots and manager ownership | `ownership_unverified` / routing `ownership_unknown`; `membership_changed` on incompatible rebind | Unknown descendants are not auto-adopted. Movement requires explicit migration; no first-root fallback |
| same / `accept`, `release`, `ingest`, `escalate` | Cached ownership is revalidated. Legacy `Store.bind_task` is a trusted explicit association, not automatic dependency adoption | `ownership_unverified`; ingest `board_status='held: ownership_unverified'` | No stale cache authorization after movement/unreadable or ambiguous topology |
| `department_lib/board.py:Board.get` | Optional `events[].id`, string/integer only, preserved as `BoardEvent.source_id`; duplicate run IDs rejected | `BoardError(kind='invalid')` | No source ID: coordinate identity retained conservatively. Payload hash is not an identity |
| `department_lib/delivery.py:release_final`, `__init__.py:_handle_deliver` | New additive `delivered_identity` result: actual prior revision/verdict/root/state when identity differs | `held / delivery_identity_conflict`, `success=False` | A different second release is not `already_delivered`; no second append |
| `department_lib/delivery.py:reconcile` | Pending/unknown plus authoritative absence remains unknown without upstream fencing | `unknown / in_flight_unknown` | No automatic retry after an uncertain attempt; intentional safety-over-liveness tradeoff |
| `__init__.py:_SCHEMAS.department_escalate`, `_handle_escalate`, `delivery.validate_escalation` | `reason_code` enum: `blocked`, `repair_loop_exhausted`, `manager_unreachable`; detail absent/empty or `{round: integer 0..1000}` only; bool is not integer | `success=False, error=escalation_reason_invalid` / `escalation_detail_invalid`; direct library raises `ValueError` | Unknown detail keys and invalid values are rejected before any write. No silent redaction-and-success |

## Internal persistence changes

* New plugin-owned `membership_bindings(task_id, root_task_id, parents_json)` table.
* Nullable `verdicts.request_run_id` column, additive migration on open, scopes
  acceptance supersession to its review request and authorized reviewer/run/revision.
* `Store.transaction` serializes writers through an `RLock`; observation,
  disposition, effects and receipts share a transaction. Conflicts remain sticky.
* `Store.claim_delivery` atomically inserts the unique durable intent and returns
  ownership only to the inserting caller. No ownership transfer/resend on absence.
* Existing superseded/revoked acceptance IDs raise a domain error instead of a
  `NoneType` crash. Public accept maps that to `held / acceptance_not_active`.
* Current-review quarantined events yield `event_quarantined`; older quarantine
  can be fenced by a fresh review request, not replayed as a clean duplicate.

## Verification compatibility

The reviewer originals assert exploit success, so unchanged originals exit zero
on vulnerable v1 and nonzero on v2. The separately shipped safety probes invert
unsafe assertions, enrich required positive controls, and remove a two-append
barrier that would deadlock a correct single claimant. Their exact diffs and
same-byte v1 RED / v2 GREEN logs are in the evidence bundle.

The original 160 test IDs are retained. Fixtures now publish real synthetic
request artifact declarations and review-lane claims. Assertions expecting old
unsafe semantics (wake inferred from chat, retry on uncertain absence, raw detail,
reviewer-only artifact identity) are deliberately migrated. This is **not** a
claim that the unchanged original suite passes: it yields 113 passed / 47 failed
on v2. Migrated 160 IDs and the full candidate suite pass. The independent reviewer
must adjudicate those migrations; a test name or worker completion is not approval.

## Limits / M2 alignment

M2 must preserve held/unknown and new failure codes, handle additive identity
results, and never display `ensured` as verified wake. This card does not change
M2 dashboard/API/desktop source. Membership migration is not a new public tool.
Artifact identity is independently checked against the worker's authoritative
submission declaration; the delivery layer does not read/re-hash artifact bytes.
Session ownership cannot be attested by notify-list. `Delivery.reconcile` still
has no public tool entry; `kanban_effects.py` malformed comment rows still can be
classified absent (P2), but absence no longer triggers resend. Synthetic evidence
is not a live gateway or production privacy guarantee.

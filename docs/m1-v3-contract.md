# M1 v3 frozen candidate contract

> **Historical (v3). Superseded by [`m1-v5-contract.md`](m1-v5-contract.md).**
> v5 final delivery is a plugin-owned immutable report, not a board comment; any old final effect/fencing description below is deprecated.
> `not_attempted` here was already unreachable in M1; v4 removed the name and
> took artifact identity from the producer only.

This document supersedes `m1-v2-contract.md` for the v3 candidate only. No production migration is authorized.

## Producer compatibility and authority

| Dimension | v2 | v3 |
|---|---|---|
| Rejection | structured/canonical verdict required | `outcome=changes_requested` from the requested reviewer, with exactly one review-lane claim in the current request generation; metadata/summary/SHA absence cannot erase rejection |
| PASS | structured/canonical PASS token | `outcome=completed`, current requested reviewer and review-lane claim, exact submission/reviewer/manager SHA binding, no authoritative rejection in that generation; structured verdict, when supplied, is an additional constraint |
| Prose | token-based authority | prose never grants authority; explicit contradictory negative tokens can only tighten a positive gate |
| Rejection persistence | superseded existing acceptance only | durable `review_rejections(task_id,request_run_id,run_id,event_id)` and monotonic `review_generations`; stale snapshots cannot resurrect PASS |
| Conflicting event IDs | timestamp-filtered stored rows | current snapshot + existing source identity checked before accept/release; durable quarantine keyed by request run, no timestamp exclusion; unknown historical ownership remains conservatively sticky |

A rejection closes its request generation. A fresh request with a new run ID is required to recover. Authoritative rejection is reported as `review_rejected`, except that an exhausted repair budget is the stronger `repair_loop_exhausted` hold. A stale manager revision never turns rejection into unreviewed.

The artifact field at ReviewEvidence top level means verified common binding, not untrusted reviewer extraction. A mismatch makes that field null while VerdictEvidence retains the reviewer SHA. Missing/malformed identity never releases. Source text and adapter profile fields are trusted board observations, not cryptographic attestation or rehashing of artifact bytes.

## Ownership

Explicit membership persists sorted complete adjacency (`membership_bindings.ancestry_json`), including the associated root's ancestry even when work has no dependency edge to that root. Every access rereads the full graph, detects cycles, missing nodes, multiple registered roots and changed intermediate edges. Changed evidence -> `ownership_changed`; unreadable/unmigrated evidence -> `ownership_unknown`. Existing membership rows without fingerprint remain held; no guessed automatic rebind. Non-explicit legacy bindings still require one verified root or a verified flat binding when no roots exist. Root resolution uses the same full traversal, never the old depth-8 fallback. Bounds: depth 64, at most 1024 visited nodes; exceeding either is UNKNOWN.

## Effect fencing and durability

Service release is two-phase:
1. In `BEGIN IMMEDIATE`, reread current board, full ownership, current department policy, monotonic request/rejection ledger, current acceptance and exact identity; conditionally claim the unique durable final intent. Commit before effect.
2. In a new `BEGIN IMMEDIATE`, revalidate those same conditions and request generation. Hold the local ingest writer fence through the synthetic/local effect. Only phase 1's inserting caller can execute phase 2. If authorization changed after claim, leave terminal `dead_letter/authorization_revoked`, no append.

An outer transaction cannot make the intent independently durable, so effect entry points return `held/effect_transaction_nested`. Restart never retries an uncertain/pending sender. SIGKILL after append retains the committed intent; found readback reconciles without another append.

**Architecture limit: remote board state and a remote comment cannot be fenced by this plugin's SQLite transaction.** The stock `CommentEffectPort` has `requires_upstream_fence=True`; service final release is `held/effect_fencing_unavailable`, with zero comment attempt/intent. This is intentionally unavailable, not a distributed exactly-once claim. Making stock final delivery live requires an upstream conditional-effect/generation capability or a redesigned shared authority boundary; repeatedly adding rereads is not a solution. Direct Delivery is a trusted low-level adapter API, not board authorization. Arbitrary Python adapters are not sandboxed.

## Escalation and diagnostics

The once-per-(department,task,reason) key is retained, but the complete body (including detail and target root) is compared. Identical replay is already_delivered; different allowed round/target is `held/delivery_identity_conflict`, `success=False`, with actual stored body and state in `delivered_identity`. Orphan escalation records with conflicting detail are `escalation_identity_conflict`, not success. Allowed detail remains only optional integer round 0..1000; unknown/nested/private fields fail before append attempts, delivery intent or escalation row. This comparison contract is deliberate rejection of updates, not an update API.

## SQLite additions / migration boundary

Additive tables: `review_quarantines`, `review_generations`, `review_rejections`; additive nullable `membership_bindings.ancestry_json`. Existing v2 `verdicts.request_run_id` remains. Only isolated copies were exercised. Old membership fingerprints are not inferred. Historic conflicts and uncertain intents sacrifice liveness rather than clear themselves. Rollback to old code is not authorized against upgraded production DBs; use an isolated schema/data copy and independently review migration first.

## M2 state set (must align before integration)

- accepted = exact active manager acceptance of current valid review; does not imply final delivery.
- held: review_rejected, no_pass_verdict, artifact_unbound, artifact_mismatch, reviewer_not_independent, reviewer_not_requested, implementer_not_member, verdict_conflict, verdict_not_final, stale_verdict, revision_mismatch, repair_loop_exhausted.
- held: ownership_unknown, ownership_changed, board_unknown, event_quarantined, review_generation_stale, department_changed, inactive_department, manager_mismatch.
- held: acceptance_missing, acceptance_stale, acceptance_not_active, effect_fencing_unavailable, effect_transaction_nested, delivery_identity_conflict, escalation_identity_conflict.
- final delivery: not_attempted / pending / unknown / confirmed / dead_letter; unknown must stay unknown in aggregation and must not offer blind retry.
- routing: subscription ensured is not runtime manager wake; session_owner_unverified and foreign_subscription remain visible.

M2 implementation is not modified or accepted by this handoff. Its existing read model still has known aggregate unknown/pending and source-ID alignment work. New contract tests cover raw missing-provenance and real rejection presentation, not Desktop rendering.

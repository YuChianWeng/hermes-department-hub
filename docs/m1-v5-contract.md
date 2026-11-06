# Department Hub M1 v5 — plugin-owned immutable delivery

Contract: `department-hub/m1-contract/v5` (candidate; independent review pending).
This supersedes v4.1 **delivery** semantics. The seven tool names, input parameter
schemas, producer rules, review authority and acceptance gate are unchanged.
The output contract is intentionally versioned: final report v2; status gains
per-task delivery and report fields. It is not wire-identical to v4.1.

## 1. What delivery means

`department_deliver` commits one **structured final acceptance report** into the
plugin-owned SQLite `deliveries.body_json`. This JSON is the final report content,
not a filename or a receipt referring to an external write. It includes the
accepted artifact identity, reviewer verdict, manager acceptance and summary.
It is **not** a copy of arbitrary submitted artifact bytes; `department_submit`
can accept binary files. No deliver-time file read or file rename is required.

**`delivered` is reachable with the stock service/CommentEffectPort. Final delivery
never invokes `append` or `find`, and never writes a board comment.** The port
remains in the service solely for escalation (unchanged) and read-only recovery
of deprecated pre-v5 remote intents. There is no final remote-append branch.
`effect_fencing_unavailable` is removed from executable code and current reasons.

A success returns `action="delivered"`, a stable `key`, and `delivered_identity`.
Repeat of the same identity returns `already_delivered` without a write, including
a retry after commit followed by SIGKILL. An existing report is a historical fact:
same-identity retry is still `already_delivered` if later reviews reject or the
department is deactivated. This does not authorize any new report.

## 2. Atomic local fence and precedence

One `BEGIN IMMEDIATE` transaction performs live-board/ownership revalidation,
active department/policy validation, exact **active** acceptance + PASS verdict
validation, exact equality with `review_generations.request_run_id`, rejection /
durable-quarantine checks, and producer identity resolution, then INSERTs a
**confirmed** report. Durable quarantine covers both the `review_quarantines` row
for the current generation **and** any `events.state='conflict'` row for the task
(a payload conflict under identical board coordinates). Both are sticky: a later
identical board payload does not clear them.

`Delivery.release_final` — the direct library boundary — executes the same local
fences, including that durable event-conflict check, inside the same writer
transaction, so a forged GateResult alone is insufficient and a direct caller
cannot deliver around an ingested conflict. It reads local tables only: live-board
ownership is validated by `DepartmentService.authorize()`, so ownership evidence
requires `DepartmentService.release()`. `synchronous=FULL`, WAL, busy timeout
15 seconds.

Checks and INSERT are serialized with ingest/accept/policy writers on independent
connections. Advisory checks performed before the transaction are not authority.
If rejection/generation advancement commits after an advisory acceptance read but
before this transaction, delivery holds with **no report row**. If the final writer
already owns the lock, ingest waits and is ordered *after* report commit; it cannot
both commit between the locked check and INSERT. No distributed atomicity with
un-ingested remote board changes is claimed.

Rejected in-transaction authorization rolls back its observations too. Deliver
preflight authority checks are read-only. No pending/dead-letter intent is created
for a new v5 held final. `effect_transaction_nested` still rejects composition in
an outer transaction (a caller rollback must not erase a reported success).

Existing final report lookup precedes new authorization: an exact match on the
four canonical identity fields (§3) -> `already_delivered`; different identity ->
`held/delivery_identity_conflict` with
**the actual stored** identity. Before any report exists an unauthorized different
identity can instead be `acceptance_stale` / another gate hold; it cannot win just
because it arrived first. This is required by the fence.

## 3. Identity, uniqueness, immutability, crash safety

```json
{"task_id":"work","revision":1,"verdict_run_id":6,"artifact_sha256":"<64 hex>"}
```

Identity equality uses exactly these four fields (not summary, root, timestamps).
`UNIQUE(task_id) WHERE kind='final'` is stronger than `(task_id,generation)` and
prevents a second final across departments or generations. A fresh PASS + accept
after delivery **must** conflict, never overwrite. Existing unknown/dead-letter
legacy rows reserve the task too; do not silently convert them into local delivery.

SQL triggers forbid UPDATE, DELETE and replacement INSERT of v2 final rows, and
reject v2 inserts that exceed the byte limit or are not `confirmed`. Application
methods never mutate these rows. This protects against ordinary SQL mistakes,
not a hostile database owner who can drop triggers or edit the database file.

Before COMMIT SIGKILL -> rollback, zero report rows, safe retry. After COMMIT
SIGKILL -> reopened status reports `delivered`, retry `already_delivered`.
The authoritative content is entirely in DB. No report files or export copies are
needed; any future export must be reconstructible/best-effort only.

Schema upgrade uses idempotent DDL. Historical databases with multiple final rows
for the same task fail the new unique index rather than deleting/choosing a report;
operator adjudication is required before upgrade. No production migration was run.

## 4. Report JSON and bounds

`deliveries.body_json` is UTF-8 JSON, **maximum 65,536 bytes** (including envelope).
Oversize -> `held/report_too_large`, rollback, no report. Summary is at most 4,096
Unicode characters, derived from the already bounded review note (currently 400),
with `Accepted report` fallback. Bound applies to the whole report, not just summary.
Status returns the stored full JSON without truncating/reconstructing it.

```json
{
  "schema":"department-hub/final/v2",
  "department":"dev", "task_id":"work", "root_task_id":"root",
  "status":"accepted", "revision":1, "generation":5,
  "verdict":{"kind":"PASS","run_id":6,"reviewer":"reviewer",
             "artifact_sha256":"<64 hex>"},
  "acceptance":{"manager":"manager"}, "unresolved_risks":[],
  "delivered_at":1793939000.0,
  "summary":"Accepted report",
  "report_kind":"accepted_artifact_identity"
}
```

`delivered_at` is UTC Unix seconds (float, commit-time report construction), not a
local timezone string and not a reviewer timestamp. Root is context only; no
comment is posted there. Empty unresolved_risks means this structured report has
no extra risk entries, not proof the artifact has no risks. Summary is untrusted
plain text for UI rendering; never render it as HTML.

## 5. Coordinator reads via department_status

Call `department_status({"department":"dev"})`. The registered tool response is
`{"success":true,"status":{...}}`. Every bound task has:

```json
{"task_id":"work","role":"work","delivery":{
  "action":"delivered","reason":"",
  "identity":{"task_id":"work","revision":1,"verdict_run_id":6,
              "artifact_sha256":"<64 hex>"},
  "report":{"schema":"department-hub/final/v2","...":"full stored JSON"}
}}
```

`status.deliveries[]` retains old `key/task_id/kind/state/detail` fields, adds
`identity` and `report` for final records. `state=confirmed` maps to public
`action=delivered`. Escalation entries have null identity/report. A v5 report stays
readable without board availability and after a later rejection/new generation.

For an undelivered task `delivery.action="held"`, `reason` is the current gate /
local-fence hold; when all checks pass but delivery has not been invoked it is
`not_delivered` (status-only reason, **not** a new release action). No receipt is
fabricated by status. Board read failure -> `unknown/board_unknown`. Legacy final
pending/unknown -> `unknown/prior_effect_unknown`; legacy terminal rows ->
`dead_letter` plus bounded stored detail. Legacy v1 reports are not exposed as v2
JSON (`report=null`). Deliver calls return `already_delivered`; a read-only snapshot
normalizes the durable fact to `delivered`, it does not persist the last call action.
Status reports durable delivered identity even if a later requested identity would
conflict. The mutation response is where `held/delivery_identity_conflict` and
`delivered_identity` identify that attempted identity conflict.

## 6. Current actions and reason codes

Actions remain `delivered / already_delivered / held / unknown / dead_letter`.
`evaluate()` gate reasons (set, **not precedence order**), producer validation,
review authority, and durable accept reasons remain as in historical v4.1 §2–4.2:

- Gate: `inactive_department`, `manager_mismatch`, `review_rejected`,
  `repair_loop_exhausted`, `reviewer_not_independent`, `reviewer_not_requested`,
  `implementer_not_member`, `rejection_unbound`, `verdict_conflict`,
  `review_unproven`, `revision_mismatch`, `no_pass_verdict`, `stale_verdict`,
  `verdict_not_final`, `artifact_declaration_conflict`, `artifact_unbound`,
  `artifact_mismatch`.
- Durable authority: `review_generation_stale`, `review_rejected`,
  `event_quarantined`, `acceptance_not_active`.
- Extra final held: `ownership_changed`, `ownership_unknown`,
  `ownership_unverified`, `board_unknown`, `department_changed`,
  `effect_transaction_nested`, `delivery_identity_conflict`, `acceptance_missing`,
  `acceptance_stale`, **`report_too_large`**, `no_delivery` (reconcile only), and
  `gate_not_releasable` (defensive fallback when a non-releasable `evaluate()`
  status carries no reason string; the enumerated gate reasons above should make
  it unreachable).
- **Removed:** `effect_fencing_unavailable`. Do not show stock-port-always-held text.
- Unknown: new local finals do not enter unknown. Legacy finals retain
  `prior_effect_unknown`; deprecated readback reconciliation and escalation retain
  `in_flight_unknown`, `readback_unavailable`, `readback_not_found`.
- Escalation semantics are unchanged: `blocked / repair_loop_exhausted /
  manager_unreachable` input enum, round-only detail 0..1000, identity conflicts
  (`delivery_identity_conflict`, `escalation_identity_conflict`), pending intent and
  remote readback remain. Port exceptions return **unknown/effect_raised**, not
  dead_letter. Retry budget -> `dead_letter/retry_budget_exhausted`; historical
  dead_letter detail is operator-readable text, not necessarily an enum.
- `not_delivered` is status-only; `not_attempted`, `event_at_*`, and dashboard
  health codes remain **not release reason codes**.

## 7. Preserved limitations and M2 handoff

Producer identity is worker-declared, **not reviewer attestation of bytes**.
Submit may follow review but must precede accept. Board SHA claims only tighten;
review-free prose cannot authorize. Deliver does not reread or freeze artifact
files. Readiness is not delivery. For an undelivered task accept/deliver preflight
still merges `strongest_hold`; existing historical delivery facts have precedence.

No M2 implementation, integration, installation, live Desktop observation, network,
push, deployment or production writes are part of this candidate. M2 source files
are unchanged; see the separately delivered `M2-v5-ALIGNMENT.md` checklist.

## 8. Versions, packaging and storage bootstrap

| Artifact | Version | Meaning |
|---|---|---|
| Contract key | `department-hub/m1-contract/v5` | Interface and behaviour contract; authoritative |
| `plugin.yaml` | `0.5.0` | Plugin package version; the minor digit tracks the M1 contract major (0.5 ↔ v5) |
| `dashboard/manifest.json` | `0.2.0` | Desktop page/backend bundle version; independent of the contract |

M2 must compare the **contract key**. A packaging bump never implies an interface
change, and an interface change always bumps the contract key.

Storage bootstrap (P2-2): `Store()` is read-only when the schema is already
current. The DDL script and the column-migration `BEGIN IMMEDIATE` run only when a
table, index, trigger or migrated column is missing, and `register_department()`
writes only when the stored JSON differs from the configured department. A
read-only tool call therefore holds no write lock, so `department_status` can
answer while another process owns the delivery writer. Residual limits: the first
open of a fresh or older database still migrates under one write lock, and a
reader arriving while a writer's COMMIT is in progress can still wait up to the
15 s busy timeout.

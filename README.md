# Department Hub — department-led orchestration for Hermes Kanban

**M1 v5 candidate: plugin-owned immutable final reports. Independent review pending;
not installed, integrated or deployed.** Normative contract:
[docs/m1-v5-contract.md](docs/m1-v5-contract.md), version
`department-hub/m1-contract/v5`. v2–v4.1 contracts are historical.

Seven tools: `department_status`, `department_ingest`, `department_bind`,
`department_submit`, `department_accept`, `department_deliver`,
`department_escalate`. Input schemas and the v4.1 acceptance gate are unchanged;
status/report outputs and delivery destination have intentionally changed.

## Capabilities and layout

| Capability | Module |
|---|---|
| Public plugin registration (no I/O at discovery), caller profile checks | `__init__.py`, `plugin.yaml` |
| Authoritative board reads and strict parsing | `department_lib/board.py`, `kanban_cli.py` |
| Explicit membership, ancestry, receipt/collision quarantine | `orchestrator.py`, `store.py` |
| Artifact producer and exact acceptance gate | `artifacts.py`, `acceptance.py` |
| Immutable final JSON in plugin SQLite; escalation intents/readback | `delivery.py`, `store.py` |
| Manager wake subscription/readback | `routing.py` |
| Historical M2 read model / UI (not changed in v5) | `dashboard/`, `desktop/`, `department_lib/dashboard.py` |

The manager profile must enable the `kanban` toolset. The plugin never edits
profiles. Example configuration (do not apply to production without approval):

```yaml
plugins:
  entries:
    department-hub:
      settings:
        state_path: ~/.hermes/department-hub/state.db
        departments:
          - id: dev
            name: Development
            manager: {profile: dept-manager, platform: api_server, chat_id: api-dev-session}
            members: [worker-a]
            reviewers: [reviewer-a]
            max_repair_rounds: 2
```

## Artifact and review contract (unchanged from v4.1)

`department_submit` is the **only artifact identity producer**. It reads a local
regular file at an absolute path, ≤64 MiB, computes SHA-256, and stores path,
real_path, byte length, algorithm, producer and time for `(task_id,revision)`.
A different SHA for an existing revision is `artifact_identity_conflict`.
New authorized repair revisions must submit again. Replayed old-generation
events do not unbind the existing artifact (see contract producer rules).

Board `metadata.artifact_sha256` declarations only tighten: contradiction with
the producer -> `artifact_declaration_conflict`; they cannot supply identity.
Without submit, PASS stays `artifact_unbound`. Submit must precede **accept**;
a held acceptance is not retroactively authorized by a later submit. Submit may
follow review: identity is **worker-declared**, not reviewer attestation of bytes.
Deliver does not re-hash, copy or freeze artifact bytes.

Review must be the current requested independent reviewer, with an authorized
review-lane claim after the request. `outcome=completed` plus structured
`metadata.verdict=PASS`, or explicit line-leading `PASS` / `REVIEW: PASS`, can
prove PASS. Friendly prose (`looks good`, `LGTM`, `已通過`) is `review_unproven`.
Negative prose cannot be laundered by PASS. Authorized `changes_requested`
rejects even without structured verdict/SHA. Unbound rejection is
`rejection_unbound`, never permission to deliver an old PASS.

Manager accept/deliver/escalate tools compare captured `ctx.profile_name` with
the department's manager; a model cannot supply a different profile in args.
This is not an ACL against a hostile runtime/database owner.

## Minimal operator flow

`M` = manager, `W` = member, `R` = configured independent reviewer.

1. M calls `department_bind(department="dev", root_task_id="root", task_ids=["work"])`.
   Root must be board-assigned to M. A result with ensured cards but
   `success:false / session_owner_unverified` means ownership was bound but public
   notify-list cannot attest the runtime session owner; it does not prove wake.
2. W calls `department_submit(task_id="work", artifact_path="/abs/report.md")`.
   Keep returned `revision` and `artifact.sha256` verbatim.
3. W requests review on the board; R claims review and completes with `REVIEW: PASS`
   (or structured PASS). Ingest is recommended/idempotent, not required before accept.
4. M calls `department_accept(task_id="work", revision=1, verdict_run_id=6,
   artifact_sha256="<producer SHA>")` -> `releasable`, `ready_to_deliver:true`.
5. M calls `department_deliver` with those same four fields -> **`delivered` with
   the stock service**. Exactly one immutable JSON report is committed in plugin
   SQLite; **zero final board comments**. Repeat -> `already_delivered`.
6. Coordinator calls `department_status(department="dev")`; read
   `status.tasks[].delivery.identity` and `.report` for the full structured report,
   including summary and `delivered_at` UTC Unix seconds. Each report ≤65,536 UTF-8
   bytes; summary ≤4,096 characters. No file/export or board comment read is needed.

Readiness is not delivery and never implies reviewer byte attestation. A later
new-generation PASS + accept cannot overwrite the report: deliver holds
`delivery_identity_conflict` with the actual `delivered_identity`. Same-identity
retry remains `already_delivered` even after a later rejection/deactivation.

## Delivery guarantees and reasons

One SQLite `BEGIN IMMEDIATE` checks active policy, active exact acceptance,
current generation, rejection/durable-quarantine and identity, then inserts the
report. Durable quarantine includes the sticky `events.state='conflict'` row, so
the direct `Delivery.release_final` boundary holds too, not just the service path;
board ownership stays service-side (`authorize()`).
Unique task final index and SQL immutability triggers protect committed reports.
Held authorization rolls back; no report/intent is left behind. Before-COMMIT
SIGKILL -> zero report and safe retry; after-COMMIT SIGKILL -> delivered on reopen.
This is local atomicity with ingest, **not** a distributed fence against a remote
board change that has not been ingested.

Read-only tool calls take no write lock: `Store()` skips its DDL/migration
`BEGIN IMMEDIATE` when the schema is current and `register_department()` skips an
unchanged binding, so `department_status` keeps answering while another process
holds the delivery writer (contract §8).

Actions remain `delivered / already_delivered / held / unknown / dead_letter`.
Gate and producer reasons are fully enumerated in contract §6 (and historical
producer §2). Final-only reasons include `acceptance_missing`, `acceptance_stale`,
`delivery_identity_conflict`, `department_changed`, `effect_transaction_nested`,
`report_too_large`, and ownership/board/authority holds. `effect_fencing_unavailable`
is **removed**. Status-only ready-but-not-invoked reason is `not_delivered`.

New local finals do not create unknown/dead-letter intents. Historical remote
pending/unknown rows remain `unknown/prior_effect_unknown`, not silently resent;
legacy readback reconciliation is deprecated. Escalation remains an explicit
separate board effect with round-only detail, identity conflict protection,
`in_flight_unknown`, `readback_unavailable`, `readback_not_found`,
`unknown/effect_raised` and `dead_letter/retry_budget_exhausted` behavior.
`escalation_identity_conflict` and `no_delivery` also remain in their respective
escalation/reconcile paths. `not_attempted`, `event_at_*` and M2 health codes are
not release reason codes.

## Tests and boundaries

```bash
cd product
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python -m pytest tests -q -p no:cacheprovider
```

All fixtures use synthetic board data and isolated SQLite. Author evidence keeps
baseline RED, unchanged-suite failures from the intentional effect-contract
change, explicit assertion migration and final GREEN separately. No claim that
an assertion-migrated suite is an unchanged legacy suite.

Not guaranteed: external exactly-once delivery; reviewer attestation; artifact
file immutability; runtime wake/owner proof; subscription inheritance isolation;
shared-board confidentiality; real-model judgment or live Desktop rendering.
Historical duplicate-final databases fail upgrade uniqueness rather than deleting
reports. The M2 Desktop read model was aligned to v5 in the separate `t_68287415`
candidate (plugin-persisted report projection, offline report history, canonical
identity). That candidate is not installed or independently verified here.

No original project, production board/profile/credentials/gateway/core/FreshTape
changes, network calls, push, install, integration or deployment in this slice.

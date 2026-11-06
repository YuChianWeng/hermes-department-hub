# Department Hub — department-led orchestration for Hermes Kanban

**Status (2026-11-06): integrated, manually installed on one host, not a
general-availability release.** M1 v5.1 + M2 v5 are merged in this repository
(integration commit `014f372`, evidence commit `add47d22`); the merged tree is
byte-identical to the independently reviewed candidate (55/55 product files).
The integration review (`t_e3d284ae`) passed the code, parity and runtime checks
and returned REQUEST_CHANGES **only** for documentation and repo hygiene — this
revision is that documentation fix.

The plugin was installed by hand on the Oracle host on 2026-11-06
07:41–07:46 UTC (config backup `config.yaml.pre-department-hub.20261106-074114`
plus a gateway restart). That install is a **footprint, not an acceptance**:
the real Desktop screen has not been independently verified (no screenshot or
hands-on record). Tested against **Hermes Agent v0.23.0 (2026.9.24)**.

Normative contract: [docs/m1-v5-contract.md](docs/m1-v5-contract.md), version
`department-hub/m1-contract/v5`. v2–v4.1 contracts are historical.

Seven tools: `department_status`, `department_ingest`, `department_bind`,
`department_submit`, `department_accept`, `department_deliver`,
`department_escalate`. Input schemas and the v4.1 acceptance gate are unchanged;
status/report outputs and delivery destination have intentionally changed.

## Installation

Two halves. On a remote Desktop they are installed on **different machines**:
the agent half on the machine that runs the gateway, the desktop half on the
machine that runs Hermes Desktop.

Everything below is a manual operator procedure — this package ships **no
installer**. Tested on Hermes Agent v0.23.0 (2026.9.24): Linux server + Windows
Desktop.

### 1. Agent half (gateway machine)

All commands run from this repository root.

```bash
install -d ~/.hermes/plugins/department-hub
cp -a plugin.yaml __init__.py department_lib dashboard \
      ~/.hermes/plugins/department-hub/
```

Do not copy `tests/`, `docs/`, `desktop/` or `.research/` into the plugin
directory; they are not runtime files. Verify:

```bash
test -f ~/.hermes/plugins/department-hub/plugin.yaml
test -f ~/.hermes/plugins/department-hub/__init__.py
test -f ~/.hermes/plugins/department-hub/dashboard/plugin_api.py
test -f ~/.hermes/plugins/department-hub/department_lib/artifacts.py
```

### 2. Desktop half (Desktop machine)

Copy **only** `desktop/department-hub/plugin.js`. The folder name must equal the
plugin id `department-hub`. The root is `<HERMES_HOME>/desktop-plugins`:

| Host | Path |
|---|---|
| Linux / macOS (default) | `~/.hermes/desktop-plugins/department-hub/plugin.js` |
| Windows (default, no `HERMES_HOME`) | `%LOCALAPPDATA%\hermes\desktop-plugins\department-hub\plugin.js` |
| Any OS with `HERMES_HOME` set | `$HERMES_HOME/desktop-plugins/department-hub/plugin.js` |

On a default Windows install that resolves to
`C:\Users\<you>\AppData\Local\hermes\desktop-plugins\department-hub\plugin.js`
— **not** `%USERPROFILE%\.hermes\...`. Putting the file in the wrong root is
silent: the app simply never lists the plugin.

Two more facts that cost time if missed:

- **A remote Desktop reads its own disk.** Copying `plugin.js` into the gateway
  server's `~/.hermes/desktop-plugins/` does nothing for a Desktop running on
  another machine; it must land in *that* machine's desktop-plugins root.
- **`hermes dashboard` in a browser does not load Desktop plugins.** The
  dashboard surface is built with `desktop_plugins=False`; only the `webapp`
  (Desktop app) surface loads them. Do not use the browser dashboard (default
  `127.0.0.1:9119`) to verify the Desktop half.

What the v0.23.0 loader does with a hand-placed file (read from
`runtime-loader.ts` L76–362 and exercised against this `plugin.js`):

- It shows up as its **own Desktop card** (`desktop:department-hub`, name
  "Department Hub", badge "Desktop") rather than merging into the agent card
  (`department-hub`, badge "Agent"). Merging keys on
  `.hermes-package.json` → `packageName`, which a hand-placed file does not
  have; in remote mode the unified half is never auto-copied to the local
  machine. The agent card's Desktop column showing "—" is **normal** (the agent
  package has no `desktop/` directory). Do **not** add a `.hermes-package.json`
  marker yourself — Electron's reconcile prunes a marked half when its local
  source is absent.
- `defaultEnabled: false` makes the loader *inventory only*: it does not call
  `register()` until you switch on **that Desktop card** in
  Capabilities → Plugins (the agent card's switch does not affect it). Once
  enabled, registration is immediate and needs no restart: route
  `/department-hub`, a sidebar entry (Departments／部門), and a `Ctrl+K`
  command. A load failure produces a toast and a red failed card.
- On Windows, rescan with `Ctrl+K` → "Reload desktop plugins". The Desktop
  file cap is 512 KiB (this `plugin.js` is 51,158 bytes), and the folder name
  must equal the plugin id.

That is **loader-level evidence**, not UI acceptance: unsupported imports `[]`,
3 specifiers rewritten, `node --check` OK after rewrite, stub-loaded default
export valid (`id`/`name`/`defaultEnabled:false`/`register` is a function),
`register(ctx)` emitting 3 records (page/nav/palette), and all 14 SDK import
names present in v0.23.0 `sdk/index.ts`. The rendered screen is still
UNVERIFIED.

### 3. `config.yaml` on the gateway machine

Add `department-hub` to `plugins.enabled` and create/merge
`plugins.entries.department-hub.settings`:

```yaml
plugins:
  enabled:
    - department-hub        # ← add this line
  disabled: []
  entries:                  # ← this key may not exist yet
    department-hub:
      settings:
        state_path: ~/.hermes/department-hub/state.db
        departments:
          - id: dev
            name: Development
            manager:
              profile: default          # must exist and enable the `kanban` toolset
              platform: api_server      # wake-subscription match value
              chat_id: api-dev-session  # placeholder; real wake needs the manager's real values
            members: [normal-worker]
            reviewers: [reviewer]
            max_repair_rounds: 2
```

Notes:

- Indent with spaces, never tabs — a broken YAML block stops the gateway.
- `platform` / `chat_id` are the values the manager wake subscription is matched
  against. Placeholders are fine for a first smoke test: `department_bind`
  answers `session_owner_unverified` and the UI shows
  `unconfirmed (ensured_not_awake)`. That is the documented answer, **not** an
  install failure. Do not guess credentials for a real wake.
- If the manager is not `default`, install the agent half into that profile too
  (`~/.hermes/profiles/<manager>/plugins/department-hub/`) with the same
  `plugins.enabled` / `plugins.entries` settings and the **same absolute**
  `state_path`. Members and reviewers that call `department_submit` /
  `department_ingest` need the same treatment.
- `state_path` must be an absolute path shared by every profile that uses the
  plugin; two profiles with different paths get two independent report stores.

### 4. Backup, restart, verify

```bash
CFG="$HOME/.hermes/config.yaml"
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a "$CFG" "$CFG.pre-department-hub.$STAMP"
sha256sum "$CFG" > "$CFG.pre-department-hub.$STAMP.sha256"

hermes -p default gateway restart --yes     # interrupts running workers; do it while idle
hermes -p default gateway status
```

Verification:

```bash
hermes plugins doctor ~/.hermes/plugins/department-hub --ci   # expect 7 tool(s), 2 hook(s)
```

- The plugin HTTP routes are served by the dashboard surface and require an
  authenticated principal. `curl http://127.0.0.1:8644/api/plugins/department-hub/health`
  returns **404** on this host: 8644 is the OpenAI-compatible API server, not the
  dashboard. An unauthenticated request to the dashboard route returns **401** —
  a correct refusal, not a broken install. Use the Desktop app / authenticated
  dashboard session.
- Desktop: Settings → Plugins → enable **Department Hub**, then open
  `/department-hub`.

### 5. Rollback

```bash
CFG="$HOME/.hermes/config.yaml"
STAMP=<the stamp printed above>
hermes -p default gateway stop
cp -a "$CFG.pre-department-hub.$STAMP" "$CFG"
sha256sum -c "$CFG.pre-department-hub.$STAMP.sha256"
rm -rf "$HOME/.hermes/plugins/department-hub"
hermes -p default gateway start
```

Also delete `~/.hermes/desktop-plugins/department-hub/` on the **Desktop**
machine. `~/.hermes/department-hub/state.db` is deliberately left in place: it
holds the immutable final reports. Delete it only if losing delivery history is
acceptable.

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
profiles.

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
This is not an ACL against a hostile runtime/database owner (see Known
limitations).

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

Run from the **repository root** (there is no `product/` subdirectory):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  env -u PYTHONPATH \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python -m pytest tests -q -p no:cacheprovider

node --check desktop/department-hub/plugin.js
node tests/js/run.mjs
```

`env -u PYTHONPATH` matters: a stray `PYTHONPATH` pointing at another Python's
site-packages produces two collection errors that look like code defects but are
environmental. Replace the interpreter path with your own venv if it differs.

All fixtures use synthetic board data and isolated SQLite. Author evidence keeps
baseline RED, unchanged-suite failures from the intentional effect-contract
change, explicit assertion migration and final GREEN separately. No claim that
an assertion-migrated suite is an unchanged legacy suite.

### Plugin validation

`hermes plugins validate .` run at the repo root reports **rc=1**, and that is
expected: its security scan walks `.research/` (≈28k historical files, uv cache
and lab sandboxes, 17,843 hits), none of which ships. Validate the shipping
package instead:

```bash
STAGE=$(mktemp -d)
rsync -a --exclude .git --exclude .research --exclude __pycache__ \
      --exclude '*.db' --exclude 'tests/js/.tmp' ./ "$STAGE"/
hermes plugins validate "$STAGE" --json      # expect ok=true, 16/16 checks
```

The same 55 shipping files validate 16/16 with zero warnings; `hermes plugins
doctor . --ci` reports 7 tools / 2 hooks and rc=0.

### Full-chain E2E (isolated)

`.research/m2-e2e.py` is the v5 harness: real `hermes kanban` CLI, real
`hermes_cli.kanban_db` transitions, real `DepartmentService` + `Delivery`, real
`DashboardService`, with `HERMES_HOME` / `HERMES_KANBAN_HOME` pinned inside a
scratch directory (it refuses to run against a non-isolated board):

```bash
PYTHONPATH=/home/ubuntu/.hermes/hermes-agent:. \
  /home/ubuntu/.hermes/hermes-agent/venv/bin/python \
  .research/m2-e2e.py <scratch>/m2-e2e-run
```

It exercises `department_submit` before accept (producer-only identity), asserts
that `department_deliver` writes **zero** board comments, and reads the final
report back from the plugin SQLite `deliveries.body_json`.

## Known limitations

- **Identity is not authentication.** `artifact_sha256` and the deliver identity
  are worker-declared; a profile is a routing/identity binding, not a credential.
  Nothing here proves a human reviewer looked at the bytes.
- **No profile ACL.** Manager checks compare captured `ctx.profile_name`; they are
  not an ACL against a hostile runtime or database owner, and there is no
  per-profile access control on the plugin store or the board.
- **The Desktop half must be installed by hand**, in that machine's
  `desktop-plugins` root, and the **real Desktop screen has not been independently
  verified** — no screenshot or hands-on record exists. File-level and SDK-mock
  checks pass; rendered pixels, native grant and OAuth do not.
- Not guaranteed: external exactly-once delivery; reviewer attestation; artifact
  file immutability; runtime wake/owner proof; subscription inheritance isolation;
  shared-board confidentiality; real-model judgment or live Desktop rendering.
- Historical duplicate-final databases fail upgrade uniqueness rather than
  deleting reports.
- A Hermes profile is not a sandbox; default subscription inheritance and
  shared-board metadata exposure remain P0 limitations (see
  `docs/m2-desktop.md` §9).

## Supersedes: `.research/M2-DESKTOP-MANUAL-ACCEPTANCE.md`

That file is retained as historical evidence and **must not be used as an install
guide**. It describes a v4.1 candidate installed from
`t_c410652a/candidate`, and its warning *「不要安裝
`/home/ubuntu/projects/hermes-department-orchestrator/`」* became false when the
reviewed candidate was integrated on 2026-11-06 — one operator had already
followed it. The correct install source is **this repository root**; the steps
above supersede it. Its config-backup/rollback pattern is still sound and is
reproduced in [Installation](#installation).

## Provenance

Integrated from the reviewed M1 v5.1 product and the M2 v5 candidate; the merged
tree matched the candidate 55/55 byte-for-byte at the time of the integration
review. `.research/` keeps the historical evidence (sandboxes, logs, snapshots)
and is excluded from git history where it contains state DBs, credentials or
package caches. See `INTEGRATION-REPORT.md` and `INTEGRATION-MANIFEST.sha256`.

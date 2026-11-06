# Department Hub — isolated research prototype v1

**Research artifact only.** Not production code, not a P0 PASS, not M1
authorization, and not permission to deploy or to modify production profiles,
boards, gateways or Hermes core.

## What this is

A runnable prototype + rerunnable tests for one bounded question:

> Under one trusted single writer, can a manager-only work DAG with a fully
> detached default report card enforce reject → rework → fresh independent PASS
> → manager acceptance → exactly one final report, survive crash/replay at the
> effect/receipt boundary, and fail closed on wrong owner / wrong artifact /
> stale verdict / wrong edges / duplicate / out-of-order events?

It does **not** claim exactly-once delivery, an unbypassable ACL, confidential
shared-board metadata, or real-model judgment quality.

## Layout

```
deptproto/            prototype implementation (stdlib + SQLite only)
  store.py            durable store, two-phase event admission, receipts/outbox
  orchestrator.py     state machine, acceptance gate, delivery/recovery
  effects.py          idempotent effect ports (file journal; CLI board comment)
  board.py            FakeBoard / JsonBoard (tests) and CliBoard (live lab)
  preflight.py        fail-before-start path/network assertions
  cli.py              crash-test CLI (seed/deliver/recover/status)
tests/                34 unittest-style pytest tests (fail-first)
scripts/
  run_unit.sh         focused + full unit tests
  sandbox.sh          user/mount/net/pid namespace launcher (fail-closed rc=78)
  live_stage.py       real gateway + 3 profiles + synthetic model stage
  live_deliver.py     real board effect + crash/recovery subprocess
  model_server.py     deterministic synthetic model (no reasoning)
reports/              PROTOTYPE-REPORT.md, TEST-REPORT.md
evidence/             red/green unit logs, live console
live-lab/             disposable live lab (state, logs, evidence); reset each run
MANIFEST.sha256       sha256 of every artifact (excluding caches/manifest)
```

## Run

```bash
cd /home/ubuntu/projects/hermes-department-orchestrator/.research/prototype-v1
./scripts/run_unit.sh          # 34 tests, rc 0
./scripts/sandbox.sh --probe   # unshare capability; rc 78 if unavailable
./scripts/sandbox.sh           # fresh isolated live stage, 40/40 assertions
```

Live stage resets `live-lab/{os-home,board,xdg,runtime,tmp,proto-*}` and the
JSONL evidence on every run. It does not touch anything outside the lab.

## Guarantees actually exercised

- Stable task/event IDs; duplicate event id, gaps and replays rejected visibly.
- Admission is not completion: `processed_at` + receipt only after state apply.
- Artifact identity is sha256-verified against the file; verdicts bind to the
  review run's artifact; acceptance binds verdict + current artifact + rev.
- Single delivery intent per department (`final:<dept>:<acceptance_id>`), with
  outbox, authoritative read-back, receipt, bounded retries, visible
  `effect_unknown` / `dead_letter` states and reconciliation on restart.
- Crash at `after_intent`, `before_effect`, `after_effect_before_receipt`,
  `after_receipt_before_confirm`, `after_admit_before_process`; one effect only.
- Live: real gateway wake for manager, reviewer and default profiles; real
  `kanban_show`/`kanban_comment` tools; crash after a real board comment and
  restart recovery without duplication.

## Explicit non-guarantees

- The trusted single writer is not an unbypassable ACL. Any CLI/UI can still
  create/link cards; `create --parent` subscription inheritance still exists.
- Profile boundaries are not security boundaries; shared board metadata is not
  confidential (`kanban_show` worker_context may expose child ids).
- External effect atomicity is unproven; no exactly-once claim. Unreadable
  effects are surfaced, not reported as success.
- No private Hermes functions and no direct production-SQLite writes were used.
- Missing public contracts (atomic no-inherit/exact recipients, owner
  validation rejection, durable ACK/replay, scoped task ACL) remain open and are
  recorded in `reports/PROTOTYPE-REPORT.md` §7.

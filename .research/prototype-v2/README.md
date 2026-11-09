# Department Hub — isolated research prototype v2 (P1 repair)

**Research artifact only.** This is not production code, not a P0 PASS, not M1
authorization, and not permission to deploy or to modify production profiles,
boards, gateways or Hermes core.

v2 is a **repair of the frozen v1 prototype** against the six P1 findings in
`.research/prototype-v1-independent-review.md` (SHA-256
`c3e76fa703675277691f95d9e6ce89c8b920868b80fb8b0d33359b642f63fce0`).
`prototype-v1/` and all of its evidence are byte-for-byte untouched
(manifest SHA-256 `26f6046b714f0289b9aafa6e9c6df360b27d30180629f7127899943d51645b97`,
193/193 files verified; see `evidence/v1-immutability.json`).

## The bounded question

> Under one trusted single writer, can a manager-only work DAG with a fully
> detached default report card enforce reject → rework → fresh independent PASS
> → manager acceptance → exactly one final report, survive crash/replay at the
> effect/receipt boundary, and **fail closed** on wrong owner, unknown owner,
> unknown external effect, wrong artifact, stale verdict, non-detached report
> edges, duplicate and out-of-order events?

It does **not** claim exactly-once delivery to an external system, an
unbypassable ACL, confidential shared-board metadata, or real-model judgment
quality.

## What v2 fixes (mapped to the six P1 findings)

| # | v1 defect | v2 behaviour |
|---|-----------|--------------|
| 1 | `recover()` drove a stale outbox straight to the effect | Every drive first probes the effect. Before any **new** effect it re-runs the full gate and compares the stored `gate_fingerprint`; stale → `held_stale`/`superseded`, no send. An already-sent-but-now-stale effect is recorded as `historical_confirmed` (a historical fact), never claimed as currently accepted and never re-sent. |
| 2 | read-back `None` was treated as "absent" and effects were re-sent | The port returns an explicit three-state `probe`: `FOUND` / `ABSENT` (authoritative) / `UNKNOWN`, plus `CONFLICT`. `UNKNOWN` never authorizes another send after any attempt; only a brand-new intent with zero attempts may send once. Read-back verifies the exact task/author and **the complete body**, not a marker substring. |
| 3 | acceptance ignored the declared artifact; a mutated file was still delivered | Verdicts must declare the exact SHA-256; acceptance must name `verdict_id`, `item_id`, `artifact_sha` and `rev`, all cross-checked against the current item, request, run, verdict and **actual bytes**. Artifacts are snapshotted content-addressed at publish; delivery re-hashes the immutable snapshot, so overwriting the source path cannot change what is delivered. Missing/corrupt blobs fail closed. |
| 4 | an unmapped session owner was admitted and got a receipt | Unknown/missing/empty session mappings and profile mismatches are dead-lettered **before admission** (no board-event row, no observation, no receipt). Processing re-verifies the mapping inside the same transaction; a session revoked between admission and processing produces no receipt. |
| 5 | only edges to registered work IDs were rejected on the report card | The report card's parents and children are read from the **authoritative board** and must both be empty; any edge (setup, root, unknown, indirect, work, review) → `wrong_report_edge`; unreadable/missing board → `board_read_unknown`/`report_missing`. Checked before every effect and in recovery. Work-card parents are also cross-checked against the registered parents. |
| 6 | domain mutation, receipt and event state spanned multiple commits | The whole handler runs in one `BEGIN IMMEDIATE` transaction: verdict+status, acceptance+status, observation+receipt+processed commit together (or roll back together). The crash point sits inside that transaction, so a process death leaves either the old state or the complete new state. Recovery re-runs pending work idempotently. |

## Layout

```
deptproto/            prototype implementation (stdlib + SQLite only)
  store.py            durable store, single-transaction mutations, receipts/outbox
  orchestrator.py     state machine, exact gate, delivery/recovery, board adapter
  effects.py          FOUND/ABSENT/UNKNOWN effect ports (journal + CLI board)
  board.py            FakeBoard / JsonBoard (tests) and CliBoard (live lab)
  preflight.py        fail-before-start path/network assertions
  crash.py            real os._exit(137) crash injection at named boundaries
  cli.py              crash-test CLI (seed/deliver/recover/status)
tests/                57 tests: 34 v1 + 12 P1 regressions + 11 v2 verification
scripts/
  run_unit.sh         focused + full unit tests
  sandbox.sh          user/mount/net/pid namespace launcher (fail-closed rc=78)
  live_stage.py       real gateway + 3 profiles + synthetic model stage (42 assertions)
  live_deliver.py     real board effect + real crash/recover subprocess
  model_server.py     deterministic synthetic model (no reasoning, no provider)
reports/              PROTOTYPE-REPORT.md, TEST-REPORT.md
evidence/             RED/GREEN logs, historical failed run, hash-bound metadata
live-lab/             frozen live-lab state and evidence from the final run
MANIFEST.sha256       sha256 of every artifact (excluding caches/manifest itself)
```

## Run

```bash
cd <repo>/.research/prototype-v2
./scripts/run_unit.sh            # 57 passed, rc 0
./scripts/sandbox.sh --probe     # unshare capability; rc 78 if unavailable
./scripts/sandbox.sh unit        # full suite inside the namespace sandbox
./scripts/sandbox.sh live        # real isolated gateway + synthetic model, 42/42
```

The sandbox replaces the real `$HOME`, `/run` and `/tmp` with tmpfs, mounts only
this prototype tree plus the installed Hermes source and Python runtime
(read-only), leaves only loopback, and fails **before any cleanup** if a path or
network envelope check fails. It refuses to run if `unshare` is unavailable.

## Evidence and honest boundaries

- **RED** (`evidence/red-p1-frozen-v1-repro.log`): the 12 regression tests run
  against the frozen v1 implementation and fail with `AssertionError` at
  behaviour assertions — no import or collection errors. The older
  `red-p1-tests.log`/`red-p1-final.log` are retained as-is.
- **GREEN**: `evidence/focused-final.log` (23 passed, 3 subtests),
  `evidence/sandbox-unit.log` (57 passed, 3 subtests, rc 0).
- **Live** (`live-lab/evidence/`, 42/42 assertions, rc 0): a **real Hermes
  gateway** with three synthetic profiles and a **deterministic synthetic
  model** (no provider, no external network). The model performs no reasoning.
  This is *not* an autonomous event-to-acceptance E2E: the harness manually
  reads the synthetic comments and calls the adapter with fixed literal
  payloads, and completes the report card through the CLI. The live lab does
  include one **real process kill** (`os._exit(137)`) at
  `after_effect_before_receipt` followed by recovery, with exactly one comment.
- The unknown-effect, conflicting-effect and multi-boundary crash cases are
  **offline synthetic** (hidden read-back / injected exceptions / local
  subprocess kills), not proofs about a production board.
- `evidence/historical-failed-live-run.json` preserves the earlier failed live
  attempt (a harness seeding bug: work-card parents did not match the real
  board). It was fixed by seeding the real setup id; the failing assertion was
  the gate correctly refusing a mismatched parent list.

## Known limitations (not regressions)

- **Trusted single writer is an assumption, not an ACL.** Any process that can
  write the same board can create or link cards; shared-board metadata is not
  confidential, and Hermes create/link inheritance can expose child metadata.
- **No exactly-once external delivery.** If an effect is applied but read-back is
  unavailable, v2 keeps it `unknown` forever and surfaces an alert for manual
  reconciliation. It never claims success and never blindly re-sends.
- **Concurrent reconcilers are out of scope.** The "fresh unattempted intent may
  send once" rule assumes a serialized single writer; two concurrent recoveries
  of the same fresh intent are not adjudicated by a distributed lock.
- **Synthetic model only.** No real-model judgment, Desktop, SDK, OAuth,
  dispatcher review spawn, or real `review_requested` event kinds were tested.
- The live crash exercises one boundary; other boundaries are covered by local
  subprocess kills against the store, not the real board.

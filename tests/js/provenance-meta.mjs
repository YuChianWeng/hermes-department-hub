#!/usr/bin/env node
/**
 * End-to-end meta test for the provenance guard.
 *
 * This file does NOT re-implement the guard's logic — it spawns the real
 * `tests/js/run.mjs` as a subprocess and asserts what a CI log would see. That
 * is the whole point: the previous round's guard passed every in-process check
 * while silently downgrading a drifted Desktop source to `skip`. Only a test
 * that runs the suite as a user/CI does can catch "the guard never went red".
 *
 * Cases (each one spawns run.mjs with HERMES_APP_ROOT pointed at a throwaway
 * copy of the four pinned sources):
 *   (i)   pristine copy                → exit 0, 6 provenance checks, 0 skips
 *   (ii)  runtime.ts + 1 byte          → exit ≠ 0, mismatch message, no ALL PASS
 *   (iii) HERMES_APP_ROOT = empty dir  → exit ≠ 0 (misconfiguration)
 *   (iv)  languages.ts changed outside its pinned slice → exit 0
 *   (v)   shared/i18n.ts changed outside its pinned slice → exit 0
 *
 * No real install is modified: every copy lives under the OS temp dir and is
 * removed at the end. On a machine without the Desktop checkout the spawn cases
 * visibly skip (the pure decision tests in provenance-pure.mjs still run there).
 *
 * Run: node tests/js/provenance-meta.mjs
 */
import { spawnSync } from 'node:child_process'
import { appendFileSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { DEFAULT_APP_ROOT, PINNED, RANGES, hasPinnedSource } from './mocks/extract-i18n-resolver.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', '..')
const runScript = join(here, 'run.mjs')
const node = process.execPath
const scratch = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'provenance-meta-'))

const failures = []
function check(name, condition, extra = '') {
  if (condition) {
    console.log(`ok   ${name}`)
  } else {
    failures.push(name + (extra ? ` :: ${extra}` : ''))
    console.log(`FAIL ${name}${extra ? ` :: ${extra}` : ''}`)
  }
}

function runSuite(envRoot) {
  const env = { ...process.env }
  if (envRoot === undefined) delete env.HERMES_APP_ROOT
  else env.HERMES_APP_ROOT = envRoot
  const result = spawnSync(node, [runScript], { cwd: root, env, encoding: 'utf8', timeout: 120000 })
  return {
    code: result.status,
    signal: result.signal,
    output: `${result.stdout || ''}${result.stderr || ''}`
  }
}

function copySource(dest) {
  for (const rel of Object.keys(PINNED)) {
    mkdirSync(dirname(join(dest, rel)), { recursive: true })
    copyFileSync(join(DEFAULT_APP_ROOT, rel), join(dest, rel))
  }
  return dest
}

const count = (output, pattern) => (output.match(pattern) || []).length
const hasSkipLine = output => /^skip /m.test(output)

if (!hasPinnedSource(DEFAULT_APP_ROOT)) {
  rmSync(scratch, { recursive: true, force: true })
  console.log(
    `SKIP provenance-meta: Desktop source not found at ${DEFAULT_APP_ROOT} — ` +
      'the spawn cases need the Hermes agent checkout (set HERMES_APP_ROOT); ' +
      'the pure decision tests in provenance-pure.mjs still run everywhere'
  )
  process.exit(0)
}

// (i) pristine copy → PASS with exactly the 6 provenance checks and no skip
{
  const dir = copySource(join(scratch, 'pristine'))
  const result = runSuite(dir)
  check('meta(i): pristine source exits 0', result.code === 0, `code=${result.code} signal=${result.signal}`)
  check('meta(i): pristine source keeps ALL PASS', result.output.includes('ALL PASS'))
  check(
    'meta(i): exactly 4 pinned-source checks run',
    count(result.output, /^ok   guard\(b\): pinned (?:slice )?sha256 /gm) === 4,
    String(count(result.output, /^ok   guard\(b\): pinned (?:slice )?sha256 /gm))
  )
  check(
    'meta(i): exactly 2 generated-file comparisons run',
    count(result.output, /^ok   guard\(b\): generated /gm) === 2,
    String(count(result.output, /^ok   guard\(b\): generated /gm))
  )
  check('meta(i): no guard(b) skip line', !hasSkipLine(result.output), result.output.match(/^skip .*/m)?.[0])
  check(
    'meta(i): provenance summary says PASS',
    /^guard\(b\) provenance: PASS$/m.test(result.output),
    result.output.slice(-200)
  )
  rmSync(dir, { recursive: true, force: true })
}

// (ii) one byte appended to runtime.ts → FAIL, actionable message, no ALL PASS
{
  const dir = copySource(join(scratch, 'mutated'))
  const target = join(dir, 'apps/desktop/src/i18n/runtime.ts')
  writeFileSync(target, `${readFileSync(target, 'utf8')} `)
  const result = runSuite(dir)
  check('meta(ii): drifted source exits non-zero', result.code !== 0, `code=${result.code}`)
  check('meta(ii): drifted source is not reported as passing', !result.output.includes('ALL PASS'))
  check(
    'meta(ii): output names the mismatch and the offending file',
    /does not match the validated v0\.23\.0 pins/.test(result.output) && result.output.includes('runtime.ts'),
    result.output.slice(-400)
  )
  check(
    'meta(ii): output carries expected and actual sha256',
    /expected [0-9a-f]{64}/.test(result.output) && /actual [0-9a-f]{64}/.test(result.output)
  )
  check(
    'meta(ii): output tells the maintainer to re-extract, not to edit the pin',
    result.output.includes('Re-extract (node tests/js/mocks/extract-i18n-resolver.mjs)') &&
      result.output.includes('Do not edit a pin to turn this green')
  )
  check('meta(ii): drifted source never says "not installed"', !/not installed/i.test(result.output))
  check('meta(ii): provenance summary says FAILED', /^guard\(b\) provenance: FAILED$/m.test(result.output))
  rmSync(dir, { recursive: true, force: true })
}

// (iii) explicitly set but empty root → FAIL, never a silent fallback
{
  const dir = join(scratch, 'empty-root')
  mkdirSync(dir, { recursive: true })
  const result = runSuite(dir)
  check('meta(iii): empty HERMES_APP_ROOT exits non-zero', result.code !== 0, `code=${result.code}`)
  check('meta(iii): empty root is not reported as passing', !result.output.includes('ALL PASS'))
  check(
    'meta(iii): empty root refuses to fall back to the default install',
    /Refusing to silently fall back/.test(result.output),
    result.output.slice(-400)
  )
  check('meta(iii): provenance summary says FAILED', /^guard\(b\) provenance: FAILED$/m.test(result.output))
  rmSync(dir, { recursive: true, force: true })
}

// (iv)/(v) an unrelated upstream edit to a slice-pinned file → still PASS
for (const [name, rel, outsideLine] of [
  ['meta(iv)', 'apps/desktop/src/i18n/languages.ts', 17],
  ['meta(v)', 'apps/shared/src/i18n.ts', 25]
]) {
  const dir = copySource(join(scratch, name.replace(/[^a-z0-9]/gi, '-')))
  const target = join(dir, rel)
  const lines = readFileSync(target, 'utf8').split('\n')
  lines[outsideLine - 1] = `${lines[outsideLine - 1]} // unrelated upstream edit`
  writeFileSync(target, lines.join('\n'))
  // also append a line at EOF: both are outside the pinned slice
  appendFileSync(target, '\n// appended by an unrelated upstream commit\n')
  const result = runSuite(dir)
  check(`${name}: unrelated edit to ${rel} exits 0`, result.code === 0, `code=${result.code}`)
  check(`${name}: unrelated edit to ${rel} keeps ALL PASS`, result.output.includes('ALL PASS'))
  check(
    `${name}: unrelated edit to ${rel} keeps provenance PASS`,
    /^guard\(b\) provenance: PASS$/m.test(result.output),
    result.output.slice(-200)
  )
  check(`${name}: unrelated edit to ${rel} produces no skip`, !hasSkipLine(result.output))
  rmSync(dir, { recursive: true, force: true })
}

rmSync(scratch, { recursive: true, force: true })

if (failures.length) {
  console.log(`\n${failures.length} FAILED`)
  for (const failure of failures) console.log(` - ${failure}`)
  process.exit(1)
}
console.log('\nALL PASS')

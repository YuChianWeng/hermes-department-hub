#!/usr/bin/env node
/**
 * Unit tests for the provenance decision function + the pin mutation matrix.
 *
 * Two layers, on purpose:
 *
 *   1. `evaluateProvenance()` is exercised with every input injected (roots,
 *      `exists`, `build`). No filesystem, no env var — so the skip / fail /
 *      pass branches and the "explicit root must not fall back" rule run on
 *      ANY machine, including one without the Desktop source installed.
 *
 *   2. The mutation matrix runs the REAL `build()` against throwaway copies of
 *      the four pinned sources. It proves what the pin actually bites on:
 *        - one byte changed inside the sliced lines  → FAIL for every file
 *        - unrelated lines changed in a slice-pinned file (languages.ts,
 *          shared/i18n.ts)                          → still PASS, and the
 *                                                      generated mocks stay
 *                                                      byte-identical
 *        - unrelated lines changed in a whole-file-pinned file (runtime.ts,
 *          plugin-i18n.ts)                          → FAIL
 *
 * The matrix visibly skips when this machine has no Desktop checkout; the pure
 * decision tests never skip.
 *
 * Run: node tests/js/provenance-pure.mjs
 */
import { appendFileSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  DEFAULT_APP_ROOT,
  PINNED,
  RANGES,
  build,
  evaluateProvenance,
  hasPinnedSource
} from './mocks/extract-i18n-resolver.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const failures = []
const skipped = []

function check(name, condition, extra = '') {
  if (condition) {
    console.log(`ok   ${name}`)
  } else {
    failures.push(name + (extra ? ` :: ${extra}` : ''))
    console.log(`FAIL ${name}${extra ? ` :: ${extra}` : ''}`)
  }
}

const skip = (name, reason) => {
  skipped.push(`${name} — ${reason}`)
  console.log(`skip ${name} — ${reason}`)
}

// ── 1. pure decision function (all inputs injected) ────────────────────────
const PASS_BUILD = root => ({ root, pins: {}, hashes: {}, files: {} })
const neverBuild = () => {
  throw new Error('evaluateProvenance called build() when it should not have')
}
const mismatchBuild = () => {
  throw Object.assign(new Error('sha256 mismatch for apps/x/runtime.ts'), {
    code: 'PIN_MISMATCH',
    rel: 'apps/desktop/src/i18n/runtime.ts',
    expected: 'e'.repeat(64),
    actual: 'a'.repeat(64)
  })
}
const readBuild = () => {
  throw Object.assign(new Error('could not read apps/x/languages.ts from /root: ENOENT'), { code: 'READ_ERROR' })
}

// (a) nothing installed anywhere → visible skip, exit 0 upstream
{
  const out = evaluateProvenance({
    envRoot: undefined,
    defaultRoot: '/nonexistent-default',
    exists: () => false,
    build: neverBuild
  })
  check('pure: absent source → SKIP', out.status === 'skip', out.status)
  check(
    'pure: SKIP detail names the path it actually probed',
    out.detail === 'Desktop source not found (looked in: /nonexistent-default)',
    out.detail
  )
  check('pure: SKIP lists exactly the probed roots', JSON.stringify(out.roots) === '["/nonexistent-default"]', out.roots)
}

// (b) empty HERMES_APP_ROOT is "unset": probe the default root
{
  const out = evaluateProvenance({
    envRoot: '',
    defaultRoot: '/default-root',
    exists: root => root === '/default-root',
    build: PASS_BUILD
  })
  check('pure: empty env string probes the default root', out.status === 'pass' && out.root === '/default-root', out.detail)
  check('pure: empty env string records only the default root', JSON.stringify(out.roots) === '["/default-root"]', out.roots)
}

// (b2) whitespace-only env is treated as unset too
{
  const out = evaluateProvenance({
    envRoot: '   ',
    defaultRoot: '/default-root',
    exists: root => root === '/default-root',
    build: PASS_BUILD
  })
  check('pure: whitespace-only env string probes the default root', out.status === 'pass' && out.root === '/default-root', out.detail)
}

// (c) explicit root that does not exist → FAIL, and NOTHING else is probed
{
  const out = evaluateProvenance({
    explicitRoot: '/explicit-broken',
    envRoot: '/env-good',
    defaultRoot: '/default-good',
    exists: root => root === '/env-good' || root === '/default-good',
    build: neverBuild
  })
  check('pure: broken explicit root → FAIL (misconfiguration)', out.status === 'fail' && out.code === 'MISCONFIGURED_ROOT', out.status)
  check('pure: broken explicit root does not fall back anywhere', JSON.stringify(out.roots) === '["/explicit-broken"]', out.roots)
  check('pure: broken explicit root detail names the bad path', out.detail.includes('/explicit-broken'), out.detail)
}

// (d) broken HERMES_APP_ROOT → FAIL, no silent fallback to the default install
{
  const out = evaluateProvenance({
    envRoot: '/env-broken',
    defaultRoot: '/default-good',
    exists: root => root === '/default-good',
    build: neverBuild
  })
  check('pure: broken HERMES_APP_ROOT → FAIL (misconfiguration)', out.status === 'fail' && out.code === 'MISCONFIGURED_ROOT', out.status)
  check('pure: broken HERMES_APP_ROOT never probes the default', JSON.stringify(out.roots) === '["/env-broken"]', out.roots)
  check('pure: broken env detail says it refuses to fall back', /refusing to silently fall back/i.test(out.detail), out.detail)
}

// (e) explicit root wins over env root
{
  const out = evaluateProvenance({
    explicitRoot: '/explicit-good',
    envRoot: '/env-broken',
    defaultRoot: '/default-good',
    exists: root => root === '/explicit-good',
    build: PASS_BUILD
  })
  check('pure: a valid explicit root is authoritative', out.status === 'pass' && out.root === '/explicit-good', out.detail)
  check('pure: explicit root is the only root probed', JSON.stringify(out.roots) === '["/explicit-good"]', out.roots)
}

// (f) source exists but the build refuses it → FAIL with actionable text
{
  const out = evaluateProvenance({ envRoot: '/root', exists: () => true, build: mismatchBuild })
  check('pure: drifted source → FAIL', out.status === 'fail' && out.code === 'PIN_MISMATCH', out.status)
  check(
    'pure: drifted source message names the file and both hashes',
    out.detail.includes('apps/desktop/src/i18n/runtime.ts') &&
      out.detail.includes(`expected ${'e'.repeat(64)}`) &&
      out.detail.includes(`actual ${'a'.repeat(64)}`),
    out.detail
  )
  check(
    'pure: drifted source message is actionable and forbids pin-editing',
    out.detail.includes('Re-extract (node tests/js/mocks/extract-i18n-resolver.mjs)') &&
      out.detail.includes('Do not edit a pin to turn this green'),
    out.detail
  )
  check(
    'pure: drifted source is never reported as "not installed"',
    !/not installed/i.test(out.detail),
    out.detail
  )
}

// (g) a read error is a FAIL too, not a skip
{
  const out = evaluateProvenance({ envRoot: '/root', exists: () => true, build: readBuild })
  check('pure: unreadable pinned file → FAIL', out.status === 'fail' && out.code === 'READ_ERROR', out.status)
}

// (h) the happy path carries the pins through
{
  const out = evaluateProvenance({ envRoot: '/root', exists: () => true, build: PASS_BUILD })
  check('pure: matching source → PASS', out.status === 'pass', out.status)
  check('pure: PASS detail is explicit', out.detail === 'Desktop source at /root matches the validated v0.23.0 pins', out.detail)
}

// ── 2. real build(): pin scope matrix ──────────────────────────────────────
const scratch = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'provenance-pure-'))
let seq = 0
const freshCopy = () => {
  const dir = join(scratch, `copy-${++seq}`)
  for (const rel of Object.keys(PINNED)) {
    mkdirSync(dirname(join(dir, rel)), { recursive: true })
    copyFileSync(join(DEFAULT_APP_ROOT, rel), join(dir, rel))
  }
  return dir
}
const mutateLine = (dir, rel, lineNo, suffix = ' ') => {
  const path = join(dir, rel)
  const lines = readFileSync(path, 'utf8').split('\n')
  lines[lineNo - 1] = `${lines[lineNo - 1]}${suffix}`
  writeFileSync(path, lines.join('\n'))
}
const appendLine = (dir, rel, line) => appendFileSync(join(dir, rel), `\n${line}\n`)

const evaluateRoot = root =>
  evaluateProvenance({ envRoot: root, defaultRoot: '/nonexistent-default', exists: hasPinnedSource, build })

if (!hasPinnedSource(DEFAULT_APP_ROOT)) {
  skip(
    'mutation matrix',
    `Desktop source not found at ${DEFAULT_APP_ROOT} (set HERMES_APP_ROOT); pure decision tests above still ran`
  )
} else {
  const committed = {
    'i18n-resolver.mjs': readFileSync(join(here, 'mocks', 'i18n-resolver.mjs'), 'utf8'),
    'i18n-plugin.mjs': readFileSync(join(here, 'mocks', 'i18n-plugin.mjs'), 'utf8')
  }

  // pristine copy → PASS, pins report their scopes
  {
    const dir = freshCopy()
    const out = evaluateRoot(dir)
    check('matrix: pristine copy → PASS', out.status === 'pass', out.detail)
    check(
      'matrix: slice-pinned files report mode slice',
      out.pins['apps/desktop/src/i18n/languages.ts'].mode === 'slice' &&
        out.pins['apps/shared/src/i18n.ts'].mode === 'slice'
    )
    check(
      'matrix: whole-file-pinned files report mode file',
      out.pins['apps/desktop/src/i18n/runtime.ts'].mode === 'file' &&
        out.pins['apps/desktop/src/i18n/plugin-i18n.ts'].mode === 'file'
    )
    rmSync(dir, { recursive: true, force: true })
  }

  // 1 byte inside the sliced lines of each file → FAIL
  for (const rel of Object.keys(RANGES)) {
    const dir = freshCopy()
    mutateLine(dir, rel, RANGES[rel][0][0])
    const out = evaluateRoot(dir)
    check(
      `matrix: one byte inside the slice of ${rel} → FAIL`,
      out.status === 'fail' && out.code === 'PIN_MISMATCH' && out.detail.includes(rel),
      `${out.status} ${out.code || ''} ${out.detail}`
    )
    rmSync(dir, { recursive: true, force: true })
  }

  // unrelated lines in a SLICE-pinned file → still PASS, mocks unchanged
  for (const [rel, lineNo] of [
    ['apps/desktop/src/i18n/languages.ts', 17], // LOCALE_OPTIONS, below the pinned DEFAULT_LOCALE line
    ['apps/shared/src/i18n.ts', 25] // mergeTranslations, below the pinned isRecord block
  ]) {
    const dir = freshCopy()
    mutateLine(dir, rel, lineNo, '  // unrelated upstream edit')
    const out = evaluateRoot(dir)
    check(`matrix: unrelated line in slice-pinned ${rel} → still PASS`, out.status === 'pass', `${out.status} ${out.detail}`)
    const regenerated = build(dir).files
    check(
      `matrix: unrelated line in slice-pinned ${rel} leaves the generated mocks byte-identical`,
      regenerated['i18n-resolver.mjs'] === committed['i18n-resolver.mjs'] &&
        regenerated['i18n-plugin.mjs'] === committed['i18n-plugin.mjs']
    )
    rmSync(dir, { recursive: true, force: true })
    // and appending at EOF is equally unrelated
    const dir2 = freshCopy()
    appendLine(dir2, rel, '// appended by an unrelated upstream commit')
    check(
      `matrix: appended line in slice-pinned ${rel} → still PASS`,
      evaluateRoot(dir2).status === 'pass',
      evaluateRoot(dir2).detail
    )
    rmSync(dir2, { recursive: true, force: true })
  }

  // unrelated lines in a WHOLE-FILE-pinned file → FAIL
  for (const rel of ['apps/desktop/src/i18n/runtime.ts', 'apps/desktop/src/i18n/plugin-i18n.ts']) {
    const dir = freshCopy()
    appendLine(dir, rel, '// appended by an upstream commit')
    const out = evaluateRoot(dir)
    check(
      `matrix: line outside the slice of whole-file-pinned ${rel} → FAIL`,
      out.status === 'fail' && out.code === 'PIN_MISMATCH' && out.detail.includes(rel),
      `${out.status} ${out.code || ''}`
    )
    rmSync(dir, { recursive: true, force: true })
  }

  // build() must never fall back to the default install for a bad explicit root
  let explicitError = null
  try {
    build('/nonexistent-explicit-root')
  } catch (err) {
    explicitError = err
  }
  check(
    'matrix: build() with a bad explicit root refuses instead of falling back',
    Boolean(explicitError) && explicitError.code === 'SOURCE_NOT_FOUND',
    explicitError ? explicitError.message : 'build() silently used a different root'
  )

  // a pinned file that is missing → READ_ERROR (FAIL, not skip)
  {
    const dir = freshCopy()
    rmSync(join(dir, 'apps/shared/src/i18n.ts'))
    const out = evaluateRoot(dir)
    check(
      'matrix: missing pinned file → FAIL (READ_ERROR)',
      out.status === 'fail' && out.code === 'READ_ERROR',
      `${out.status} ${out.code || ''} ${out.detail}`
    )
    rmSync(dir, { recursive: true, force: true })
  }

  // a pinned line range that is past EOF → RANGE_OUT_OF_BOUNDS (FAIL, not skip)
  {
    const dir = freshCopy()
    const path = join(dir, 'apps/desktop/src/i18n/languages.ts')
    writeFileSync(path, readFileSync(path, 'utf8').split('\n').slice(0, 10).join('\n'))
    const out = evaluateRoot(dir)
    check(
      'matrix: pinned line range past EOF → FAIL (RANGE_OUT_OF_BOUNDS)',
      out.status === 'fail' && out.code === 'RANGE_OUT_OF_BOUNDS',
      `${out.status} ${out.code || ''} ${out.detail}`
    )
    rmSync(dir, { recursive: true, force: true })
  }

  // and the whole-file hashes in the banner are still the validated v0.23.0 ones
  {
    const built = build()
    check(
      'matrix: generated mocks match the committed files byte for byte',
      built.files['i18n-resolver.mjs'] === committed['i18n-resolver.mjs'] &&
        built.files['i18n-plugin.mjs'] === committed['i18n-plugin.mjs']
    )
  }
}

rmSync(scratch, { recursive: true, force: true })

if (failures.length) {
  console.log(`\n${failures.length} FAILED`)
  for (const failure of failures) console.log(` - ${failure}`)
  process.exit(1)
}
console.log(`\nALL PASS${skipped.length ? ` (${skipped.length} section skipped)` : ''}`)

#!/usr/bin/env node
/**
 * Verbatim extractor for the Hermes Desktop v0.23.0 plugin i18n resolver.
 *
 * WHY THIS EXISTS (architectural, not cosmetic)
 * ---------------------------------------------
 * `tests/js/mocks/sdk.mjs` used to resolve plugin strings with a whole-string
 * flat lookup (`bundle[key]`). The real Desktop does not: `translateFrom` walks
 * a NESTED tree with `key.split('.').reduce(...)` and returns the raw key when
 * the walk fails. A flat `'owner.unverified'` key therefore passes every mocked
 * test and renders literally as `owner.unverified` in the real app. That
 * mismatch shipped twice (Desktop spike v1→v2, then M2).
 *
 * The only durable fix is to make the test double BE the real resolver. This
 * script slices the real functions out of the installed Desktop source by line
 * range — no hand copying — strips TypeScript types with Node's own
 * `stripTypeScriptTypes`, and writes two plain-ESM modules the test imports:
 *
 *   tests/js/mocks/i18n-resolver.mjs   isRecord + DEFAULT_LOCALE + resolvePath/render/translateFrom
 *   tests/js/mocks/i18n-plugin.mjs     registry + mergeMessages + registerPluginLocales + translatePlugin
 *
 * PROVENANCE IS FAIL-CLOSED
 * -------------------------
 * A pin that only runs when everything is already fine is not a guard. The
 * three states are explicit and decided by `evaluateProvenance()` below:
 *
 *   source absent (no env root, default root has no runtime.ts) → visible SKIP, exit 0
 *   source present but a pin/range/read fails                  → FAIL, exit non-zero
 *   explicit root (HERMES_APP_ROOT or a caller argument) broken → FAIL, never fall back
 *
 * `findAppRoot()` used to silently fall back to the default install when
 * `HERMES_APP_ROOT` pointed somewhere useless, so a typo in CI verified the
 * wrong tree. Root resolution is now strict: an explicitly supplied root is
 * authoritative, and only an absent/empty env var probes the default.
 *
 * Usage:
 *   node tests/js/mocks/extract-i18n-resolver.mjs            # (re)generate
 *   HERMES_APP_ROOT=/path/to/hermes-agent node ...           # non-default install
 */

import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))

/** Default install root of the Hermes Desktop v0.23.0 source tree. */
export const DEFAULT_APP_ROOT = '/home/ubuntu/.hermes/hermes-agent'

/** The file whose presence defines "this directory is a Desktop source tree". */
export const RUNTIME_REL = 'apps/desktop/src/i18n/runtime.ts'

/**
 * Pinned sha256 per file, with the pin SCOPE made explicit.
 *
 *   mode 'file'  → the WHOLE file is pinned.
 *   mode 'slice' → only the lines this extractor actually copies are pinned.
 *
 * Why the split: `runtime.ts` and `plugin-i18n.ts` are pinned whole because the
 * parts we do NOT slice (`createPluginI18n.t`, `usePluginI18n`, the atom
 * plumbing) are exactly what the test doubles (`sdk.mjs`) re-implement. Any
 * upstream change there can silently change what the tests prove, so the whole
 * file must be re-read and re-reviewed.
 *
 * `languages.ts` (we only copy DEFAULT_LOCALE, L15) and `apps/shared/src/i18n.ts`
 * (we only copy isRecord, L17-19) are pinned at slice level instead. Those
 * files grow for reasons unrelated to the resolver — adding a UI language edits
 * `LOCALE_OPTIONS` far below L15 — and a whole-file pin would turn every such
 * upstream commit into a red suite that people learn to "fix" by editing the
 * pin. At that point the guard certifies nothing. Pinning the slice text keeps
 * every change to the depended-upon lines fatal while unrelated code stays free
 * to move.
 *
 * `sourceSha256` is the whole-file hash of the validated v0.23.0 file. It is
 * printed in the generated banner as historical provenance but is NOT enforced
 * for slice-mode files — enforcing it would make an unrelated upstream edit
 * rewrite the generated mock, which is the false positive this split removes.
 *
 * Never edit a pin to turn a red suite green: re-extract, read the upstream
 * diff, re-review the semantics, then update the pin.
 */
export const PINNED = {
  'apps/desktop/src/i18n/runtime.ts': {
    mode: 'file',
    sha256: '2bc3965d90d9d2939ae1804863a1012f77a2da25cb17a638ce47c9b6b279998f'
  },
  'apps/desktop/src/i18n/plugin-i18n.ts': {
    mode: 'file',
    sha256: '196e727b2de0caa3c5aaa6213ac3912519b3f8f11b5c7fe2fd81d080968d69cf'
  },
  'apps/desktop/src/i18n/languages.ts': {
    mode: 'slice',
    sha256: 'ac56e05321182109b1fd2b7b6d4f74f89f2d8461b263794b4b06bfaf1ca88fb6',
    sourceSha256: '62f368da6a46fbb5b72c1bbfa6bd8c8d30f5998ec598386d04e00fec23399f2b'
  },
  'apps/shared/src/i18n.ts': {
    mode: 'slice',
    sha256: '66f0b54794a2152579ec11bb50b5b316292fe1e8cc858935c0bfc0190157b4a2',
    sourceSha256: '346a7d455250b67af2a72c34b039e588a48b241993e7a3556762f680e2faebca'
  }
}

/** Inclusive 1-indexed line ranges sliced out of each source file. */
export const RANGES = {
  'apps/shared/src/i18n.ts': [[17, 19]],
  'apps/desktop/src/i18n/languages.ts': [[15, 15]],
  'apps/desktop/src/i18n/runtime.ts': [[17, 58]],
  'apps/desktop/src/i18n/plugin-i18n.ts': [
    [63, 63],
    [69, 107]
  ]
}

export const RESOLVER_FILE = 'i18n-resolver.mjs'
export const PLUGIN_FILE = 'i18n-plugin.mjs'

export function sha256(text) {
  return createHash('sha256').update(text).digest('hex')
}

export function sliceLines(text, [from, to]) {
  return text.split('\n').slice(from - 1, to).join('\n')
}

function strip(source) {
  return stripTypeScriptTypes(source, { mode: 'strip' }).replace(/\s+$/, '')
}

/** True when `root` looks like a Desktop source tree. Never throws. */
export function hasPinnedSource(root) {
  if (!root) return false
  try {
    readFileSync(join(String(root), RUNTIME_REL), 'utf8')
    return true
  } catch (_err) {
    return false
  }
}

/** An Error carrying the machine-readable reason the provenance guard failed. */
function provenanceError(code, rel, message, extra = {}) {
  const err = new Error(message)
  err.code = code
  err.rel = rel
  Object.assign(err, extra)
  return err
}

const trimRoot = value => (typeof value === 'string' ? value.trim() : value)

/**
 * Strict root resolution. An explicitly supplied root is authoritative; only an
 * absent/empty env var falls through to the default install.
 */
export function resolveRoot({ explicitRoot, envRoot, defaultRoot = DEFAULT_APP_ROOT } = {}) {
  const explicit = trimRoot(explicitRoot)
  if (explicit) return { root: explicit, source: 'explicit' }
  const env = trimRoot(envRoot)
  if (env) return { root: env, source: 'env' }
  return { root: defaultRoot || null, source: 'default' }
}

/**
 * @deprecated Use `resolveRoot()` / `evaluateProvenance()`. Kept strict so old
 * callers cannot get the silent fallback back: an explicit root that does not
 * exist returns null instead of being replaced by the default install.
 */
export function findAppRoot(explicit) {
  const { root } = resolveRoot({ explicitRoot: explicit, envRoot: process.env.HERMES_APP_ROOT })
  return hasPinnedSource(root) ? root : null
}

function assertRange(rel, text, [from, to]) {
  const lines = text.split('\n').length
  if (!(from >= 1) || !(to >= from) || to > lines) {
    throw provenanceError(
      'RANGE_OUT_OF_BOUNDS',
      rel,
      `${rel}: line range L${from}-${to} is outside the file (${lines} lines)`
    )
  }
}

/**
 * Slice the real resolver out of `appRoot` and return the two generated files
 * without touching disk. Throws when a pinned hash, a line range or a read
 * fails. An explicit `appRoot` is used as-is; there is no fallback.
 */
export function build(appRoot) {
  const explicit = trimRoot(appRoot)
  const root = explicit || resolveRoot({ envRoot: process.env.HERMES_APP_ROOT }).root
  if (!root || !hasPinnedSource(root)) {
    throw provenanceError(
      'SOURCE_NOT_FOUND',
      RUNTIME_REL,
      explicit
        ? `Desktop source root ${explicit} was passed explicitly but has no ${RUNTIME_REL}. Refusing to fall back to another checkout.`
        : `Desktop source not found (looked in: ${root || '(no default root configured)'})`
    )
  }

  const sources = {}
  /** Whole-file sha256 — the generated banner keeps the exact same text it always had. */
  const hashes = {}
  const pins = {}
  for (const rel of Object.keys(RANGES)) {
    let text
    try {
      text = readFileSync(join(root, rel), 'utf8')
    } catch (err) {
      throw provenanceError('READ_ERROR', rel, `could not read ${rel} from ${root}: ${err.message}`)
    }
    sources[rel] = text
    hashes[rel] = sha256(text)
    const ranges = RANGES[rel]
    for (const range of ranges) assertRange(rel, text, range)
    const pin = PINNED[rel]
    const actual =
      pin.mode === 'slice' ? sha256(ranges.map(range => sliceLines(text, range)).join('\n')) : hashes[rel]
    pins[rel] = { mode: pin.mode, expected: pin.sha256, actual, ok: actual === pin.sha256 }
    if (!pins[rel].ok) {
      throw provenanceError(
        'PIN_MISMATCH',
        rel,
        `sha256 mismatch for ${rel} (${pin.mode} pin)\n  expected ${pin.sha256}\n  actual   ${actual}\n` +
          'The Desktop i18n source changed. Re-extract and re-review the resolver semantics ' +
          'before trusting these tests (do not silently update the pin).',
        { expected: pin.sha256, actual, mode: pin.mode }
      )
    }
  }

  const provenance = Object.keys(RANGES)
    .map(rel => {
      const ranges = RANGES[rel].map(([a, b]) => (a === b ? `L${a}` : `L${a}-${b}`)).join(',')
      const shown = PINNED[rel].mode === 'slice' ? PINNED[rel].sourceSha256 : hashes[rel]
      return `//   ${rel} (${ranges}) sha256 ${shown}`
    })
    .join('\n')

  const banner = tag => `// GENERATED FILE — do not edit by hand.
// Produced by tests/js/mocks/extract-i18n-resolver.mjs from the Hermes Desktop
// v0.23.0 source, sliced verbatim by line range and type-stripped by Node.
//
// The real Desktop resolver is the ONLY authority for how plugin keys resolve:
// a dot path is walked into NESTED objects and a miss returns the raw key.
// Re-generate with:  node tests/js/mocks/extract-i18n-resolver.mjs
//
// Part: ${tag}
${provenance}
`

  const resolverBody = [
    strip(sliceLines(sources['apps/shared/src/i18n.ts'], RANGES['apps/shared/src/i18n.ts'][0])),
    '',
    strip(
      sliceLines(sources['apps/desktop/src/i18n/languages.ts'], RANGES['apps/desktop/src/i18n/languages.ts'][0])
    ),
    '',
    strip(sliceLines(sources['apps/desktop/src/i18n/runtime.ts'], RANGES['apps/desktop/src/i18n/runtime.ts'][0]))
  ].join('\n')

  const pluginRanges = RANGES['apps/desktop/src/i18n/plugin-i18n.ts']
  const pluginBody = [
    "// The real file imports this atom from 'nanostores'; the registry version is",
    '// only a re-render token for React, which these plain-ESM tests never mount.',
    'const $version = { value: 0, get() { return this.value }, set(next) { this.value = next } }',
    '',
    strip(sliceLines(sources['apps/desktop/src/i18n/plugin-i18n.ts'], pluginRanges[0])),
    '',
    strip(sliceLines(sources['apps/desktop/src/i18n/plugin-i18n.ts'], pluginRanges[1]))
  ].join('\n')

  return {
    root,
    hashes,
    pins,
    files: {
      [RESOLVER_FILE]: `${banner('isRecord, DEFAULT_LOCALE, resolvePath, render, translateFrom')}\n${resolverBody}\n`,
      [PLUGIN_FILE]: `${banner('registry, mergeMessages, registerPluginLocales, translatePlugin')}\nimport { translateFrom } from './${RESOLVER_FILE}'\n\n${pluginBody}\n`
    }
  }
}

const ACTIONABLE =
  'Re-extract (node tests/js/mocks/extract-i18n-resolver.mjs), read the upstream diff, ' +
  're-review the semantics, then update PINNED. Do not edit a pin to turn this green.'

/**
 * Pure decision function for the provenance guard: every input is injected, so
 * the "no source installed" and "explicit root is broken" branches can be
 * tested without touching the real install or adding a test-only env var.
 *
 * @param {object}   input
 * @param {?string}  input.explicitRoot  root passed by a caller (authoritative when non-empty)
 * @param {?string}  input.envRoot       HERMES_APP_ROOT (authoritative when non-empty)
 * @param {?string}  input.defaultRoot   root probed only when both above are absent/empty
 * @param {Function} [input.exists]      (root) => boolean, defaults to hasPinnedSource
 * @param {Function} [input.build]       (root) => built, defaults to build()
 * @returns {{status:'pass'|'skip'|'fail', roots:string[], detail:string}}
 */
export function evaluateProvenance({
  explicitRoot = null,
  envRoot = undefined,
  defaultRoot = DEFAULT_APP_ROOT,
  exists = hasPinnedSource,
  build: buildFn = build
} = {}) {
  const roots = []
  const seen = new Set()
  const probe = root => {
    const value = String(root)
    if (!seen.has(value)) {
      seen.add(value)
      roots.push(value)
    }
  }

  const attempt = root => {
    let built
    try {
      built = buildFn(root)
    } catch (err) {
      const detail =
        err && err.code === 'PIN_MISMATCH'
          ? `Desktop source found at ${root} but does not match the validated v0.23.0 pins ` +
            `(${err.rel}: expected ${err.expected}, actual ${err.actual}). ${ACTIONABLE}`
          : `Desktop source found at ${root} but is unusable (${(err && err.message) || err}). ${ACTIONABLE}`
      return {
        status: 'fail',
        code: (err && err.code) || 'SOURCE_UNUSABLE',
        roots,
        root,
        detail
      }
    }
    return {
      status: 'pass',
      roots,
      root,
      pins: built.pins,
      hashes: built.hashes,
      build: built,
      detail: `Desktop source at ${root} matches the validated v0.23.0 pins`
    }
  }

  const explicit = trimRoot(explicitRoot)
  if (explicit) {
    probe(explicit)
    if (!exists(explicit)) {
      return {
        status: 'fail',
        code: 'MISCONFIGURED_ROOT',
        roots,
        root: explicit,
        detail:
          `Desktop source root ${explicit} was given explicitly but has no ${RUNTIME_REL}. ` +
          'Refusing to fall back to another checkout — fix the root or omit it.'
      }
    }
    return attempt(explicit)
  }

  const env = trimRoot(envRoot)
  if (env) {
    probe(env)
    if (!exists(env)) {
      return {
        status: 'fail',
        code: 'MISCONFIGURED_ROOT',
        roots,
        root: env,
        detail:
          `HERMES_APP_ROOT is set to ${env} but has no ${RUNTIME_REL}. ` +
          `Refusing to silently fall back to ${defaultRoot || 'the default root'} — fix or unset HERMES_APP_ROOT.`
      }
    }
    return attempt(env)
  }

  if (defaultRoot) {
    probe(defaultRoot)
    if (exists(defaultRoot)) return attempt(defaultRoot)
  }

  return {
    status: 'skip',
    roots,
    detail: `Desktop source not found (looked in: ${
      roots.length ? roots.join(', ') : '(no default root configured)'
    })`
  }
}

/** Text currently committed on disk for one generated file. */
export function readGenerated(name) {
  return readFileSync(join(here, name), 'utf8')
}

function main() {
  let built
  try {
    built = build()
  } catch (err) {
    console.error(`extract-i18n-resolver: ${err.message}`)
    process.exit(1)
  }
  for (const [name, content] of Object.entries(built.files)) {
    writeFileSync(join(here, name), content)
    const written = readGenerated(name)
    console.log(
      `wrote ${relative(process.cwd(), join(here, name))} (${written.length} bytes, sha256 ${sha256(written)})`
    )
  }
  console.log(`source root: ${built.root}`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main()
}

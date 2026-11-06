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
 * Every source file's sha256 is pinned below. If the pin does not match, this
 * script (and the test that re-runs `build()` in memory) FAILS loudly instead
 * of silently testing a resolver the app no longer ships.
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

/** Pinned sha256 of every file we slice from. A mismatch means the Desktop
 *  source moved under us: re-extract and re-review, do not "fix" the pin. */
export const PINNED = {
  'apps/desktop/src/i18n/runtime.ts': '2bc3965d90d9d2939ae1804863a1012f77a2da25cb17a638ce47c9b6b279998f',
  'apps/desktop/src/i18n/plugin-i18n.ts': '196e727b2de0caa3c5aaa6213ac3912519b3f8f11b5c7fe2fd81d080968d69cf',
  'apps/desktop/src/i18n/languages.ts': '62f368da6a46fbb5b72c1bbfa6bd8c8d30f5998ec598386d04e00fec23399f2b',
  'apps/shared/src/i18n.ts': '346a7d455250b67af2a72c34b039e588a48b241993e7a3556762f680e2faebca'
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

/** Locate the Desktop source root, or return null when it is not installed. */
export function findAppRoot(explicit) {
  const candidates = [explicit, process.env.HERMES_APP_ROOT, DEFAULT_APP_ROOT].filter(Boolean)
  for (const root of candidates) {
    try {
      readFileSync(join(root, 'apps/desktop/src/i18n/runtime.ts'), 'utf8')
      return root
    } catch (_err) {
      /* keep looking */
    }
  }
  return null
}

function sliceLines(text, [from, to]) {
  return text.split('\n').slice(from - 1, to).join('\n')
}

function strip(source) {
  return stripTypeScriptTypes(source, { mode: 'strip' }).replace(/\s+$/, '')
}

/**
 * Slice the real resolver out of `appRoot` and return the two generated files
 * without touching disk. Throws when a pinned hash does not match.
 */
export function build(appRoot) {
  const root = findAppRoot(appRoot)
  if (!root) {
    throw new Error(
      `Desktop source not found. Set HERMES_APP_ROOT to the hermes-agent checkout (looked in ${
        process.env.HERMES_APP_ROOT || DEFAULT_APP_ROOT
      }).`
    )
  }

  const sources = {}
  const hashes = {}
  for (const rel of Object.keys(RANGES)) {
    const text = readFileSync(join(root, rel), 'utf8')
    sources[rel] = text
    hashes[rel] = sha256(text)
    const pinned = PINNED[rel]
    if (hashes[rel] !== pinned) {
      throw new Error(
        `sha256 mismatch for ${rel}\n  expected ${pinned}\n  actual   ${hashes[rel]}\n` +
          'The Desktop i18n source changed. Re-extract and re-review the resolver semantics ' +
          'before trusting these tests (do not silently update the pin).'
      )
    }
  }

  const provenance = Object.keys(RANGES)
    .map(rel => {
      const ranges = RANGES[rel].map(([a, b]) => (a === b ? `L${a}` : `L${a}-${b}`)).join(',')
      return `//   ${rel} (${ranges}) sha256 ${hashes[rel]}`
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
    files: {
      [RESOLVER_FILE]: `${banner('isRecord, DEFAULT_LOCALE, resolvePath, render, translateFrom')}\n${resolverBody}\n`,
      [PLUGIN_FILE]: `${banner('registry, mergeMessages, registerPluginLocales, translatePlugin')}\nimport { translateFrom } from './${RESOLVER_FILE}'\n\n${pluginBody}\n`
    }
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

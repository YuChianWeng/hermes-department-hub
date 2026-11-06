/**
 * SDK-mock test for desktop/department-hub/plugin.js.
 *
 * This is NOT a Desktop render test. It rewrites the three allow-listed imports
 * to local stubs, registers the plugin against a fake ctx, then calls the
 * contributed components as plain functions and inspects the jsx() tree.
 * What it proves: the registration surface, i18n coverage, and the status /
 * evidence text the page derives from a backend payload. Real pixels remain a
 * separate acceptance step (screenshot or hands-on).
 */
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, rmSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginPath = join(here, '..', '..', 'desktop', 'department-hub', 'plugin.js')
const sdk = await import('./mocks/sdk.mjs')
const { state, setLocale } = sdk
// The REAL Desktop v0.23.0 resolver, sliced verbatim out of the app source by
// mocks/extract-i18n-resolver.mjs. Never substitute a local lookup here.
const { translateFrom } = await import('./mocks/i18n-resolver.mjs')
const { build, findAppRoot, PINNED, RESOLVER_FILE, PLUGIN_FILE, readGenerated, sha256 } = await import(
  './mocks/extract-i18n-resolver.mjs'
)

// sha256 of the two GENERATED mock files, so a hand edit is caught even on a
// machine without the Desktop source installed. Regenerate with:
//   node tests/js/mocks/extract-i18n-resolver.mjs
const GENERATED_SHA256 = {
  'i18n-resolver.mjs': '9c18a8566ac738e4506f002be2165c6fddd271e3e803b9f58f9136cd3458cca6',
  'i18n-plugin.mjs': '5da8301dc18329b5005c0dea0894f57d2a8fd1db4f405b1e06e573f1e4641fd3'
}

const source = readFileSync(pluginPath, 'utf8')
  .replace("from '@hermes/plugin-sdk'", "from '../mocks/sdk.mjs'")
  .replace("from 'react/jsx-runtime'", "from '../mocks/jsx-runtime.mjs'")
  .replace("from 'react'", "from '../mocks/react.mjs'")

const scratchDir = join(here, '.tmp')
mkdirSync(scratchDir, { recursive: true })
const modulePath = join(scratchDir, 'plugin.mjs')
writeFileSync(modulePath, source)
const mod = await import(pathToFileURL(modulePath).href)
const plugin = mod.default

const failures = []
function check(name, condition, extra = '') {
  if (condition) {
    console.log(`ok   ${name}`)
  } else {
    failures.push(name + (extra ? ` :: ${extra}` : ''))
    console.log(`FAIL ${name}${extra ? ` :: ${extra}` : ''}`)
  }
}

// ── flatten + render helpers ────────────────────────────────────────────────
function flatten(node, out = []) {
  if (node === null || node === undefined || node === false || node === true) return out
  if (Array.isArray(node)) {
    for (const child of node) flatten(child, out)
    return out
  }
  if (typeof node === 'string' || typeof node === 'number') {
    out.push({ text: String(node) })
    return out
  }
  if (typeof node === 'object' && node.props) {
    out.push({ type: node.type, props: node.props })
    if (typeof node.type === 'function') {
      flatten(node.type(node.props), out)
    } else {
      flatten(node.props.children, out)
    }
  }
  return out
}
const textOf = node =>
  flatten(node)
    .map(item => item.text || '')
    .join(' ')
    .replace(/\s+/g, ' ')
function bundleKeys(node, prefix = '', out = []) {
  for (const [key, value] of Object.entries(node)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) bundleKeys(value, path, out)
    else out.push(path)
  }
  return out
}
const nodes = node => flatten(node).filter(item => item.type !== undefined)

/** Resolve `key` against ONE bundle using the real resolver. The source ignores
 *  the locale, so a miss returns the raw key instead of silently borrowing the
 *  English string — that is what makes this usable as a completeness probe. */
const resolveIn = (bundle, key, args = [], locale = 'en') => translateFrom(() => bundle, locale, key, args)

const LOCALES = () => [
  ['en', state.bundles.en],
  ['zh-hant', state.bundles['zh-hant']]
]

/** A copy of `bundle` where every leaf is a marker function. Resolving a key
 *  against it yields '__HIT__' if and only if the REAL resolver can walk to a
 *  leaf — so this distinguishes "reaches a label" from "returns the raw key"
 *  even when the English label happens to equal the key ('active' → 'active'). */
const MARKER = '__HIT__'
function markerBundle(node) {
  if (node && typeof node === 'object' && !Array.isArray(node)) {
    const out = {}
    for (const [key, value] of Object.entries(node)) out[key] = markerBundle(value)
    return out
  }
  return () => MARKER
}
const reachCache = new Map()
const reaches = (locale, key) => {
  if (!reachCache.has(locale)) reachCache.set(locale, markerBundle(state.bundles[locale] || {}))
  return translateFrom(() => reachCache.get(locale), locale, key, ['a', 'b']) === MARKER
}

// The three i18n doors the page can reach: `usePluginI18n` (React) and
// `ctx.i18n.t` (handlers) both end in translateFrom; assert both.
const RAW_KEY = /(?:^|[\s(（])(?:subscription|owner|wake|status)\.[a-z_]+/

// ── registration ────────────────────────────────────────────────────────────
check('plugin id is department-hub', plugin.id === 'department-hub', plugin.id)
check('plugin ships opt-in', plugin.defaultEnabled === false)

const contributions = []
const ctx = {
  source: 'plugin:department-hub',
  register: c => {
    contributions.push(c)
    return () => {}
  },
  registerMany: list => {
    for (const item of list) contributions.push(item)
    return () => {}
  },
  rest: (...args) => state.rest(...args),
  socket: () => () => {},
  onDispose: () => {},
  setTimeout: () => () => {},
  setInterval: () => () => {},
  addEventListener: () => () => {},
  // Both translation doors come from mocks/sdk.mjs and both end in the REAL
  // v0.23.0 `translateFrom`. A hand-written lookup here is the bug this test
  // exists to catch — do not inline one again.
  i18n: sdk.createPluginI18n('department-hub'),
  storage: { get: () => undefined, set: () => {}, remove: () => {} }
}
plugin.register(ctx)

const page = contributions.find(c => c.area === 'routes')
const nav = contributions.find(c => c.area === 'sidebar.nav')
const palette = contributions.find(c => c.area === 'palette')
check('registers a full page route', Boolean(page) && page.data.path === '/department-hub')
check(
  'registers a sidebar nav row',
  Boolean(nav) &&
    nav.data.path === '/department-hub' &&
    nav.data.codicon === 'organization' &&
    nav.data.label === 'Departments'
)
check('registers a palette command', Boolean(palette) && palette.data.id === 'department-hub.open')

// ── i18n coverage ───────────────────────────────────────────────────────────
// Keys are compared on their DEEP (dotted) form: the real resolver walks nested
// objects, so the top-level key list is not the contract — the leaf path set is.
const enKeys = bundleKeys(state.bundles.en).sort()
const zhKeys = bundleKeys(state.bundles['zh-hant'] || {}).sort()
check('ships en + zh-hant bundles', enKeys.length > 30 && zhKeys.length > 30, `${enKeys.length}/${zhKeys.length}`)
check(
  'locale bundles have identical keys',
  JSON.stringify(enKeys) === JSON.stringify(zhKeys),
  `missing-zh=${enKeys.filter(k => !zhKeys.includes(k)).join(',')} extra-zh=${zhKeys
    .filter(k => !enKeys.includes(k))
    .join(',')}`
)
check(
  'zh-hant really is Traditional Chinese',
  String(state.bundles['zh-hant'].pageTitle) === '部門' &&
    String(state.bundles['zh-hant'].nav) === '部門'
)

// ── GUARD: the flat-dotted-key class cannot come back ───────────────────────
// Regression class (ARCHITECTURAL): a page string keyed as a dotted FLAT name
// ('owner.unverified') is invisible to the Desktop resolver, which walks NESTED
// objects and returns the key on a miss. It therefore renders literally as
// `owner.unverified`. M2 shipped 40 such keys while 136 mocked checks passed,
// because the mock resolved with a whole-string flat lookup. Both halves are
// pinned here: the resolver IS the real one, and every reachable key must
// resolve through it.

// (a) negative control — proves the harness can tell the two apart at all.
check(
  'guard(a): flat dotted key is NOT resolvable by the real resolver',
  translateFrom(() => ({ 'a.b': 'x' }), 'en', 'a.b', []) === 'a.b',
  'the harness would not detect a flat key'
)
check(
  'guard(a): the same key nested IS resolvable',
  translateFrom(() => ({ a: { b: 'x' } }), 'en', 'a.b', []) === 'x'
)
check(
  'guard(a): a miss returns the raw key (fallback contract)',
  translateFrom(() => ({ a: {} }), 'en', 'a.nope', []) === 'a.nope'
)
check(
  'guard(a): active locale wins, then the default locale, then the key',
  translateFrom(l => (l === 'en' ? { k: 'EN' } : { k: 'ZH' }), 'zh-hant', 'k', []) === 'ZH' &&
    translateFrom(l => (l === 'en' ? { k: 'EN' } : {}), 'zh-hant', 'k', []) === 'EN' &&
    translateFrom(() => ({}), 'zh-hant', 'k', []) === 'k'
)

// (b) provenance — the resolver under test is the shipped one, byte for byte.
const appRoot = findAppRoot()
let built = null
let buildError = null
try {
  built = build()
} catch (err) {
  buildError = err
}
if (appRoot && built) {
  for (const [rel, pinned] of Object.entries(PINNED)) {
    check(`guard(b): pinned sha256 ${rel}`, built.hashes[rel] === pinned, built.hashes[rel])
  }
  check(
    'guard(b): generated resolver == a fresh verbatim extraction (no hand edits)',
    readGenerated(RESOLVER_FILE) === built.files[RESOLVER_FILE],
    're-run: node tests/js/mocks/extract-i18n-resolver.mjs'
  )
  check(
    'guard(b): generated plugin registry == a fresh verbatim extraction (no hand edits)',
    readGenerated(PLUGIN_FILE) === built.files[PLUGIN_FILE],
    're-run: node tests/js/mocks/extract-i18n-resolver.mjs'
  )
} else {
  console.log(
    `skip guard(b): Desktop source not installed at ${appRoot || '(not found)'} — ` +
      `set HERMES_APP_ROOT to verify the source hash pins. ${
        buildError ? buildError.message.split('\n')[0] : ''
      }`
  )
}
// (b1) the pin must actually bite: a ONE-BYTE change to a copied source has to
// make build() refuse. Without this, a pin that silently never runs would look
// identical to a pin that passes.
if (appRoot) {
  const pinRoot = join(here, '.tmp', 'pin-probe')
  rmSync(pinRoot, { recursive: true, force: true })
  for (const rel of Object.keys(PINNED)) {
    mkdirSync(dirname(join(pinRoot, rel)), { recursive: true })
    copyFileSync(join(appRoot, rel), join(pinRoot, rel))
  }
  const target = join(pinRoot, 'apps/desktop/src/i18n/runtime.ts')
  writeFileSync(target, `${readFileSync(target, 'utf8')} `)
  let pinError = null
  try {
    build(pinRoot)
  } catch (err) {
    pinError = err
  }
  check(
    'guard(b): one changed byte in a source file makes the pin fail',
    Boolean(pinError) && /sha256 mismatch/.test(pinError.message),
    pinError ? pinError.message.split('\n')[0] : 'build() accepted a modified source'
  )
}
check(
  'guard(b): committed resolver files match their own sha256 pins',
  sha256(readGenerated(RESOLVER_FILE)) === GENERATED_SHA256[RESOLVER_FILE] &&
    sha256(readGenerated(PLUGIN_FILE)) === GENERATED_SHA256[PLUGIN_FILE],
  `${sha256(readGenerated(RESOLVER_FILE))} / ${sha256(readGenerated(PLUGIN_FILE))}`
)

// (c) backend enumeration — every state the backend can send that the page
// routes through t(). Sources read directly, not inferred from the bundle:
//   subscription/owner/wake → department_lib/dashboard.py _manager_status()
//   work                     → department_lib/dashboard.py _work_state() rollup
//   board                    → hermes_cli/kanban_workflow.py DEFAULT_STATUSES
const BACKEND_STATES = {
  subscription: ['unknown', 'missing', 'duplicate', 'subscribed', 'foreign_only'],
  // The backend only ever emits 'unverified' (dashboard.py L703). 'verified' has
  // no producer today; it stays in the bundle so the page can render it if M1
  // ever attests a live session owner.
  owner: ['unverified'],
  wake: ['unconfirmed'],
  work: ['unknown', 'blocked', 'review', 'running', 'done', 'queued'],
  board: ['triage', 'todo', 'scheduled', 'ready', 'running', 'blocked', 'review', 'done', 'archived']
}
const ENUMERATED = [
  ['subscription', ['subscribed', 'missing', 'duplicate', 'foreign_only', 'unknown'], ['1', '1']],
  ['owner', ['unverified', 'verified'], ['session_owner_unverified']],
  ['wake', ['unconfirmed', 'confirmed'], ['ensured_not_awake']],
  // 9 board columns + the backend's own 'queued'/'unknown' rollup values.
  [
    'status',
    ['blocked', 'done', 'review', 'running', 'ready', 'todo', 'triage', 'scheduled', 'archived', 'queued', 'unknown'],
    ['blocked']
  ]
]
for (const [locale, bundle] of LOCALES()) {
  for (const [group, states, args] of ENUMERATED) {
    for (const state_ of states) {
      const key = `${group}.${state_}`
      const out = resolveIn(bundle, key, args, locale)
      check(
        `guard(c)[${locale}]: ${key} resolves through the real resolver`,
        reaches(locale, key) && typeof out === 'string' && out.length > 0 && out !== key && !out.includes('undefined'),
        out
      )
    }
  }
  for (const [group, states] of Object.entries(BACKEND_STATES)) {
    const prefix = group === 'work' || group === 'board' ? 'status' : group
    const missing = states.filter(state_ => !reaches(locale, `${prefix}.${state_}`))
    check(
      `guard(c)[${locale}]: every backend ${group} state has a label`,
      missing.length === 0,
      missing.join(',')
    )
  }
  const flat = Object.keys(bundle).filter(key => key.includes('.'))
  check(`guard(c)[${locale}]: no flat dotted top-level key left in the bundle`, flat.length === 0, flat.join(','))
}

// (d) static scan — every literal key the page asks for, and every dynamic
//     prefix. Multi-line `t(\n 'key')` calls count too; the old single-line
//     regex silently missed four of them.
const literalKeys = [
  ...new Set([...source.matchAll(/(?<![A-Za-z0-9_$])t\(\s*'([^']+)'/g)].map(match => match[1]))
].sort()
check(
  'guard(d): the literal scan sees multi-line t() calls too',
  ['already_delivered', 'eventLine', 'identityFull', 'readyToDeliver'].every(key => literalKeys.includes(key)),
  `scanned ${literalKeys.length} keys`
)
const dynamicPrefixes = [...new Set([...source.matchAll(/(?<![A-Za-z0-9_$])t\(`([A-Za-z0-9_]+)\.\$\{/g)].map(m => m[1]))].sort()
check(
  'guard(d): the only dynamic t() prefixes are the three enumerated groups',
  JSON.stringify(dynamicPrefixes) === JSON.stringify(['owner', 'subscription', 'wake']),
  dynamicPrefixes.join(',')
)
for (const [locale, bundle] of LOCALES()) {
  const unresolved = literalKeys.filter(key => !reaches(locale, key))
  check(
    `guard(d)[${locale}]: every literal t() key resolves (${literalKeys.length} keys)`,
    unresolved.length === 0,
    unresolved.join(',')
  )
  // A page key written as a dotted FLAT literal is the same defect by another
  // route: guard(c)'s no-flat-top-level check plus this one cover both halves.
  const flatLiteral = literalKeys.filter(key => key.includes('.') && Object.keys(bundle).includes(key))
  check(`guard(d)[${locale}]: no literal t() key names a flat dotted top-level key`, flatLiteral.length === 0, flatLiteral.join(','))
}

// (e) render layer — a fixture byte-matched to Oneal's 2026-11-06 screenshot:
//     manager subscription 'missing', owner 'unverified' with its code, wake
//     'unconfirmed', work state 'unknown', zero bound cards.
const screenshotDept = {
  id: 'ops',
  name: 'Operations',
  active: true,
  manager: 'manager-a',
  members: [],
  reviewers: [],
  max_repair_rounds: 2,
  root_task_id: 't-root',
  task_count: 0,
  tasks_truncated: false,
  work: {
    state: 'unknown',
    by_status: {},
    blocked: 0,
    awaiting_review: 0,
    review_held: 0,
    held_codes: {},
    last_activity_at: null,
    state_source: 'derived from Kanban task statuses'
  },
  manager_status: {
    profile: 'manager-a',
    subscription: { state: 'missing', matching: 0, foreign: 0, wake_mode: false },
    owner: { state: 'unverified', code: 'session_owner_unverified' },
    wake: { state: 'unconfirmed', code: 'ensured_not_awake' },
    verified: false
  },
  membership: { state: 'unbound', rebind_required: false, explicit_bindings: 0 },
  final_report: { state: 'none', count: 0, delivered_count: 0 },
  unknown_tasks: 0
}
state.query.list = {
  data: {
    schema: 'department-hub/dashboard/v1',
    source: 'kanban',
    generated_at: Math.floor(Date.now() / 1000) - 30,
    board: { available: true },
    degraded: [],
    departments: [screenshotDept]
  },
  isLoading: false,
  error: null
}
state.query.detail = {
  data: {
    schema: 'department-hub/department/v1',
    generated_at: Math.floor(Date.now() / 1000) - 10,
    department: { ...screenshotDept, tasks: [], unknown_task_details: [] },
    events: []
  },
  isLoading: false,
  error: null
}

const SCREENSHOT_EXPECTATIONS = {
  en: ['not subscribed', 'unverified (session_owner_unverified)', 'unconfirmed (ensured_not_awake)', 'Unknown'],
  'zh-hant': ['未訂閱', '未驗證（', '未確認（', '未知']
}
for (const [locale, expectations] of Object.entries(SCREENSHOT_EXPECTATIONS)) {
  setLocale(locale)
  const rendered = textOf(page.render())
  for (const expected of expectations) {
    check(`guard(e)[${locale}]: renders ${JSON.stringify(expected)}`, rendered.includes(expected), rendered.slice(0, 200))
  }
  check(
    `guard(e)[${locale}]: no raw subscription./owner./wake./status. key is rendered`,
    !RAW_KEY.test(rendered),
    rendered.match(RAW_KEY)?.[0]
  )
}
setLocale('en')

// every board status the page can badge, in both locales
const boardTask = status_ => ({
  task_id: `s-${status_}`,
  role: 'work',
  title: `Card ${status_}`,
  status: status_,
  assignee: 'worker-a',
  updated_at: Math.floor(Date.now() / 1000) - 60,
  review: null,
  acceptance: null,
  final_report: null
})
state.query.detail = {
  data: {
    schema: 'department-hub/department/v1',
    generated_at: Math.floor(Date.now() / 1000) - 10,
    department: {
      ...screenshotDept,
      work: { ...screenshotDept.work, state: 'blocked' },
      tasks: ENUMERATED[3][1].map(boardTask),
      unknown_task_details: []
    },
    events: []
  },
  isLoading: false,
  error: null
}
for (const [locale, bundle] of LOCALES()) {
  setLocale(locale)
  const statusText = textOf(page.render())
  const raw = [
    ...new Set((statusText.match(/status\.[a-z_]+/g) || []).filter(token => token.startsWith('status.')))
  ]
  check(`guard(e)[${locale}]: 11 card badges render as text, never as status.*`, raw.length === 0, raw.join(','))
  const unlabelled = ENUMERATED[3][1].filter(
    state_ => !statusText.includes(resolveIn(bundle, `status.${state_}`, ['x'], locale))
  )
  check(`guard(e)[${locale}]: every board status badge shows its label`, unlabelled.length === 0, unlabelled.join(','))
}
setLocale('en')

// restore the pristine query boxes the later sections assume
state.query.detail = { data: undefined, isLoading: false, error: null }
state.query.list = { data: undefined, isLoading: false, error: null }


// ── list page against the real backend payload shape ───────────────────────
const listPayload = {
  schema: 'department-hub/dashboard/v1',
  source: 'kanban',
  generated_at: Math.floor(Date.now() / 1000) - 30,
  board: { available: true },
  degraded: [],
  departments: [
    {
      id: 'dev',
      name: 'Development',
      active: true,
      manager: 'manager-a',
      members: ['worker-a'],
      reviewers: ['reviewer-a'],
      max_repair_rounds: 2,
      root_task_id: 't1',
      task_count: 2,
      tasks_truncated: false,
      work: {
        state: 'blocked',
        by_status: { blocked: 1, done: 1 },
        blocked: 1,
        awaiting_review: 1,
        review_held: 1,
        held_codes: { no_pass_verdict: 1 },
        last_activity_at: Math.floor(Date.now() / 1000) - 120,
        state_source: 'derived from Kanban task statuses'
      },
      manager_status: {
        profile: 'manager-a',
        subscription: { state: 'subscribed', matching: 1, foreign: 0, wake_mode: true },
        owner: { state: 'unverified', code: 'session_owner_unverified' },
        wake: { state: 'unconfirmed', code: 'ensured_not_awake' },
        verified: false
      },
      membership: { state: 'membership_changed', rebind_required: true, explicit_bindings: 1 },
      final_report: { state: 'delivered', count: 1, delivered_count: 1, latest_task_id: 't1' },
      unknown_tasks: 0
    }
  ]
}

state.query.list = { data: listPayload, isLoading: false, error: null }
let tree = page.render()
let text = textOf(tree)
check('page title renders', text.includes('Departments'), text.slice(0, 120))
check('marks the surface read-only', text.includes('Read-only'))
check('names Kanban as the source', text.includes('Source: Kanban'))
check('shows the department', text.includes('Development'))
check('shows the manager binding', text.includes('Manager: manager-a'))
check('renders Kanban status as text', text.includes('Blocked'))
check('renders blocked count as text', text.includes('1 blocked'))
check('renders the final-report state', text.includes('Final report delivered'))
check('renders held review count as text', text.includes('1 held'))
check('states it is a snapshot, not a live push', text.includes('observed snapshot'))
check('labels profile-vs-sandbox honestly', text.includes('not a security sandbox'))

const listNodes = nodes(tree)
check(
  'department list is a labelled listbox',
  listNodes.some(n => n.props.role === 'listbox' && n.props['aria-label'])
)
check(
  'department option exposes aria-selected',
  listNodes.some(n => n.props.role === 'option' && n.props['aria-selected'] === 'true')
)
check(
  'department option is keyboard focusable',
  listNodes.some(n => n.props.role === 'option' && n.props.tabIndex === 0)
)
const optionNode = listNodes.find(n => n.props.role === 'option')
check('arrow keys are handled on the option', Boolean(optionNode) && typeof optionNode.props.onKeyDown === 'function')

// ── detail: review + acceptance + blocking evidence ────────────────────────
state.query.detail = {
  data: {
    schema: 'department-hub/department/v1',
    generated_at: Math.floor(Date.now() / 1000) - 10,
    department: {
      ...listPayload.departments[0],
      tasks: [
        {
          task_id: 't1',
          role: 'root',
          title: 'Ship the widget',
          status: 'done',
          assignee: 'reviewer-a',
          updated_at: Math.floor(Date.now() / 1000) - 60,
          updated_at_iso: '2026-11-05T07:00:00Z',
          last_event: { kind: 'completed', at: Math.floor(Date.now() / 1000) - 60 },
          blocked: null,
          review: {
            revision: 2,
            verdict: 'PASS',
            run_id: 44,
            reviewer: 'reviewer-a',
            artifact_sha256: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
            verdict_conflict: false,
            repair_rounds: 1
          },
          acceptance: { revision: 2, artifact_sha256: 'abc', manager: 'manager-a', current: false },
          final_report: {
            state: 'unknown',
            code: 'in_flight_unknown',
            key: 'k',
            detail: 'in_flight_unknown',
            attempts: 1,
            identity_conflict: true,
            delivered_identity: { task_id: 't1', revision: 1, verdict_run_id: 41, artifact_sha256: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789' }
          }
        },
        {
          task_id: 't2',
          role: 'work',
          title: 'Second card',
          status: 'blocked',
          assignee: 'worker-a',
          updated_at: Math.floor(Date.now() / 1000) - 30,
          last_event: { kind: 'blocked' },
          blocked: { recorded: true, reason: 'waiting for the operator key', is_current: true },
          review: { revision: 1, verdict: 'NONE', held: true, held_code: 'no_pass_verdict' },
          binding: { source: 'explicit_membership', state: 'membership_changed', rebind_required: true },
          acceptance: null,
          final_report: null
        }
      ],
      unknown_task_details: []
    },
    events: [
      {
        event_id: 'e1',
        task_id: 't1',
        kind: 'completed',
        run_id: 44,
        at: Math.floor(Date.now() / 1000) - 60,
        state: 'applied',
        detail: ''
      }
    ]
  },
  isLoading: false,
  error: null
}
text = textOf(page.render())
check('detail shows the card title', text.includes('Ship the widget'))
check('detail shows review PASS with run id', text.includes('review PASS') && text.includes('44'))
check('detail shows the artifact sha prefix', text.includes('abcdef012345'))
check('detail shows a stale acceptance as stale', text.includes('manager acceptance is stale (revision 2'))
check('detail does not call the held revision accepted', !text.includes('manager accepted revision 2'))
check('detail shows the blocking reason verbatim', text.includes('Blocked: waiting for the operator key'))
check('detail shows the durable event log', text.includes('completed') && text.includes('applied'))
check('detail shows the server snapshot line', text.includes('Server snapshot'))
check('detail shows a held review as held', text.includes('review held (no_pass_verdict)'))
check('detail shows the held code', text.includes('held: no_pass_verdict'))
check(
  'held review is never rendered as a pass',
  (text.match(/review PASS/g) || []).length === 1
)
check('detail shows a membership rebind warning', text.includes('Membership changed — rebind required'))
check('detail shows unknown delivery instead of a success', text.includes('final report: unknown (in_flight_unknown)'))
check(
  'detail shows the delivery identity conflict with its rule',
  text.includes('A different identity was already delivered; original report preserved')
)
check(
  'detail shows the actual stored identity in canonical form',
  text.includes('stored delivered identity: task t1 · revision 1 · run 41 · SHA-256 abcdef012345')
)
check('manager subscription is shown as subscribed', text.includes('subscribed'))
check('manager owner is explicitly unverified', text.includes('unverified (session_owner_unverified)'))
check('manager wake is explicitly unconfirmed', text.includes('unconfirmed (ensured_not_awake)'))
check(
  'manager status is three separate labels',
  text.includes('Subscription') && text.includes('Session owner') && text.includes('Wake confirmed')
)

// ── v4.1: every frozen held/error code is rendered and never as PASS ───────
const ACCEPT_HELD_CODES = [
  'inactive_department', 'manager_mismatch', 'repair_loop_exhausted',
  'review_rejected', 'reviewer_not_independent', 'reviewer_not_requested',
  'implementer_not_member', 'rejection_unbound', 'verdict_conflict',
  'review_unproven', 'revision_mismatch', 'no_pass_verdict', 'stale_verdict',
  'verdict_not_final', 'artifact_declaration_conflict', 'artifact_unbound',
  'artifact_mismatch', 'review_generation_stale', 'event_quarantined',
  'acceptance_not_active'
]
const DELIVER_EXTRA_HELD_CODES = [
  'acceptance_missing', 'acceptance_stale', 'delivery_identity_conflict',
  'department_changed', 'effect_transaction_nested', 'ownership_changed',
  'ownership_unknown', 'ownership_unverified', 'board_unknown',
  'escalation_identity_conflict', 'no_delivery', 'report_too_large'
]

const detailBase = (tasks, extra = {}) => ({
  data: {
    schema: 'department-hub/department/v1',
    contract: { version: 'department-hub/m1-contract/v5' },
    read_only: true,
    generated_at: Math.floor(Date.now() / 1000) - 10,
    department: {
      ...listPayload.departments[0],
      // Neutralise the list-level rollup so task-level wording is what is asserted.
      final_report: { state: 'none', count: 0, delivered_count: 0 },
      work: { ...listPayload.departments[0].work, review_held: 0, held_codes: {} },
      tasks,
      unknown_task_details: []
    },
    events: []
  },
  isLoading: false,
  error: null
})

const acceptHeldTasks = ACCEPT_HELD_CODES.map((code, index) => ({
  task_id: `a${index}`,
  role: 'work',
  title: `Held ${code}`,
  status: 'done',
  assignee: 'worker-a',
  review: {
    revision: 1,
    verdict: 'PASS',
    run_id: 10,
    reviewer: 'reviewer-a',
    artifact_sha256: null,
    held: true,
    held_code: code,
    state: 'held',
    ready_to_deliver: false
  },
  binding: { state: 'verified', source: 'explicit_membership', rebind_required: false },
  acceptance: null,
  final_report: null,
  delivery_readiness: { state: 'held', code, ready: false, delivered_identity: null }
}))
state.query.detail = detailBase(acceptHeldTasks)
text = textOf(page.render())
for (const code of ACCEPT_HELD_CODES) {
  check(`accept-held ${code} is rendered with its code`, text.includes(`review held (${code})`), code)
  check(`accept-held ${code} appears as held:`, text.includes(`held: ${code}`), code)
}
check(
  'no accept-held card renders as review PASS',
  (text.match(/review PASS/g) || []).length === 0,
  String((text.match(/review PASS/g) || []).length)
)
check('no accept-held card renders as ready to deliver', !text.includes('Ready to deliver'))

const deliverHeldTasks = DELIVER_EXTRA_HELD_CODES.map((code, index) => ({
  task_id: `d${index}`,
  role: 'work',
  title: `Delivery ${code}`,
  status: 'done',
  assignee: 'worker-a',
  review: {
    revision: 2,
    verdict: 'PASS',
    run_id: 21,
    reviewer: 'reviewer-a',
    artifact_sha256: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
    held: false,
    held_code: null,
    state: 'pass',
    ready_to_deliver: true
  },
  binding: { state: 'verified', source: 'explicit_membership', rebind_required: false },
  acceptance: { revision: 2, artifact_sha256: 'abc', manager: 'manager-a', current: true },
  final_report: null,
  delivery_readiness: { state: 'held', code, ready: false, delivered_identity: null }
}))
state.query.detail = detailBase(deliverHeldTasks)
text = textOf(page.render())
for (const code of DELIVER_EXTRA_HELD_CODES) {
  check(`delivery-held ${code} is rendered with its code`, text.includes(`delivery held (${code})`), code)
}
check('a delivery hold is never rendered as ready', !text.includes('Ready to deliver'))
check(
  'a delivery hold is never rendered as delivered',
  !text.includes('final report: delivered') && !text.includes('already delivered')
)

// ── v4.1: ready / delivered / conflict / unknown / dead-letter / in-flight ──
const readyTask = {
  task_id: 'r1',
  role: 'work',
  title: 'Ready card',
  status: 'done',
  assignee: 'worker-a',
  review: {
    revision: 3,
    verdict: 'PASS',
    run_id: 31,
    reviewer: 'reviewer-a',
    artifact_sha256: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
    held: false,
    held_code: null,
    state: 'pass',
    ready_to_deliver: true
  },
  binding: { state: 'verified', source: 'explicit_membership', rebind_required: false },
  acceptance: { revision: 3, artifact_sha256: 'abc', manager: 'manager-a', current: true },
  final_report: null,
  delivery_readiness: {
    state: 'ready',
    code: null,
    ready: true,
    current_identity: { revision: 3, verdict_run_id: 31, verdict_kind: 'PASS' },
    delivered_identity: null
  }
}
state.query.detail = detailBase([readyTask])
text = textOf(page.render())
check('ready state is labelled as manager-accepted evidence', text.includes('manager-accepted evidence'))
check('ready state names the review run', text.includes('review run 31'))
check('ready state says the SHA is worker-declared', text.includes('worker-declared SHA-256'))
check('ready state carries the no-byte-attestation caveat', text.includes('not a reviewer byte attestation'))
check('ready state is not rendered as delivered', !text.includes('final report: delivered') && !text.includes('already delivered'))
check('ready text never claims reviewer verification', !/reviewer (has )?verified/i.test(text))

const deliveredTask = {
  ...readyTask,
  task_id: 'r2',
  title: 'Delivered card',
  final_report: {
    state: 'confirmed',
    action: 'delivered',
    code: null,
    identity_conflict: false,
    delivered_identity: { task_id: 'r2', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' },
    report: {
      schema: 'department-hub/final/v2',
      generation: 5,
      delivered_at: Math.floor(Date.now() / 1000) - 20,
      delivered_at_iso: '2026-11-05T07:01:00Z',
      summary: 'Accepted report',
      summary_chars: 15,
      identity: { task_id: 'r2', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' },
      report_kind: 'accepted_artifact_identity',
      size_bytes: 512
    },
    report_state: 'ok',
    report_code: null,
    delivered_at: Math.floor(Date.now() / 1000) - 20
  },
  delivery_readiness: {
    state: 'already_delivered',
    code: null,
    ready: false,
    delivered_identity: { task_id: 'r2', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' },
    stored_identity: { task_id: 'r2', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' }
  }
}
state.query.detail = detailBase([deliveredTask])
text = textOf(page.render())
check('a confirmed row renders as delivered', text.includes('final report: delivered'))
check(
  'the stored identity is shown in the canonical four-field form',
  text.includes('stored delivered identity: task r2 · revision 3 · run 31 · SHA-256 abcdef')
)
check(
  'already-delivered wording is explicit and says no duplicate was created',
  text.includes('Already delivered — existing report; no duplicate created')
)
check('the stored report summary is rendered', text.includes('Accepted report'))
check('the report schema is rendered', text.includes('department-hub/final/v2'))
check('the report generation is rendered', text.includes('generation 5'))
check('an already-delivered task is not offered as ready', !text.includes('Ready to deliver'))

const conflictTask = {
  ...readyTask,
  task_id: 'r3',
  title: 'Second generation card',
  final_report: {
    state: 'confirmed',
    action: 'delivered',
    code: null,
    identity_conflict: true,
    delivered_identity: { task_id: 'r3', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' },
    report: null,
    report_state: 'legacy',
    report_code: null
  },
  delivery_readiness: {
    state: 'held',
    code: 'delivery_identity_conflict',
    ready: false,
    current_identity: { task_id: 'r3', revision: 4, verdict_run_id: 44, artifact_sha256: 'fedcba' },
    stored_identity: { task_id: 'r3', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' },
    delivered_identity: { task_id: 'r3', revision: 3, verdict_run_id: 31, artifact_sha256: 'abcdef' }
  }
}
state.query.detail = detailBase([conflictTask])
text = textOf(page.render())
check('second generation is held with the identity code', text.includes('delivery held (delivery_identity_conflict)'))
check(
  'second generation explains the conflict and that the report is preserved',
  text.includes('A different identity was already delivered; original report preserved, cannot be overwritten')
)
check(
  'second generation keeps the stored identity visible',
  text.includes('stored delivered identity: task r3 · revision 3 · run 31 · SHA-256 abcdef')
)
check(
  'second generation shows the current identity separately',
  text.includes('current identity: task r3 · revision 4 · run 44 · SHA-256 fedcba')
)
check('second generation is never offered as ready', !text.includes('Ready to deliver'))

// ── v5: plugin-persisted report section, escaping, offline readability ─────
const htmlSentinel = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
const historyEntry = {
  task_id: 'r4',
  identity: { task_id: 'r4', revision: 7, verdict_run_id: 70, artifact_sha256: 'a'.repeat(64) },
  report: {
    schema: 'department-hub/final/v2',
    generation: 9,
    delivered_at: Math.floor(Date.now() / 1000) - 60,
    delivered_at_iso: '2026-11-05T07:00:00Z',
    summary: htmlSentinel,
    summary_chars: htmlSentinel.length,
    identity: { task_id: 'r4', revision: 7, verdict_run_id: 70, artifact_sha256: 'a'.repeat(64) },
    report_kind: 'accepted_artifact_identity',
    size_bytes: 900
  },
  report_state: 'ok',
  report_code: null,
  delivered_at: Math.floor(Date.now() / 1000) - 60
}
const offlinePayload = detailBase([{
  ...readyTask,
  task_id: 'r4',
  title: 'Card the board cannot read now',
  unknown: { code: 'unavailable', detail: 'board read failed' }
}])
offlinePayload.data.department.tasks = [{
  task_id: 'r4', role: 'work', unknown: { code: 'unavailable', detail: 'board read failed' }
}]
offlinePayload.data.department.final_report = {
  state: 'delivered',
  code: null,
  count: 1,
  delivered_count: 1,
  reports_available: 1,
  history: [historyEntry]
}
state.query.detail = offlinePayload
tree = page.render()
text = textOf(tree)
check('delivered reports section is visible', text.includes('Delivered — plugin-persisted reports'))
check(
  'delivered section names the plugin SQLite source, not a board comment',
  text.includes('deliveries.body_json') && text.includes('not a board comment')
)
check('delivered section shows the delivery time', text.includes('delivered '))
check(
  'delivered section shows the canonical stored identity',
  text.includes('task r4 · revision 7 · run 70 · SHA-256 aaaaaaaaaaaa')
)
check('delivered summary is still readable when the board read fails', text.includes(htmlSentinel))
check(
  'summary is rendered as plain text, never as HTML',
  !JSON.stringify(tree).includes('dangerouslySetInnerHTML') &&
    nodes(tree).every(n => ['img', 'script', 'iframe'].indexOf(String(n.type)) === -1)
)
check(
  'no HTML element is created from the sentinel',
  !nodes(tree).some(n => String(n.type).toLowerCase().includes('img'))
)

// report_too_large: the row is listed, but the content is not rendered.
const oversizedPayload = detailBase([{ ...readyTask, task_id: 'r5' }])
oversizedPayload.data.department.final_report = {
  ...offlinePayload.data.department.final_report,
  history: [{
    ...historyEntry,
    task_id: 'r5',
    report: null,
    report_state: 'report_too_large',
    report_code: 'report_too_large'
  }],
  report_too_large: 1
}
state.query.detail = oversizedPayload
text = textOf(page.render())
check('oversized report shows report_too_large', text.includes('report_too_large'))
check('oversized report content is not rendered', !text.includes(htmlSentinel))

const stateTask = (id, action, code, readiness) => ({
  ...readyTask,
  task_id: id,
  title: `State ${action}`,
  final_report: { state: action === 'in_flight' ? 'pending' : action, action, code, identity_conflict: false, delivered_identity: null },
  delivery_readiness: { state: readiness, code, ready: false, delivered_identity: null }
})
state.query.detail = detailBase([
  stateTask('u1', 'unknown', 'readback_not_found', 'unknown'),
  stateTask('dl1', 'dead_letter', 'retry_budget_exhausted', 'dead_letter'),
  stateTask('f1', 'in_flight', 'in_flight_unknown', 'unknown')
])
text = textOf(page.render())
check('unknown delivery keeps its code', text.includes('final report: unknown (readback_not_found)'))
check('unknown delivery is marked not-resent', text.includes('delivery unknown (readback_not_found) — not resent'))
check('dead-letter keeps its code', text.includes('final report: dead-letter (retry_budget_exhausted)'))
check('dead-letter is rendered as a delivery hold', text.includes('delivery dead-letter (retry_budget_exhausted)'))
check('in-flight is not rendered as delivered', text.includes('final report: in flight') && !text.includes('already delivered'))

// ── error + empty states ────────────────────────────────────────────────────
state.query.list = { data: undefined, isLoading: false, error: new Error('404 not found') }
text = textOf(page.render())
check('read failure is an explicit error state', text.includes('Cannot read departments') && text.includes('404 not found'))
check('read failure never renders an empty-list message', !text.includes('No departments configured'))

state.query.list = { data: { ...listPayload, departments: [], degraded: [] }, isLoading: false, error: null }
text = textOf(page.render())
check(
  'empty configuration is explained',
  text.includes('No departments configured') && text.includes('cannot write config')
)

const createToggle = nodes(page.render()).find(n => n.props['aria-label'] === 'New department')
check('new-department control is labelled', Boolean(createToggle))

if (failures.length) {
  console.log(`\n${failures.length} FAILED`)
  for (const failure of failures) console.log(` - ${failure}`)
  process.exit(1)
}
console.log('\nALL PASS')

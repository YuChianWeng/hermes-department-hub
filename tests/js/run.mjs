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
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginPath = join(here, '..', '..', 'desktop', 'department-hub', 'plugin.js')
const sdk = await import('./mocks/sdk.mjs')
const { state } = sdk

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
const nodes = node => flatten(node).filter(item => item.type !== undefined)

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
  i18n: {
    register: bundles => {
      state.bundles = bundles
      return () => {}
    },
    t: (key, ...args) =>
      typeof state.bundles.en[key] === 'function'
        ? state.bundles.en[key](...args)
        : state.bundles.en[key] || key
  },
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
const enKeys = Object.keys(state.bundles.en).sort()
const zhKeys = Object.keys(state.bundles['zh-hant'] || {}).sort()
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

const literalKeys = new Set()
for (const match of source.matchAll(/\bt\('([A-Za-z0-9_.]+)'/g)) literalKeys.add(match[1])
const missingEn = [...literalKeys].filter(key => !(key in state.bundles.en))
check('every literal t() key exists in en', missingEn.length === 0, missingEn.join(','))
const missingZh = [...literalKeys].filter(key => !(key in state.bundles['zh-hant']))
check('every literal t() key exists in zh-hant', missingZh.length === 0, missingZh.join(','))

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

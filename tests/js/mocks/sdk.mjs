// ─────────────────────────────────────────────────────────────────────────────
// THIS IS A STAND-IN, NOT TANSTACK QUERY (and not the real @hermes/plugin-sdk).
//
// It models ONLY the contract desktop/department-hub/plugin.js depends on:
//   * `useQuery` returns the current box (data / isLoading / isFetching / error)
//     and registers its queryKey, the way a mounted observer would;
//   * `useQueryClient().refetchQueries(filters)` matches by queryKey PREFIX,
//     awaits every match, and resolves — it NEVER rejects on a fetch failure;
//   * a failed fetch is reported in query state (`getQueryCache().findAll()` →
//     `state.status === 'error'`), because that is the only way to see it.
//
// Those two last points are not guesses. They were measured against the real
// @tanstack/query-core@5.104.1 (the version @tanstack/react-query@5.104.1
// depends on): `refetchQueries()` resolved to `undefined` while the matched
// query's state.status was 'error'. A try/catch around it would never fire.
//
// i18n is deliberately NOT stubbed with a lookup of our own. `usePluginI18n`
// and `ctx.i18n.t` both route through the REAL Desktop v0.23.0 resolver that
// `extract-i18n-resolver.mjs` slices verbatim out of the installed app source:
// `translatePlugin` → `translateFrom` → `resolvePath`, i.e. a dot path is
// walked into NESTED objects and a miss returns the raw key.
//
// This matters because a flat whole-string lookup (`bundle[key]`) here is
// exactly what let 40 flat dotted keys pass every mocked check while the real
// Desktop rendered `owner.unverified` literally. Do not reintroduce one.
import { DEFAULT_LOCALE } from './i18n-resolver.mjs'
import { registerPluginLocales, translatePlugin } from './i18n-plugin.mjs'

export const state = {
  // Separate boxes: the page's list query vs a department detail query.
  query: { list: { data: undefined, isLoading: false, error: null }, detail: { data: undefined, isLoading: false, error: null } },
  // The bundle object the plugin registered (for key-set assertions only —
  // resolution always goes through the real resolver, never through this map).
  bundles: {},
  locale: DEFAULT_LOCALE,
  navigated: [],
  rest: async () => ({}),
  // ── controllable query stand-in (reset with resetQueryLog()) ──
  refetches: [], // one entry per refetchQueries() call: { queryKey, matched: [[key]] }
  fetches: [], // one entry per query actually re-fetched: { queryKey, box }
  failBoxes: new Set(), // 'list' and/or 'detail' → that fetch fails, reported as state
  refetchThrows: false, // extra probe: refetchQueries rejects (NOT real v5, kept for hardening)
  refetchDelayMs: 0, // how long the "server" takes overall, driven by the fake clock
  fetchDelays: {}, // per box ('list'/'detail') latency, driven by the fake clock
  refetchCalls: 0
}

const registry = new Map() // serialized queryKey -> { queryKey, box }

const keyId = queryKey => JSON.stringify(queryKey)
const isPrefix = (queryKey, wanted) =>
  wanted.length <= queryKey.length && wanted.every((part, index) => queryKey[index] === part)

function register(key, box) {
  registry.set(keyId(key), { queryKey: key.slice(), box })
}

function matchedEntries(wanted, exact = false) {
  return [...registry.values()].filter(entry =>
    exact ? entry.queryKey.length === wanted.length && isPrefix(entry.queryKey, wanted) : isPrefix(entry.queryKey, wanted)
  )
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

/** One simulated fetch. Failure is STORED ON THE BOX, never thrown out:
 *  that is what the real library does for refetch()/refetchQueries(). */
async function runFetch(entry) {
  const box = state.query[entry.box]
  state.fetches.push({ queryKey: entry.queryKey.slice(), box: entry.box })
  box.isFetching = true
  try {
    const latency = state.fetchDelays[entry.box] || 0
    if (latency > 0) await delay(latency)
    if (state.failBoxes.has(entry.box)) {
      throw new Error(`stand-in fetch failed for ${entry.queryKey.join('/')}`)
    }
    box.error = null
    box.isLoading = false
    return { status: 'success', isError: false, error: null }
  } catch (error) {
    box.error = error
    return { status: 'error', isError: true, error }
  } finally {
    box.isFetching = false
  }
}

export const queryClient = {
  /** Real v5 semantics: prefix match, await all matches, resolve (undefined). */
  async refetchQueries(filters = {}) {
    const wanted = (filters && filters.queryKey) || []
    const matched = matchedEntries(wanted, Boolean(filters && filters.exact))
    state.refetchCalls += 1
    state.refetches.push({ queryKey: wanted.slice(), matched: matched.map(entry => entry.queryKey.slice()) })
    for (const entry of matched) state.query[entry.box].isFetching = true
    if (state.refetchDelayMs > 0) await delay(state.refetchDelayMs)
    if (state.refetchThrows) {
      for (const entry of matched) state.query[entry.box].isFetching = false
      throw new Error('stand-in refetchQueries rejected (not real v5 behaviour)')
    }
    await Promise.all(matched.map(entry => runFetch(entry)))
    return undefined
  },
  getQueryCache() {
    return {
      findAll(filters = {}) {
        const wanted = (filters && filters.queryKey) || []
        return matchedEntries(wanted, Boolean(filters && filters.exact)).map(entry => {
          const box = state.query[entry.box]
          return {
            queryKey: entry.queryKey.slice(),
            state: {
              status: box.error ? 'error' : box.data === undefined ? 'pending' : 'success',
              error: box.error || null
            }
          }
        })
      }
    }
  }
}

export function useQueryClient() {
  return queryClient
}

export function useQuery(options) {
  const key = (options && options.queryKey) || []
  const boxName = key.length > 2 ? 'detail' : 'list'
  register(key, boxName)
  const box = state.query[boxName]
  return {
    data: box.data,
    isLoading: Boolean(box.isLoading),
    isFetching: Boolean(box.isFetching),
    error: box.error || null,
    refetch: () => queryClient.refetchQueries({ queryKey: key, exact: true })
  }
}

export function resetQueryLog() {
  state.refetches = []
  state.fetches = []
  state.failBoxes = new Set()
  state.refetchThrows = false
  state.refetchDelayMs = 0
  state.fetchDelays = {}
  state.refetchCalls = 0
  // A fresh mount starts with an empty cache in these tests: leftovers from an
  // earlier scenario would otherwise be refetched by the prefix match.
  registry.clear()
}

export const host = {
  navigate: path => {
    state.navigated.push(path)
  }
}

export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'

/** Switch the locale the translators resolve against (mirrors the app's
 *  `display.language` being changed). */
export function setLocale(locale) {
  state.locale = locale
}

/** Mirror of `createPluginI18n` in apps/desktop/src/i18n/plugin-i18n.ts
 *  (L111-118): the disposer tracking is dropped, the resolution is identical. */
export function createPluginI18n(pluginId = 'department-hub') {
  return {
    register: bundles => {
      state.bundles = bundles
      return registerPluginLocales(pluginId, bundles)
    },
    onLocaleChange: () => () => {},
    registerAppLocale: () => () => {},
    t: (key, ...args) => translatePlugin(pluginId, state.locale, key, args)
  }
}

/** Mirror of `usePluginI18n` (L122-134): same `translatePlugin` call, with the
 *  locale read at call time instead of from a React store subscription. */
export function usePluginI18n(pluginId = 'department-hub') {
  return (key, ...args) => translatePlugin(pluginId, state.locale, key, args)
}

const passthrough = props => (props && props.children !== undefined ? props.children : '')
export const Button = passthrough
export const Input = props => props['aria-label'] || props.placeholder || ''
export const EmptyState = props => [props.title, props.description].filter(Boolean).join(' — ')
export const ErrorState = props => [props.title, props.description, props.children].filter(Boolean)
export const Skeleton = () => ''
export const Separator = () => ''
export const cn = (...parts) => parts.filter(Boolean).join(' ')
export const relativeTime = ms => {
  const diff = Math.round((ms - Date.now()) / 60000)
  return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(diff, 'minute')
}

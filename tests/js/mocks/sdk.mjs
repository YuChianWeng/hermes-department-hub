// Stubs for the exact @hermes/plugin-sdk surface desktop/plugin.js imports.
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
  rest: async () => ({})
}

export const host = {
  navigate: path => {
    state.navigated.push(path)
  }
}

export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'

export function useQuery(options) {
  const key = (options && options.queryKey) || []
  const box = key.length > 2 ? state.query.detail : state.query.list
  return {
    data: box.data,
    isLoading: Boolean(box.isLoading),
    isFetching: false,
    error: box.error || null,
    refetch: () => {}
  }
}

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

// Stubs for the exact @hermes/plugin-sdk surface desktop/plugin.js imports.
export const state = {
  // Separate boxes: the page's list query vs a department detail query.
  query: { list: { data: undefined, isLoading: false, error: null }, detail: { data: undefined, isLoading: false, error: null } },
  bundles: {},
  locale: 'en',
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

export function usePluginI18n() {
  return (key, ...args) => {
    const bundle = state.bundles[state.locale] || state.bundles.en || {}
    const value = bundle[key]
    if (typeof value === 'function') return value(...args)
    if (typeof value === 'string') return value
    return key
  }
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

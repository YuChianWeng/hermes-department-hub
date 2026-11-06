// GENERATED FILE — do not edit by hand.
// Produced by tests/js/mocks/extract-i18n-resolver.mjs from the Hermes Desktop
// v0.23.0 source, sliced verbatim by line range and type-stripped by Node.
//
// The real Desktop resolver is the ONLY authority for how plugin keys resolve:
// a dot path is walked into NESTED objects and a miss returns the raw key.
// Re-generate with:  node tests/js/mocks/extract-i18n-resolver.mjs
//
// Part: isRecord, DEFAULT_LOCALE, resolvePath, render, translateFrom
//   apps/shared/src/i18n.ts (L17-19) sha256 346a7d455250b67af2a72c34b039e588a48b241993e7a3556762f680e2faebca
//   apps/desktop/src/i18n/languages.ts (L15) sha256 62f368da6a46fbb5b72c1bbfa6bd8c8d30f5998ec598386d04e00fec23399f2b
//   apps/desktop/src/i18n/runtime.ts (L17-58) sha256 2bc3965d90d9d2939ae1804863a1012f77a2da25cb17a638ce47c9b6b279998f
//   apps/desktop/src/i18n/plugin-i18n.ts (L63,L69-107) sha256 196e727b2de0caa3c5aaa6213ac3912519b3f8f11b5c7fe2fd81d080968d69cf

export function isRecord(value         )                                   {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export const DEFAULT_LOCALE                = 'en'

/** Walk a dot-path (`a.b.c`) into a nested message tree. */
function resolvePath(source         , key        )          {
  return key.split('.').reduce         ((current, part) => (isRecord(current) ? current[part] : undefined), source)
}

/** A string is returned as-is, a function is called with `args`, else `null`. */
function render(value         , args           )                {
  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'function') {
    return (value                                  )(...args)
  }

  return null
}

/** The active → DEFAULT → key resolution every translator shares. `source`
 *  yields a message tree per locale — the app catalog, or a plugin's bundles. */
export function translateFrom(
  source                             ,
  locale        ,
  key        ,
  args           
)         {
  const active = render(resolvePath(source(locale), key), args)

  if (active !== null) {
    return active
  }

  if (locale !== DEFAULT_LOCALE) {
    const fallback = render(resolvePath(source(DEFAULT_LOCALE), key), args)

    if (fallback !== null) {
      return fallback
    }
  }

  return key
}

// GENERATED FILE — do not edit by hand.
// Produced by tests/js/mocks/extract-i18n-resolver.mjs from the Hermes Desktop
// v0.23.0 source, sliced verbatim by line range and type-stripped by Node.
//
// The real Desktop resolver is the ONLY authority for how plugin keys resolve:
// a dot path is walked into NESTED objects and a miss returns the raw key.
// Re-generate with:  node tests/js/mocks/extract-i18n-resolver.mjs
//
// Part: registry, mergeMessages, registerPluginLocales, translatePlugin
//   apps/shared/src/i18n.ts (L17-19) sha256 346a7d455250b67af2a72c34b039e588a48b241993e7a3556762f680e2faebca
//   apps/desktop/src/i18n/languages.ts (L15) sha256 62f368da6a46fbb5b72c1bbfa6bd8c8d30f5998ec598386d04e00fec23399f2b
//   apps/desktop/src/i18n/runtime.ts (L17-58) sha256 2bc3965d90d9d2939ae1804863a1012f77a2da25cb17a638ce47c9b6b279998f
//   apps/desktop/src/i18n/plugin-i18n.ts (L63,L69-107) sha256 196e727b2de0caa3c5aaa6213ac3912519b3f8f11b5c7fe2fd81d080968d69cf

import { translateFrom } from './i18n-resolver.mjs'

// The real file imports this atom from 'nanostores'; the registry version is
// only a re-render token for React, which these plain-ESM tests never mount.
const $version = { value: 0, get() { return this.value }, set(next) { this.value = next } }

const registry = new Map                                     ()

function isRecord(value         )                                   {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function mergeMessages(base                , overrides                )                 {
  const result                 = { ...base }

  for (const [key, value] of Object.entries(overrides)) {
    const prev = result[key]
    result[key] = isRecord(prev) && isRecord(value) ? mergeMessages(prev, value) : value
  }

  return result
}

export function registerPluginLocales(pluginId        , bundles                     )             {
  const byLocale = registry.get(pluginId) ?? new Map                        ()
  registry.set(pluginId, byLocale)

  for (const [locale, messages] of Object.entries(bundles)                                          ) {
    if (!messages) {
      continue
    }

    const prev = byLocale.get(locale)
    byLocale.set(locale, prev ? mergeMessages(prev, messages) : messages)
  }

  $version.set($version.get() + 1)

  return () => {
    registry.delete(pluginId)
    $version.set($version.get() + 1)
  }
}

export function translatePlugin(pluginId        , locale        , key        , args           )         {
  return translateFrom(l => registry.get(pluginId)?.get(l), locale, key, args)
}

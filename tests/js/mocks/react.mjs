// Hooks that return their initial value; the harness calls components directly.
export function useState(value) {
  const initial = typeof value === 'function' ? value() : value
  return [initial, () => {}]
}
export function useMemo(fn) {
  return fn()
}
export function useEffect() {}
export function useRef(value) {
  return { current: value }
}
export default { useState, useMemo, useEffect, useRef }

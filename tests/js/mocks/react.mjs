// ─────────────────────────────────────────────────────────────────────────────
// THIS IS A STAND-IN, NOT REACT.
//
// The harness calls contributed components as plain functions and flattens the
// returned jsx() tree, so there is no scheduler and no reconciler here. What
// this file models is the ONE contract desktop/department-hub/plugin.js relies
// on: hook slots are per component FUNCTION and survive across renders, and a
// setState call records a value the next render must observe.
//
// It is deliberately dumb:
//   - setState never schedules anything. The harness re-renders by calling the
//     component again (that is what a React update would do).
//   - slots are keyed by component function identity, not by instance. No
//     component here is rendered in a list or twice at once, so a list-keyed
//     model would add nothing.
//   - useEffect stores the effect for `runEffects()`/`unmount()`; it does not
//     run on its own.
//
// The previous version returned `[initial, () => {}]` and a fresh `{current}`
// every render. That made `useState`/`useRef` impossible to observe, which is
// how a Refresh button with no in-progress state could pass every check.
// ─────────────────────────────────────────────────────────────────────────────

const frames = new Map() // component function -> { cursor, slots, effects }
const stack = []

const describe = value => (typeof value === 'function' ? value.name || 'anonymous' : String(value))

function currentFrame(hook) {
  const frame = stack[stack.length - 1]
  if (!frame) {
    throw new Error(`${hook}() called outside a component render — call the component through the harness`)
  }
  return frame
}

/** Run `fn` as one component render, so its hooks get their own slots.
 *  Returns whatever `run` returns; the jsx tree shape is unchanged. */
export function withComponent(component, run) {
  const key = component
  if (!frames.has(key)) frames.set(key, { cursor: 0, slots: [], effects: [] })
  const frame = frames.get(key)
  frame.cursor = 0
  stack.push(frame)
  try {
    return run()
  } finally {
    stack.pop()
  }
}

/** Forget every component's hook state (a fresh "mount"). */
export function resetHooks() {
  frames.clear()
  stack.length = 0
}

/** Run the effects a component registered (cleanup functions are returned). */
export function runEffects(component) {
  const frame = frames.get(component)
  if (!frame) return []
  const cleanups = []
  for (const effect of frame.effects.splice(0)) {
    const cleanup = effect()
    if (typeof cleanup === 'function') cleanups.push(cleanup)
  }
  return cleanups
}

/** Current hook state for assertions/debugging (never used to fake behaviour). */
export function peekHooks(component) {
  const frame = frames.get(component)
  return frame ? frame.slots.map(slot => ('value' in slot ? slot.value : slot)) : []
}

export function useState(initial) {
  const frame = currentFrame('useState')
  const index = frame.cursor++
  if (!(index in frame.slots)) {
    frame.slots[index] = { value: typeof initial === 'function' ? initial() : initial }
  }
  const slot = frame.slots[index]
  const setState = next => {
    slot.value = typeof next === 'function' ? next(slot.value) : next
  }
  return [slot.value, setState]
}

export function useRef(value) {
  const frame = currentFrame('useRef')
  const index = frame.cursor++
  if (!(index in frame.slots)) frame.slots[index] = { current: value }
  return frame.slots[index]
}

export function useMemo(fn) {
  currentFrame('useMemo').cursor++ // hook order only; nothing is cached
  return fn()
}

export function useEffect(effect) {
  const frame = currentFrame('useEffect')
  frame.cursor++
  frame.effects.push(effect)
}

export function useCallback(fn) {
  currentFrame('useCallback').cursor++
  return fn
}

export { describe }
export default { useState, useMemo, useEffect, useRef, useCallback, withComponent, resetHooks, runEffects, peekHooks }

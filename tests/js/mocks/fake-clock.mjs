// ─────────────────────────────────────────────────────────────────────────────
// THIS IS A STAND-IN, NOT A TIMER LIBRARY.
//
// A ~60-line fake clock, so the suite can prove two timing rules without any
// real sleep:
//   * the Refresh button stays in its in-progress state for AT LEAST 600 ms;
//   * the "Updated HH:MM:SS" confirmation disappears after ~3 s.
//
// It patches ONLY the globals the page reaches for at call time: setTimeout,
// clearTimeout and Date.now. `new Date()` (no argument) is deliberately NOT
// patched — it bypasses Date.now in V8 — which is why the page builds its
// completion stamp as `new Date(Date.now())`: that keeps the stamp on this
// clock instead of on wall time. If the page ever switched to `new Date()`
// these tests would still pass but would measure wall time, so the completion
// timestamp is asserted against the fake epoch.
// ─────────────────────────────────────────────────────────────────────────────

const realSetTimeout = globalThis.setTimeout
const realClearTimeout = globalThis.clearTimeout
const realSetImmediate = globalThis.setImmediate

let installed = false
let previousNow = null
let nowMs = 0
let sequence = 0
const timers = new Map()

const drain = () => new Promise(resolve => realSetImmediate(resolve))

function fakeSetTimeout(fn, ms = 0, ...args) {
  const id = ++sequence
  timers.set(id, { id, at: nowMs + Math.max(0, Number(ms) || 0), fn, args })
  return id
}

function fakeClearTimeout(id) {
  timers.delete(id)
}

export function installClock(startMs = 0) {
  if (installed) throw new Error('fake clock already installed')
  installed = true
  nowMs = startMs
  previousNow = Date.now
  Date.now = () => nowMs
  globalThis.setTimeout = fakeSetTimeout
  globalThis.clearTimeout = fakeClearTimeout
  return { advance, now: () => nowMs, setNow: value => { nowMs = value }, pending: () => timers.size, uninstall }
}

/** Move the clock forward, firing every timer that comes due (including timers
 *  scheduled by callbacks that fire during this advance), draining microtasks
 *  after each one so promise continuations see the new time. */
export async function advance(ms) {
  const target = nowMs + Math.max(0, Number(ms) || 0)
  await drain()
  for (;;) {
    const due = [...timers.values()].filter(timer => timer.at <= target).sort((a, b) => a.at - b.at || a.id - b.id)
    if (due.length === 0) break
    const timer = due[0]
    timers.delete(timer.id)
    nowMs = Math.max(nowMs, timer.at)
    timer.fn(...timer.args)
    await drain()
  }
  nowMs = target
  await drain()
}

export function pendingTimers() {
  return timers.size
}

export function uninstall() {
  if (!installed) return
  installed = false
  timers.clear()
  globalThis.setTimeout = realSetTimeout
  globalThis.clearTimeout = realClearTimeout
  if (previousNow) Date.now = previousNow
}

export default { installClock, advance, pendingTimers, uninstall }

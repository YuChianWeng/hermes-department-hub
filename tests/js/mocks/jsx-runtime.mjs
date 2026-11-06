// Minimal jsx()/jsxs() recorder used by the component-logic test.
export function jsx(type, props, key) {
  return { type, props: props || {}, key }
}
export const jsxs = jsx
export const Fragment = 'fragment'

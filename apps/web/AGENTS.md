# Frontend Conventions

These rules apply only inside `apps/web`, not to Python/backend code.

- Prefer `const` arrow functions for components, hooks, helpers and callbacks.
  Keep constructors and class methods when their prototype/`this` semantics matter.
- In React component/custom-hook bodies, declare state, refs and derived values
  first, then handler functions, then effect-only hooks (`useEffect`,
  `useLayoutEffect`, `useInsertionEffect`), and finally render guards and JSX.
  Preserve hook order and do not move unsafe expressions above their guards.
- Always use braces for `if` and `else`, including one-line returns and throws.
- Use Prettier for readable multiline code and JSX; do not compress components
  onto a few lines.
- Before committing, run `npm run lint`, `npm run format:check`, `npm test`,
  and `npm run build`. Check function initialization order after replacing
  hoisted function declarations with arrows.

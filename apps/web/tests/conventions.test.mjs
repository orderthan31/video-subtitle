import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Linter } from 'eslint';
import config from '../eslint.config.mjs';

const lint = (source) => new Linter().verify(source, config, { filename: 'src/Fixture.tsx' });

test('frontend conventions reject unbraced guards and declared functions', () => {
  const messages = lint('function Component() { if (true) return null; return null; }');
  assert.ok(messages.some((message) => message.ruleId === 'curly'));
  assert.ok(messages.some((message) => message.ruleId === 'func-style'));
});

test('frontend conventions reject declarations after effect-only hooks', () => {
  for (const hook of ['useEffect', 'useLayoutEffect', 'useInsertionEffect']) {
    const messages = lint(
      `const Component = () => { ${hook}(() => {}, []); const value = 1; return <div>{value}</div>; };`,
    );
    assert.ok(messages.some((message) => message.ruleId === 'frontend/effect-order'));
  }
});

test('frontend conventions accept values then handlers then effects and JSX', () => {
  const messages = lint(`const Component = () => {
    const value = 1;
    const handle = () => { if (value) { return value; } return 0; };
    useEffect(() => { handle(); }, []);
    return <div>{value}</div>;
  };`);
  assert.deepEqual(messages, []);
});

test('constructors and prototype methods retain their existing semantics', () => {
  assert.deepEqual(
    lint('class Queue { constructor() {} run() { if (this.ready) { return; } } }'),
    [],
  );
});

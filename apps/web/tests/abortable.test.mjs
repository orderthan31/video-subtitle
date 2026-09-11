import {test} from 'node:test';
import assert from 'node:assert/strict';
import {abortable} from '../src/abortable.ts';

test('cancel releases a stalled operation even when it ignores abort', async () => {
  const parent = new AbortController();
  let signal;
  const pending = abortable(value => {
    signal = value;
    return new Promise(() => {});
  }, parent.signal, 0);
  parent.abort();
  await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(signal.aborted, true);
});

test('a stalled upload request times out with a retryable error', async () => {
  let signal;
  await assert.rejects(abortable(value => {
    signal = value;
    return new Promise(() => {});
  }, undefined, 10), TypeError);
  assert.equal(signal.aborted, true);
});

test('an already cancelled operation never starts', async () => {
  const parent = new AbortController();
  parent.abort();
  await assert.rejects(abortable(async () => assert.fail('started'), parent.signal), {name: 'AbortError'});
});

test('success removes parent listener and deadline', async () => {
  const parent = new AbortController();
  let signal;
  assert.equal(await abortable(async value => {signal = value; return 42;}, parent.signal, 10), 42);
  parent.abort();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(signal.aborted, false);
});

test('late failure after cancellation is consumed', async () => {
  const parent = new AbortController();
  let reject;
  const pending = abortable(() => new Promise((_, fail) => {reject = fail;}), parent.signal, 0);
  parent.abort();
  await assert.rejects(pending, {name: 'AbortError'});
  reject(new Error('late network failure'));
  await new Promise(resolve => setTimeout(resolve, 0));
});

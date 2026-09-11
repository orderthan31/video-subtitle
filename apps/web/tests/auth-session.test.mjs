import {test} from 'node:test';
import assert from 'node:assert/strict';
import {authenticatedFetch, sessionEvents, setCsrfToken} from '../src/auth-session.ts';

test('authenticated requests include cookies and CSRF only for unsafe methods', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => {calls.push(init); return new Response('{}');});
  setCsrfToken('test-csrf');
  for (const method of ['GET', 'HEAD', 'OPTIONS', 'POST', 'put', 'DELETE']) {
    await authenticatedFetch('/api/jobs', {method, headers: {'Content-Type': 'application/json'}});
  }
  assert.deepEqual(calls.map(c => c.headers.get('X-CSRF-Token')), [null, null, null, 'test-csrf', 'test-csrf', 'test-csrf']);
  for (const call of calls) {
    assert.equal(call.headers.get('X-Session-Token'), 'test-csrf');
    assert.equal(call.credentials, 'include');
    assert.equal(call.cache, 'no-store');
    assert.equal(call.headers.get('Content-Type'), 'application/json');
  }
  setCsrfToken();
  await authenticatedFetch('/api/uploads', {method: 'POST'});
  assert.equal(calls.at(-1).headers.get('X-CSRF-Token'), null);
  assert.equal(calls.at(-1).headers.get('X-Session-Token'), null);
  setCsrfToken('old-session');
  await authenticatedFetch('/api/auth/login', {method: 'POST'}, true);
  assert.equal(calls.at(-1).headers.get('X-Session-Token'), null);
  setCsrfToken();
});

test('unauthorized session expires once but login failures do not clear it', async t => {
  let expired = 0;
  const handler = () => expired++;
  sessionEvents.addEventListener('expired', handler);
  t.after(() => {sessionEvents.removeEventListener('expired', handler); setCsrfToken();});
  t.mock.method(globalThis, 'fetch', async () => new Response('{}', {status: 401}));
  setCsrfToken('current');
  await authenticatedFetch('/api/auth/login', {method: 'POST'}, true);
  assert.equal(expired, 0);
  await authenticatedFetch('/api/jobs');
  await authenticatedFetch('/api/jobs');
  assert.equal(expired, 1);
});

test('a delayed unauthorized response cannot expire a newer login', async t => {
  let finish, expired = 0;
  const handler = () => expired++;
  sessionEvents.addEventListener('expired', handler);
  t.after(() => {sessionEvents.removeEventListener('expired', handler); setCsrfToken();});
  t.mock.method(globalThis, 'fetch', () => new Promise(resolve => {finish = resolve;}));
  setCsrfToken('old');
  const pending = authenticatedFetch('/api/jobs');
  setCsrfToken('new');
  finish(new Response('{}', {status: 401}));
  await pending;
  assert.equal(expired, 0);
});

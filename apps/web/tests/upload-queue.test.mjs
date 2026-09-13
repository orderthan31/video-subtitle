import { test } from 'node:test';
import assert from 'node:assert/strict';
import { UploadQueue } from '../src/upload-queue.ts';

const file = (name) => new File(['video'], name);
const flush = () => new Promise((resolve) => setImmediate(resolve));

test('uploads run serially with independent settings and release completed files', async () => {
  const started = [],
    releases = [];
  const queue = new UploadQueue(async (item) => {
    started.push([item.name, item.payload.video_description]);
    await new Promise((resolve) => releases.push(resolve));
  });
  const options = { video_description: 'first' };
  queue.add(file('one.mp4'), options);
  options.video_description = 'second';
  queue.add(file('two.mp4'), options);
  assert.deepEqual(started, [['one.mp4', 'first']]);
  assert.equal(queue.items[1].state, 'waiting');
  releases.shift()();
  await flush();
  assert.deepEqual(started, [
    ['one.mp4', 'first'],
    ['two.mp4', 'second'],
  ]);
  assert.equal(queue.items[0].file, undefined);
  releases.shift()();
  await flush();
  assert.ok(queue.items.every((item) => item.state === 'done'));
});

test('failed upload does not block the queue; retry keeps its identity and server job', async () => {
  let fail = true;
  const queue = new UploadQueue(async (item) => {
    item.jobId ||= item.name;
    if (item.name === 'one.mp4' && fail) {
      throw new Error('offline');
    }
  });
  queue.add(file('one.mp4'), {});
  queue.add(file('two.mp4'), {});
  await flush();
  const key = queue.items[0].key;
  assert.equal(queue.items[0].state, 'error');
  assert.equal(queue.items[1].state, 'done');
  fail = false;
  queue.resume(key);
  await flush();
  assert.equal(queue.items[0].key, key);
  assert.equal(queue.items[0].jobId, 'one.mp4');
  assert.equal(queue.items[0].state, 'done');
  queue.forgetCompleted(['one.mp4', 'two.mp4']);
  assert.equal(queue.items.length, 0);
});

test('hidden page stops admissions, then resumes without parallel transfers', async () => {
  let count = 0,
    active = 0,
    peak = 0;
  const queue = new UploadQueue(async (_item, signal) => {
    count++;
    active++;
    peak = Math.max(active, peak);
    await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
    active--;
  });
  queue.add(file('one.mp4'), {});
  queue.add(file('two.mp4'), {});
  queue.suspend(true);
  await flush();
  assert.equal(count, 1);
  assert.equal(queue.items[0].state, 'waiting');
  queue.suspend(false);
  await flush();
  assert.equal(count, 2);
  assert.equal(peak, 1);
  queue.pause(queue.items[0].key);
  await flush();
  assert.equal(queue.items[0].state, 'paused');
  assert.equal(count, 3);
  queue.dispose();
  await flush();
});

test('strict-mode setup cleanup setup does not disable queue and resume avoids duplicates', async () => {
  const queue = new UploadQueue(async () => {});
  queue.dispose();
  queue.activate();
  queue.suspend(true);
  queue.add(file('one.mp4'), {}, 'existing');
  queue.add(file('one.mp4'), {}, 'existing');
  assert.equal(queue.items.length, 1);
  queue.suspend(false);
  await flush();
  assert.equal(queue.items[0].state, 'done');
});

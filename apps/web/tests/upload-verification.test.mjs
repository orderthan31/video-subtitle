import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { blockSize, verifyUploadedPrefix } from '../src/upload-verification.ts';

test('hashes every stored byte in bounded blocks, including the partial tail', async () => {
  const data = Buffer.alloc(blockSize + 19, 23);
  data[blockSize] = 77;
  const file = new Blob([data, Buffer.from('not uploaded yet')]);
  const blocks = [];
  await verifyUploadedPrefix(file, data.length, new AbortController().signal, async (block) =>
    blocks.push(block),
  );
  assert.deepEqual(
    blocks.map((b) => [b.offset, b.length]),
    [
      [0, blockSize],
      [blockSize, 19],
    ],
  );
  for (const block of blocks) {
    assert.equal(block.uploaded_bytes, data.length);
    assert.equal(
      block.sha256,
      createHash('sha256')
        .update(data.subarray(block.offset, block.offset + block.length))
        .digest('hex'),
    );
  }
});

test('same-size changed content stops verification without subsequent blocks', async () => {
  let calls = 0;
  const file = new Blob([Buffer.alloc(blockSize + 1, 1)]);
  await assert.rejects(
    verifyUploadedPrefix(file, file.size, new AbortController().signal, async (block) => {
      calls++;
      const original = createHash('sha256').update(Buffer.alloc(block.length, 2)).digest('hex');
      if (block.sha256 !== original) {
        throw new Error('different file');
      }
    }),
    /different file/,
  );
  assert.equal(calls, 1);
});

test('pause cancels before the next block', async () => {
  const abort = new AbortController();
  let calls = 0;
  const file = new Blob([Buffer.alloc(blockSize + 1)]);
  await assert.rejects(
    verifyUploadedPrefix(file, file.size, abort.signal, async () => {
      calls++;
      abort.abort();
    }),
    { name: 'AbortError' },
  );
  assert.equal(calls, 1);
});

test('new uploads need no verification and invalid sizes are rejected', async () => {
  const file = new Blob(['abc']);
  const verify = async () => assert.fail('unexpected verification');
  const signal = new AbortController().signal;
  await verifyUploadedPrefix(file, 0, signal, verify);
  for (const size of [-1, 4, NaN, 1.5]) {
    await assert.rejects(verifyUploadedPrefix(file, size, signal, verify));
  }
});

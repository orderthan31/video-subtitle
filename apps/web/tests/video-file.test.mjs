import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isVideoFile } from '../src/video-file.ts';
import { UploadQueue } from '../src/upload-queue.ts';

test('video selection accepts real filenames and video MIME types without renaming', async () => {
  const seen = [];
  const queue = new UploadQueue(async (item) => {
    seen.push(item.name);
  });
  for (const name of ['여행 영상 (최종).MP4', '1001.mp4', '1000012345.mp4']) {
    const file = new File(['video'], name, { type: '' });
    assert.equal(isVideoFile(file), true);
    queue.add(file, {});
  }
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(seen, ['여행 영상 (최종).MP4', '1001.mp4', '1000012345.mp4']);
  assert.equal(isVideoFile({ name: 'provider-video', type: 'video/mp4' }), true);
  assert.equal(isVideoFile({ name: 'video.mkv', type: 'application/octet-stream' }), true);
});

test('generic file picker rejects non-video selections', () => {
  for (const file of [
    { name: 'photo.jpg', type: 'image/jpeg' },
    { name: 'subtitle.srt', type: '' },
    { name: 'script.mp4.exe', type: 'application/octet-stream' },
    { name: 'notes.txt', type: 'text/plain' },
  ]) {
    assert.equal(isVideoFile(file), false);
  }
});

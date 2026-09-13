const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const declaration = source.match(/const zeroSecondVideo = ([^;]+);/)[1];
const zeroSecondVideo = vm.runInNewContext(`(${declaration})`);
test('clips whose player shows 0:00 do not offer playback', () => {
  for (const duration of [0, 0.04, 0.11, 0.999]) assert.equal(zeroSecondVideo(duration), true);
  for (const duration of [1, 3.39, null, undefined, NaN, Infinity, -1]) assert.equal(zeroSecondVideo(duration), false);
});

const playbackSource = vm.runInNewContext('(' + source.match(/const playbackSource = ([\s\S]*?);/)[1] + ')');
test('live speed restores the original file and normal playback; fast mode restores preview', () => {
  const run = {video_url: 'https://example.com/previews/run.mp4', compressed: true};
  assert.equal(playbackSource(run, true).url, 'https://example.com/videos/run.mp4');
  assert.equal(playbackSource(run, true).rate, 1);
  assert.equal(playbackSource(run, false).url, run.video_url);
  assert.equal(playbackSource(run, false).rate, 1);
  const original = {video_url: 'https://example.com/videos/run.mp4', compressed: false};
  assert.equal(playbackSource(original, true).rate, 1);
  assert.equal(playbackSource(original, false).rate, 10);
});

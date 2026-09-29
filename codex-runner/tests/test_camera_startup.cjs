const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(process.env.YAM_TEST_HOSTED_JS || path.join(__dirname, '../static/hosted.js'), 'utf8');
const panels = source.slice(source.indexOf('  function synchronizedPanels('), source.indexOf('  function camera(image)'));

function harness({ready = false} = {}) {
  const draws = [], images = [], timers = new Map(), labels = [];
  let callback, id = 0;
  const tiles = ['top', 'observer', 'left', 'right'].map(role => ({
    dataset: {cameraRole: role}, addEventListener() {},
    getContext: () => ({drawImage: (...args) => draws.push({role, args})}),
  }));
  tiles.forEach(() => labels.push({textContent: ''}));
  const video = {readyState: ready ? 2 : 0, paused: true, currentTime: 0};
  const atlas = {getContext: () => ({drawImage: (...args) => draws.push({role: 'atlas', args})})};
  const context = {
    selectedRobot: 'yam-1', Date, encodeURIComponent, video,
    label: {textContent: 'Loading video'},
    setTimeout(fn) {timers.set(++id, fn); return id;}, clearTimeout(id) {timers.delete(id);},
    requestAnimationFrame(fn) {callback = fn; return 1;}, cancelAnimationFrame() {callback = null;},
    Image: class {
      constructor() {images.push(this);}
      async decode() {}
      removeAttribute() {this.removed = true;}
    },
    window: {addEventListener() {}},
    document: {
      hidden: false, createElement: () => atlas,
      querySelectorAll: selector => selector === 'canvas[data-sync-tile]' ? tiles
        : selector === '[data-sync-status]' ? labels : [],
    },
  };
  vm.runInNewContext(panels + '\nsynchronizedPanels(video, label);', context);
  return {context, video, atlas, tiles, labels, images, draws, timers, paint() {callback?.();}};
}

test('real snapshots fill the panels before video connects and remain clearly labeled', async () => {
  const h = harness();
  assert.equal(h.images.length, 4);
  assert.equal(h.draws.length, 0);
  for (const [i, image] of h.images.entries()) {
    const url = new URL(image.src, 'https://playground.test');
    assert.equal(url.pathname, '/api/monitor/cameras/' + h.tiles[i].dataset.cameraRole);
    assert.equal(url.searchParams.get('robot_id'), 'yam-1');
    await image.onload();
  }
  h.paint();
  assert.equal(h.draws.length, 4);
  assert(h.draws.every((draw, i) => draw.args[0] === h.images[i]));
  assert(h.tiles.every(tile => tile.dataset.frameSource === 'snapshot' && !tile.dataset.presentedFrame));
  assert(h.labels.every(label => label.textContent === 'Still image · Loading video'));
  assert.equal(h.timers.size, 0);
});

test('the first decoded video frame draws at time zero, even before autoplay starts', () => {
  const h = harness({ready: true});
  assert.equal(h.images.length, 0);
  assert.equal(h.draws.length, 5);
  assert.equal(h.draws[0].args[0], h.video);
  assert(h.draws.slice(1).every(draw => draw.args[0] === h.atlas));
  assert(h.tiles.every(tile => tile.dataset.frameSource === 'video' && tile.dataset.presentedFrame === '1'));
  h.paint();
  assert.equal(h.draws.length, 5, 'paused frame is not repeatedly counted');
  h.video.paused = false; h.video.currentTime = .1; h.paint();
  assert.equal(h.draws.length, 10);
});

test('a late snapshot cannot overwrite synchronized video', async () => {
  const h = harness();
  let finish;
  h.images[0].decode = () => new Promise(resolve => {finish = resolve;});
  const loading = h.images[0].onload();
  h.video.readyState = 2; h.paint();
  finish(); await loading;
  assert.equal(h.draws.length, 5);
  assert(h.tiles.every(tile => tile.dataset.frameSource === 'video'));
  assert(h.images.every(image => image.removed));
  assert.equal(h.timers.size, 0);
});

test('snapshot errors and timeout do not block live video', () => {
  const h = harness();
  h.images[0].onerror();
  for (const timeout of [...h.timers.values()]) timeout();
  h.paint();
  assert.equal(h.draws.length, 0);
  assert(h.labels.every(label => label.textContent === 'Loading video'));
  h.video.readyState = 2; h.paint();
  assert.equal(h.draws.length, 5);
});

test('switching robots or stopping discards in-flight snapshots', async () => {
  for (const action of ['switch', 'stop']) {
    const h = harness();
    let finish;
    h.images[0].decode = () => new Promise(resolve => {finish = resolve;});
    const loading = h.images[0].onload();
    if (action === 'switch') h.context.selectedRobot = 'another-robot';
    else h.video.yamStopPainting();
    finish(); await loading;
    assert.equal(h.draws.length, 0);
    h.video.yamStopPainting();
    assert.equal(h.timers.size, 0);
  }
});

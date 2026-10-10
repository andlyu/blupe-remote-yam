const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/../static/hosted.js', 'utf8');
const code = source.slice(source.indexOf('  function cameraExpansion('), source.indexOf('  function synchronizedPanels('));

function harness({phone = false, native} = {}) {
  let focused, nativeCalls = 0, plays = 0;
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.listeners = {}; }
    get isConnected() { return this === body || !!this.parent?.isConnected; }
    append(...nodes) { nodes.forEach(node => { node.remove(); node.parent = this; this.children.push(node); }); }
    remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
    before(node) { node.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this), 0, node); }
    replaceWith(node) { this.before(node); this.remove(); }
    closest() { return this.tag === 'figure' ? this : this.parent?.closest(); }
    querySelector() { return this.children.find(node => node.tag === 'button'); }
    setAttribute(name, value) { this.attrs[name] = value; }
    getAttribute(name) { return this.attrs[name]; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    fire(name, event = {}) { return this.listeners[name]?.({target:this, ...event}); }
    focus() { focused = this; }
    showModal() { this.open = true; }
    close() { this.open = false; this.fire('close'); }
  }
  const body = new Node('body'), grid = new Node('section'), panel = new Node('figure');
  const tile = new Node('canvas'), button = new Node('button'), neighbor = new Node('figure');
  panel.dataset.cameraRole = 'left'; button.setAttribute('aria-label', 'Expand left camera');
  panel.append(tile, button); grid.append(panel, neighbor); body.append(grid);
  const classes = new Set();
  body.classList = {add: name => classes.add(name), remove: name => classes.delete(name)};
  const document = {body, createComment: () => new Node('comment'), createElement: tag => new Node(tag),
    async exitFullscreen() { document.fullscreenElement = null; }};
  if (native) panel.requestFullscreen = async () => { nativeCalls++; await native(); document.fullscreenElement = panel; };
  const context = {document, window:{matchMedia: () => ({matches:phone})}, tiles:[tile], video:{async play() { plays++; }}};
  vm.runInNewContext(code + '\nstop = cameraExpansion(video, tiles);', context);
  return {body, grid, panel, tile, button, neighbor, document, classes,
    stop: context.stop, get dialog() { return body.children.find(node => node.tag === 'dialog'); },
    get focused() {return focused;}, get nativeCalls() {return nativeCalls;}, get plays() {return plays;}};
}

test('phone thumbnail opens the same live canvas in a modal and Close restores order and focus', async () => {
  const h = harness({phone:true, native:async () => {}});
  await h.panel.fire('click', {target:h.tile});
  assert.equal(h.nativeCalls, 0);
  assert(h.dialog.open);
  assert.equal(h.panel.parent, h.dialog);
  assert.equal(h.panel.children[0], h.tile);
  assert.equal(h.plays, 1);
  assert(h.classes.has('cameraExpanded'));
  h.dialog.children[0].fire('click');
  assert.equal(h.dialog, undefined);
  assert.deepEqual(h.grid.children, [h.panel, h.neighbor]);
  assert.equal(h.focused, h.tile);
  assert(!h.classes.has('cameraExpanded'));
});

for (const [name, native] of [['missing', undefined], ['denied', async () => {throw Error('Fullscreen unavailable');}]]) {
  test(name + ' native fullscreen falls back and Escape restores the camera', async () => {
    const h = harness({native});
    await h.panel.fire('click', {target:h.button});
    assert(h.dialog.open);
    h.dialog.close(); // The browser's Escape path emits the same close event.
    assert.equal(h.panel.parent, h.grid);
    assert.equal(h.focused, h.button);
  });
}

test('desktop retains native fullscreen and clicking again exits it', async () => {
  const h = harness({native:async () => {}});
  await h.panel.fire('click');
  assert.equal(h.document.fullscreenElement, h.panel);
  assert.equal(h.dialog, undefined);
  await h.panel.fire('click');
  assert.equal(h.document.fullscreenElement, null);
});

test('stopping a stream restores the tile before its grid is replaced', async () => {
  const h = harness({phone:true});
  await h.panel.fire('click');
  h.stop();
  assert.equal(h.dialog, undefined);
  assert.equal(h.panel.parent, h.grid);
  assert(!h.classes.has('cameraExpanded'));
  await h.panel.fire('click');
  assert.equal(h.dialog, undefined);
});

test('a rejected native request cannot reopen a stopped camera', async () => {
  let reject;
  const h = harness({native: () => new Promise((resolve, fail) => {reject = fail;})});
  const opening = h.panel.fire('click');
  h.stop(); reject(Error('Fullscreen denied'));
  await opening;
  assert.equal(h.dialog, undefined);
  assert.equal(h.panel.parent, h.grid);
});

test('camera is keyboard operable without scrolling the page', async () => {
  const h = harness({phone:true}); let prevented = false;
  h.tile.fire('keydown', {key:' ', preventDefault() {prevented = true;}});
  assert(prevented && h.dialog.open);
  assert.equal(h.tile.attrs.role, 'button');
  assert.equal(h.tile.attrs['aria-label'], 'Expand left camera');
});

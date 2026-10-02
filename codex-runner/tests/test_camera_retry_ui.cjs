const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const start = source.indexOf("    else if (state.provider?.vision?.retry?.state === 'retrying')");
const branch = 'if (false) {}\n' + source.slice(start, source.indexOf('    const queue = state.queue_snapshot;', start));

test('camera waiting, failure and recovery are visible during polling', () => {
  const element = {textContent: ''};
  const messages = [];
  const context = vm.createContext({state: {}, $: () => element,
    message(text, error = false) { element.textContent = text; messages.push({text, error}); }});
  const update = retry => {
    context.state = {provider: {vision: {retry}}};
    vm.runInContext(branch, context);
  };
  update({state: 'retrying', message: 'Waiting for next left camera frame (4/10; HTTP 503)'});
  assert.equal(element.textContent, 'Waiting for next left camera frame (4/10; HTTP 503)');
  update({state: 'failed', message: 'Camera failure: left camera frame unavailable after 10 attempts (HTTP 503)'});
  assert.equal(messages.at(-1).error, true);
  assert.match(element.textContent, /after 10 attempts/);
  update({state: 'retrying', message: 'Waiting for next top camera frame (2/10; TimeoutError)'});
  update(null);
  assert.match(element.textContent, /Waiting for next top/);
  update({state: 'recovered'});
  assert.equal(element.textContent, 'Camera feeds recovered. Continuing the run.');
  update({state: 'retrying', cause: 'No image', message: 'No image: waiting for depth.'});
  assert.equal(element.textContent, 'No image: waiting for depth.');
  assert.equal(messages.at(-1).error, true);
  update(null);
  assert.equal(element.textContent, 'No image: waiting for depth.');
  update({state: 'recovered'});
  assert.equal(element.textContent, 'Camera feeds recovered. Continuing the run.');
  assert.equal(messages.at(-1).error, false);
});

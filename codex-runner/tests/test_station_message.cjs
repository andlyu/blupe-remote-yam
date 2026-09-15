const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const start = source.indexOf('    const disabledAutoReady =');
const end = source.indexOf('    const entries =', start);

function message(station, state = {}) {
  const elements = {station: {dataset: {}}, status: {}};
  vm.runInNewContext(source.slice(start, end), {
    station, state, active: ['queued', 'preparing', 'running'].includes(state.status),
    faultNotice: 'Operator attention needed.', $: id => elements[id],
  });
  return elements.station;
}

const parked = {connected: true, mode: 'readonly', queue_ready: false, available: false};
assert.equal(message(parked).textContent, 'Ready to queue your next run.');
assert.equal(message(parked).dataset.tone, 'ready');
// Queue submission remains possible without claiming automatic start is enabled.
assert.equal(message(parked, {robot_auto_queue_enabled: false}).textContent,
  'Ready to queue your next run.');
// One unchanged readonly station must describe the visitor's full queue lifecycle.
for (const [status, expected] of [
  ['idle', 'Ready to queue your next run.'],
  ['queued', 'Your run is queued — waiting for robot readiness.'],
  ['preparing', 'The robot is preparing your run.'],
  ['running', 'Run active — waiting for robot control.'],
  ['stopped', 'Ready to queue your next run.'],
  ['queued', 'Your run is queued — waiting for robot readiness.'],
]) assert.equal(message(parked, {status}).textContent, expected);
assert.equal(message(parked, {status: 'queued'}).dataset.tone, 'waiting');
assert.equal(message({...parked, queue_ready: true}, {status: 'queued'}).textContent,
  'Your run is queued — waiting for your turn.');
assert.equal(message({...parked, mode: 'active'}, {status: 'running'}).textContent,
  'Your run is running.');
assert.equal(message({...parked, connected: false}).textContent, 'Robot is offline');
assert.doesNotMatch(message({...parked, mode: 'FAULT'}).textContent, /^Ready/);
assert.equal(message({...parked, mode: 'fault'}, {status: 'queued'}).textContent,
  'Robot fault — Operator attention needed.');
assert.equal(message({...parked, connected: false}, {status: 'queued'}).textContent,
  'Robot is offline');
assert.equal(message({...parked, mode: 'DISABLED'}).textContent,
  'Robot stopped — waiting for operator readiness');
assert.equal(message({...parked, mode: 'DISABLED'}, {robot_auto_queue_enabled: true}).textContent,
  'Ready for the next run. Join the queue to start.');
assert.equal(message({...parked, queue_ready: true, mode: 'active'}).textContent,
  'Ready for the next run. Join the queue to start.');
console.log('Station readiness messages passed');

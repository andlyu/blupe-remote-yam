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
for (const status of ['queued', 'preparing', 'running']) {
  assert.doesNotMatch(message(parked, {status}).textContent, /^Ready/);
}
assert.equal(message({...parked, connected: false}).textContent, 'Robot is offline');
assert.doesNotMatch(message({...parked, mode: 'FAULT'}).textContent, /^Ready/);
assert.equal(message({...parked, mode: 'DISABLED'}).textContent,
  'Robot stopped — waiting for operator readiness');
assert.equal(message({...parked, mode: 'DISABLED'}, {robot_auto_queue_enabled: true}).textContent,
  'Ready for the next run. Join the queue to start.');
assert.equal(message({...parked, queue_ready: true, mode: 'active'}).textContent,
  'Ready for the next run. Join the queue to start.');
console.log('Station readiness messages passed');

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const start = source.indexOf('  const ROBOT_STATUS =');
const end = source.indexOf('  function render(state)', start);
const elements = {station: {dataset: {}}, status: {}};
const context = vm.createContext({$: id => elements[id]});
vm.runInContext(source.slice(start, end), context);
const now = 1800000000;
const robots = ['yam-1', 'robot-abecb4cd868ab24b', 'robot-3652c537a175cbae'];
function state(robot, station = {}, extra = {}) {
  return {status: 'idle', last_observation: {homed: true, settled: true, observed_at: now},
    queue_snapshot: {generated_at: now, stations: [{jetson_id: robot, connected: true,
      observed_at: now, mode: 'READY', queue_ready: true, available: true, ...station}]}, ...extra};
}
function label(s, robot) {
  context.renderRobotStatus(context.robotStatus(s, robot, now));
  assert.equal(elements.status.textContent, elements.station.textContent);
  return elements.station.textContent;
}
for (const robot of robots) {
  test(`${robot}: shared labels through queue, Stop, park, and next run`, () => {
    for (const [station, extra, expected] of [
      [{}, {}, 'Ready for the next run'],
      [{}, {status: 'queued'}, 'Queued — waiting for your turn'],
      [{mode: 'readonly', queue_ready: false}, {status: 'queued'}, 'Queued — waiting for robot readiness'],
      [{mode: 'readonly', queue_ready: false}, {status: 'preparing'}, 'Preparing'],
      [{mode: 'readonly', queue_ready: false}, {status: 'running'}, 'Running'],
      [{mode: 'MOVING_HOME', queue_ready: false}, {status: 'stopped'}, 'Moving home'],
      [{mode: 'PARKING_ZERO', queue_ready: false}, {status: 'stopped'}, 'Parking'],
      [{mode: 'readonly', queue_ready: false}, {status: 'stopped'}, 'Stopped'],
      [{mode: 'readonly', queue_ready: false}, {status: 'queued'}, 'Queued — waiting for robot readiness'],
      [{}, {status: 'queued'}, 'Queued — waiting for your turn'],
      [{}, {status: 'stopped'}, 'Ready for the next run'],
      [{connected: false}, {status: 'running'}, 'Offline'],
      [{mode: 'fault'}, {status: 'queued'}, 'Fault'],
      [{mode: 'unexpected_mode'}, {}, 'Checking / unavailable'],
      [{mode: 'EXECUTING', queue_ready: false}, {}, 'Running'],
      [{mode: 'INITIALIZING', queue_ready: false}, {}, 'Preparing'],
    ]) assert.equal(label(state(robot, station, extra), robot), expected);
  });
  test(`${robot}: readiness requires measured home, settling and automatic queue`, () => {
    for (const homed of [false, null]) {
      const s = state(robot, {}, {status: 'queued', robot_auto_queue_enabled: true,
        last_observation: {homed, settled: true, observed_at: now}});
      assert.equal(label(s, robot), 'Queued — waiting for robot readiness');
    }
    for (const mode of ['DISABLED', 'readonly', 'STOPPED']) {
      const s = state(robot, {mode, queue_ready: false}, {robot_auto_queue_enabled: true,
        last_observation: {homed: false, settled: true, observed_at: now}});
      assert.equal(label(s, robot), 'Stopped but ready'); // Parked automatic queue can wake for work.
      assert.equal(elements.station.dataset.tone, 'ready');
    }
    assert.equal(label(state(robot, {}, {robot_auto_queue_enabled: false}), robot), 'Stopped');
    assert.equal(label(state(robot, {queue_ready: false}), robot), 'Stopped'); // available is insufficient.
    assert.equal(label(state(robot, {mode: 'active'}), robot), 'Checking / unavailable'); // Legacy manual readiness.
    assert.equal(label(state(robot, {}, {last_observation: {homed: true, settled: false, observed_at: now}}), robot), 'Stopped');
  });
  test(`${robot}: stopped-ready is green without granting queue admission`, () => {
    const parked = state(robot, {mode: 'STOPPED_READY', queue_ready: false}, {
      status: 'stopped', last_observation: {homed: false, settled: true, observed_at: now}});
    assert.equal(label(parked, robot), 'Stopped but ready');
    assert.equal(elements.station.dataset.tone, 'ready');
    assert.equal(label({...parked, status: 'queued'}, robot), 'Queued — waiting for robot readiness');
    assert.equal(label({...parked, robot_auto_queue_enabled: false}, robot), 'Stopped');
    for (const [station, expected] of [
      [{mode: 'STOPPED', queue_ready: false}, 'Stopped'],
      [{mode: 'MOVING_HOME'}, 'Moving home'],
      [{mode: 'PARKING_ZERO'}, 'Parking'],
      [{mode: 'FAULT'}, 'Fault'],
      [{connected: false}, 'Offline'],
      [{mode: 'STOPPED_READY', observed_at: now - 11}, 'Checking / unavailable'],
    ]) {
      const s = state(robot, {queue_ready: false, ...station}, {
        status: 'stopped', last_observation: parked.last_observation});
      assert.equal(label(s, robot), expected);
      assert.notEqual(elements.station.dataset.tone, 'ready');
    }
    const css = fs.readFileSync(path.join(__dirname, '../static/hosted.css'), 'utf8');
    assert.match(css, /#station\[data-tone="ready"\]\{color:#28643b\}/);
  });
  test(`${robot}: missing, stale and recovering status never claims readiness`, () => {
    assert.equal(label(state(robot, {}, {queue_snapshot: null}), robot), 'Checking / unavailable');
    assert.equal(label(state(robot, {observed_at: now-11}), robot), 'Checking / unavailable');
    assert.equal(label(state(robot, {}, {last_observation: {homed: true, settled: true, observed_at: now-11}}), robot), 'Checking / unavailable');
    assert.equal(label(state(robot, {}, {queue_snapshot: {generated_at: now-11, stations: []}}), robot), 'Checking / unavailable');
    assert.equal(label(state(robot), 'different-robot'), 'Checking / unavailable');
    assert.equal(label(state(robot), robot), 'Ready for the next run');
  });
}
test('the editable spec and rendered labels have the same twelve names', () => {
  const doc = fs.readFileSync(path.join(__dirname, '../../docs/UI-LABELS.md'), 'utf8');
  const labels = [...doc.matchAll(/\*\*(.+?):\*\*/g)].map(match => match[1]);
  const actual = vm.runInContext('Object.values(ROBOT_STATUS).map(value => value[0])', context);
  assert.equal(labels.length, 12);
  assert.deepEqual(JSON.parse(JSON.stringify(actual)), labels);
});

test('status outage and recovery clear stale labels and errors; history failure does not hide robot status', async () => {
  const nodes = {station:{dataset:{}}, status:{}, message:{textContent:''}, astraStreamState:{}};
  let response = new Error('Status temporarily unavailable');
  let historyFails = false;
  const c = vm.createContext({
    $: id => nodes[id], ended:false, csrf:'test-session', lastHistory:Date.now(), setTimeout(){},
    api:async()=>{if (response instanceof Error) throw response; return response;},
    message(text){nodes.message.textContent=text;},
    render(state){nodes.station.textContent=state.label;},
    renderRobotStatus(){nodes.station.textContent='Checking / unavailable';},
    history:async()=>{if (historyFails) throw new Error('History unavailable');},
  });
  const begin = source.indexOf("  let statusPollError =");
  const finish = source.indexOf("  window.addEventListener('pagehide'", begin);
  vm.runInContext(source.slice(begin, finish), c);
  await c.poll();
  assert.equal(nodes.station.textContent, 'Checking / unavailable');
  assert.equal(nodes.message.textContent, 'Status temporarily unavailable');
  response = {label:'Ready for the next run'};
  await c.poll();
  assert.equal(nodes.station.textContent, response.label);
  assert.equal(nodes.message.textContent, '');
  c.lastHistory=0; historyFails=true;
  await c.poll();
  assert.equal(nodes.station.textContent, response.label);
  assert.equal(nodes.message.textContent, 'History unavailable');
});

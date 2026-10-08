const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(__dirname + '/../static/hosted.js', 'utf8');
const initialSelection = source.match(/^  let selectedRobot = .*;$/m)[0];
const loadSelector = source.slice(source.indexOf('  async function loadRobotSelector()'),
  source.indexOf('  async function start()', source.indexOf('  async function loadRobotSelector()')));
const refreshAvailability = source.slice(source.indexOf('  async function refreshRobotAvailability()'),
  source.indexOf('  setInterval(refreshRobotAvailability'));
const roboHouse = 'robot-ba8413962083809c';

async function load({yamConnected = false, houseConnected = true, query = '', defaultRobot} = {}) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      value: '', textContent: '', options: [],
      replaceChildren(...options) { this.options = options; },
      append(option) { this.options = this.options.filter(item => item !== option); this.options.push(option); }
    });
    return nodes.get(id);
  };
  const catalog = {selected: 'yam-1', robots: [
    {id: roboHouse, name: 'Robo-house YAM', connected: houseConnected},
    {id: 'yam-1', name: 'YAM1', connected: yamConnected}
  ]};
  const location = {search: query, href: 'https://example.test/' + query};
  const events = [], cameraUpdates = [];
  let sessionsStarted = 0;
  const context = vm.createContext({
    $: node, api: async () => structuredClone(catalog), location, URL, URLSearchParams,
    window: {yamApplication: {defaultRobot}, dispatchEvent: event => events.push(event.detail),
      history: {replaceState(_state, _title, url) { location.href = url.href; location.search = url.search; }}},
    CustomEvent: class { constructor(_type, options) { this.detail = options.detail; } },
    document: {createElement: () => ({}), querySelectorAll: () => []},
    selectRobotPrompt() {}, renderRobotStatus() {}, buttons() {},
    updateCameraAvailability: connected => cameraUpdates.push(connected),
    start: async () => { sessionsStarted++; },
    submitting: false, csrf: 'existing-session', active: true, ended: false, lastChatSnapshot: ''
  });
  await vm.runInContext(initialSelection + '\n' + loadSelector + '\n' + refreshAvailability + '\nloadRobotSelector()', context);
  return {context, catalog, nodes, events, cameraUpdates, selector: node('robotSelector'),
    selected: () => vm.runInContext('selectedRobot', context), sessionsStarted: () => sessionsStarted};
}

test('default chooses connected Robo-house when YAM1 is disconnected', async () => {
  const state = await load();
  assert.equal(state.selected(), roboHouse);
  assert.equal(state.selector.value, roboHouse);
  assert.deepEqual(state.events, [roboHouse]);
});

test('connected YAM1 takes priority over the first connected robot', async () => {
  const state = await load({yamConnected: true});
  assert.equal(state.selected(), 'yam-1');
  assert.equal(state.selector.value, 'yam-1');
});

test('YAM1 preference requires confirmed boolean connectivity', async () => {
  for (const yamConnected of [null, 'true']) {
    assert.equal((await load({yamConnected})).selected(), roboHouse);
  }
});

test('explicit robot_id retains a disconnected selection', async () => {
  const state = await load({query: '?robot_id=yam-1'});
  assert.equal(state.selected(), 'yam-1');
  assert.equal(state.selector.value, 'yam-1');
});

test('dedicated-page default retains priority over availability and robot_id', async () => {
  const state = await load({defaultRobot: 'yam-1', query: '?robot_id=' + roboHouse});
  assert.equal(state.selected(), 'yam-1');
  assert.equal(state.selector.value, 'yam-1');
});

test('missing dedicated-page robot keeps the existing unavailable behavior', async () => {
  const state = await load({defaultRobot: 'missing-robot'});
  assert.equal(state.selected(), 'missing-robot');
  assert.equal(state.nodes.get('robotSelectorStatus').textContent, 'Robot list unavailable');
});

test('all-offline catalog retains the existing selected-robot fallback', async () => {
  const state = await load({houseConnected: false});
  assert.equal(state.selected(), 'yam-1');
});

test('manual choice and ongoing session survive availability refresh', async () => {
  const state = await load();
  state.selector.value = 'yam-1';
  await state.selector.onchange();
  assert.equal(state.selected(), 'yam-1');
  assert.equal(state.context.location.search, '?robot_id=yam-1');
  assert.equal(state.sessionsStarted(), 1);
  state.context.csrf = 'ongoing-session';
  state.context.active = true;
  await vm.runInContext('refreshRobotAvailability()', state.context);
  assert.equal(state.selected(), 'yam-1');
  assert.equal(state.selector.value, 'yam-1');
  assert.equal(state.sessionsStarted(), 1);
  assert.equal(state.context.csrf, 'ongoing-session');
  assert.equal(state.context.active, true);
  assert.deepEqual(state.cameraUpdates, [false]);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/hosted.js'), 'utf8');
const setupSource = source.split('  let setupProvider = null;')[1].split('  async function copyCodexPrompt()')[0];
const submitSource = source.split("  $('runForm').addEventListener('submit', async event => {")[1].split("  for (const [id, path, text] of [")[0];

function fixture(ready) {
  const nodes = {}, handlers = {}, requests = [];
  const $ = id => nodes[id] ||= {value: '', hidden: true, open: false, textContent: '',
    showModal() { this.open = true; }, close() { this.open = false; },
    focus() {}, select() {}, addEventListener(event, handler) { handlers[id] = handler; }};
  for (const [id, value] of Object.entries({provider:'claude', model:'claude-opus-5-5',
    prompt:'Place apple on plate', runnerName:'Tester', runDuration:'3'})) $(id).value = value;
  const setup = {provider:'claude', label:'Opus (Claude)', ready:false,
    state:'upgrade_required', message:'Update Claude Code', setup_prompt:'Set up or update Opus for this playground.'};
  const context = vm.createContext({$, window:{}, navigator:{clipboard:{async writeText() {}}},
    message() {}, buttons() {}, updateSavedKey() {}, guideRunAttention() {},
    async api(path) {
      requests.push(path);
      if (path.endsWith('/check')) return {...setup, ready:true, message:'Signed in', availability_note:'Local setup only.'};
      if (!ready) throw Object.assign(new Error('Setup required'), {subscriptionSetup:setup});
      return {saved_key_providers:[]};
    }});
  vm.runInContext('let setupProvider=null, submitting=false, active=false, ended=false, contactRequested=false;' + setupSource +
    "$('runForm').addEventListener('submit', async event => {" + submitSource, context);
  return {nodes, handlers, requests, context};
}

test('missing or outdated provider shows setup prompt without marking a run active', async () => {
  const f = fixture(false);
  await f.handlers.runForm({preventDefault(){}});
  assert.equal(f.nodes.subscriptionHelp.open, true);
  assert.match(f.nodes.subscriptionSetupPrompt.value, /Set up or update Opus/);
  assert.equal(vm.runInContext('active', f.context), false);
  assert.equal(vm.runInContext('submitting', f.context), false);
  await f.handlers.recheckSubscription();
  assert.equal(f.nodes.subscriptionHelp.open, false);
  assert.deepEqual(f.requests, ['/api/run', '/api/claude/check']);
  assert.equal(vm.runInContext('active', f.context), false); // Recheck never launches.
});

test('ready provider follows the existing queue submission', async () => {
  const f = fixture(true);
  await f.handlers.runForm({preventDefault(){}});
  assert.equal(vm.runInContext('active', f.context), true);
  assert.equal(f.nodes.subscriptionHelp.open, false);
  assert.deepEqual(f.requests, ['/api/run']);
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/analytics.js'), 'utf8');
function setup(storage = new Map(), hostname = 'blupe-yam.100-61-149-60.sslip.io') {
  const context = {window: {addEventListener() {}}, location: {hostname}, document: {hidden: true, addEventListener() {}, querySelector: () => ({}), getElementById: () => ({}), createElement: () => ({}), head: {appendChild() {}}}, IntersectionObserver: class { observe() {} }, clearTimeout() {}, windowPlaceholder: null,
    localStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v)}};
  vm.runInNewContext(source, context);
  const ga = context.window.yamAnalytics;
  const events = () => Array.from(context.window.dataLayer || []).filter(a => a[0] === 'event').map(a => a[1]);
  return {ga, context, events};
}
const run = {run_id: 'a'.repeat(32), provider: 'openai', model: 'gpt-6-astra', execution_confirmed: false};
test('queued/running do not count as execution; terminal snapshot retains start; reload dedupes', () => {
  const storage = new Map();
  const s = setup(storage); s.ga.init(false);
  s.ga.requested('openai', 'gpt-6-astra');
  s.ga.observe({status: 'running', analytics_run: run});
  assert(!s.events().includes('run_started'));
  const terminal = {status: 'stopped', analytics_run: {...run, execution_confirmed: true}};
  s.ga.observe(terminal); s.ga.observe(terminal);
  assert.deepEqual(s.events(), ['page_view','controller_loaded','run_requested','run_started','run_completed']);
  const reload = setup(storage); reload.ga.init(false); reload.ga.observe(terminal);
  assert.deepEqual(reload.events(), ['page_view','controller_loaded']);
});
test('failure and cancellation before motion never count as completed', () => {
  const s = setup(); s.ga.init(false);
  s.ga.observe({status: 'stopped', error: 'SECRET prompt', analytics_run: run});
  assert.deepEqual(s.events(), ['page_view','controller_loaded','run_failed']);
  assert(!JSON.stringify(s.context.window.dataLayer).includes('SECRET'));
});
test('only fixed metadata is sent; local and simulated sessions do not load GA', () => {
  const s = setup(); s.ga.init(false); s.ga.requested('SECRET', 'alice@example.com');
  const data = JSON.stringify(s.context.window.dataLayer);
  assert(!data.includes('SECRET')); assert(!data.includes('alice@'));
  const local = setup(new Map(), 'localhost'); local.ga.init(false); assert.deepEqual(local.events(), []);
  const simulated = setup(); simulated.ga.init(true); assert.deepEqual(simulated.events(), []);
});
test('section exposure requires continuous foreground visibility and counts once', () => {
  const timers = new Map(), listeners = {};
  let timerId = 0;
  const element = {getBoundingClientRect: () => ({top:0,bottom:200,left:0,right:200,width:200,height:200})};
  const s = setup();
  Object.assign(s.context, {innerHeight:800, innerWidth:400,
    setTimeout(fn, delay) { assert.equal(delay, 1000); timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); }});
  s.context.document.hidden = false;
  s.context.document.querySelector = selector => selector === '#runForm' ? {previousElementSibling:element} : element;
  s.context.document.getElementById = () => element;
  s.context.document.addEventListener = (name, fn) => { listeners[name] = fn; };
  s.context.window.addEventListener = (name, fn) => { listeners[name] = fn; };
  s.ga.init(false);
  assert.equal(timers.size, 3);
  assert(!s.events().includes('run_setup_viewed'));
  s.context.document.hidden = true; listeners.visibilitychange();
  assert.equal(timers.size, 0);
  s.context.document.hidden = false; listeners.visibilitychange();
  const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn());
  listeners.scroll();
  assert.equal(timers.size, 0);
  assert.equal(s.events().filter(n => n === 'run_setup_viewed').length, 1);
});
test('paid path keeps route metadata and payment dedup without requiring scroll steps', () => {
  const s = setup(); s.ga.init(false, true);
  s.ga.selected('openai', 'gpt-6-astra');
  s.ga.paid('private-order'); s.ga.paid('private-order');
  s.ga.observe({status:'running', analytics_run:{...run, run_route:'paid', execution_confirmed:true}});
  assert.deepEqual(s.events(), ['page_view','controller_loaded','provider_selected','payment_succeeded','run_started']);
  assert(!JSON.stringify(s.context.window.dataLayer).includes('private-order'));
  const started = Array.from(s.context.window.dataLayer).find(a => a[1] === 'run_started');
  assert.equal(started[2].run_route, 'paid');
});
test('three-option menu distinguishes Stripe from BYOK and no-key direct routes', () => {
  const s = setup(); s.ga.init(false, true);
  s.ga.selected('stripe', 'gpt-6-astra');
  s.ga.selected('openai', 'gpt-6-astra');
  s.ga.requested('local_raise_lower', 'gpt-6-astra');
  const events = Array.from(s.context.window.dataLayer).filter(a => ['provider_selected','run_requested'].includes(a[1]));
  assert.deepEqual(events.map(a => a[2].run_route), ['paid','direct','direct']);
  assert.deepEqual(events.map(a => a[2].provider), ['openai','openai','local_raise_lower']);
});

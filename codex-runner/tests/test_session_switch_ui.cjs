const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');

test('a late unauthorized response cannot disable a newly connected robot session', async () => {
  let deliver;
  const response = new Promise(resolve => {deliver=resolve;});
  const c = vm.createContext({robotGeneration:1, csrf:'', selectedRobot:'so101', ended:false,
    buttons(){}, $:()=>({value:''}), fetch:async()=>response});
  const start = source.indexOf('  async function api(');
  vm.runInContext(source.slice(start, source.indexOf('  function buttons()', start)), c);
  const pending = c.api('/api/status'); // Began before /api/session completed.
  c.csrf='new-session';
  deliver({ok:false, status:401, json:async()=>({error:'Session expired'})});
  await assert.rejects(pending, error=>error.stale===true);
  assert.equal(c.ended, false);
});

test('polling waits for session creation and resumes after switching away from an ended session', async () => {
  let calls=0, scheduled=0;
  const c = vm.createContext({csrf:'', ended:false, lastHistory:Date.now(),
    $:()=>({textContent:''}), api:async()=>{calls++;return {};},
    render(){}, message(){}, renderRobotStatus(){},
    setTimeout(){scheduled++;}, history:async()=>{}});
  const start = source.indexOf("  let statusPollError =");
  vm.runInContext(source.slice(start, source.indexOf("  window.addEventListener('pagehide'", start)), c);
  await c.poll();
  assert.equal(calls, 0); // Never send an anonymous background status request.
  c.ended=true; await c.poll();
  assert.equal(scheduled, 2); // Keep the loop available for another robot selection.
  c.ended=false; c.csrf='new-session'; await c.poll();
  assert.equal(calls, 1);
});

test('chat authentication failure does not revoke valid robot controls', async () => {
  let disabled=0;
  const c=vm.createContext({robotGeneration:1,csrf:'valid-session',selectedRobot:'so101',ended:false,
    buttons(){disabled++;},$:()=>({value:''}),fetch:async()=>({ok:false,status:401,json:async()=>({error:'Chat unavailable'})})});
  const start=source.indexOf('  async function api(');
  vm.runInContext(source.slice(start,source.indexOf('  function buttons()',start)),c);
  await assert.rejects(c.api('/api/chat'),/Chat unavailable/);
  assert.equal(c.ended,false);assert.equal(disabled,0);
  await assert.rejects(c.api('/api/status'),/Chat unavailable/);
  assert.equal(c.ended,true);assert.equal(disabled,1);
});

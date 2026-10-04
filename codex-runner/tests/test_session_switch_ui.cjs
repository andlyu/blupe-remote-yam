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
  const c = vm.createContext({csrf:'', ended:false, needsSessionRecovery:false, lastHistory:Date.now(),
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
  const c=vm.createContext({robotGeneration:1,csrf:'valid-session',selectedRobot:'so101',ended:false,viewerDisconnected:false,needsSessionRecovery:false,
    buttons(){disabled++;},$:()=>({value:''}),fetch:async()=>({ok:false,status:401,json:async()=>({error:'Chat unavailable'})})});
  const start=source.indexOf('  async function api(');
  vm.runInContext(source.slice(start,source.indexOf('  function buttons()',start)),c);
  await assert.rejects(c.api('/api/chat'),/Chat unavailable/);
  assert.equal(c.ended,false);assert.equal(disabled,0);
  await assert.rejects(c.api('/api/status'),/Chat unavailable/);
  assert.equal(c.ended,true);assert.equal(disabled,1);
  assert.equal(c.needsSessionRecovery,true);
});

function recoveryFixture(fetch) {
  const calls=[],messages=[],delays=[];let reconnects=0,renders=0;
  const nodes={};
  const c=vm.createContext({robotGeneration:1,csrf:'old-token',selectedRobot:'yam-1',ended:false,
    needsSessionRecovery:false,viewerDisconnected:false,active:false,ownRunLive:false,
    lastChatSnapshot:'old',lastHistory:Date.now(),
    $:id=>nodes[id]||=( {textContent:'',value:''} ),buttons(){},updateSavedKey(){},
    message:text=>messages.push(text),render:()=>renders++,renderRobotStatus(){},history:async()=>{},
    document:{hidden:false,querySelectorAll:()=>[{yamReconnect(){reconnects++;}}]},
    setTimeout:(fn,delay)=>delays.push(delay),
    fetch:async(path,options)=>{calls.push({path,options});return fetch(path,options);}});
  const api=source.indexOf('  async function api(');
  vm.runInContext(source.slice(api,source.indexOf('  function buttons()',api)),c);
  const poll=source.indexOf('  let statusPollError =');
  vm.runInContext(source.slice(poll,source.indexOf("  window.addEventListener('pagehide'",poll)),c);
  return {c,calls,messages,delays,reconnects:()=>reconnects,renders:()=>renders};
}
const reply=(status,data)=>({ok:status===200,status,json:async()=>data});

test('server restart renews the viewer and video without reload or replaying actions',async()=>{
  let statusCalls=0;
  const f=recoveryFixture(async path=>path==='/api/session'
    ? reply(200,{csrf:'new-token'})
    : ++statusCalls===1?reply(401,{error:'Your browser session ended. Reload to start a new one.'}):reply(200,{status:'idle'}));
  await f.c.poll();assert.equal(f.c.ended,true);assert.equal(f.c.needsSessionRecovery,true);
  await f.c.poll();assert.equal(f.c.csrf,'new-token');assert.equal(f.c.ended,false);
  assert.equal(f.reconnects(),1);
  await f.c.poll();assert.equal(f.renders(),1);
  assert.deepEqual(f.calls.map(x=>x.path),['/api/status','/api/session','/api/status']);
  assert.equal(f.calls.filter(x=>x.options.method==='POST').length,1);
});

test('a failed renewal backs off and retries instead of requiring refresh',async()=>{
  let sessions=0;
  const f=recoveryFixture(async path=>path==='/api/session'
    ? ++sessions===1?reply(503,{error:'Unavailable'}):reply(200,{csrf:'renewed'})
    :reply(401,{error:'Expired'}));
  await f.c.poll();await f.c.poll();
  assert.equal(f.c.needsSessionRecovery,true);assert.equal(f.delays.at(-1),3000);
  await f.c.poll();assert.equal(f.c.ended,false);assert.equal(f.reconnects(),1);
});

test('a rejected run is never resubmitted during viewer recovery',async()=>{
  const f=recoveryFixture(async path=>path==='/api/session'?reply(200,{csrf:'new'}):reply(401,{error:'Expired'}));
  await assert.rejects(f.c.api('/api/run',{prompt:'move'}),/Expired/);
  await f.c.poll();
  assert.deepEqual(f.calls.map(x=>x.path),['/api/run','/api/session']);
});

test('explicit disconnect does not automatically create another session',async()=>{
  const f=recoveryFixture(async()=>reply(401,{error:'Expired'}));
  f.c.viewerDisconnected=true;
  await assert.rejects(f.c.api('/api/status'),/Expired/);
  await f.c.poll();assert.equal(f.c.needsSessionRecovery,false);assert.equal(f.calls.length,1);
});

test('late renewal from the previous robot cannot reconnect or replace the new session',async()=>{
  let deliver;
  const pending=new Promise(resolve=>{deliver=resolve;});
  const f=recoveryFixture(async()=>pending);
  const renewal=f.c.renewViewerSession();
  f.c.robotGeneration=2;f.c.csrf='other-robot-token';
  deliver(reply(200,{csrf:'late-token'}));
  await assert.rejects(renewal,error=>error.stale===true);
  assert.equal(f.c.csrf,'other-robot-token');assert.equal(f.reconnects(),0);
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const helperStart = source.indexOf('function aspireConfiguredRun(');
const helperEnd = source.indexOf("(() => {\n  if (!['127.0.0.1'", helperStart);
const helperSource = source.slice(helperStart, helperEnd);
const providerBody = source.split('  function providerChanged() {')[1].split("  $('provider').addEventListener('change', () => { providerChanged()")[0];
const submitBody = source.split("  $('runForm').addEventListener('submit', async event => {")[1].split("  for (const [id, path, text] of [")[0];
function fixture(provider = 'aspire', available = true) {
  const nodes = {}, handlers = {}, events = [], requests = [];
  const $ = id => nodes[id] ||= {value:'', hidden:false, dataset:{}, options:[], selectedOptions:[],
    close(){}, addEventListener(event, handler){handlers[id]=handler;}};
  Object.entries({provider, model:'gpt-6-astra', prompt:'Place the red block on the towel. ', runnerName:'Human edited name',
    runDuration:'5', reasoningEffort:'ultra', responseSpeed:'fast',automaticRetryLimit:'1'}).forEach(([id,value]) => $(id).value=value);
  $('useApiDepth').dataset.available='true';
  $('localRunDialog').dataset.local='true';
  let resolve, reject;
  const response = new Promise((yes,no) => {resolve=yes;reject=no;});
  const storage=new Map();
  const context = vm.createContext({$, aspireRunAvailable:available, configuredProviders:{}, setupProvider:null,selectedRobot:'fixture',
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    window:{yamAspireLineage:{requested(task){events.push(['requested',task]);},accepted(){}, rejected(reason){events.push(['rejected',reason]);}}},
    requestAnimationFrame:fn=>queueMicrotask(fn),renderModelRunResult(){},renderConversation(){},
    renderCurrentConversation(){},selectedModelName(){return 'ASPIRE';},
    openAspireTaskPanel(){events.push(['panel']);}, updateRunLabel(){}, updateSavedKey(){}, buttons(){},
    message(text,error){events.push(['message',text,error]);}, guideRunAttention(state){events.push(['attention',state.status]);},
    api(path,payload){events.push(['api',path]);requests.push({path,payload:{...payload}});return response;}});
  const guardStart=source.indexOf('function createRunLaunchGuard(');
  vm.runInContext(helperSource,context);
  vm.runInContext(source.slice(source.indexOf("  $('automaticRetryLimit').addEventListener('change'"),source.indexOf("  $('codexCheck').addEventListener('click'")),context);
  vm.runInContext(source.slice(guardStart,source.indexOf("(() => {\n  'use strict';",guardStart))+
    '\nconst runLaunchGuard=createRunLaunchGuard();',context);
  vm.runInContext('let submitting=false,active=false,ended=false,contactRequested=false;' +
    "$('runForm').addEventListener('submit', async event => {" + submitBody, context);
  return {$, context, events, requests, storage,changeLimit:()=>handlers.automaticRetryLimit(),resolve, reject, ready:()=>new Promise(resolve=>setImmediate(resolve)),submit:() => handlers.runForm({preventDefault(){}}),
    change:vm.runInContext('(function(){'+providerBody+')',context)};
}

test('ASPIRE is available only through a configured local Codex depth station', () => {
  const context=vm.createContext({});vm.runInContext(helperSource,context);
  const session={local_runner:true,codex:{ready:true},api_depth:{available:true},segmentation:{backend:'runpod_sam3'}};
  assert.equal(context.aspireConfiguredRun(session),true);
  for(const field of ['local_runner','codex','api_depth','segmentation']) assert.equal(context.aspireConfiguredRun({...session,[field]:null}),false,field);
});

test('selecting ASPIRE opens a preview and preserves human task and attribution without submission', () => {
  const f=fixture();f.change();
  assert.equal(f.$('model').value,'gpt-6-astra');assert.equal(f.$('useApiDepth').checked,true);
  assert.equal(f.$('apiDepthSettings').hidden,true);assert.equal(f.$('effortField').hidden,true);
  assert.equal(f.$('speedField').hidden,true);assert.equal(f.$('conversationSharingSettings').hidden,true);
  assert.equal(f.$('reasoningEffort').value,'high');assert.equal(f.$('responseSpeed').value,'standard');
  assert.equal(f.$('prompt').value,'Place the red block on the towel. ');
  assert.equal(f.$('runnerName').value,'Human edited name');assert.deepEqual(f.requests,[]);
  assert.deepEqual(f.events,[['panel']]);
  f.$('provider').value='codex';f.change();assert.equal(f.$('useApiDepth').checked,false);
  assert.equal(f.$('effortField').hidden,false);assert.equal(f.$('speedField').hidden,false);
  assert.equal(f.$('reasoningEffort').value,'ultra');assert.equal(f.$('responseSpeed').value,'fast');
  assert.equal(f.$('conversationSharingSettings').hidden,false);
  f.$('provider').value='aspire';f.change();
  assert.equal(f.$('effortField').hidden,true);assert.equal(f.$('speedField').hidden,true);
  f.$('localRunDialog').dataset.local='false';f.$('provider').value='codex';f.change();
  assert.equal(f.$('conversationSharingSettings').hidden,true);
});

test('ASPIRE submits exactly one canonical Codex native request and anchors it before acceptance', async () => {
  const f=fixture();f.change();const pending=f.submit();await f.ready();
  assert.equal(f.requests.length,1);const {path,payload}=f.requests[0];assert.equal(path,'/api/run');
  assert.equal(payload.provider,'codex');assert.equal(payload.use_api_depth,true);assert.equal(payload.model,'gpt-6-astra');
  assert.equal(payload.prompt,'Place the red block on the towel. ');assert.equal(payload.runner_name,'Human edited name');
  assert.equal(payload.reasoning_effort,'high');assert.equal(payload.response_speed,'standard');
  assert.equal(payload.automatic_retry_limit,1);
  assert(f.events.some(item=>item[0]==='requested'&&item[1]===payload.prompt));
  assert(f.events.findIndex(item=>item[0]==='requested')<f.events.findIndex(item=>item[0]==='api'));
  assert.equal(vm.runInContext('active',f.context),false);
  f.resolve({status:'preparing'});await pending;
  assert.equal(vm.runInContext('active',f.context),true);assert(f.events.some(item=>item[0]==='attention'&&item[1]==='preparing'));
});

test('retry limit persists per robot and changes only the next explicit submission',async()=>{
  for(const limit of [0,2,3]) {
    const f=fixture();f.change();f.$('automaticRetryLimit').value=String(limit);f.changeLimit();
    assert.equal(f.storage.get('yam-aspire-retry-limit:fixture'),String(limit));assert.equal(f.requests.length,0);
    f.$('automaticRetryLimit').dataset.robot='';f.$('automaticRetryLimit').value='1';f.change();
    assert.equal(f.$('automaticRetryLimit').value,String(limit));
    const pending=f.submit();await f.ready();assert.equal(f.requests[0].payload.automatic_retry_limit,limit);
    f.resolve({status:'preparing'});await pending;
  }
  const f=fixture();f.$('automaticRetryLimit').value='4';await f.submit();await f.ready();assert.equal(f.requests.length,0);
  assert(f.events.some(e=>e[0]==='rejected'&&/whole number/.test(e[1])));
});

test('fresh ASPIRE submission overrides stale hidden effort and speed values', async () => {
  for (const changed of [false,true]) {
    const f=fixture();if(changed) f.change();
    f.$('reasoningEffort').value='low';f.$('responseSpeed').value='fast';
    const pending=f.submit();await f.ready();
    assert.equal(f.requests[0].payload.reasoning_effort,'high');
    assert.equal(f.requests[0].payload.response_speed,'standard');
    f.resolve({status:'preparing'});await pending;
  }
});

test('direct Codex explicitly opts out of ASPIRE on the configured station', async () => {
  const f=fixture('codex');f.$('useApiDepth').checked=true;const pending=f.submit();await f.ready();
  assert.equal(f.requests[0].payload.use_api_depth,false);assert(f.events.some(item=>item[0]==='requested'));
  assert.equal(f.requests[0].payload.reasoning_effort,'ultra');assert.equal(f.requests[0].payload.response_speed,'fast');
  f.resolve({});await pending;
});

test('unavailable ASPIRE cannot submit and rejected requests retain their concrete failure', async () => {
  const unavailable=fixture('aspire',false);await unavailable.submit();assert.deepEqual(unavailable.requests,[]);
  const f=fixture();const pending=f.submit();await f.ready();f.reject(new Error('Subscription needs login'));await pending;
  assert(f.events.some(item=>item[0]==='rejected'&&item[1]==='Subscription needs login'));
  assert.equal(vm.runInContext('active',f.context),false);
});

test('pending task resists stale public history and adopts real preparation or immediate failure', () => {
  const methods=source.split('  window.yamAspireLineage = {')[1].split("  selector.addEventListener('change'")[0];
  const context=vm.createContext({window:{},refresh(){}});
  vm.runInContext(helperSource+'let pendingAttempt,liveRun,liveState,selected,signature;window.yamAspireLineage={'+methods,context);
  const api=context.window.yamAspireLineage;api.requested('Place the red block on the towel.',1);
  api.live({task:'Old task',status:'stopped'},{status:'idle'});
  assert.equal(vm.runInContext('liveRun.task',context),'Place the red block on the towel.');
  assert.equal(vm.runInContext('liveRun.task_progress.lineage.usage_recorded',context),false);
  const progress={task:'Place the red block on the towel.',updates:[{happened:'Reading scene',changed:'Fresh capture',next_action:'Plan'}]};
  api.accepted('current-attempt',1);
  api.live(null,{status:'preparing',attempt_id:'current-attempt',provider:{task_progress:progress}});
  assert.equal(vm.runInContext('liveRun.task_progress',context),progress);
  api.requested(progress.task,2);api.accepted('next-attempt',2);
  api.live(null,{status:'failed',attempt_id:'next-attempt',error:'Capture failed',provider:{task_progress:progress}});
  assert.equal(vm.runInContext('liveRun.status',context),'failed');assert.equal(vm.runInContext('liveRun.error',context),'Capture failed');
});

test('accepted fallback shows Astra routing instead of an ASPIRE program and retains the prompt', async () => {
  const f=fixture();const notices=[];
  f.context.window.yamPolicyRoute=route=>notices.push(route);
  const models=[];f.context.applyModelName=run=>models.push(run.model_name);
  const pending=f.submit();await f.ready();
  const route={actual_policy:'astra',requested_policy:'aspire',prompt:f.requests[0].payload.prompt,
    message:'No saved ASPIRE program matches this task. Running Astra instead.'};
  f.resolve({attempt_id:'fallback',status:'queued',launch_route:route});await pending;
  assert.equal(notices.at(-1),route);
  assert.deepEqual(models,['Astra']);
  assert(f.events.some(item=>item[0]==='message'&&item[1]===route.message));
  assert(!f.events.some(item=>item[0]==='message'&&item[1]?.includes('ASPIRE request accepted')));
  const methods=source.split('  window.yamAspireLineage = {')[1].split("  selector.addEventListener('change'")[0];
  const c=vm.createContext({window:{},refresh(){}});
  vm.runInContext(helperSource+'let pendingAttempt,liveRun,liveState,selected,signature;window.yamAspireLineage={'+methods,c);
  const api=c.window.yamAspireLineage;api.requested(route.prompt,1);api.accepted('fallback',1,route);
  assert.equal(vm.runInContext('liveRun.task_progress',c),null);
  api.live(null,{attempt_id:'fallback',status:'queued',provider:{launch_route:route}});
  assert.equal(vm.runInContext('liveRun.launch_route.actual_policy',c),'astra');
  assert.equal(vm.runInContext('liveRun.task',c),route.prompt);
  assert.equal(vm.runInContext('liveRun.task_progress',c),null);
});

test('route notice exposes the verified optional development link without code-generation claims', () => {
  const box={append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},children:[]};
  const c=vm.createContext({window:{},document:{getElementById:()=>box,createTextNode:text=>({text}),createElement:()=>({})}});
  const start=source.indexOf('window.yamPolicyRoute = function(');
  vm.runInContext(source.slice(start,source.indexOf('function aspireConfiguredRun(',start)),c);
  c.window.yamPolicyRoute({message:'Running Astra instead.',development_url:'https://github.com/andlyu/blupe-remote-yam'});
  assert.equal(box.hidden,false);assert.equal(box.children[0].text,'Running Astra instead.');
  assert.equal(box.children[1].href,'https://github.com/andlyu/blupe-remote-yam');
  c.window.yamPolicyRoute(null);assert.equal(box.hidden,true);assert.equal(box.children.length,0);
});

test('queued fallback conversation names the actual Astra task instead of stale ASPIRE history', () => {
  const c=vm.createContext({});const start=source.indexOf('function routedConversationRun(');
  vm.runInContext(source.slice(start,source.indexOf('function aspireConfiguredRun(',start)),c);
  const state={status:'queued',attempt_id:'new',runner_name:'Operator',provider:{launch_route:{actual_policy:'astra',prompt:'Uncap the pen with the right arm.'}},interactions:{events:[]}};
  const run=c.routedConversationRun({attempt_id:'old',model_name:'ASPIRE',task:'Old task',events:[{message:'Old plan'}]},state);
  assert.equal(run.model_name,'Astra');assert.equal(run.task,state.provider.launch_route.prompt);
  assert.equal(run.attempt_id,'new');assert.equal(run.events.length,0);
  assert.equal(run.task_progress,undefined);
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.YAM_TEST_HOSTED_JS || path.join(__dirname,'../static/hosted.js'),'utf8');
const start = source.indexOf('function aspireLineageGroups(');
const end = source.indexOf("// ASPIRE task progress is shared;",start);
const context = vm.createContext({});
vm.runInContext(source.slice(start,end),context);
const catalog = {executable:{skill:'pick_place_block',source_integrity:true}};

test('unchanged-code rerun and no-progress diagnosis are not labelled new fixes', () => {
  const rerun=context.aspireAttemptUpdates([{id:'diagnosis',policy:'astra',mode:'offline_rerun_diagnosis',
    status:'RERUN_VALIDATED',parent_attempt_id:'latest',rerun_reason:'Fresh target is measurable',diagnosis:'Fresh target is measurable'}])[0];
  assert.match(rerun.happened,/Justified unchanged-code rerun/);
  assert.doesNotMatch(rerun.happened,/repair/);
  assert.match(rerun.changed,/Fresh target is measurable/);
  const blocked=context.aspireAttemptUpdates([{id:'diagnosis',policy:'astra',mode:'offline_code_repair',
    status:'NO_PROGRESS',parent_attempt_id:'latest',reason:'No meaningful correction was produced'}])[0];
  assert.match(blocked.happened,/NO_PROGRESS/);
  assert.match(blocked.changed,/No meaningful correction/);
});

test('local preview generates missing programs and reuses complete exact-task programs outside template grammar', () => {
  const prompt='Move the towel into the tray and fold it once';
  const local={...catalog,execution_environment:'local',saved_program_tasks:[]};
  let route=context.aspirePromptRoute(prompt,local);
  assert.equal(route.compatible,false);assert.equal(route.generate,true);
  assert.match(route.title,/Generate an ASPIRE/);assert.doesNotMatch(route.reason,/normal Astra/);
  local.saved_program_tasks=[{task:prompt,source_sha256:'a'.repeat(64)}];
  route=context.aspirePromptRoute(prompt,local);
  assert.equal(route.compatible,true);assert.equal(route.generate,false);
  assert.equal(route.exact.source_sha256,'a'.repeat(64));
  assert.equal(context.aspirePromptRoute(prompt+' Keep the right arm parked.',local).compatible,false);
  assert.match(context.aspirePromptRoute(prompt,catalog).title,/Astra instead/);
});

test('SAM 3 startup has a real elapsed timer and distinct ready, failed and terminal states', () => {
  const run={status:'queued',task_progress:{vision:{model:'facebook/sam3',state:'starting',started_at:100}}};
  const progress=() => context.aspireSam3Progress(run,137000);
  assert.equal(progress().title,'Starting SAM 3 vision');assert.equal(progress().timer,'37s elapsed');
  run.task_progress.vision={...run.task_progress.vision,state:'ready',ready_at:145.8};
  assert.equal(progress().title,'SAM 3 vision ready');assert.equal(progress().timer,'Startup took 45s');
  run.task_progress.vision={...run.task_progress.vision,state:'failed',ended_at:150};
  assert.equal(progress().title,'SAM 3 vision unavailable');assert.equal(progress().timer,'Stopped after 50s');
  for(const status of ['stopped','failed','idle','timed_out']) assert.equal(context.aspireSam3Progress({...run,status}),null);
  assert.equal(context.aspireSam3Progress({status:'running'}),null);
  assert.equal(context.aspireSam3Progress({...run,task_progress:{vision:{model:'facebook/sam3',state:'starting'}}}).timer,'');
});

test('robot viewer names the SAM 3 wait while preserving queue and initialization context', () => {
  const elements={},$=id=>elements[id] ||= {hidden:false,textContent:'',classList:{toggle(){}},
    closest:()=>$('queuePlace'),focus(){},scrollIntoView(){}};
  const c=vm.createContext({$,Date:{now:()=>137000},aspireSam3Progress:context.aspireSam3Progress,
    window:{matchMedia:()=>({matches:true})}});
  // The helper uses the clock in its defining realm.
  context.Date={now:()=>137000};
  vm.runInContext(source.slice(source.indexOf('  let attentionSession = null;'),source.indexOf('  function modelResponseTools(')),c);
  const provider={task_progress:{vision:{model:'facebook/sam3',state:'starting',started_at:100}}};
  c.guideRunAttention({status:'queued',session_id:'fixture',provider,queue_position:2});
  assert.equal($('viewerCueTitle').textContent,'Waiting in the robot queue');
  assert.match($('viewerCueDetail').textContent,/37s elapsed.*vision worker.*wait for your turn/);
  c.guideRunAttention({status:'preparing',session_id:'fixture',provider});
  assert.equal($('viewerCueTitle').textContent,'Starting SAM 3 vision');
  assert.match($('viewerCueDetail').textContent,/robot initializes/);
  c.guideRunAttention({status:'running',session_id:'fixture',provider});
  assert.match($('viewerCueDetail').textContent,/Task motion waits for vision/);
  provider.task_progress.vision={...provider.task_progress.vision,state:'ready',ready_at:145};
  c.guideRunAttention({status:'running',session_id:'fixture',provider});
  assert.equal($('viewerCueTitle').textContent,'Your run is active');
  delete context.Date;
});

test('prompt preview chooses supported input binding without claiming execution', () => {
  for (const prompt of ['Place the red block on the red chip.','Move the red block onto a clear patch of the green towel.', 'Pick up the green block and place it on the blue poker chip.']) {
    const route=context.aspirePromptRoute(prompt,catalog);
    assert.equal(route.compatible,true);
    assert.match(route.reason,/checks scene compatibility/);
  }
  for (const prompt of ['Uncap a pen','Place the red block on the red chip without lifting it.', 'Do not place the red block on the red chip.', 'Put the block on a chip','Place red block on blue towel','Stack red and green blocks']) {
    assert.equal(context.aspirePromptRoute(prompt,catalog).compatible,false);
  }
  assert.equal(context.aspirePromptRoute('Place red block on red chip',{executable:{source_integrity:false}}).compatible,false);
});

test('retrieved context stays outside the used group and old history is unknown', () => {
  const groups=context.aspireLineageGroups({retrieved:[{id:'grasp',version:'1'}]});
  assert.equal(groups.used.length,0);assert.equal(groups.retrievedOnly.length,1);
  assert.equal(groups.unknown,true);assert.equal(groups.newTitle,'New skills added');
});

test('versions separate exact used records from unrelated retrieved versions', () => {
  const groups=context.aspireLineageGroups({usage_recorded:true,used:[{id:'grasp',version:'1'}],
    retrieved:[{id:'grasp',version:'1'},{id:'grasp',version:'2'}]});
  assert.equal(groups.used.length,1);assert.equal(groups.retrievedOnly.length,1);
  assert.equal(groups.retrievedOnly[0].version,'2');assert.equal(groups.unknown,false);
});

test('needed becomes created only with a recorded creation; planning is not physical success', () => {
  assert.equal(context.aspireLineageGroups({new_skills:[{title:'Novel behavior'}]}).pending,true);
  const groups=context.aspireLineageGroups({new_skills:[{creation:'recorded_code',planning_success:true,physical_success:null}]});
  assert.equal(groups.newTitle,'New skills added');assert.equal(groups.created[0].physical_success,null);
  assert.match(source,/Physical success not demonstrated/);
  assert.match(source,/Original harness:/);
  assert.match(source,/After parking:/);
});

test('Codex authorship requires recorded evidence and reuse never implies new generation', () => {
  assert.equal(context.aspireProgramAttribution({authorship:{model:'gpt-6-astra'}}),'Program authorship not recorded');
  assert.equal(context.aspireProgramAttribution({authorship:{generated_by_codex:true,mode:'generation'}}),'Generated by Codex');
  assert.equal(context.aspireProgramAttribution({authorship:{generated_by_codex:true,mode:'reuse',task_coding_model_requests:0}}),'Reusing code generated by Codex');
});

test('old failed plan and revised attempt keep separate three-line summaries and unknown causes', () => {
  const updates=context.aspireRecordedUpdates({attempts:[
    {attempt:1,source_sha256:'a',lineage:{},plan:{status:'PLAN_FAILED',reason:'Tip mesh collides with rod',planning_success:false}},
    {attempt:2,source_sha256:'b',lineage:{},code_revision_reason:{reason:'Choose supported offset/yaw'},plan:{status:'PLAN_ONLY',planning_success:true}}
  ]});
  assert.equal(updates.length,4);
  for(const row of updates) assert.deepEqual(Object.keys(row).sort(),['changed','happened','next_action']);
  assert.match(updates[1].changed,/collides with rod/);
  assert.match(updates[2].changed,/supported offset\/yaw/);
  assert.match(updates[3].happened,/plan passed/);
  assert.match(updates[3].next_action,/physical outcome is a separate/);
  assert.match(updates[0].changed,/not explicitly recorded/);
});

test('red-chip visual review does not hide unresolved automated checks', () => {
  const updates=context.aspireRecordedUpdates({attempts:[],review_status:'VISUALLY_RETAINED_ON_RED_CHIP_METRIC_UNVERIFIED',
    review_basis:'Retained placement in fresh parked image',postpark_checks:{centered_over_chip:false,dimensions_consistent:false}});
  assert.match(updates[0].next_action,/centered_over_chip, dimensions_consistent/);
  assert.match(updates[0].changed,/unverified/);
});


test('preparing spectator shows current task with a truthful detail limitation instead of stale history', () => {
  const stale={task:'Old tower and duck',status:'stopped',error:'Old failure'};
  const current=context.aspireLiveTask(stale,{whats_running:[{task:'Stack two blocks.',status:'preparing',runner_name:'Fixture'}]});
  assert.equal(current.task,'Stack two blocks.');assert.equal(current.status,'preparing');
  assert.equal(current.error,undefined);
  assert.match(current.task_progress.updates[0].changed,/submitting client/);
  assert.equal(current.task_progress.updates.length,1);
  const progress={task:'Stack two blocks.',updates:[{happened:'Writing code',changed:'New behavior',next_action:'Test plan'}]};
  const own=context.aspireLiveTask(stale,{status:'preparing',provider:{task_progress:progress}});
  assert.equal(own.task_progress,progress);
  const shared={task:'Stack two blocks.',status:'preparing',task_progress:progress};
  assert.equal(context.aspireLiveTask(shared,{whats_running:[{task:'Stack two blocks.',status:'preparing'}]}),shared);
});

test('stopped task adopts its fresh parked outcome instead of retaining release-time progress', () => {
  const stale={task:'Old red block task',status:'stopped'};
  const progress={task:'Green block on red chip',updates:[{happened:'After-parking outcome: UNVERIFIED.'}],
    outcome:{status:'UNVERIFIED',success:false,failed_checks:['centered_over_chip']}};
  const current=context.aspireLiveTask(stale,{status:'stopped',provider:{task_progress:progress}});
  assert.equal(current.task,progress.task);
  assert.equal(current.task_progress.outcome.success,false);
  assert.equal(current.task_progress.outcome.failed_checks[0],'centered_over_chip');
});

async function disclosureFixture({fetchCatalog,hostname='127.0.0.1'} = {}) {
  class Element {
    constructor(tag) {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.handlers={};this.open=false;this.value='';this.textContent='';this.scrollTop=0;this.scrollHeight=500;this.clientHeight=100;this.classList={add(){}};}
    get hidden() {return this._hidden || false;}
    set hidden(value) {this._hidden=value;this.hiddenWrites=(this.hiddenWrites || 0)+1;}
    get textContent() {return this._text || '';}
    set textContent(value) {this._text=value;this.textWrites=(this.textWrites || 0)+1;}
    append(...children) {for(const child of children) {if(child.parentElement) child.parentElement.children=child.parentElement.children.filter(node=>node!==child);child.parentElement=this;this.children.push(child);}}
    insertBefore(child,before) {if(child===before)return;if(child.parentElement) child.parentElement.children=child.parentElement.children.filter(node=>node!==child);child.parentElement=this;const index=before ? this.children.indexOf(before) : this.children.length;this.children.splice(index,0,child);}
    replaceChildren(...children) {for(const child of this.children) child.parentElement=null;this.children=[];this.append(...children);}
    remove() {if(this.parentElement) this.parentElement.children=this.parentElement.children.filter(node=>node!==this);this.parentElement=null;}
    addEventListener(type,fn) {(this.handlers[type] ||= []).push(fn);}
    dispatch(type) {for(const fn of this.handlers[type] || []) fn({target:this});}
    setAttribute() {}
    before(...children) {this.parentElement?.append(...children);}
    after(...children) {this.parentElement?.append(...children);}
    closest() {return this.parentElement;}
    get options() {return this.children;}
    get isConnected() {return this.connected || !!this.parentElement?.isConnected;}
    descendants() {return this.children.flatMap(child => [child,...child.descendants()]);}
    querySelectorAll(selector) {return this.descendants().filter(child => child.tagName==='DETAILS' &&
      (selector==='details' || child.dataset.aspireBuiltFrom));}
  }
  const root=new Element('main');root.connected=true;
  const nodes={};
  for(const id of ['liveConversationPanel','sidePrompt','prompt','robotSelector','provider','sideConversationTitle',
    'sideConversationState','sideConversationMessages','conversationModeTitle','currentRunner','currentPrompt',
    'conversationState','sideRunner','reasoningButton','taskRunFailure','policyRouteNotice']) {
    const element=new Element(id==='sidePrompt' ? 'p' : 'div');element.id=id;nodes[id]=element;root.append(element);
  }
  nodes.liveConversationPanel.dataset.mode='reasoning';nodes.robotSelector.value='fixture';nodes.provider.value='aspire';
  const promptBox=new Element('details');root.append(promptBox);promptBox.append(nodes.sidePrompt);
  nodes.prompt.value='Fixture task';
  const episode={id:'saved-one',episode_id:'episode-one',task:'Saved task',images:[],replays:[],
    attempts:[{attempt:1,lineage:{usage_recorded:true},execution:{},plan:{}}]};
  const fixtureCatalog={robot_id:'fixture',episodes:[episode],recipes:[]};
  const document={createElement:tag=>new Element(tag),
    getElementById:id=>root.descendants().find(node=>node.id===id),
    querySelector:selector=>selector==='[data-conversation-mode="reasoning"]' ? nodes.reasoningButton : null};
  const intervals=[];const window={addEventListener(){},setInterval(callback){intervals.push(callback);}};
  const c=vm.createContext({document,window,location:{hostname,search:''},URLSearchParams,
    fetch:async()=>({ok:true,json:async()=>fetchCatalog ? fetchCatalog(fixtureCatalog) : fixtureCatalog})});
  vm.runInContext(source.slice(start),c);
  await new Promise(resolve=>setImmediate(resolve));
  const conversationEvents=new Map();let now=10000;
  Object.assign(c, {Date:class extends Date {static now(){return now;}},$:id=>document.getElementById(id),selectedModelName:()=> 'ASPIRE',
    renderModelRunResult:()=>null,renderConversation(events,id){conversationEvents.set(id,events);},renderAstraStream(){}});
  const names=source.slice(source.indexOf('  function applyModelName('),source.indexOf('  function modelRequestProgress('));
  const render=source.slice(source.indexOf('  function renderCurrentConversation('),source.indexOf('  function renderPublicRunNotice('));
  vm.runInContext('let liveModelName="ASPIRE",lastLive=null;'+names+render,c);
  const work=document.getElementById('aspireTaskWork');
  return {api:window.yamAspireLineage,document,work,episode,nodes,
    poll(run,state={}) {c.renderCurrentConversation(run,state);},
    lastRun:()=>c.renderCurrentConversation.lastRun,
    advanceClock:(seconds=1)=>{now+=seconds*1000;},tick:()=>intervals.forEach(callback=>callback()),events:()=>conversationEvents.get('sideConversationMessages'),
    builtFrom:box=>(box || work).querySelectorAll('details').filter(node=>node.children[0]?.textContent==='Built from'),
    allText:box=>[box,...box.descendants()].map(node=>node.textContent).join('\n'),
    history(run) {const box=new Element('div');root.append(box);window.yamAspireLineage.history(box,run);return box;}};
}

test('SAM 3 task card ticks without replacing the work log and clears for a new task', async () => {
  const f=await disclosureFixture();
  f.api.requested('Fixture task',1);f.api.accepted('vision-task',1);
  const progress={lineage:{usage_recorded:true},updates:[{happened:'Program found',changed:'Saved source',next_action:'Initialize'}],
    vision:{model:'facebook/sam3',state:'starting',started_at:1}};
  const run=()=>({attempt_id:'vision-task',task:'Fixture task',status:'running',task_progress:progress});
  f.api.live(run(),{});
  const card=f.document.getElementById('aspireVisionStatus'),title=card.children[0];
  const log=f.work.descendants().find(node=>node.tagName==='OL'),row=log.children[0];log.scrollTop=25;
  assert.equal(card.hidden,false);assert.match(f.allText(card),/Starting SAM 3 vision\n9s elapsed/);
  f.advanceClock();f.api.live(run(),{});
  assert.match(f.allText(card),/10s elapsed/);assert.equal(card.children[0],title);
  assert.equal(f.work.descendants().find(node=>node.tagName==='OL'),log);assert.equal(log.children[0],row);assert.equal(log.scrollTop,25);
  progress.vision={...progress.vision,state:'ready',ready_at:46};f.api.live(run(),{});
  assert.match(f.allText(card),/SAM 3 vision ready\nStartup took 45s/);
  progress.vision={...progress.vision,state:'failed',ended_at:50};f.api.live(run(),{});
  assert.match(f.allText(card),/SAM 3 vision unavailable/);
  f.api.live({...run(),status:'stopped'},{});assert.equal(card.hidden,true);
  f.api.requested('Another task',2);f.api.accepted('next-task',2);
  f.api.live({attempt_id:'next-task',task:'Another task',status:'preparing',task_progress:{updates:[]}},{});
  assert.equal(f.document.getElementById('aspireVisionStatus').hidden,true);
});

test('reasoning Built from starts closed and preserves manual choices across acceptance and updates', async () => {
  const f=await disclosureFixture();
  assert.equal(f.builtFrom().length,1);assert.equal(f.builtFrom()[0].open,false,'prompt preview starts closed');
  f.api.requested('Fixture task',1);
  const initial=f.builtFrom()[0];assert.equal(initial.open,false,'fresh current task starts closed');
  assert.equal(initial.children[0].tagName,'SUMMARY');assert.equal(initial.children[0].textContent,'Built from');
  assert.match(f.allText(initial),/New skills added/);assert.match(f.allText(initial),/Skills reused/);
  initial.open=true; // Acceptance can precede the native asynchronous toggle event.
  f.api.accepted('current-one',1);
  assert.equal(f.builtFrom()[0],initial,'acceptance keeps the pending task disclosure');assert.equal(f.builtFrom()[0].open,true);
  const update=number=>f.api.live({attempt_id:'current-one',task:'Fixture task',status:'preparing',
    task_progress:{lineage:{usage_recorded:true},updates:[{happened:'Update '+number,changed:'Evidence '+number,next_action:'Continue'}]}},{});
  update(1);assert.equal(f.builtFrom()[0].open,true);
  assert.match(f.allText(f.work),/Update 1/,'content still updates');
  f.builtFrom()[0].open=false;update(2);assert.equal(f.builtFrom()[0].open,false);
  initial.dispatch('toggle');update(3);assert.equal(f.builtFrom()[0].open,false,'detached toggles cannot restore stale state');
  f.builtFrom()[0].open=true;update(4);assert.equal(f.builtFrom()[0].open,true);
  f.api.requested('Fixture task',2);assert.equal(f.builtFrom()[0].open,false,'a new attempt has its own default');
});

test('shared past-task disclosures start closed and retain choices during a normal re-render', async () => {
  const f=await disclosureFixture();
  const library=f.document.getElementById('aspireRecordedTasks');
  assert.equal(f.builtFrom(library).length,1);assert.equal(f.builtFrom(library)[0].open,false);
  const selector=f.document.getElementById('aspireTaskSelector');selector.value=f.episode.id;selector.dispatch('change');
  assert.equal(f.builtFrom().length,2);assert(f.builtFrom().every(box=>!box.open));
  f.builtFrom().forEach(box=>{box.open=true;});
  f.api.live({task:'Different current task',status:'stopped'},{});
  assert(f.builtFrom().every(box=>box.open));
  f.builtFrom().forEach(box=>{box.open=false;});
  f.api.live({task:'Different current task',status:'failed',error:'Fixture error'},{});
  assert(f.builtFrom().every(box=>!box.open));
  for(const run of [{episode_id:'episode-one'},{episode_id:'unrecorded',lineage:{usage_recorded:false}}]) {
    const history=f.history(run),disclosure=f.builtFrom(history)[0];assert.equal(disclosure.open,false);
    disclosure.open=true;disclosure.dispatch('toggle');
    assert.equal(f.builtFrom(f.history(run))[0].open,true);
  }
});

test('new live notes preserve the log, existing rows, source disclosures and reading position', async () => {
  const f=await disclosureFixture();
  f.api.requested('Fixture task',1);f.api.accepted('current-one',1);
  const lineage={usage_recorded:true,program:{source:'saved.py',source_sha256:'abc',code:'saved code'}};
  const updates=[{happened:'First note',changed:'Measured scene',next_action:'Plan'}];
  const live=()=>f.api.live({attempt_id:'current-one',task:'Fixture task',status:'preparing',task_progress:{lineage,updates:[...updates]}},{});
  live();
  const log=f.work.descendants().find(node=>node.tagName==='OL');
  const first=log.children[0], built=f.builtFrom()[0];built.open=true;
  const code=built.querySelectorAll('details').find(node=>node.children[0]?.textContent==='Complete program code');code.open=true;
  log.scrollTop=25;
  for(let i=0;i<20;i++) {updates.push({happened:'Note '+i,changed:'Evidence '+i,next_action:'Continue'});live();}
  assert.equal(f.work.descendants().find(node=>node.tagName==='OL'),log);
  assert.equal(log.children[0],first);
  assert.equal(f.builtFrom()[0],built);assert.equal(built.open,true);assert.equal(code.isConnected,true);assert.equal(code.open,true);
  assert.equal(log.scrollTop,25,'new notes must not pull the reader to the bottom');
  assert.equal(log.children.length,22,'the task prompt stays first, followed by every update');assert.match(f.allText(log),/Note 19/);
  log.scrollTop=400;updates.push({happened:'Latest note',changed:'New evidence',next_action:'Continue'});live();
  assert.equal(log.scrollTop,log.scrollHeight,'readers at the bottom keep following');
});

test('status-only polls keep current task nodes and repeated notes untouched', async () => {
  const f=await disclosureFixture();
  f.api.requested('Fixture task',1);f.api.accepted('current-one',1);
  const progress={lineage:{usage_recorded:true},updates:[{happened:'Plan saved',changed:'Evidence',next_action:'Continue'}]};
  const live=status=>f.api.live({attempt_id:'current-one',task:'Fixture task',status,task_progress:progress},{});
  live('preparing');const log=f.work.descendants().find(node=>node.tagName==='OL'),built=f.builtFrom()[0],row=log.children[0];
  live('running');live('stopped');
  assert.equal(f.work.descendants().find(node=>node.tagName==='OL'),log);assert.equal(f.builtFrom()[0],built);assert.equal(log.children[0],row);
  f.api.requested('Another task',2);
  assert.notEqual(f.work.descendants().find(node=>node.tagName==='OL'),log,'a new submission must replace the old task');
});

test('editing the prompt keeps a populated preview after clearing its previous content', async () => {
  const f=await disclosureFixture(),prompt=f.document.getElementById('prompt');
  for(const text of ['First preview task','Second preview task']) {
    prompt.value=text;prompt.dispatch('input');
    assert.equal(f.document.getElementById('sidePrompt').textContent,text);
    assert.equal(f.work.descendants().filter(node=>node.tagName==='OL').length,1);
    assert.match(f.allText(f.work),/Reuse a compatible executable|Run with Astra instead/);
  }
});


test('ordinary idle polls leave the ASPIRE preview prompt and headings untouched', async () => {
  const f=await disclosureFixture();
  f.poll(null);
  const ids=['sidePrompt','conversationModeTitle','reasoningButton'];
  const writes=ids.map(id=>f.nodes[id].textWrites);
  const preview=f.work.children[0],built=f.builtFrom()[0];built.open=true;
  for(let i=0;i<12;i++) f.poll(null);
  assert.deepEqual(ids.map(id=>f.nodes[id].textWrites),writes,'the generic conversation renderer must not rewrite ASPIRE text each poll');
  assert.equal(f.nodes.sidePrompt.textContent,'Fixture task');
  assert.equal(f.work.children[0],preview);assert.equal(f.builtFrom()[0],built);assert.equal(built.open,true);
});

test('idle preview survives unrelated stopped-run status and identity changes', async () => {
  const f=await disclosureFixture(),preview=f.work.children[0],built=f.builtFrom()[0];built.open=true;
  for(let i=0;i<12;i++) f.poll({attempt_id:'other-'+i,task:'Unrelated previous task',status:i%2 ? 'stopped' : 'failed',error:i%2 ? null : 'Old error'});
  assert.equal(f.work.children[0],preview,'polling a previous task must not rebuild the draft preview');
  assert.equal(f.builtFrom()[0],built);assert.equal(built.open,true);
  assert.equal(f.nodes.sidePrompt.textContent,'Fixture task');
});

test('reopening an unchanged idle preview keeps its existing content', async () => {
  const f=await disclosureFixture(),preview=f.work.children[0],built=f.builtFrom()[0];built.open=true;
  f.api.preview();f.api.preview();
  assert.equal(f.work.children[0],preview);assert.equal(f.builtFrom()[0],built);assert.equal(built.open,true);
});

test('polling restores generic conversation text when ASPIRE falls back to Astra', async () => {
  const f=await disclosureFixture();
  const route={actual_policy:'astra',prompt:'Uncap a pen'};
  f.poll({task:route.prompt,model_name:'Astra',status:'preparing',launch_route:route}, {status:'preparing',provider:{launch_route:route}});
  assert.equal(f.document.getElementById('aspireTaskPanel').hidden,true);
  assert.equal(f.nodes.sidePrompt.textContent,route.prompt);
  assert.equal(f.nodes.conversationModeTitle.textContent,'Astra decision notes');
  assert.equal(f.nodes.reasoningButton.textContent,'Astra decision notes');
});


test('ordinary active-task polls preserve task labels and existing log rows', async () => {
  const f=await disclosureFixture();
  const updates=[{happened:'Planning',changed:'Current scene',next_action:'Execute after planning'}];
  const run={attempt_id:'current',task:'Current task',status:'running',task_progress:{updates}};
  f.poll(run);
  const row=f.work.descendants().find(node=>node.tagName==='OL').children[0];
  const ids=['sidePrompt','conversationModeTitle','reasoningButton'];
  const writes=ids.map(id=>f.nodes[id].textWrites);
  for(let i=0;i<12;i++) f.poll(run);
  assert.deepEqual(ids.map(id=>f.nodes[id].textWrites),writes);
  assert.equal(f.work.descendants().find(node=>node.tagName==='OL').children[0],row);
  assert.equal(f.nodes.sidePrompt.textContent,'Current task');
});


test('editing a finished task keeps the draft preview through polls of its terminal progress', async () => {
  const f=await disclosureFixture();
  const run={attempt_id:'finished',task:'Finished task',status:'running',task_progress:{updates:[]}};
  f.poll(run);run.status='stopped';f.poll(run);
  f.nodes.prompt.value='New draft task';f.nodes.prompt.dispatch('input');
  assert.equal(f.nodes.sidePrompt.textContent,'New draft task');
  const preview=f.work.children[0],built=f.builtFrom()[0];built.open=true;
  for(let i=0;i<12;i++) f.poll(run);
  assert.equal(f.nodes.sidePrompt.textContent,'New draft task');
  assert.equal(f.work.children[0],preview);assert.equal(f.builtFrom()[0],built);assert.equal(built.open,true);
  assert.equal(f.document.getElementById('aspireTaskSelector').options[0].textContent,'Prompt preview · not submitted');
  f.api.requested('New draft task',1);
  assert.equal(f.nodes.sidePrompt.textContent,'New draft task');
  assert.match(f.allText(f.work),/Reviewing your prompt/);
});

test('current reasoning remains usable while its recorded catalog is pending or null', async () => {
  let finishCatalog;
  const f=await disclosureFixture({fetchCatalog:()=>new Promise(resolve=>finishCatalog=resolve)});
  const run={attempt_id:'current',task:'Place the block.',status:'running',task_progress:{task:'Place the block.',
    lineage:{usage_recorded:true,used:[{id:'saved',version:'1',episodes:['episode-one']}]},
    updates:[{happened:'Planning.',changed:'Fresh scene.',next_action:'Wait for the plan.'}]}};
  assert.doesNotThrow(()=>f.poll(run));
  assert.match(f.allText(f.work),/Planning/);
  finishCatalog(null);
  await new Promise(resolve=>setImmediate(resolve));
  assert.doesNotThrow(()=>f.poll({...run,status:'failed',error:'Planner failed.'}));
  assert.match(f.allText(f.work),/Planner failed/);
});

test('terminal failure appears in chat without a duplicate beside Run or replacing the draft', async () => {
  const f=await disclosureFixture();
  const reason='AspireNotReady: Reconciled ASPIRE feedback changed lease/cursor';
  const run={attempt_id:'previous',task:'Place the green block on the towel.',status:'stopped',error:reason,
    task_progress:{task:'Place the green block on the towel.',lineage:null,updates:[
      {happened:'Program found.',changed:'Saved code.',next_action:'Capture and plan after Home.'},
      {happened:'Task blocked.',changed:reason,next_action:'Review the error.'}]}};
  f.nodes.prompt.value='Next task draft';
  f.poll(run);
  let text=f.allText(f.work);
  assert.ok(text.indexOf('Task failed')>=0 && text.indexOf('Task failed')<text.indexOf('Program found.'));
  assert.doesNotMatch(text,/Preparing this task/);
  assert.match(text,/AspireNotReady: Reconciled ASPIRE feedback changed lease\/cursor/);
  assert.equal(f.document.getElementById('aspireTaskSelector').options[0].textContent,'Submitted task · failed');
  assert.equal(f.nodes.taskRunFailure.hidden,true);
  const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.ok(failure,'failure is a regular chat message');
  assert.match(f.allText(failure),/Task failed[\s\S]*Place the green block on the towel[\s\S]*AspireNotReady/);
  assert.equal(f.nodes.policyRouteNotice.hidden,true,'terminal tasks cannot keep a preparation lead');
  assert.equal(f.nodes.prompt.value,'Next task draft');
  const log=f.work.descendants().find(node=>node.tagName==='OL');
  for(let i=0;i<12;i++) f.poll(run);
  assert.equal(f.work.descendants().find(node=>node.dataset.messageId==='task-failure'),failure,'unchanged polling preserves the chat message');
  assert.equal(f.work.descendants().find(node=>node.tagName==='OL'),log);
  f.poll({...run,display_error:"Cannot read properties of null (reading 'episodes')"});
  text=f.allText(f.work);
  assert.match(text,/Playground error/);
  assert.match(text,/reading 'episodes'/);
  assert.equal(f.nodes.prompt.value,'Next task draft');
  f.api.requested('Next task draft',1);
  f.poll({task:'Next task draft',status:'preparing',reviewing_prompt:true});
  assert.equal(f.nodes.taskRunFailure.hidden,true,'new submissions clear the old failure immediately');
  assert.doesNotMatch(f.allText(f.work),/Task failed|AspireNotReady/);
});

test('owner diagnostic replaces a generic public error in the chat failure', async () => {
  const f=await disclosureFixture();
  const reason='AspireNotReady: Reconciled ASPIRE feedback changed lease/cursor';
  const progress={task:'Current task',updates:[]};
  f.poll({attempt_id:'current',task:progress.task,status:'stopped',error:'The run encountered a model or runner error.'},
    {status:'stopped',attempt_id:'current',error:reason,provider:{task_progress:progress}});
  assert.match(f.allText(f.work),/AspireNotReady/);
  const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.match(f.allText(failure),/AspireNotReady/);
  assert.doesNotMatch(f.allText(failure),/model or runner error/);
  assert.equal(f.nodes.taskRunFailure.hidden,true);
  assert.equal(f.lastRun().error,reason,'conversation and camera notes use the resolved diagnostic too');
});

test('hosted failures without native progress appear as chat messages', async () => {
  for(const status of ['failed','stopped','timed_out','disconnected']) {
    const f=await disclosureFixture({hostname:'playground.blupe.io'});
    f.nodes.provider.value='guest_astra';
    const reason='RuntimeError: right_tip_outside_workspace';
    f.poll({attempt_id:'ordinary-astra',task:'Place the block.',status,error:reason});
    const panel=f.document.getElementById('aspireTaskPanel');
    assert.equal(panel.hidden,false,status);
    assert.equal(panel.dataset.enabled,'true');
    const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
    assert.ok(failure,'failure is a chat message for '+status);
    assert.match(f.allText(failure),/Place the block[\s\S]*right_tip_outside_workspace/);
    assert.equal(f.nodes.taskRunFailure.hidden,true);
    assert.equal(f.nodes.sideConversationMessages.hidden,true);
  }
});

test('owner errors without native progress replace the generic error for the same attempt', async () => {
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  f.nodes.provider.value='guest_astra';
  const run={attempt_id:'ordinary-astra',task:'Place the block.',status:'stopped',error:'The run encountered a model or runner error.'};
  f.poll(run,{attempt_id:'ordinary-astra',status:'stopped',error:'RuntimeError: exact preparation failure'});
  const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.match(f.allText(failure),/exact preparation failure/);
  assert.doesNotMatch(f.allText(failure),/model or runner error/);
  f.poll(run,{attempt_id:'another-attempt',status:'stopped',error:'Unrelated error'});
  assert.doesNotMatch(f.allText(f.work),/Unrelated error/);
});

test('Astra fallback failures include the owner diagnostic in chat', async () => {
  const f=await disclosureFixture();
  const route={actual_policy:'astra',prompt:'Uncap a pen'};
  f.poll({attempt_id:'fallback',task:route.prompt,status:'running'},
    {attempt_id:'fallback',status:'running',provider:{launch_route:route}});
  assert.equal(f.document.getElementById('aspireTaskPanel').hidden,true,'active fallback keeps its response notes');
  const reason='RuntimeError: trajectory rejected before motion';
  f.poll({attempt_id:'fallback',task:route.prompt,status:'stopped',error:'The run encountered a model or runner error.'},
    {attempt_id:'fallback',status:'stopped',error:reason,provider:{launch_route:route}});
  const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.ok(failure,'fallback failure uses the chat');
  assert.match(f.allText(failure),/Uncap a pen[\s\S]*trajectory rejected before motion/);
  assert.doesNotMatch(f.allText(failure),/model or runner error/);
  assert.equal(f.nodes.taskRunFailure.hidden,true);
});

test('rejected hosted preparation without progress appears in chat', async () => {
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  f.nodes.provider.value='guest_astra';
  f.api.requested('Place the block.',1,false);
  const reason='ValueError: request preparation failed';
  f.api.rejected(reason,1);
  const panel=f.document.getElementById('aspireTaskPanel');
  assert.equal(panel.hidden,false);
  const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.ok(failure);
  assert.match(f.allText(failure),/request preparation failed/);
});

test('local submissions without native progress keep failure inside chat', async () => {
  const f=await disclosureFixture();
  f.api.requested('Place the block.',1,false);
  f.api.accepted('ordinary-astra',1);
  f.poll({attempt_id:'ordinary-astra',task:'Place the block.',status:'failed',error:'RuntimeError: task failed'});
  assert.equal(f.document.getElementById('aspireTaskPanel').hidden,false);
  assert.match(f.allText(f.work),/Task failed[\s\S]*RuntimeError: task failed/);
  assert.equal(f.nodes.taskRunFailure.hidden,true);
});

test('stopped, queued and running tasks without lineage do not claim to be preparing', async () => {
  const f=await disclosureFixture();
  for(const [status,label] of [['queued','Task queued'],['running','Task running'],['stopped','Task stopped'],['completed','Task completed']]) {
    f.poll({attempt_id:'current',task:'Current task',status,task_progress:{updates:[]}});
    assert.match(f.allText(f.work),new RegExp(label));
    assert.doesNotMatch(f.allText(f.work),/Preparing this task/);
    assert.equal(f.nodes.taskRunFailure.hidden,true);
  }
});

test('failure reasons use terminal diagnostics and retain long exact details', async () => {
  const f=await disclosureFixture();
  const reason='Planner failed: '+ 'measured geometry '.repeat(35);
  const run={task:'Current task',status:'stopped',error:'The run encountered a model or runner error.',
    task_progress:{updates:[{happened:'Task blocked.',changed:reason,next_action:'Review.'}]}};
  f.poll(run);
  const failure=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.match(f.allText(failure),/Planner failed/);
  const content=failure.descendants().find(node=>node.className==='aspireMessageContent');
  assert.ok(Array.from(content.descendants().map(node=>node.textContent).join('')).length<=400);
  failure.descendants().find(node=>node.tagName==='BUTTON').dispatch('click');
  assert.ok(failure.descendants().some(node=>node.textContent===reason));
  assert.equal(run.task_progress.updates[0].changed,reason,'full recorded error is preserved');
  assert.equal(f.nodes.taskRunFailure.hidden,true);
  run.task_progress.updates.push({happened:'After-parking outcome: UNVERIFIED.',changed:'No placement result.',next_action:'Review evidence.'});
  f.poll(run);
  assert.match(f.allText(failure),/Planner failed/,'parking updates retain the original failure reason');
  f.poll({...run,status:'running',error:null});
  assert.equal(f.nodes.taskRunFailure.hidden,true,'historical blocked notes do not make a recovering run failed');
  f.poll({...run,status:'timed_out',error:'Run reached time limit'});
  const timeout=f.work.descendants().find(node=>node.dataset.messageId==='task-failure');
  assert.match(f.allText(timeout),/Task timed out[\s\S]*Run reached time limit/);
  assert.equal(f.nodes.taskRunFailure.hidden,true);
});


test('unchanged terminal errors keep one conversation timestamp while real new errors and attempts update', async () => {
  const f=await disclosureFixture();
  const run={attempt_id:'failed-one',task:'Current task',status:'stopped',error:'Exact planner failure',events:[]};
  f.poll(run);const snapshot=JSON.stringify(f.events()),stamp=f.events()[0].timestamp;
  for(let i=0;i<12;i++) {f.advanceClock();f.poll({...run});assert.equal(JSON.stringify(f.events()),snapshot);}
  assert.deepEqual(run.events,[],'synthesized messages must not mutate recorded evidence');
  f.advanceClock();f.poll({...run,error:'Another exact failure'});
  assert.equal(f.events()[0].message,'Another exact failure');assert(f.events()[0].timestamp>stamp);
  f.advanceClock();f.poll({...run,attempt_id:'failed-two'});assert(f.events()[0].timestamp>stamp);
  f.poll({...run,attempt_id:'recorded-end',ended_at:7});assert.equal(f.events()[0].timestamp,7);
});

test('unchanged polls preserve all shared panel labels and visibility in preview, terminal and recorded views', async () => {
  const f=await disclosureFixture();
  const run={attempt_id:'terminal',task:'Terminal task',status:'stopped',error:'Exact failure',task_progress:{updates:[]}};
  const ids=['sideRunner','sidePrompt','sideConversationTitle','sideConversationState','sideConversationMessages',
    'conversationModeTitle','reasoningButton','taskRunFailure'];
  const verify=()=>{
    f.poll(run);
    const writes=ids.map(id=>[f.nodes[id].textWrites,f.nodes[id].hiddenWrites]);
    const panel=f.document.getElementById('aspireTaskPanel'),hidden=panel.hiddenWrites;
    const content=[...f.work.children];
    for(let i=0;i<12;i++) {f.advanceClock();f.poll({...run});}
    assert.deepEqual(ids.map(id=>[f.nodes[id].textWrites,f.nodes[id].hiddenWrites]),writes);
    assert.equal(panel.hiddenWrites,hidden);assert.deepEqual(f.work.children,content);
  };
  verify();
  f.nodes.prompt.value='New draft task';f.nodes.prompt.dispatch('input');verify();
  const selector=f.document.getElementById('aspireTaskSelector');selector.value=f.episode.id;selector.dispatch('change');verify();
});

test('a stopped physical run keeps active repair visible over a draft preview', async () => {
  const f=await disclosureFixture();f.api.preview();
  const progress={updates:[],attempts:[{id:'repair-1',mode:'offline_code_repair',status:'running',trace:[]}],
    stage:{stage:'offline_repair',title:'Astra is diagnosing',detail:'Repairing the latest attempt',active:true,started_at:5}};
  const run={attempt_id:'repairing',task:'Place the red block on the towel',status:'stopped',task_progress:progress};
  f.api.live(run,{});
  const selector=f.document.getElementById('aspireTaskSelector');
  assert.equal(selector.disabled,true);
  assert.equal(f.nodes.sidePrompt.textContent,run.task);
  assert.match(f.allText(f.document.getElementById('aspireStageStatus')),/Astra is diagnosing[\s\S]*Working/);
  f.nodes.prompt.value='Another draft';f.nodes.prompt.dispatch('input');f.api.preview();
  assert.equal(f.nodes.sidePrompt.textContent,run.task);
  progress.stage.active=false;progress.attempts[0].status='FAILED';f.api.live(run,{});
  assert.equal(selector.disabled,false);
  assert.match(f.allText(f.document.getElementById('aspireStageStatus')),/Not working/);
});

test('native stage timer and linked recovery preserve current work and reader state', async () => {
  const f=await disclosureFixture();f.api.requested('Fixture task',1);f.api.accepted('current-one',1);
  const updates=[{happened:'Saved program failed.',changed:'IK non-convergence',next_action:'Astra recovery'}];
  const progress={updates,lineage:{usage_recorded:true},stage:{stage:'native_planning',title:'Testing native candidate plans',
    detail:'Candidate 8; rejected 7.',elapsed_s:12,last_failure:'IK non-convergence'},
    attempts:[{id:'aspire-1',policy:'aspire',status:'PLAN_FAILED',source_sha256:'old',result:{reason:'IK non-convergence'}},
      {id:'astra-1',policy:'astra',status:'running',parent_attempt_id:'aspire-1'}]};
  const live=()=>f.api.live({attempt_id:'current-one',task:'Fixture task',status:'running',task_progress:progress},{});
  live();const card=f.document.getElementById('aspireStageStatus'),log=f.work.descendants().find(n=>n.tagName==='OL');
  const row=log.children[0],built=f.builtFrom()[0];built.open=true;log.scrollTop=25;
  progress.stage.elapsed_s=17;progress.stage.detail='Candidate 12; rejected 11.';live();
  assert.equal(f.document.getElementById('aspireStageStatus'),card);
  assert.match(f.allText(card),/17s in this stage/);assert.match(f.allText(card),/Candidate 12; rejected 11/);
  assert.equal(log.children[0],row);assert.equal(log.scrollTop,25);assert.equal(built.open,true);
  assert.match(f.allText(f.document.getElementById('aspireTaskActivity')),/Follows aspire-1/);
  progress.stage={stage:'offline_repair',title:'Astra offline repair',detail:'Planning only',elapsed_s:4,active:true};
  f.api.live({attempt_id:'current-one',task:'Fixture task',status:'stopped',task_progress:progress},{});
  assert.equal(card.hidden,false);assert.match(f.allText(card),/Astra offline repair/);
});

test('repair messages appear directly in the activity feed and preserve reader state through revisions', async () => {
  const f=await disclosureFixture();
  const attempt={id:'astra-repair-1',parent_attempt_id:'aspire-1',policy:'astra',mode:'offline_code_repair',status:'running',started_at:5,trace:[]};
  const progress={lineage:{usage_recorded:true},updates:[{happened:'Original UNVERIFIED.',changed:'Keep original evidence',next_action:'Repair offline',timestamp:4}],
    attempts:[{id:'aspire-1',policy:'aspire',status:'UNVERIFIED'},attempt],
    stage:{stage:'offline_repair',title:'Astra is diagnosing',detail:'Planning only',elapsed_s:129,active:true,started_at:5}};
  const run={attempt_id:'current-repair',task:'Current black chip task',status:'stopped',task_progress:progress};
  const poll=()=>f.api.live(run,{});poll();
  const box=f.document.getElementById('aspireTaskActivity'),ol=box.children[0];
  assert.equal(ol.tagName,'OL');assert.equal(ol.children[0].dataset.role,'You');
  assert.match(f.allText(box),/Follows aspire-1/);
  assert.equal(box.descendants().filter(n=>n.tagName==='DETAILS').length,0,'updates require no disclosure clicks');
  attempt.trace.push({id:1,timestamp:8,attempt_id:attempt.id,kind:'model_progress',progress_type:'summary',message:'Revising occluded-target handling.',revision:1});
  poll();assert.match(f.allText(box),/Astra[\s\S]*Revision 1[\s\S]*Revising occluded-target handling/);
  const row=ol.children.find(n=>n.dataset.messageId==='astra-repair-1-trace-1'),message=row.descendants().find(n=>n.tagName==='P' && n.textContent==='Revising occluded-target handling.');
  ol.scrollTop=25;f.builtFrom()[0].open=true;
  attempt.trace.push({id:2,timestamp:9,attempt_id:attempt.id,kind:'activity',message:'Complete plan failed.',changed:'IK non-convergence',next_action:'Revise a distinct candidate'});
  progress.stage.elapsed_s=140;poll();
  assert.equal(f.document.getElementById('aspireTaskActivity'),box);assert.equal(box.children[0],ol);
  assert.equal(ol.children.find(n=>n.dataset.messageId===row.dataset.messageId),row);
  assert.ok(row.descendants().includes(message));assert.equal(ol.scrollTop,25);
  assert.equal(f.builtFrom()[0].open,true);assert.match(f.allText(box),/IK non-convergence/);
  attempt.trace[0]={...attempt.trace[0],message:'Revising the chip-fit uncertainty handling.'};poll();
  assert.ok(row.descendants().includes(message));assert.match(message.textContent,/chip-fit uncertainty/);
  const writes=ol.descendants().map(n=>[n.textWrites,n.hiddenWrites]);
  for(let i=0;i<8;i++) {progress.stage.elapsed_s++;poll();}
  assert.deepEqual(ol.descendants().map(n=>[n.textWrites,n.hiddenWrites]),writes);
  attempt.status='PLAN_VALIDATED';attempt.source_sha256='a'.repeat(64);attempt.ended_at=10;
  attempt.trace.push({id:3,timestamp:10,attempt_id:attempt.id,kind:'model_response',message:'Centering remains unknown.',lesson:'Preserve verification checks.'});
  progress.stage=null;poll();
  assert.equal(box.children[0],ol);assert.match(f.allText(box),/Centering remains unknown/);
  assert.match(f.allText(box),/Revised code saved · plan passed/);
  assert.match(f.allText(f.document.getElementById('aspireStageStatus')),/Not working/);
  assert.doesNotMatch(f.allText(f.document.getElementById('aspireTaskAnswer')),/Task success verified/);
});

test('phase card distinguishes recent work, silent waiting, lost status and verified completion', async () => {
  const f=await disclosureFixture();
  const attempt={id:'repair-1',policy:'astra',status:'running',mode:'offline_code_repair',trace:[
    {id:1,timestamp:9,attempt_id:'repair-1',kind:'model_progress',progress_type:'summary',revision:2,message:'Revising grasp.'}]};
  const progress={stage:{stage:'offline_repair',title:'Astra repair',started_at:1,elapsed_s:9,active:true},attempts:[attempt],updates:[]};
  const run={attempt_id:'phase',task:'Current task',status:'stopped',task_progress:progress};
  f.api.live(run,{});
  const card=f.document.getElementById('aspireStageStatus');
  assert.match(f.allText(card),/Writing repair code[\s\S]*Working[\s\S]*revision 2 · Last update 1s ago/);
  f.advanceClock(20);f.tick();
  assert.equal(card.dataset.state,'connection');assert.match(f.allText(card),/Connection delayed[\s\S]*Current activity is unknown/);
  f.api.live(run,{});assert.equal(card.dataset.state,'active');
  f.advanceClock(50);f.api.live(run,{});
  assert.equal(card.dataset.state,'waiting');assert.match(f.allText(card),/Waiting for update[\s\S]*no new progress is confirmed/);
  progress.stage=null;attempt.status='PLAN_VALIDATED';f.api.live(run,{});
  assert.equal(card.dataset.state,'stopped');assert.match(f.allText(card),/Not working/);
  progress.outcome={success:true,status:'SUCCESS'};f.api.live(run,{});
  assert.equal(card.dataset.state,'verified');assert.match(f.allText(card),/Complete · verified/);
});

test('code-writing conversation works before queue admission without leaking raw model fields',()=>{
  const run={task:'Write a task',status:'preparing',launch_route:{code_generation_requested:true},task_progress:{updates:[],attempts:[]},events:[
    {id:1,timestamp:1,kind:'model_progress',progress_type:'summary',message:'Writing the grasp.',encrypted_content:'secret'},
    {id:2,timestamp:2,kind:'model_progress',progress_type:'raw_reasoning',message:'private'},
    {id:3,timestamp:3,kind:'model_response',message:'source payload'}]};
  const messages=context.aspireActivityEntries(run);
  assert.equal(messages[0].role,'You');assert.equal(messages[1].role,'Astra');assert.equal(messages[1].message,'Writing the grasp.');
  assert.doesNotMatch(JSON.stringify(messages),/secret|private|source payload/);
  assert.equal(context.aspireWorkStatus(run,10).status,'Working','initial coding summaries count as real activity');
  assert.equal(context.aspireWorkStatus({...run,events:[]},10).status,'Waiting for update','no activity timestamp must never imply fresh progress');
});

test('hosted chat renders transported native progress and public summaries without local library requests',async()=>{
  let catalogRequests=0;
  const f=await disclosureFixture({hostname:'playground.blupe.io',fetchCatalog(){catalogRequests++;throw Error('Local library requested remotely');}});
  f.nodes.provider.value='guest_astra';
  const task='Place the green block on the blue chip.';
  const progress={task,updates:[{happened:'Native plan passed.',changed:'Executing the saved program.',next_action:'Verify after parking.',timestamp:10}],
    stage:{stage:'execution',title:'Running the task',active:true,event_at:10},attempts:[]};
  f.poll({task,status:'running',attempt_id:'hosted',events:[
    {speaker:'Tool',kind:'model_request',message:'ASPIRE_TASK_PROGRESS_V1\n'+JSON.stringify({task_progress:progress}),timestamp:10},
    {id:2,speaker:'Tool',kind:'model_request',message:'PUBLIC_MODEL_EVENT_V1\n'+JSON.stringify({task,kind:'model_progress',progress_type:'summary',message:'The grasp is ready.',timestamp:11}),timestamp:11}]});
  assert.equal(catalogRequests,0);
  assert.equal(f.document.getElementById('aspireTaskPanel').hidden,false);
  assert.equal(f.document.getElementById('aspireTaskPanel').dataset.enabled,'true');
  assert.match(f.allText(f.work),/Native plan passed/);
  assert.match(f.allText(f.work),/The grasp is ready/);
  assert.doesNotMatch(f.allText(f.work),/ASPIRE_TASK_PROGRESS_V1|PUBLIC_MODEL_EVENT_V1/);
});

test('legacy current repair shows only existing public summaries from its own request window', () => {
  const attempt={id:'astra-repair-1',started_at:10,ended_at:20};
  const events=[{id:1,timestamp:5,kind:'model_progress',message:'Previous development',progress_type:'summary'},
    {id:2,timestamp:11,kind:'model_request',message:'Generate/revise an ASPIRE program'},
    {id:3,timestamp:12,kind:'model_progress',message:'Actual repair summary',details:{progress_type:'summary',encrypted_content:'secret'}},
    {id:4,timestamp:13,kind:'model_progress',message:'hidden',progress_type:'raw_reasoning'},
    {id:5,timestamp:14,kind:'model_progress',message:'Other attempt',details:{progress_type:'summary',attempt_id:'other'}}];
  const trace=context.aspireAttemptTrace(attempt,events);
  assert.equal(trace.length,1);assert.equal(trace[0].message,'Actual repair summary');
  assert.equal(trace[0].attempt_id,attempt.id);assert.doesNotMatch(JSON.stringify(trace),/secret|hidden|Previous|Other attempt/);
  events.push({id:6,timestamp:15,kind:'model_request',message:'Unrelated promotion'});
  assert.equal(context.aspireAttemptTrace(attempt,events).length,0);
});

test('task answer separates plan validation from success and patches the next action without resetting reader state',async()=>{
  assert.equal(context.aspireTaskAnswer({status:'stopped',task_progress:{outcome:{success:false},attempts:[{status:'PLAN_VALIDATED'}]}}).state,'unresolved');
  const f=await disclosureFixture();
  const progress={updates:[],lineage:{usage_recorded:true},resolution:{state:'recovering',title:'Retrying the task · 1 of 3',detail:'Fresh geometry passed',next_action:'Verify after parking'}};
  const run={attempt_id:'retry',task:'Black chip task',status:'preparing',task_progress:progress};
  const poll=()=>f.api.live(run,{});poll();
  const box=f.document.getElementById('aspireTaskAnswer'),title=box.children[0];f.builtFrom()[0].open=true;
  assert.match(f.allText(box),/1 of 3/);
  progress.resolution={state:'action_needed',title:'Expose the target chip before recovery',detail:'Fresh rim unavailable',next_action:'Move black block off green chip; then Run'};
  run.status='failed';poll();assert.equal(f.document.getElementById('aspireTaskAnswer'),box);assert.equal(box.children[0],title);
  assert.match(f.allText(box),/Move black block off green chip/);assert.equal(f.builtFrom()[0].open,true);
  const writes=box.descendants().map(n=>[n.textWrites,n.hiddenWrites]);for(let i=0;i<8;i++)poll();
  assert.deepEqual(box.descendants().map(n=>[n.textWrites,n.hiddenWrites]),writes);
});


test('linked repair displays its exact planning failure alongside Astra diagnosis',()=>{
  const error='RuntimeError: Missing required mask: chip';
  const row=context.aspireAttemptUpdates([{id:'astra-repair-1',policy:'astra',mode:'offline_code_repair',
    parent_attempt_id:'aspire-1',status:'FAILED',result:{status:'PROGRAM_ERROR',reason:error},
    diagnosis:'Added alternative chip descriptions.'}])[0];
  assert.ok(row.changed.includes(error));
  assert.ok(row.changed.includes('Added alternative chip descriptions.'));
});


test('saved parked error is visible even when older task updates contain a generic outcome',()=>{
  const original={happened:'After-parking outcome: UNVERIFIED.',changed:'Placement not confirmed',next_action:'Review images'};
  const item={task_updates:[original],verification_error:'Missing required mask: block'};
  const updates=context.aspireRecordedUpdates(item);
  assert.equal(updates.at(-1).changed,'Missing required mask: block');
  assert.equal(updates.at(-1).happened,'Post-parking verification failed.');
  assert.equal(item.task_updates.length,1);
  assert.equal(item.task_updates[0],original);
});


test('recorded tasks use the same conversation while clearly remaining stopped',async()=>{
  const f=await disclosureFixture();
  const selector=f.document.getElementById('aspireTaskSelector');selector.value=f.episode.id;selector.dispatch('change');
  const chat=f.work.descendants().find(n=>n.tagName==='OL');
  assert.equal(chat.className,'aspireActivityLog');assert.equal(chat.children[0].dataset.role,'You');
  assert.match(f.allText(f.work),/Recorded task · not running/);
  assert.match(f.allText(f.work),/viewing this task does not start work/);
});

test('historical planning errors remain visible in chat without changing their saved evidence',()=>{
  const item={task:'Saved task',attempts:[{id:'repair-code-1',policy:'astra',plan:{status:'PROGRAM_ERROR',reason:'Missing required mask: chip'},execution:{}}],images:[],replays:[]};
  const original=JSON.stringify(item),run=context.aspireRecordedActivity(item),messages=context.aspireActivityEntries(run);
  assert.ok(messages.some(message=>message.message.includes('Missing required mask: chip')));
  assert.equal(JSON.stringify(item),original);
});


test('the same activity feed retains execution, verification, repair and exact planning failures',()=>{
  const run={task:'Place black block on blue chip',status:'stopped',task_progress:{
    updates:[{timestamp:1,happened:'Executing the admitted plan.',changed:'13 motion calls',next_action:'Verify after parking'}],
    outcome:{checked_at:2,status:'UNVERIFIED',success:false,reason:'Could not detect the target chip'},
    attempts:[{id:'repair-1',parent_attempt_id:'program-1',mode:'offline_code_repair',policy:'astra',started_at:3,
      ended_at:5,status:'PLAN_FAILED',reason:'Missing required mask: chip',trace:[
        {id:1,attempt_id:'repair-1',timestamp:4,kind:'model_progress',progress_type:'summary',message:'Revising chip detection.'}]}]}};
  const entries=context.aspireActivityEntries(run);
  const text=JSON.stringify(entries);
  assert.match(text,/13 motion calls/);assert.match(text,/Could not detect the target chip/);
  assert.match(text,/Revising chip detection/);assert.match(text,/Missing required mask: chip/);
  const timed=entries.filter(row=>Number.isFinite(row.timestamp)).map(row=>row.timestamp);
  assert.deepEqual(Array.from(timed),[1,2,3,4,5]);
});

test('operator input and validated code remain distinct from verified physical success',()=>{
  const run={status:'stopped',task_progress:{resolution:{state:'action_needed',title:'Expose the target chip',detail:'Chip center is not measurable.'},
    attempts:[{id:'repair-1',mode:'offline_code_repair',status:'PLAN_VALIDATED',source_sha256:'a'.repeat(64)}]}};
  const phase=context.aspireWorkStatus(run,100,1);
  assert.equal(phase.status,'Needs your input');assert.equal(phase.title,'Expose the target chip');
  assert.equal(phase.path.at(-1).state,'hold');assert.equal(phase.source,'a'.repeat(64));
  assert.notEqual(phase.state,'verified');
  run.task_progress.resolution={state:'unresolved'};
  assert.equal(context.aspireWorkStatus(run,100,1).status,'Not working','a stopped task does not become a live connection warning');
});

test('long chat messages expand without losing full errors or reader choice during live updates',async()=>{
  const f=await disclosureFixture();
  const error='RuntimeError: Revised complete sequences rejected: '+JSON.stringify({configurations:Array.from({length:20},(_,i)=>({side:'right',tilt_deg:30,orientation_rank:i,site:[0.636,-0.219,0.045]}))});
  const progress={updates:[{happened:'Complete plan failed.',changed:error,next_action:'Revise and test the complete plan.'}],
    stage:{stage:'offline_repair',title:'Writing repair code',active:true}};
  const run={attempt_id:'compact-message',task:'Current task',status:'stopped',task_progress:progress};
  const original=JSON.stringify(run);f.api.live(run,{});
  const log=f.work.descendants().find(n=>n.className==='aspireActivityLog');
  const row=log.children.find(n=>n.dataset.messageId==='update-0');
  const content=row.children.find(n=>n.className==='aspireMessageContent');
  const toggle=row.children.find(n=>n.className==='aspireMessageToggle');
  const shown=()=>content.children.filter(n=>!n.hidden).map(n=>n.textContent).join('');
  assert.equal(Array.from(shown()).length,400);assert.ok(shown().endsWith('…'));
  assert.equal(toggle.hidden,false);assert.equal(toggle.type,'button');
  assert.equal(f.document.getElementById('aspireTaskAnswer').hidden,true);
  assert.equal(f.work.children.find(n=>n.className==='aspireAuthorship').hidden,true);
  toggle.dispatch('click');assert.ok(shown().includes(error));assert.equal(toggle.textContent,'Show less');
  log.scrollTop=25;f.api.live(run,{});
  assert.equal(log.children.find(n=>n.dataset.messageId==='update-0'),row);
  assert.equal(toggle.textContent,'Show less');assert.equal(log.scrollTop,25);
  assert.equal(JSON.stringify(run),original);
  progress.updates[0].changed+=' Latest failure evidence.';f.api.live(run,{});
  assert.ok(shown().includes('Latest failure evidence.'));assert.equal(toggle.textContent,'Show less');
  toggle.dispatch('click');assert.equal(Array.from(shown()).length,400);assert.equal(toggle.textContent,'Show more');
  progress.updates[0].changed='Short explanation.';f.api.live(run,{});
  assert.equal(toggle.hidden,true);assert.ok(shown().includes('Short explanation.'));assert.ok(!shown().includes('…'));
  progress.stage.active=false;progress.outcome={status:'UNVERIFIED',success:false,reason:'Placement was not confirmed.'};f.api.live(run,{});
  assert.equal(f.document.getElementById('aspireTaskAnswer').hidden,false,'terminal outcomes remain available');
});

test('external native publisher reconstructs ASPIRE evidence and never treats it as model reasoning', async () => {
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  const progress={task:'Native fixture',updates:[{happened:'Segmenting current scene.',changed:'Fresh capture.',next_action:'Plan.'}],
    lineage:{usage_recorded:true,used:[],retrieved:[],new_skills:[],authorship:{generated_by_codex:true,mode:'reuse'}},
    attempts:[{id:'aspire-1',policy:'aspire',status:'FAILED',result:{reason:'Grasp failed'}}],
    resolution:{state:'unresolved',title:'Retry limit reached 2 of 2',detail:'Post parking evaluation did not confirm.',next_action:'Review checks.'},
    outcome:{status:'UNVERIFIED',success:false,reason:'Post parking evaluation did not confirm.'}};
  const envelope={kind:'model_request',speaker:'Tool',message:'ASPIRE_TASK_PROGRESS_V1\n'+JSON.stringify({task_progress:progress})};
  const run={run_id:'external-native',task:progress.task,status:'stopped',events:[envelope]};
  f.poll(run);
  assert.equal(f.document.getElementById('aspireTaskPanel').dataset.enabled, 'true');
  assert.match(f.allText(f.work),/Reusing code generated by Codex/);
  assert.match(f.allText(f.work),/Retry limit reached 2 of 2/);
  assert.match(f.allText(f.work),/After-parking result · UNVERIFIED/);
  assert.match(f.allText(f.work),/Segmenting current scene/);
  assert.match(f.allText(f.work),/aspire-1/);assert.match(f.allText(f.work),/FAILED/);
  assert.equal(f.events().length,0);
  for(const bad of [{...envelope,speaker:'User'}, {...envelope,kind:'model_response'},
    {...envelope,message:envelope.message.replace('Native fixture','Other task')},
    {...envelope,message:'ASPIRE_TASK_PROGRESS_V1\n{bad json'}]) {
    assert.equal(context.aspireSharedRun({...run,events:[bad]}).task_progress,undefined);
  }
});

test('hosted worker progress reaches the same public panel without a text envelope', async () => {
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  const progress={task:'Hosted fixture',updates:[{happened:'Native plan passed.',changed:'Full candidate accepted.',next_action:'Execute.'}],
    lineage:{usage_recorded:true,used:[],retrieved:[],new_skills:[],authorship:{generated_by_codex:true,mode:'reuse'}},
    stage:{stage:'native_planning',title:'Testing native candidate plans',active:true,started_at:1},
    attempts:[{id:'aspire-1',policy:'aspire',status:'running'}]};
  f.poll({run_id:'hosted-native',task:progress.task,status:'running',task_progress:progress,events:[]});
  assert.match(f.allText(f.work),/Reusing code generated by Codex/);
  assert.match(f.allText(f.work),/Native plan passed/);
  assert.match(f.allText(f.work),/Testing native candidate plans/);
  progress.stage=null;progress.outcome={status:'UNVERIFIED',success:false,reason:'Parked checks did not confirm.'};
  f.poll({run_id:'hosted-native',task:progress.task,status:'stopped',task_progress:progress,events:[]});
  assert.match(f.allText(f.work),/After-parking result · UNVERIFIED/);
});

test('hosted task details retain actual provenance, attempts and parked outcome notes', async () => {
  let catalogRequests=0;
  const f=await disclosureFixture({hostname:'playground.blupe.io',fetchCatalog(){catalogRequests++;return null;}});
  assert.ok(f.api,'task progress must initialize on the public hostname');
  const progress={lineage:{usage_recorded:true,authorship:{generated_by_codex:true,mode:'reuse'}},
    updates:[{happened:'After-parking outcome: UNVERIFIED.',changed:'Post-parking evaluation did not confirm: SENTINEL_CENTER',next_action:'Review the outcome.'}],
    attempts:[{id:'aspire-1',policy:'aspire',status:'FAILED',result:{reason:'SENTINEL_ATTEMPT'}}],
    resolution:{state:'unresolved',title:'Task remains unverified',detail:'SENTINEL_OUTCOME',next_action:'Review the failed check.'},
    outcome:{status:'UNVERIFIED',success:false,reason:'Post-parking evaluation did not confirm: SENTINEL_CENTER'}};
  f.api.live({attempt_id:'hosted',task:'Fixture task',status:'stopped',task_progress:progress},{});
  assert.equal(f.document.getElementById('aspireTaskPanel').hidden,false);
  const text=f.allText(f.work);
  assert.match(text,/Reusing code generated by Codex/);
  assert.match(text,/aspire-1/);assert.match(text,/FAILED/);
  for(const sentinel of ['SENTINEL_CENTER','SENTINEL_ATTEMPT','SENTINEL_OUTCOME']) assert.ok(text.includes(sentinel));
  assert.doesNotMatch(text,/Retry limit reached|2 of 2/);
  assert.equal(catalogRequests,0,'station library endpoint is local only');
});

test('hosted status cannot expose a physical recovery button from station resolution data', async () => {
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  assert.ok(f.api);
  f.api.live({attempt_id:'hosted',task:'Fixture task',status:'stopped',task_progress:{
    resolution:{state:'unresolved',title:'Task remains unverified',detail:'Unverified',next_action:'Review',recovery_available:true},
    outcome:{status:'UNVERIFIED',reason:'SENTINEL_UNVERIFIED',images:[{camera:'top',url:'/api/aspire-lineage/artifacts/'+'a'.repeat(64)}]}}},{});
  const answer=f.document.getElementById('aspireTaskAnswer');
  assert.equal(answer.children.find(n=>n.tagName==='BUTTON').hidden,true);
  assert.equal(f.work.descendants().some(n=>n.tagName==='IMG' || n.tagName==='A'),false,'local artifacts are not online links');
});

test('hosted terminal task without parked evidence explicitly stays unverified', async () => {
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  assert.ok(f.api);
  f.api.live({attempt_id:'hosted',task:'Fixture task',status:'stopped',task_progress:{updates:[],attempts:[]}},{});
  assert.match(f.allText(f.work),/Physical success has not been verified/);
  assert.doesNotMatch(f.allText(f.work),/Complete · verified/);
});

test('ASPIRE task work log renders public model updates even when native progress is unchanged', async () => {
  const f=await disclosureFixture();
  const run={attempt_id:'summary-fixture',task:'Fixture task',status:'running',events:[],task_progress:{
    lineage:{usage_recorded:true},updates:[{timestamp:100,happened:'Native planner running.',changed:'Testing current geometry.',next_action:'Wait for the full plan.'}]}};
  f.api.live(run,{});
  assert.doesNotMatch(f.allText(f.work),/SENTINEL_ASPIRE_SUMMARY/);
  run.events.push({id:1,timestamp:101,kind:'model_progress',progress_type:'summary',message:'SENTINEL_ASPIRE_SUMMARY'});
  f.api.live(run,{});
  assert.match(f.allText(f.work),/SENTINEL_ASPIRE_SUMMARY/);
  assert.match(f.allText(f.work),/Native planner running/);
  run.events.push({id:2,timestamp:102,kind:'model_progress',progress_type:'raw_reasoning',message:'PRIVATE_SENTINEL'});
  f.api.live(run,{});
  assert.doesNotMatch(f.allText(f.work),/PRIVATE_SENTINEL/);
});

test('public stage timer advances across successive shared snapshots',async()=>{
  const f=await disclosureFixture({hostname:'playground.blupe.io'});
  let first;
  for(const elapsed_s of [12.4,13.7]) {
    const progress={task:'Native timer fixture',updates:[],stage:{stage:'planning',title:'Checking paths',active:true,elapsed_s}};
    f.poll({run_id:'timer-fixture',task:progress.task,status:'running',events:[{kind:'model_request',speaker:'Tool',
      message:'ASPIRE_TASK_PROGRESS_V1\n'+JSON.stringify({task_progress:progress})}]});
    const stage=f.document.getElementById('aspireStageStatus');
    assert.match(f.allText(stage),new RegExp(Math.floor(elapsed_s)+'s'));
    if(first)assert.equal(stage,first);first=stage;
  }
});

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.env.YAM_TEST_HOSTED_JS || path.join(__dirname, '../static/hosted.js'),'utf8');
const start = source.indexOf('  let attentionSession = null;');
const end = source.indexOf('  function render(state)', start);
const elements = {};
const $ = id => elements[id] ||= {hidden:false,textContent:'',classes:new Set(),focusCount:0,scrolls:[],
  classList:{toggle(name,on){
    // DOMTokenList treats an undefined optional force argument as a toggle.
    if(on === undefined) on=!$(id).classes.has(name);
    on ? $(id).classes.add(name) : $(id).classes.delete(name);
  }},
  closest(selector){assert.equal(selector,'.queuePlace');return $('queuePlace');},
  focus(){this.focusCount++;},scrollIntoView(options){this.scrolls.push(options);}};
const context = vm.createContext({$,window:{matchMedia:()=>({matches:true})}});
vm.runInContext(source.slice(start,end),context);
const render = state => context.guideRunAttention(state);
render({status:'queued',session_id:'mine',queue_position:3});
assert.equal($('position').textContent,'#3');
assert.equal($('yourQueue').scrolls.length,1);
render({status:'queued',session_id:'mine',queue_position:2});
assert.equal($('position').textContent,'#2');
assert.equal($('yourQueue').scrolls.length,1);
render({status:'preparing',session_id:'mine'});
assert.equal($('position').textContent,'Up next');
assert.equal($('viewerCue').hidden,false);
assert.equal($('viewerCueTitle').textContent,'Your run is preparing');
render({status:'running',session_id:'mine',first_call_wander_enabled:true,run_metrics:{model_calls:0}});
assert.equal($('viewerCueTitle').textContent,'Your robot is warming up');
assert.equal($('viewerCueDetail').textContent,'The arms do a short dance while Astra plans the first move. Your task begins after they return.');
assert.equal($('queueGuidance').textContent,'Startup dance — waiting for Astra’s first decision.');
// Repeated warmup polls and the first settled decision must not scroll again.
render({status:'running',session_id:'mine',first_call_wander_enabled:true,run_metrics:{model_calls:0}});
assert.equal($('liveViewer').scrolls.length,1);
render({status:'running',session_id:'mine',first_call_wander_enabled:true,run_metrics:{model_calls:1}});
assert.equal($('viewerCueTitle').textContent,'Your run is live');
assert.equal($('viewerCueDetail').textContent,'Watch your robot here. The video may follow with a short delay.');
// Missing metrics or disabled startup animation must not claim a dance.
render({status:'running',session_id:'mine',first_call_wander_enabled:true});
assert.equal($('viewerCueTitle').textContent,'Your run is live');
render({status:'running',session_id:'mine',first_call_wander_enabled:false,run_metrics:{model_calls:0}});
assert.equal($('viewerCueTitle').textContent,'Your run is live');
render({status:'running',session_id:'mine'});
assert.equal($('viewerCue').hidden,false);
assert.equal($('viewerCueTitle').textContent,'Your run is live');
assert.equal($('liveViewer').scrolls.length,1);
assert.equal($('liveViewer').scrolls[0].behavior,'instant');
render({status:'running',session_id:'mine'});
assert.equal($('liveViewer').scrolls.length,1);
render({status:'stopped',session_id:'mine'});
assert.equal($('viewerCue').hidden,true);
assert.equal($('liveViewer').classes.has('yourRunLive'),false);
render({status:'idle',public_run:{status:'running'}});
assert.equal($('viewerCue').hidden,true);
for(let i=0;i<3;i++) {
  render({status:'idle'});
  assert.equal($('liveConversationPanel').classes.has('yourTaskReview'),false,'idle polling must not alternate the review highlight');
}

// ASPIRE highlights review immediately; acceptance/queueing alone is not active execution.
render({status:'preparing',aspire:true});
assert.equal($('viewerCue').hidden,false);
assert.equal($('viewerCueTitle').textContent,'Reviewing your prompt…');
assert.equal($('viewerCueDetail').textContent,'Working through your prompt and preparing the next steps.');
assert.equal($('queuePlace').hidden,true);
assert.equal($('position').textContent,'—');
assert.equal($('yourQueue').classes.has('yourTurnWaiting'),false);
assert.equal($('liveConversationPanel').classes.has('yourTaskReview'),true);
assert.equal($('liveConversationPanel').focusCount,1);
assert.equal($('liveViewer').classes.has('yourRunLive'),false);
render({status:'queued',aspire:true,session_id:'aspire-session',queue_position:2});
assert.equal($('viewerCueTitle').textContent,'Waiting in the robot queue');
assert.equal($('position').textContent,'#2');
assert.equal($('yourQueue').classes.has('yourTurnWaiting'),true);
assert.equal($('liveConversationPanel').classes.has('yourTaskReview'),false);
assert.equal($('liveViewer').classes.has('yourRunLive'),false);
render({status:'preparing',session_id:'aspire-session',provider:{task_progress:{task:'Fixture'}}});
assert.equal($('viewerCueTitle').textContent,'Your run is preparing');
assert.equal($('yourQueue').classes.has('yourTurnWaiting'),false);
assert.equal($('queuePlace').hidden,true);
assert.equal($('liveViewer').classes.has('yourRunLive'),false);
render({status:'running',session_id:'aspire-session',provider:{task_progress:{task:'Fixture'}}});
assert.equal($('viewerCueTitle').textContent,'Your run is active');
assert.equal($('liveViewer').classes.has('yourRunLive'),true);
assert.equal($('liveConversationPanel').classes.has('yourTaskReview'),false);
const scrolls=$('liveViewer').scrolls.length;
render({status:'running',session_id:'aspire-session',provider:{task_progress:{task:'Fixture'}}});
assert.equal($('liveViewer').scrolls.length,scrolls);
console.log('Queue handoff checks passed');

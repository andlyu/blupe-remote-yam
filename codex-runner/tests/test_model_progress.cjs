const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.YAM_TEST_HOSTED_JS || require('node:path').join(__dirname, '../static/hosted.js'), 'utf8');
const helper = source.slice(source.indexOf('  function modelRequestProgress('), source.indexOf('  function renderAstraStream('));
const context = {}; vm.createContext(context); vm.runInContext(helper, context);
const progress = (events, status='running') => context.modelRequestProgress({status,events}, 'Astra', 120000);
test('first request shows measured elapsed time, not invented reasoning', () => {
 const text = progress([{kind:'model_request',timestamp:100}]);
 assert.match(text, /first decision · 20s/);
 assert.match(text, /Public summaries appear/);
});
test('response, error and ended run clear the pending request', () => {
 const request = {kind:'model_request',timestamp:100};
 for (const kind of ['model_response','model_error']) assert.equal(progress([request,{kind}]), '');
 assert.equal(progress([request], 'stopped'), '');
});
test('only the first call shows progress and missing timestamps are handled', () => {
 assert.equal(progress([{kind:'model_response'}, {kind:'model_request'}]), '');
 assert.doesNotMatch(progress([{kind:'model_request'}]), /NaN/);
});

test('live first-call summary is visible before a response and replaced by the final note', () => {
 const nodes = {};
 const ui = {liveModelName:'Astra', $:id => nodes[id] ||= {textContent:'',scrollTop:0}};
 vm.createContext(ui);
 const notes = source.slice(source.indexOf('  function astraStreamNote('), source.indexOf("  let liveModelName"));
 const render = source.slice(source.indexOf('  function renderAstraStream('), source.indexOf('  const ROBOT_STATUS'));
 vm.runInContext(notes + helper + render, ui);
 const events = [{kind:'model_request',timestamp:100}, {kind:'model_progress',message:'Checking gripper alignment.',progress_type:'summary'}];
 ui.renderAstraStream({status:'running',events});
 assert.match(nodes.astraStreamOutput.textContent, /^First-call summary: Checking gripper alignment/);
 events.push({kind:'model_response',message:'move_to: {"note":"Moving above the block."}',timestamp:120});
 ui.renderAstraStream({status:'running',events});
 assert.equal(nodes.astraStreamOutput.textContent, 'Moving above the block.');
});


function reasoningFixture() {
 class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.textContent='';this.scrollHeight=0;this.scrollTop=0;this.clientHeight=100;this.dataset={};this.classList={add(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(){} addEventListener(){}
 }
 const nodes={liveConversationPanel:{dataset:{mode:'reasoning'}},sideConversationMessages:new Element('ol')};
 const ui=vm.createContext({document:{createElement:tag=>new Element(tag)},$:id=>nodes[id],liveModelName:'Astra',sideConversationEvents:[],conversationSnapshots:{}});
 const notes=source.slice(source.indexOf('  function astraStreamNote('),source.indexOf('  let liveModelName'));
 const render=source.slice(source.indexOf('  function renderConversation('),source.indexOf('  const sampleDialog'));
 vm.runInContext(notes+render,ui);
 const text=element=>[element.textContent,...element.children.map(text)].join(' ');
 return {ui,nodes,text:()=>text(nodes.sideConversationMessages)};
}

test('notes view renders streamed public summaries/status and plain ASPIRE response summaries', () => {
 const f=reasoningFixture();
 f.ui.renderConversation([
  {kind:'model_progress',timestamp:100,progress_type:'summary',message:'SENTINEL_STREAM_SUMMARY'},
  {kind:'model_progress',timestamp:101,details:{progress_type:'status'},message:'SENTINEL_STATUS'},
  {kind:'model_progress',timestamp:102,progress_type:'raw_reasoning',message:'PRIVATE_SENTINEL'},
  {kind:'model_response',timestamp:103,message:'SENTINEL_ASPIRE_RESPONSE'}
 ],'sideConversationMessages');
 for(const value of ['SENTINEL_STREAM_SUMMARY','SENTINEL_STATUS','SENTINEL_ASPIRE_RESPONSE','public summary']) assert.ok(f.text().includes(value));
 assert.doesNotMatch(f.text(),/PRIVATE_SENTINEL/);
});

test('actual public give_up format displays its stop reason in the notes view', () => {
 const f=reasoningFixture();
 f.ui.renderConversation([{kind:'model_response',speaker:'gpt-6-astra',timestamp:100,
  message:'give_up: {"reason":"A camera-to-arm coordinate mapping or measured object coordinates are needed to command a reliable grasp and placement without guessing critical geometry.","hindsight":"Provide calibration."}'}],'sideConversationMessages');
 assert.match(f.text(),/Run stopped: A camera-to-arm coordinate mapping/);
 assert.doesNotMatch(f.text(),/response notes will appear/);
});

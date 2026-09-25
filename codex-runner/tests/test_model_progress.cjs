const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/hosted.js'), 'utf8');
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

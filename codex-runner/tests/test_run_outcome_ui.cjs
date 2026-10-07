const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const helpers = source.slice(source.indexOf('  function modelResponseTools('), source.indexOf('  let liveModelName'));
const summary = 'The requested end state is already satisfied: a block is on the green towel.';
const response = (name, args) => ({kind:'model_response', message:`${name}: ${JSON.stringify(args)}`});
const completed = () => ({status:'stopped', model_name:'Astra',
  events:[{kind:'model_request'}, response('done', {summary, hindsight:'none'})],
  run_metrics:{model_calls:1, accepted_packets:0}});
function fixture() {
  const nodes = {};
  const context = vm.createContext({liveModelName:'Astra',
    $:id => nodes[id] ||= {hidden:true, textContent:'', dataset:{}}});
  vm.runInContext(helpers, context);
  return {nodes, context};
}

test('already-satisfied run is visible to spectators with its explanation and no-movement fact', () => {
  const {nodes, context} = fixture();
  const result = context.renderModelRunResult(completed());
  assert.equal(result.label, 'Completed');
  assert.equal(nodes.runOutcome.hidden, false);
  assert.equal(nodes.runOutcome.dataset.tone, 'success');
  assert.equal(nodes.runOutcomeSummary.textContent, summary);
  assert.equal(nodes.runOutcomeAttribution.textContent, 'Astra reported task complete');
  assert.equal(nodes.runOutcomeDetail.textContent, '1 model call · No task movements sent');
  assert.equal(context.astraStreamNote(completed().events[1], true), 'Task complete: '+summary);
});

test('give-up reasons remain visible without being presented as success', () => {
  const {nodes, context} = fixture();
  const run = {...completed(), events:[response('give_up', {reason:'The target is outside the workspace.'})]};
  context.renderModelRunResult(run);
  assert.equal(nodes.runOutcomeTitle.textContent, 'Could not complete');
  assert.equal(nodes.runOutcome.dataset.tone, 'warning');
  assert.equal(nodes.runOutcomeSummary.textContent, 'The target is outside the workspace.');
  assert.equal(context.astraStreamNote(run.events[0], true), 'Could not complete: The target is outside the workspace.');
});

test('authored code policy exposes progress and detailed pre-motion camera failure', () => {
  const {nodes, context} = fixture();
  const note = 'Observing the scene from fresh RGB-D cameras';
  const progress = {kind:'model_response', details:{response:JSON.stringify({note})}};
  assert.equal(context.astraStreamNote(progress, true), note);
  const reason = 'NoDepthImage: top RGB-D frame age 5.10s exceeds the 5s freshness limit';
  const event = {kind:'model_response', details:{tools:[{name:'give_up',arguments:{reason}}],
    response:'give_up: '+JSON.stringify({reason})}};
  context.renderModelRunResult({status:'stopped',model_name:'Code policy',events:[progress,event],
    run_metrics:{model_calls:0,accepted_packets:0}});
  assert.equal(nodes.runOutcomeTitle.textContent, 'Could not complete');
  assert.equal(nodes.runOutcomeSummary.textContent, reason);
  assert.equal(nodes.runOutcomeDetail.textContent, 'No task movements sent');
  assert.equal(context.astraStreamNote(event, true), 'Could not complete: '+reason);
});

test('next run, robot switch and errors clear a previous completion card', () => {
  const {nodes, context} = fixture();
  for (const run of [null, {...completed(), status:'running'}, {...completed(), status:'timed_out'},
      {...completed(), error:'Robot safety check failed; motion was stopped.'}]) {
    context.renderModelRunResult(completed());
    context.renderModelRunResult(run);
    assert.equal(nodes.runOutcome.hidden, true);
    assert.equal(nodes.runOutcomeSummary.textContent, '');
  }
});

test('manual stop and malformed or stale decisions never imply completion', () => {
  const {context} = fixture();
  for (const events of [[], [response('move_to', {note:'Almost done: working.'})],
      [{kind:'model_response', message:'done: {"summary":'}],
      [response('done', {summary}), {kind:'model_request'}],
      [response('done', {summary}), {kind:'model_error'}],
      [response('done', {summary:[]})]]) {
    assert.equal(context.modelRunResult({...completed(), events}), null);
  }
});

test('local structured tools preserve escaped text and missing metrics do not imply no movement', () => {
  const {nodes, context} = fixture();
  const text = 'Placed the "green" block.\n<img src=x onerror=alert(1)>';
  const event = {kind:'model_response', details:{tools:[{name:'done', arguments:JSON.stringify({summary:text})}]}};
  context.renderModelRunResult({status:'stopped', model_name:'Opus', events:[event]});
  assert.equal(nodes.runOutcomeSummary.textContent, text);
  assert.equal(nodes.runOutcomeDetail.hidden, true);
  assert.equal(nodes.runOutcomeAttribution.textContent, 'Opus reported task complete');
  assert.equal(context.astraStreamNote(event, true), 'Task complete: '+text);
});

test('result card is outside the hideable conversation panel and accessible', () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/hosted.html'), 'utf8');
  assert.ok(html.indexOf('id="runOutcome"') > html.indexOf('class="watchVisuals"'));
  assert.match(html, /id="runOutcome"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"/);
});

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
 assert.match(text, /intermediate reasoning is not available/);
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

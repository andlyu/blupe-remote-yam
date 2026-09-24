const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/hosted.js'),'utf8');
const body = source.split('  function runWithProvider(value, label) {')[1].split("  $('runGroot').onclick")[0];
function fixture(name,active=false){
  let submitted=0, focused=0, providerChanges=0, selected=null;
  const nodes={provider:{value:'openai'},model:{value:''},runnerName:{value:name,focus(){focused++}},runSettings:{open:false},runForm:{requestSubmit(){submitted++}}};
  const run=vm.runInNewContext('(function(value,label){'+body+')',{$:id=>nodes[id],active,submitting:false,ended:false,providerChanged(){providerChanges++},applyModelName(){},lastLive:null,updateRunLabel(){},message(){},window:{yamAnalytics:{selected(provider){selected=provider}}}});
  return {run,nodes,result:()=>({submitted,focused,providerChanges,selected})};
}
test('each model button uses the shared form submission, never a separate arm launch',()=>{
  for (const [value,label] of [['groot','Run GR00T'],['codex','Run with Astra'],['claude','Run with Opus']]) {
    const f=fixture('Operator');f.run(value,label);
    assert.equal(f.nodes.provider.value,value);
    assert.equal(f.result().submitted,1);
    assert.equal(f.result().providerChanges,1);
    assert.equal(f.result().selected,value);
  }
});
test('missing name opens setup; an active run cannot launch another',()=>{
  const f=fixture('');f.run('groot','Run GR00T');assert.equal(f.nodes.runSettings.open,true);assert.equal(f.result().submitted,0);assert.equal(f.result().focused,1);
  const busy=fixture('Operator',true);busy.run('claude','Run with Opus');assert.equal(busy.result().submitted,0);assert.equal(busy.nodes.provider.value,'openai');
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/hosted.js'),'utf8');
const block = source.slice(source.indexOf('      if (session.joint_policy) {'),source.indexOf("      $('runGroot').hidden = !session.groot_enabled;"));
for (const local of [false,true]) test(`MakerMods Opus selection (${local?'subscription':'API'})`, () => {
  const selected = local ? 'claude' : 'anthropic';
  const provider = {value:selected,options:['openai','anthropic','astra','claude','codex'].map(value=>({value})),
    replaceChildren(...options){this.options=options;this.value=options[0].value;}};
  const nodes = {provider,runClaude:{hidden:true},runAstra:{hidden:false}};
  vm.runInNewContext(block, {session:{joint_policy:true,claude_supported:true,local_runner:local,
    claude:local?{}:null,codex:local?{}:null},$:id=>nodes[id],providerChanged(){}});
  assert.equal(provider.value,selected);
  assert.deepEqual(provider.options.map(x=>x.value),local?['openai','anthropic','claude','codex']:['openai','anthropic']);
  assert.equal(nodes.runClaude.hidden,false);
});

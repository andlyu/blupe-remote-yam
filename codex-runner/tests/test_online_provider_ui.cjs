const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const body=source.split('  function providerChanged() {')[1].split("  $('provider').addEventListener('change'")[0];
test('online choice changes key issuer and model; no-AI removes key requirement',()=>{
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,close(){}});return nodes.get(id)};
 const change=vm.runInNewContext('(function(){'+body+')',{$,setupProvider:null,window:{},updateRunLabel(){},updateSavedKey(){},claudeModel:'claude-opus-5-5'});
 $('provider').value='anthropic';change();
 assert.equal($('apiKeyLabel').textContent,'Claude API key');
 assert.equal($('apiKeyCreditProvider').textContent,'Anthropic');
 assert.equal($('model').value,'claude-opus-5-5');assert.equal($('providerFields').hidden,false);
 assert.equal($('apiKey').required,true);assert.match($('apiKeyCreate').href,/platform.claude.com/);
 $('provider').value='local_raise_lower';change();
 assert.equal($('providerFields').hidden,true);assert.equal($('apiKey').required,false);assert.equal($('prompt').readOnly,true);
 $('provider').value='openai';change();
 assert.equal($('model').value,'gpt-6-astra');assert.equal($('prompt').readOnly,false);
 assert.equal($('apiKeyCreditProvider').textContent,'OpenAI');
 assert.equal($('apiKeyLabel').textContent,'OpenAI API key');assert.match($('apiKeyCreate').href,/platform.openai.com/);
 for (const provider of ['codex','claude']) {
  $('provider').value=provider;change();
  assert.equal($('providerFields').hidden,true);
  assert.equal($('apiKey').required,false);
  assert.equal($(provider+'Setup').hidden,false);
 }
});

const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const body=source.split('  function updateSavedKey(providers) {')[1].split('  function providerChanged()')[0];
test('status polling never requires the hidden API key during Stripe checkout',()=>{
 const nodes={provider:{value:'openai'},providerFields:{hidden:true},apiKey:{}};
 const update=vm.runInNewContext('(function(providers){'+body+')',{$:id=>nodes[id],savedKeyProviders:[],updateRunLabel(){}});
 for(let poll=0;poll<4;poll++){update([]);assert.equal(nodes.apiKey.required,false)}
 nodes.providerFields.hidden=false;update([]);assert.equal(nodes.apiKey.required,true);
 update(['openai']);assert.equal(nodes.apiKey.required,false);
 nodes.provider.value='anthropic';update([]);assert.equal(nodes.apiKey.required,true);
 nodes.provider.value='codex';nodes.providerFields.hidden=true;update([]);assert.equal(nodes.apiKey.required,false);
});

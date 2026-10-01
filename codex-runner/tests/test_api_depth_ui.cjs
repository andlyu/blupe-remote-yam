const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../static/hosted.html'),'utf8');
const body=source.split('  function providerChanged() {')[1].split("  $('provider').addEventListener('change'")[0];
test('API depth starts unchecked and is exposed only for configured Astra',()=>{
 const checkbox=html.match(/<input\b[^>]*id="useApiDepth"[^>]*>/)[0];
 assert.doesNotMatch(checkbox,/\bchecked\b/);
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,dataset:{},close(){}});return nodes.get(id)};
 const change=vm.runInNewContext('(function(){'+body+')',{$,setupProvider:null,window:{},updateRunLabel(){},updateSavedKey(){},claudeModel:'claude-opus-5-5'});
 $('provider').value='codex';change();assert.equal($('apiDepthSettings').hidden,true);
 $('useApiDepth').dataset.available='true';change();assert.equal($('apiDepthSettings').hidden,false);
 for(const provider of ['claude','openai','anthropic','local_raise_lower']){
  $('provider').value=provider;change();assert.equal($('apiDepthSettings').hidden,true);
 }
});

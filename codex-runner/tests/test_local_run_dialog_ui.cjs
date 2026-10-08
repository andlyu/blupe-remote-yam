const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const body=source.slice(source.indexOf('  function configureLocalRunDialog('),source.indexOf('  function updateRunLabel()'));
test('local and online setup use the same dialog without submitting and retain runner attribution',()=>{
 const nodes={}; const $=id=>nodes[id]||=( {dataset:{},classList:{toggle(){},add(){}},open:false,hidden:false,
 append(node){node.parent=this},before(node){node.parent='form'},showModal(){this.open=true},close(){this.open=false},focus(){this.focused=true}} );
 const context=vm.createContext({$,active:false,submitting:false,ended:false,csrf:'valid',updateRunLabel(){}});
 vm.runInContext(body,context);
 vm.runInContext('configureLocalRunDialog(true)',context);
 assert.equal($('runSettings').parent,$('localRunDialogBody'));
 assert.equal($('runnerIdentity').hidden,false);
 assert.equal($('openLocalRun').hidden,false);
 $('openLocalRun').onclick();
 assert.equal($('localRunDialog').open,true);
 assert.equal($('provider').focused,true);
 $('closeLocalRun').onclick();assert.equal($('localRunDialog').open,false);
 context.active=true;$('openLocalRun').onclick();assert.equal($('localRunDialog').open,false);
 vm.runInContext('configureLocalRunDialog(false)',context);
 assert.equal($('runSettings').parent,$('localRunDialogBody'));assert.equal($('runnerIdentity').hidden,false);
 assert.equal($('openLocalRun').hidden,false);
 context.active=false;$('openLocalRun').onclick();assert.equal($('localRunDialog').open,true);
 const html=fs.readFileSync(path.join(__dirname,'../static/hosted.html'),'utf8');
 assert(html.indexOf('id="provider"')<html.indexOf('id="email"'));
 assert(html.indexOf('id="email"')<html.indexOf('id="setupRunActions"'));
});

test('ASPIRE README guidance appears only in the selected ASPIRE run dialog, without submitting',()=>{
 const nodes={},handlers={};
 const $=id=>nodes[id]||={value:'',dataset:{},classList:{toggle(){},add(){}},open:false,
   append(node){node.parent=this},showModal(){this.open=true},close(){this.open=false},focus(){},
   addEventListener(type,handler){handlers[id+type]=handler}};
 const context=vm.createContext({$,active:false,submitting:false,ended:false,csrf:'valid',updateRunLabel(){},
   providerChanged(){},applyModelName(){},lastLive:null,window:{}});
 vm.runInContext(body,context);
 const change=source.split("  $('provider').addEventListener('change', () => { providerChanged();")[1].split('\n')[0];
 vm.runInContext("$('provider').addEventListener('change', () => { providerChanged();"+change,context);
 vm.runInContext('configureLocalRunDialog(false)',context);
 for(const provider of ['openai','anthropic','codex','claude','guest_astra']) {
   $('provider').value=provider;$('openLocalRun').onclick();
   assert.equal($('localRunDialog').open,true);assert.equal($('aspireSetupGuidance').hidden,true,provider);
   $('closeLocalRun').onclick();
 }
 for(const provider of ['hosted_aspire','aspire']) {
   $('provider').value=provider;$('openLocalRun').onclick();
   assert.equal($('localRunDialog').open,true);assert.equal($('aspireSetupGuidance').hidden,false,provider);
   $('provider').value='openai';handlers.providerchange();assert.equal($('aspireSetupGuidance').hidden,true);
   $('provider').value=provider;handlers.providerchange();assert.equal($('aspireSetupGuidance').hidden,false);
   $('closeLocalRun').onclick();
 }
 context.active=true;$('openLocalRun').onclick();assert.equal($('localRunDialog').open,false);
 const html=fs.readFileSync(path.join(__dirname,'../static/hosted.html'),'utf8');
 assert.match(html,/id="aspireSetupGuidance"[^>]*hidden>[^<]*Codex, follow the <a href="https:\/\/github.com\/andlyu\/blupe-remote-yam\/blob\/main\/codex-runner\/docs\/aspire\/README.md"/);
 const generic=html.split('<dialog id="codexInstructions"')[1].split('</dialog>')[0];
 assert.doesNotMatch(generic,/ASPIRE/);
});

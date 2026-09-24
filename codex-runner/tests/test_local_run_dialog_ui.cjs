const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const body=source.slice(source.indexOf('  function configureLocalRunDialog('),source.indexOf('  function updateRunLabel()'));
test('local setup opens model and email in a dialog without submitting; hosted restores inline setup',()=>{
 const nodes={}; const $=id=>nodes[id]||=( {dataset:{},classList:{toggle(){}},open:false,hidden:false,
 append(node){node.parent=this},before(node){node.parent='form'},showModal(){this.open=true},close(){this.open=false},focus(){this.focused=true}} );
 const context=vm.createContext({$,active:false,submitting:false,ended:false,csrf:'valid',updateRunLabel(){}});
 vm.runInContext(body,context);
 vm.runInContext('configureLocalRunDialog(true)',context);
 assert.equal($('runSettings').parent,$('localRunDialogBody'));
 assert.equal($('runnerIdentity').hidden,true);
 assert.equal($('openLocalRun').hidden,false);
 $('openLocalRun').onclick();
 assert.equal($('localRunDialog').open,true);
 assert.equal($('provider').focused,true);
 $('closeLocalRun').onclick();assert.equal($('localRunDialog').open,false);
 context.active=true;$('openLocalRun').onclick();assert.equal($('localRunDialog').open,false);
 vm.runInContext('configureLocalRunDialog(false)',context);
 assert.equal($('runSettings').parent,'form');assert.equal($('runnerIdentity').hidden,false);
 assert.equal($('openLocalRun').hidden,true);
 const html=fs.readFileSync(path.join(__dirname,'../static/hosted.html'),'utf8');
 assert(html.indexOf('id="provider"')<html.indexOf('id="email"'));
 assert(html.indexOf('id="email"')<html.indexOf('id="setupRunActions"'));
});

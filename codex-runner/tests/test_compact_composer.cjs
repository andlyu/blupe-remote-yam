const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.env.YAM_TEST_HOSTED_JS || __dirname+'/../static/hosted.js','utf8');
const start=source.indexOf("  send.addEventListener('click', () => {");
const handler=source.slice(start,source.indexOf('  const prompt =',start));
function harness({active=false,valid=true,queued=false}={}) {
  const clicks=[],nodes={};let click;
  for(const id of ['run','stop','leaveQueue','openLocalRun'])nodes[id]={click:()=>clicks.push(id)};
  nodes.leaveQueue.hidden=!queued;nodes.leaveQueue.disabled=!queued;
  nodes.runForm={classList:{contains:()=>active},checkValidity:()=>valid};
  vm.runInNewContext(handler,{openComposerSettings:()=>clicks.push('openLocalRun'),$:id=>nodes[id],send:{addEventListener:(_,fn)=>{click=fn;}}});
  click();return clicks;
}
test('missing required guest or connection fields reveal setup before submission',()=>{
  assert.deepEqual(harness({valid:false}),['openLocalRun']);
});
test('valid task delegates to the existing run handler',()=>{
  assert.deepEqual(harness(),['run']);
});
test('active task and queue preserve their respective stop handlers',()=>{
  assert.deepEqual(harness({active:true}),['stop']);
  assert.deepEqual(harness({active:true,queued:true}),['leaveQueue']);
});

const setupStart=source.indexOf('  function openComposerSettings()');
const setup=source.slice(setupStart,source.indexOf("  $('composerSettings').addEventListener",setupStart));
for(const mode of ['missing','present','disabled'])test('settings dialog with '+mode+' hosted launch handler',()=>{
  let existing=0,opened=0,focused=0;
  const settings={parentElement:null},body={append(node){node.parentElement=this;}};
  const dialog={open:false,showModal(){this.open=true;opened++;}};
  const launch={disabled:mode==='disabled',click(){existing++;if(mode==='present')dialog.open=true;}};
  const nodes={openLocalRun:launch,localRunDialog:dialog,runSettings:settings,localRunDialogBody:body};
  const context={$:id=>nodes[id],provider:{focus(){focused++;}}};
  vm.runInNewContext(setup+'\nopenComposerSettings();',context);
  assert.equal(existing,mode==='disabled'?0:1);
  assert.equal(dialog.open,mode!=='disabled');
  assert.equal(opened,mode==='missing'?1:0);
  if(mode==='missing'){assert.equal(settings.parentElement,body);assert.equal(settings.open,true);assert.equal(focused,1);}
});

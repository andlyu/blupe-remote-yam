const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../static/hosted.js','utf8');
const start=source.indexOf("(() => {\n  const panel = document.getElementById('visitorChatPanel');");
const popup=source.slice(start,source.indexOf('\n})();',start)+6);
function harness() {
  const callbacks={},expanded={},focus=[];let storageReads=0;
  const main={classList:{toggle(){}}};
  const panel={hidden:false,closest:()=>main};
  const button=id=>({hidden:false,setAttribute(name,value){expanded[id]=value;},focus(){focus.push(id);},addEventListener(_,callback){callbacks[id]=callback;}});
  const nodes={visitorChatPanel:panel,openVisitorChat:button('open'),closeVisitorChat:button('close')};
  vm.runInNewContext(popup,{document:{getElementById:id=>nodes[id],addEventListener(_,callback){callbacks.escape=callback;}},localStorage:{getItem(){storageReads++;return 'true';},setItem(){}}});
  return {panel,expanded,focus,callbacks,reads:()=>storageReads};
}
test('an older saved open drawer cannot cover the playground on a fresh load',()=>{
  const h=harness();assert.equal(h.panel.hidden,true);assert.equal(h.expanded.open,'false');assert.equal(h.reads(),0);assert.deepEqual(h.focus,[]);
});
test('visitors open explicitly and close from the button or Escape with focus restored',()=>{
  const h=harness();h.callbacks.open();assert.equal(h.panel.hidden,false);assert.equal(h.expanded.open,'true');
  h.callbacks.close();assert.equal(h.panel.hidden,true);
  h.callbacks.open();h.callbacks.escape({key:'Escape'});assert.equal(h.panel.hidden,true);assert.equal(h.expanded.open,'false');assert.equal(h.focus.at(-1),'open');
});

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'),'utf8');
const start = source.indexOf('  let attentionSession = null;');
const end = source.indexOf('  function render(state)', start);
const elements = {};
const $ = id => elements[id] ||= {hidden:false,textContent:'',classes:new Set(),focusCount:0,scrolls:[],
  classList:{toggle(name,on){on ? $(id).classes.add(name) : $(id).classes.delete(name);}},
  closest(selector){assert.equal(selector,'.queuePlace');return $('queuePlace');},
  focus(){this.focusCount++;},scrollIntoView(options){this.scrolls.push(options);}};
const context = vm.createContext({$,window:{matchMedia:()=>({matches:true})}});
vm.runInContext(source.slice(start,end),context);
const render = state => context.guideRunAttention(state);
render({status:'queued',session_id:'mine',queue_position:3});
assert.equal($('position').textContent,'#3');
assert.equal($('yourQueue').scrolls.length,1);
render({status:'queued',session_id:'mine',queue_position:2});
assert.equal($('position').textContent,'#2');
assert.equal($('yourQueue').scrolls.length,1);
render({status:'preparing',session_id:'mine'});
assert.equal($('position').textContent,'Up next');
assert.equal($('viewerCue').hidden,true);
render({status:'running',session_id:'mine'});
assert.equal($('viewerCue').hidden,false);
assert.equal($('liveViewer').scrolls.length,1);
assert.equal($('liveViewer').scrolls[0].behavior,'instant');
render({status:'running',session_id:'mine'});
assert.equal($('liveViewer').scrolls.length,1);
render({status:'stopped',session_id:'mine'});
assert.equal($('viewerCue').hidden,true);
assert.equal($('liveViewer').classes.has('yourRunLive'),false);
render({status:'idle',public_run:{status:'running'}});
assert.equal($('viewerCue').hidden,true);
console.log('Queue handoff checks passed');

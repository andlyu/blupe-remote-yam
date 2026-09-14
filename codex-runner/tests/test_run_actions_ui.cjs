const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const updateSource = source.split('  function updateRunLabel() {')[1].split("  $('run').addEventListener")[0];
test('expanding and collapsing setup preserves action nodes and reading order', () => {
  const run = {}, codex = {};
  const group = {children:[run,codex],get firstElementChild(){return this.children[0]},prepend(node){this.children=this.children.filter(n=>n!==node);this.children.unshift(node)}};
  const parent = () => ({append(node){node.parentElement=this}});
  const prompt = parent(), footer = parent(); group.parentElement = prompt;
  const nodes = {run,openCodexInstructions:codex,runButtons:group,runSettings:{open:false},setupRunActions:footer,provider:{value:'openai'},runForm:{classList:{toggle(){}}}};
  const update = vm.runInNewContext('(function(){'+updateSource+')', {$:id=>nodes[id],document:{querySelector:()=>prompt},runSetupNeeded:()=>true,active:false,submitting:false});
  update(); assert.equal(group.parentElement,prompt); assert.deepEqual(group.children,[run,codex]);
  nodes.runSettings.open=true; update(); assert.equal(group.parentElement,footer); assert.deepEqual(group.children,[codex,run]); assert.equal(run.textContent,'Run');
  nodes.runSettings.open=false; update(); assert.equal(group.parentElement,prompt); assert.deepEqual(group.children,[run,codex]); assert.equal(run.textContent,'Setup keys and run');
});

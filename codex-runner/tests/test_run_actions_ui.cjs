const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const updateSource = source.split('  function updateRunLabel() {')[1].split("  $('run').addEventListener")[0];
test('expanding and collapsing setup preserves action nodes and reading order', () => {
  const run = {}, codex = {}, astra = {}, claude = {}, groot = {};
  const group = {children:[run,codex,astra,claude,groot],get firstElementChild(){return this.children[0]},prepend(node){this.children=this.children.filter(n=>n!==node);this.children.unshift(node)}};
  for (const anchor of [run,codex]) anchor.after = (...added) => { group.children=group.children.filter(n=>!added.includes(n)); group.children.splice(group.children.indexOf(anchor)+1,0,...added); };
  const parent = () => ({append(node){node.parentElement=this}});
  const prompt = parent(), footer = parent(); group.parentElement = prompt;
  const nodes = {run,runGroot:groot,runAstra:astra,runClaude:claude,openCodexInstructions:codex,runButtons:group,runSettings:{open:false},setupRunActions:footer,provider:{value:'openai'},runForm:{classList:{toggle(){}}}};
  const update = vm.runInNewContext('(function(){'+updateSource+')', {$:id=>nodes[id],window:{},document:{querySelector:()=>prompt},runSetupNeeded:()=>true,active:false,submitting:false});
  update(); assert.equal(group.parentElement,prompt); assert.deepEqual(group.children,[run,codex,astra,claude,groot]);
  nodes.runSettings.open=true; update(); assert.equal(group.parentElement,footer); assert.deepEqual(group.children,[run,astra,claude,groot,codex]); assert.equal(run.textContent,'Run');
  nodes.runSettings.open=false; update(); assert.equal(group.parentElement,prompt); assert.deepEqual(group.children,[run,codex,astra,claude,groot]); assert.equal(run.textContent,'Setup LLM Keys and run');
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
const body = source.split('  async function copyCodexPrompt() {')[1].split("  $('copyCodexPrompt').onclick")[0];
function fixture(writeText) {
  const prompt = {textContent:' Clone https://github.com/andlyu/blupe-remote-yam.git, and run locally using the Codex subscription. '};
  const nodes = {copyCodexPrompt:{},codexCopyStatus:{}};
  let selected;
  const copy = vm.runInNewContext('(async function(){'+body+')', {$:id=>nodes[id],navigator:{clipboard:{writeText}},document:{querySelector:()=>prompt,createRange:()=>({selectNodeContents(node){selected=node}})},window:{getSelection:()=>({removeAllRanges(){},addRange(){}})}});
  return {copy,nodes,prompt,selection:()=>selected};
}
test('copies only the prompt and announces success', async () => {
  let copied;
  const f=fixture(async text=>{copied=text}); await f.copy();
  assert.equal(copied,f.prompt.textContent.trim());
  assert.equal(f.nodes.copyCodexPrompt.textContent,'Copied');
  assert.equal(f.nodes.codexCopyStatus.textContent,'Prompt copied.');
});
test('clipboard denial selects the prompt for manual copying without false success', async () => {
  const f=fixture(async()=>{throw new Error('denied')}); await f.copy();
  assert.equal(f.selection(),f.prompt);
  assert.match(f.nodes.codexCopyStatus.textContent,/copy it manually/);
  assert.notEqual(f.nodes.copyCodexPrompt.textContent,'Copied');
});

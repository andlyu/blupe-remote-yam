const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../static/hosted.html'),'utf8');
const js=fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const begin=js.indexOf('  let attentionSession =');const end=js.indexOf('  function renderAstraStream(',begin);
for(const [,id] of js.slice(begin,end).matchAll(/\$\('([^']+)'\)/g))assert.ok(html.includes('id="'+id+'"'),id+' missing from deployed HTML');

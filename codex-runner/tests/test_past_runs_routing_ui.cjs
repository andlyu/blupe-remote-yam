const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8').split('\n})();')[0]+'\n})();';
test('history waits for robot selection and ignores a previous robot response', async () => {
  const nodes = new Map(), listeners = {}, requests = [];
  const element = () => ({addEventListener(){},after(){},replaceChildren(){},removeAttribute(){},dataset:{},open:false});
  const document = {getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},createElement:element};
  vm.runInNewContext(source, {document,window:{addEventListener(name,fn){listeners[name]=fn}},encodeURIComponent,
    fetch(url,options){return new Promise(resolve=>requests.push({url,options,resolve}))}});
  assert.equal(requests.length,0);
  listeners['blupe-robot-selected']({detail:'robot-3652c537a175cbae'});
  assert.match(requests[0].url,/robot_id=robot-3652c537a175cbae/);
  assert.match(nodes.get('datasetViewer').src,/lerobot-visualize-dataset\.hf\.space\/andlyu\/Public-MakerMods-SO101-runs\/episode_0$/);
  assert.match(nodes.get('datasetLink').href,/visualize_dataset\?path=.*Public-MakerMods-SO101-runs/);
  assert.equal(requests[0].options.headers['X-Blupe-Robot'],'robot-3652c537a175cbae');
  listeners['blupe-robot-selected']({detail:'so101'});
  assert.match(requests[1].url,/offset=0&robot_id=so101/);
  requests[0].resolve({ok:true,json:async()=>({runs:[],next_offset:12})});
  await new Promise(setImmediate);
  assert.equal(nodes.get('pastRunsStatus').textContent,'Loading past runs…');
  assert.equal(nodes.get('refreshPastRuns').disabled,true);
  requests[1].resolve({ok:true,json:async()=>({runs:[],next_offset:null})});
  nodes.get('pastRunsList').children=[];
  await new Promise(setImmediate);
  assert.equal(nodes.get('pastRunsStatus').textContent,'No published runs yet.');
  assert.equal(nodes.get('refreshPastRuns').disabled,false);
});

 test('robot switching uses browser history even with a local history function', async () => {
  const full = fs.readFileSync(path.join(__dirname, '../static/hosted.js'), 'utf8');
  const handler = full.match(/selector\.onchange = async \(\) => \{([\s\S]*?)\n      \};/)[1];
  let started = 0, replaced = 0;
  const node = {value:'so101', textContent:'', replaceChildren(){}};
  const context = {makerModsPage:false, makerModsRobot:'robot-3652c537a175cbae', robotCatalog:{robots:[]}, submitting:false, selector:node, selectedRobot:'yam-1', robotGeneration:0,
    URL, location:{href:'http://localhost/?robot_id=yam-1'},
    history:async()=>{}, window:{history:{replaceState(a,b,url){replaced++;assert.equal(url.searchParams.get('robot_id'),'so101');}},dispatchEvent(){}},
    CustomEvent:class {}, $:()=>node, document:{querySelectorAll:()=>[]}, buttons(){}, renderRobotStatus(){},
    start:async()=>{started++;}, csrf:'',active:false,ended:false,lastChatSnapshot:''};
  await vm.runInNewContext('(async()=>{'+handler+'})()',context);
  assert.equal(replaced,1); assert.equal(started,1);
});

const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/../static/hosted.js','utf8');
test('online-first ordering preserves selection and restarts cameras only on connection change',async()=>{
 const options=[{value:'yam'},{value:'so101'},{value:'maker'},{value:''}];
 const selector={options,value:'yam',append(o){this.options=this.options.filter(x=>x!==o);this.options.push(o)}};
 let updates=[];
 const fn=source.slice(source.indexOf('  async function refreshRobotAvailability()'),source.indexOf('  setInterval(refreshRobotAvailability'));
 const ctx={api:async()=>({robots:[{id:'yam',connected:false},{id:'so101',connected:false},{id:'maker',connected:true}]}),$:()=>selector,selectedRobot:'yam',robotCatalog:null,updateCameraAvailability:v=>updates.push(v)};
 await vm.runInNewContext(fn+';refreshRobotAvailability()',ctx);
 assert.deepEqual(selector.options.map(o=>o.value),['maker','yam','so101','']);assert.equal(selector.value,'yam');assert.deepEqual(updates,[false]);
});
test('disconnect replaces camera tiles, keeps run controls, and does not start a stream',()=>{
 const a=source.indexOf('  function selectedCameras(names)'),b=source.indexOf('  async function refreshRobotAvailability',a);
 let stopped=0,removed=0,notice;
 const controls={replaceWith(n){assert.equal(n,controls)}};
 const viewer={innerHTML:'',dataset:{},querySelectorAll:()=>[{remove(){removed++}}],prepend(n){notice=n}};
 const ctx={$:id=>({liveViewer:viewer,liveRunControls:controls,videoDelayNotice:{}}[id]),document:{querySelectorAll:()=>[{yamStop(){stopped++}}],createElement:()=>({setAttribute(){},style:{}})},originalCameraMarkup:'initial',cameraNames:[],cameraEpoch:0,camerasDisconnected:true,selectedRobot:'yam-1'};
 vm.runInNewContext(source.slice(a,b)+";selectedCameras(['left','top','right']);",ctx);
 assert.equal(stopped,1);assert.equal(removed,1);assert.equal(notice.textContent,'Arms are disconnected');
});

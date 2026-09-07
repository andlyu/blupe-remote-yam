// Run with: node tests/test_local_replay.cjs (no browser or robot required).
const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs'), path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/interactions.js'),'utf8').split('\n(() => {')[0];
function environment({unsupported=false,broken=false,missing=false}={}) {
 let now=0, tracksStopped=0, capturedFps=null;
 class Recorder {
  static isTypeSupported(){return true;}
  constructor(){if(broken)throw new Error('encoder unavailable'); this.state='inactive';this.mimeType='video/webm';}
  start(){this.state='recording';}
  stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable({data:new Blob(['final encoded chunk'])});this.onstop();});}
 }
 const ctx={fillRect(){},fillText(){},drawImage(){}};
 const canvas={getContext:()=>ctx,captureStream:fps=>{capturedFps=fps;return {getTracks:()=>[{stop(){tracksStopped++;}}]};}};
 const document={hidden:false,createElement:()=>canvas,querySelector:()=>missing?null:{naturalWidth:640,naturalHeight:360,closest:()=>null}};
 const sandbox={module:{exports:{}},Blob,MediaRecorder:unsupported?undefined:Recorder,HTMLCanvasElement:{prototype:{captureStream(){}}},document,performance:{now:()=>now},setInterval:()=>1,clearInterval(){},queueMicrotask};
 vm.runInNewContext(source,sandbox);
 return {recorder:new sandbox.module.exports.YamLocalReplay(),document,tick(){now+=3000;},tracks:()=>tracksStopped,capturedFps:()=>capturedFps};
}
const state=(id,status)=>({status,interactions:{run_id:id}});
test('does not record queue; final chunk is playable input and stop is idempotent',async()=>{
 const {recorder,tick,tracks}=environment();
 recorder.update(state('a','queued'));assert.equal(recorder.active,null);
 recorder.update(state('a','running'));assert.equal(await recorder.get('a'),null);
 tick();recorder.update(state('a','stopped'));const pending=recorder.get('a');
 recorder.update(state('a','stopped'));const record=await pending;
 assert.equal(await record.blob.text(),'final encoded chunk');assert.equal(record.duration,3);assert.equal(record.partial,false);assert.equal(tracks(),1);
 recorder.update(state('a','running'));assert.equal(recorder.active,null);
});
test('old completion does not clobber new capture on run transition',async()=>{
 const {recorder}=environment();recorder.update(state('a','running'));recorder.update(state('b','running'));
 assert.equal((await recorder.get('a')).id,'a');assert.equal(recorder.active.id,'b');
 recorder.update(state('b','disconnected'));assert.equal((await recorder.get('b')).id,'b');
});
test('late entry or hidden-page capture is labeled partial',async()=>{
 const {recorder,document}=environment();recorder.update(state('a','running'));recorder.finish();assert.equal((await recorder.get('a')).partial,true);
 recorder.update(state('b','queued'));recorder.update(state('b','running'));document.hidden=true;recorder.draw(recorder.active);recorder.finish();assert.equal((await recorder.get('b')).partial,true);
});
test('unsupported encoder and missing cameras leave archive fallback available',async()=>{
 for(const options of [{unsupported:true},{broken:true},{missing:true}]){
  const {recorder}=environment(options);recorder.update(state('a','running'));recorder.finish();assert.equal(await recorder.get('a'),null);
 }
});
test('memory retains only three finalized replays',async()=>{
 const {recorder}=environment();for(let n=0;n<5;n++){recorder.update(state(String(n),'running'));recorder.finish();await recorder.get(String(n));}
 assert.equal(recorder.records.size,3);assert.equal(await recorder.get('0'),null);
});

test("local replay captures at a smooth 30 fps",async()=>{
 const {recorder,capturedFps}=environment();recorder.update(state("smooth","running"));
 assert.equal(capturedFps(),30);recorder.finish();await recorder.get("smooth");
});

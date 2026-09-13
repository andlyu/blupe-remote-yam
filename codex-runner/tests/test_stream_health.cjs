const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('path').join(__dirname,'../static/stream-health.js'),'utf8');
let now=0,callback,interval,reconnects=0; const visibility={}; const reports=[];
const video={dataset:{transport:'webrtc'},readyState:4,paused:false,currentTime:0,seekable:{length:0},
 requestVideoFrameCallback(fn){callback=fn;return 1},cancelVideoFrameCallback(){},addEventListener(){}};
const badge={dataset:{}},detail={};const document={hidden:false,addEventListener:(name,fn)=>visibility[name]=fn};
const context={window:{addEventListener(){}},document,performance:{now:()=>now},setInterval:fn=>{interval=fn;return 1},clearInterval(){},console:{info(){}}};
vm.createContext(context);vm.runInContext(source,context);
const monitor=new context.window.YamStreamHealth(video,{badge,detail,recover:()=>reconnects++,report:r=>reports.push(r),clock:()=>now});
badge.dataset ||= {}; // Real DOM elements always have dataset.
assert.equal(monitor.snapshot().state,'connecting');
now=8100; interval(); assert.equal(monitor.snapshot().state,'stalled');assert.equal(reconnects,1);
for(let i=0;i<20;i++){now+=100;callback(now,{captureTime:now-200});}
interval();assert.equal(monitor.snapshot().state,'live');assert.equal(monitor.snapshot().fps,10);
assert.equal(badge.textContent,'Live video');
now+=3100;interval();assert.equal(monitor.snapshot().state,'stalled');assert.equal(monitor.stale(),true);
// The video clock advancing alone cannot turn the health green.
video.currentTime+=10;interval();assert.equal(monitor.snapshot().state,'stalled');
now+=6000;interval();assert.equal(reconnects,2);
callback(now,{captureTime:now-4000});interval();assert.equal(monitor.snapshot().state,'behind');
video.dataset.transport='hls';video.seekable={length:1,end:()=>30};video.currentTime=22;
callback(now,{});interval();assert.equal(monitor.snapshot().state,'delayed');
video.currentTime=1;interval();assert.equal(monitor.snapshot().state,'behind');
document.hidden=true;visibility.visibilitychange();assert.equal(monitor.snapshot().state,'paused');
now+=100000;interval();assert.equal(reconnects,2);
document.hidden=false;visibility.visibilitychange();assert.equal(monitor.snapshot().state,'connecting');
video.dataset.transport='webrtc';video.seekable={length:0};now+=100;callback(now,{captureTime:now-100});interval();assert.equal(monitor.snapshot().state,'live');
assert(reports.some(r=>r.state==='stalled'));assert(reports.filter(r=>r.state==='live').length>=2);
monitor.close();console.log('Stream startup failure, actual frames, freeze, recovery, lag, HLS, hidden-tab checks passed');

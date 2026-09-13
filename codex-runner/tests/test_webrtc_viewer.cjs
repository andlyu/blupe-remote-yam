const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(require('node:path').join(__dirname,'../static/hosted.js'),'utf8');
const camera=src.slice(src.indexOf('  function camera(image)'),src.indexOf('  async function poll()',src.indexOf('  function camera(image)')));
function harness(role='observer', monitored=false){
 let health;
 let now=0, video, readerOptions, destroyed=0, closed=0;const intervals=[],timers=new Map(),visibility={};let id=0;
 const label={textContent:''};
 const context={$:()=>null,csrf:null,synchronizedPanels:()=>{},ended:false,performance:{now:()=>now},location:{href:'https://test.invalid/'},URL,
 setTimeout:f=>{timers.set(++id,f);return id},clearTimeout:i=>timers.delete(i),setInterval:f=>intervals.push(f),clearInterval:()=>{},
 fetch:async()=>{throw Error('WebRTC must not request HLS health')},AbortSignal,
 document:{hidden:false,addEventListener:(n,f)=>visibility[n]=f,createElement:()=>video={currentTime:0,readyState:0,paused:true,events:{},dataset:{},setAttribute(){},removeAttribute(){},load(){},pause(){this.paused=true},play(){this.paused=false;return Promise.resolve()},addEventListener(n,f){this.events[n]=f},getAttribute(){return null}}},
 window:{RTCPeerConnection:function(){},addEventListener(){},Hls:null},
 MediaMTXWebRTCReader:class{constructor(o){readerOptions=o}close(){closed++}},
 Hls:class{static isSupported(){return true}static Events={ERROR:'error',MANIFEST_PARSED:'manifest'};constructor(){this.events={}}on(n,f){this.events[n]=f}loadSource(u){this.url=u}attachMedia(v){v.hls=this}destroy(){destroyed++}}
 };context.window.Hls=context.Hls;context.window.MediaMTXWebRTCReader=context.MediaMTXWebRTCReader;
 if(monitored) context.window.YamStreamHealth=class {constructor(video, options){health=options;}};
 vm.createContext(context);vm.runInContext(camera+'\ncamera(image)',Object.assign(context,{image:{dataset:{camera:role},alt:role,parentElement:{querySelector:()=>label},replaceWith(){}}}));
 return {get health(){return health},context,video,label,intervals,timers,visibility,get reader(){return readerOptions},get closed(){return closed},setNow(t){now=t}};
}
(async()=>{
 const h=harness();assert.equal(h.video.dataset.transport,'webrtc');const old=h.reader;
 old.onTrack({streams:[{}]});h.video.currentTime=1;h.video.readyState=4;h.video.events.timeupdate();assert.equal(h.label.textContent,'Live');
 await h.intervals[0]();assert.equal(h.video.dataset.transport,'webrtc');
 h.setNow(9000);await h.intervals[0]();assert.equal(h.video.dataset.transport,'hls');assert.equal(h.closed,1);assert.equal(h.video.hls.url,'/live-video/hls/observer/index.m3u8');
 const before=h.video.srcObject;old.onTrack({streams:[{stale:true}]});assert.equal(h.video.srcObject,before);
 h.video.currentTime=0;h.video.paused=false;h.video.events.timeupdate();assert.notEqual(h.label.textContent,'Live');
 const e=harness();e.reader.onError('ICE failed');assert.equal(e.video.dataset.transport,'hls');
 const t=harness();[...t.timers.values()][0]();assert.equal(t.video.dataset.transport,'hls');
 const sync=harness('synchronized');assert.equal(sync.video.dataset.transport,'webrtc');assert.equal(sync.reader.url,'https://test.invalid/synchronized/whep');
 sync.reader.onError('ICE failed');assert.equal(sync.video.dataset.transport,'hls');assert.equal(sync.video.hls.url,'/synchronized/index.m3u8');
 await sync.intervals[0]();assert.equal(sync.video.dataset.transport,'hls'); // no independent-camera health request
 const monitored=harness('synchronized',true);
 monitored.setNow(8100);monitored.health.recover();
 assert.equal(monitored.video.dataset.transport,'hls');
 const firstHls=monitored.video.hls;
 monitored.setNow(18100);monitored.health.recover();
 await monitored.intervals[0]();
 assert.equal(monitored.video.hls,firstHls,'HLS startup must not be interrupted by either watchdog');
 monitored.setNow(29100);monitored.health.recover();
 const pending=[...monitored.timers.keys()];assert.equal(pending.length,1);
 monitored.health.recover();assert.deepEqual([...monitored.timers.keys()],pending,'Do not postpone a pending reconnect');
 const l=harness('left');assert.equal(l.video.dataset.transport,'hls');
 const v=harness();v.context.document.hidden=true;v.visibility.visibilitychange();assert.equal(v.closed,1);v.context.document.hidden=false;v.visibility.visibilitychange();assert.equal(v.video.dataset.transport,'webrtc');
 console.log('PASS: WebRTC playback, independent health, timeout/error/stall fallback, stale callbacks, zero-time disconnect, visibility reconnect, other-camera HLS.');
})().catch(e=>{console.error(e);process.exitCode=1});

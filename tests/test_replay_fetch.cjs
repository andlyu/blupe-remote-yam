const {test}=require('node:test');
const assert=require('node:assert/strict');
const {fetchReplayVideo}=require('../static/interactions.js');
test('reports actual received bytes and known total',async()=>{
 const old=global.fetch,progress=[];
 global.fetch=async()=>new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array([1,2]));c.enqueue(new Uint8Array([3,4]));c.close();}}),{headers:{'Content-Length':'4'}});
 try {const blob=await fetchReplayVideo('/video',{signal:new AbortController().signal,onProgress:(...p)=>progress.push(p)});assert.equal(blob.size,4);assert.deepEqual(progress,[[0,4],[2,4],[4,4]]);}finally{global.fetch=old;}
});
test('does not invent a percentage without a total',async()=>{
 const old=global.fetch,progress=[];global.fetch=async()=>new Response(new Uint8Array([1,2]));
 try{await fetchReplayVideo('/video',{signal:new AbortController().signal,onProgress:(...p)=>progress.push(p)});assert.deepEqual(progress,[[0,null],[2,null]]);}finally{global.fetch=old;}
});
test('rejects incomplete downloads and canceled playback',async()=>{
 const old=global.fetch;global.fetch=async()=>new Response(new Uint8Array([1]),{headers:{'Content-Length':'2'}});
 try {
  await assert.rejects(fetchReplayVideo('/video',{signal:new AbortController().signal,onProgress(){}}),/incomplete/);
  const controller=new AbortController();controller.abort();
  await assert.rejects(fetchReplayVideo('/video',{signal:controller.signal,onProgress(){}}),{name:'AbortError'});
 }finally{global.fetch=old;}
});

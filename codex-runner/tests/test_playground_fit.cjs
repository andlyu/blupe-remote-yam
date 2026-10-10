const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../static/hosted.js'),'utf8');
const fit = source.slice(source.indexOf('  function fitCameras()'),source.indexOf('  const sizing = new ResizeObserver(fitCameras)'));

function harness() {
  const values = new Map();
  const viewer={dataset:{layout:'observer-focus'}};
  const window={innerWidth:1440,innerHeight:650,scrollY:0};
  const workspace={clientWidth:1404,clientHeight:0,documentTop:200,
    getBoundingClientRect:()=>({top:workspace.documentTop-window.scrollY}),
    style:{setProperty:(name,value)=>values.set(name,value),removeProperty:name=>values.delete(name)},
  };
  Object.defineProperty(workspace.style,'height',{
    get:()=>values.get('height'),
    set:value=>{values.set('height',value);workspace.clientHeight=parseFloat(value);},
  });
  const context={window,workspace,visuals:{clientWidth:354},
    $:id=>id==='liveViewer' ? viewer : {offsetHeight:21},
    document:{querySelector:()=>({offsetHeight:24})},
  };
  vm.runInNewContext(fit+'\nthis.fit=fitCameras;',context);
  return {window,workspace,viewer,values,fit:context.fit};
}

test('scrolling below the console cannot inflate its height on a resize callback',()=>{
  const h=harness();h.fit();const height=h.values.get('height');
  assert.equal(height,'432px');
  for (const scroll of [500,2000,50000,0]) {
    h.window.scrollY=scroll;h.fit();
    assert.equal(h.values.get('height'),height);
  }
});

test('an asynchronously displayed banner reduces camera height and retains the queue',()=>{
  const h=harness();h.workspace.documentTop=68;h.fit();
  const initial=h.workspace.clientHeight;
  h.workspace.documentTop+=118;h.fit();
  assert.equal(h.workspace.clientHeight,initial-118);
  assert.equal(h.workspace.documentTop+h.workspace.clientHeight,632);
});

test('phone cameras remain above chat and discard desktop fixed sizing',()=>{
  const h=harness();h.fit();h.window.innerWidth=390;h.fit();
  assert.equal(h.values.has('height'),false);
  assert.equal(h.values.has('--console-camera-width'),false);
  assert.equal(h.viewer.dataset.detailLayout,'below');
  assert(parseFloat(h.values.get('--console-camera-height'))<300);
});

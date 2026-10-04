const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/../static/hosted.js', 'utf8');

test('RoboHouse selects its continuous stream with three correctly labeled camera tiles', () => {
  const tiles = Array.from({length: 4}, (_, i) => {
    const caption = {firstChild: {textContent: ''}}, button = {setAttribute(k,v){this[k]=v}};
    const figure = {dataset:{},removed:false, remove(){this.removed=true}, querySelector(s){return s==='figcaption' ? caption : button}};
    return {dataset:{syncTile:String(i)}, parentElement:{}, figure, caption, button,
      closest(){return figure}, setAttribute(k,v){this[k]=v}};
  });
  const image = {dataset:{camera:'synchronized'}}, controls = {replaceWith(){}};
  const viewer = {innerHTML:'',dataset:{},querySelectorAll(){return tiles}};
  const notice = {}, connected = [], badges = [];
  const context = {cameraNames:[],cameraEpoch:0,camerasDisconnected:false,originalCameraMarkup:'',
    selectedRobot:'robot-ba8413962083809c', videoStream:{path:'robo-house',cameras:['top','left','right']},
    $:id=>({liveViewer:viewer,liveRunControls:controls,videoDelayNotice:notice})[id],
    document:{querySelectorAll(){return [image]},createElement(){throw Error('Unexpected JPEG polling')}},
    addModelBadge:(_,role)=>badges.push(role),camera:e=>connected.push(e.dataset.streamPath)};
  const a=source.indexOf('  function selectedCameras(names)'), b=source.indexOf('  async function refreshRobotAvailability',a);
  vm.runInNewContext(source.slice(a,b)+";selectedCameras(['top','left','right'])",context);
  assert.deepEqual(connected,['robo-house']);
  assert.deepEqual(badges,['top','left','right']);
  assert.deepEqual(tiles.slice(0,3).map(t=>t.dataset.cameraRole),['top','left','right']);
  assert.equal(tiles[3].figure.removed,true);
  assert.equal(notice.hidden,false);
  assert.equal(tiles[1].button['aria-label'],'Expand left camera');
  assert.equal(viewer.dataset.layout,'top-with-grippers');
  assert.deepEqual(tiles.slice(0,3).map(t=>t.figure.dataset.cameraRole),['top','left','right']);
  assert.deepEqual(tiles.slice(0,3).map(t=>t.caption.firstChild.textContent),['Top ','Left wrist ','Right wrist ']);
  context.selectedRobot='yam-1';
  vm.runInNewContext(source.slice(a,b)+";selectedCameras(['top','left','right'])",context);
  assert.equal(viewer.dataset.layout,'multi');
});

test('RoboHouse YAM is the default viewer, while explicit robot choices retain priority', () => {
  const a=source.indexOf("      if (!window.yamApplication?.defaultRobot && !new URLSearchParams(location.search).get('robot_id'))");
  const b=source.indexOf('      selector.replaceChildren',a);
  assert(a>=0 && b>a);
  for (const [query,appDefault,expected] of [['',undefined,'robot-ba8413962083809c'],['?robot_id=yam-1',undefined,'yam-1'],['','yam-1','yam-1']]) {
    const ctx={window:{yamApplication:{defaultRobot:appDefault}},location:{search:query},URLSearchParams,
      selectedRobot:'yam-1',robotCatalog:{robots:[{id:'yam-1',connected:true},{id:'robot-ba8413962083809c',connected:true}]}};
    vm.runInNewContext(source.slice(a,b),ctx);assert.equal(ctx.selectedRobot,expected);
  }
});

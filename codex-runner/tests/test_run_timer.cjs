const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/hosted.js'), 'utf8');
const start = source.indexOf('  // Shared spectator stopwatch');
const end = source.indexOf('  let attentionSession = null;', start);
let now = 1000, tick;
const elements = {runTimer:{},ownRunTimer:{},runTimerDetail:{}};
const context = vm.createContext({$:id=>elements[id],performance:{now:()=>now},setInterval:fn=>{tick=fn;}});
vm.runInContext(source.split('(() => {')[0],context);
vm.runInContext(source.slice(start,end),context);
function render(run) { context.syncStopwatch(run); return elements.runTimer.textContent; }
assert.equal(render(null), '—:—');
assert.equal(render({status:'running',run_elapsed_s:0,run_duration_s:300}), '05:00');
now += 2100; tick();
assert.equal(elements.runTimer.textContent,'04:58');
assert.equal(elements.runTimerDetail.textContent,'04:58 remaining');
// Reopening the page or watching somebody else's run uses its server elapsed time.
context.state = {status:'queued',run_elapsed_s:999,public_run:{status:'running',run_elapsed_s:61,run_duration_s:600}};
const syncStart=source.indexOf('    const live = state.public_run;');
vm.runInContext("{"+source.slice(syncStart,source.indexOf("    $('currentRunner')",syncStart))+"}",context);
assert.equal(elements.runTimer.textContent,'08:59');
now += 6000; tick();
assert.equal(elements.runTimerDetail.textContent,'Reconnecting · timer paused');
const frozen=elements.runTimer.textContent; now+=10000;tick();assert.equal(elements.runTimer.textContent,frozen);
assert.equal(render({status:'stopped',run_elapsed_s:72,run_duration_s:600}), '08:48');
now += 5000;tick();assert.equal(elements.runTimer.textContent,'08:48');
assert.equal(elements.runTimerDetail.textContent,'Run ended');
assert.equal(render({status:'running',run_elapsed_s:0,run_duration_s:300}), '05:00');
assert.equal(render({status:'running',run_elapsed_s:602,run_duration_s:600}), '00:00');
assert.equal(elements.runTimerDetail.textContent,'00:00 remaining');
assert.equal(render({run_elapsed_s:null}), '—:—');
// Repeated server polls across the minute boundary update both countdowns.
for (let elapsed = 0; elapsed <= 3; elapsed++) {
  assert.equal(render({status:'running',run_elapsed_s:elapsed,run_duration_s:60}),
    ['01:00','00:59','00:58','00:57'][elapsed]);
  assert.equal(elements.ownRunTimer.textContent, elements.runTimer.textContent);
  now += 1000;
}
assert.equal(render({status:'stopped',run_elapsed_s:18,run_duration_s:60}), '00:42');
now += 10000; tick();
assert.equal(elements.ownRunTimer.textContent, '00:42');
console.log('Shared countdown checks passed');
// A local runner relayed through the API has timestamps, without run_elapsed_s.
context.state = {queue_snapshot:{generated_at:1061},public_run:{run_id:'local-episode',status:'running',run_started_at:1000,run_duration_s:300}};
vm.runInContext("{"+source.slice(syncStart,source.indexOf("    $('currentRunner')",syncStart))+"}",context);
assert.equal(elements.runTimer.textContent,'03:59');
now += 2000; tick();
assert.equal(elements.runTimer.textContent,'03:57');
// Refreshing/reopening gets the current server time, independent of browser time.
context.state.queue_snapshot.generated_at=1120;
vm.runInContext("{"+source.slice(syncStart,source.indexOf("    $('currentRunner')",syncStart))+"}",context);
assert.equal(elements.runTimer.textContent,'03:00');
// Older APIs omit the end timestamp: freeze at the last known server elapsed time.
assert.equal(render({...context.state.public_run,status:'stopped'}),'03:00');
now += 10000; tick(); assert.equal(elements.runTimer.textContent,'03:00');
assert.equal(render({...context.state.public_run,status:'stopped',run_ended_at:1130}),'02:50');
assert.equal(render({...context.state.public_run,run_id:'other',status:'stopped'}),'—:—');
assert.equal(render({...context.state.public_run,run_id:'other',run_started_at:null}),'—:—');

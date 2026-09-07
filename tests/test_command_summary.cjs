// Run with: node tests/test_command_summary.cjs
const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs'), path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/interactions.js'),'utf8').split('\n(() => {')[0];
const sandbox={module:{exports:{}},Blob,console}; vm.runInNewContext(source,sandbox);
const {summarizeModelCommand}=sandbox.module.exports;

test('summarizes a joint trajectory without dumping provider payload',()=>{
 const summary=summarizeModelCommand({type:'joint_trajectory',waypoint_count:58,cadence_hz:10,first_step_id:478,last_step_id:535,provider:{note:'large nested payload'}});
 assert.equal(summary,'Joint trajectory · 58 waypoints · 10 Hz · steps 478–535');
 assert.ok(!summary.includes('provider'));
});

test('summarizes compact arm commands and unknown commands',()=>{
 assert.equal(summarizeModelCommand({type:'move_to',left:{},right:{}}),'Move to · left + right');
 assert.equal(summarizeModelCommand({type:'stop'}),'Stop');
 assert.equal(summarizeModelCommand(null),'Waiting');
});

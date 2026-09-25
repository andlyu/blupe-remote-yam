const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/hosted.js'), 'utf8').split('(() => {')[0];
const ctx = {}; vm.createContext(ctx); vm.runInContext(source, ctx);
test('charts per-step seconds and centimetres, excludes missing data', () => {
  const target = {style:{}};
  ctx.renderStepMetricsChart(target, [{step:1,model_s:3,arm_motion_s:2,left_displacement_m:.05,right_displacement_m:null}, {step:2,model_s:1,arm_motion_s:4,left_displacement_m:0,right_displacement_m:.1}]);
  assert.match(target.innerHTML, /Step 1: Model thinking 3.00 s/);
  assert.match(target.innerHTML, /Step 1: Arm motion 2.00 s/);
  assert.match(target.innerHTML, /Step 1: Left 5.0 cm/);
  assert.match(target.innerHTML, /Step 2: Right 10.0 cm/);
  assert.doesNotMatch(target.innerHTML, /Step 1: Right|NaN|Infinity/);
});
test('empty and older runs do not invent metrics', () => {
  const target = {style:{}};
  ctx.renderStepMetricsChart(target, []);
  assert.match(target.textContent, /No step metrics/);
  ctx.renderStepMetricsChart(target, [{step:1}]);
  assert.doesNotMatch(target.innerHTML, /<rect|<circle/);
});
test('run totals show three metrics and disclose incomplete execution', () => {
  const target = {style:{}};
  ctx.renderRunMetrics(target, {model_s:12,execution_s:7,left_path_m:.3,right_path_m:.2,model_calls:2,execution_packets:1,accepted_packets:2,distance_waypoints:3,confirmed_waypoints:3}, 'stopped');
  assert.match(target.textContent, /summary \(partial\)/);
  assert.match(target.textContent, /50.0 cm/);
  assert.match(target.textContent, /Thinking: 12.00 s/);
  assert.match(target.textContent, /Execution: 7.00 s/);
});
test('comparison keeps run totals distinct from legacy per-step measurements', () => {
  const modern = ctx.runComparisonMetrics({run_metrics:{model_s:15,execution_s:8,left_path_m:.4,right_path_m:.1,model_calls:2,execution_packets:1,accepted_packets:1,distance_waypoints:5,confirmed_waypoints:5}});
  assert.equal(modern.distance,50); assert.equal(modern.thinking,15); assert.equal(modern.execution,8);
  assert.equal(modern.legacy,false); assert.equal(modern.partial,false);
  const legacy = ctx.runComparisonMetrics({step_timings:[{model_s:7,arm_motion_s:3,left_displacement_m:.1}]});
  assert.equal(legacy.distance,10); assert.equal(legacy.legacy,true); assert.equal(legacy.partial,true);
  assert.equal(ctx.runComparisonMetrics({}).distance,null);
});

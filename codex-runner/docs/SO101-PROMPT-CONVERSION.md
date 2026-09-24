# YAM → SO101 Cartesian policy

Current prompt: `YAM-PROMPT.txt` is an exact snapshot of `SYSTEM_PROMPT` in
`src/remote_yam/robocurve_contract.py`. The Python constant remains the runtime source.
The user task is sent separately as `Goal: <instruction>`; images and measured state follow.

1. **Prompt and tools:** keep observation → move → observation, human-readable notes,
   `done`, and `give_up`. Replace two YAM arms with one SO101, remove YAM dimensions
   and workspace bounds, and describe the verified SO101 base axes and grasp point.
   Expose XYZ plus only supported orientation constraints: five arm joints cannot
   satisfy arbitrary six-dimensional poses. Keep gripper commands normalized 0–1.
2. **IK:** replace the YAM-only `BimanualRelativeIK` used by `RoboCurveTrajectory`
   with an SO101 solver. Use the existing SO101 model assets, verify the grasp frame,
   and verify model joint signs/offsets against the active LeRobot calibration.
   Named saved zero/home poses are not calibration zeros. Validate FK/IK round trips
   offline, joint limits, unreachable targets, and every interpolated waypoint.
3. **Runner:** select the prompt, tool schema, geometry, and camera names by robot.
   Report measured SO101 joints and calculated Cartesian state; use the front camera.
   Preserve Codex transport, conversation notes, cancellation, and session budgets.
   Emit five left joint targets, an empty right array, and the normalized left gripper
   through the existing cloud/controller protocol.
4. **Validation:** verify units and gripper polarity, configure measured workspace/table
   constraints and speed limits, test rejection without dispatch, then validate a small
   attended physical move. Do not enable Cartesian hardware execution until the model
   and real calibration agree.

Status: implemented in `so101_policy.py` and `so101_trajectory.py` and selected
by the runner for the configured (5, 0) robot. `SO101-PROMPT.txt` contains the
new prompt. `move_to` accepts x/y/z, absolute wrist_roll in radians, and gripper.
Pitch is unconstrained; the solver selects nearby shoulder/elbow/wrist-flex angles.
The first version does not expose a world-frame yaw or full-pose target.

Offline tests cover FK/IK, full path pacing, limits, unreachable targets, budget
rejection, front-camera prompt/tool calls, and Codex's SO101 decision schema.
The running service has not been restarted and no hardware move was issued.
Joint limits now come from the robot's private LeRobot calibration, loaded from
`.local/robot-calibrations/<sha256(robot_id)>.json`. Hardware runner setup refuses
SO101 Cartesian control without this file. For dice_06, wrist flex is ±108.0879°,
so the saved 95.5165° home angle is inside the calibrated range. Tool wrist-roll
bounds, IK clipping and observation validation all use these calibrated limits.
Calibration data stays outside source control. The bounds on Cartesian XYZ are
base-frame software limits, not measured table geometry or collision checking.

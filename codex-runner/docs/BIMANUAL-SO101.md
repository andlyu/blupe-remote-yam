# Bimanual SO101 prompt and IK

1. **Prompt:** `src/remote_yam/bimanual_so101_policy.py` describes two five-joint SO101s, two grippers, and labeled camera views. OpenAI and local Codex use the same contract.
2. **Targets:** `left_x/y/z/wrist_roll/gripper` and `right_x/y/z/wrist_roll/gripper`. XYZ uses meters in each arm’s own base frame; wrist roll uses absolute servo radians. Other orientation components are unconstrained.
3. **IK:** Reuses `SO101Trajectory` separately with each arm’s calibration limits. Both full paths must pass before dispatch. Packets contain five joint angles per arm in degrees and two normalized grippers at 10 Hz. Unrequested arms hold measured joints; shorter paths hold their endpoint. This does not add inter-arm collision checking or a common world-frame transform.
4. **Runner setup:** Add the following to the existing `YAM_DASHBOARD_ROBOTS` array, preserving other robots:

```json
{"id":"robot-3652c537a175cbae","name":"MakerMods Bimanual SO101","hardware":"bimanual_so101","joint_counts":[5,5],"cameras":["overhead","side","left","right"]}
```

5. **Calibration:** `.local/robot-calibrations/<sha256(robot_id)>.json` contains `{"left":"left-calibration.json","right":"right-calibration.json"}`. Paths resolve relative to the manifest. These are each arm’s LeRobot calibration files. The local checkout has the Jetson pair’s manifest and files prepared; copy them to any other runner that will execute this robot. Missing calibration blocks Cartesian runs.
6. **Cameras:** Left = Arm A wrist, right = Arm B wrist, with the corrected controller mapping. The robot selector uses the existing per-robot camera, conversation, and queue routing. All Episodes remains unchanged.

Model and IK tests do not move hardware. Deploy/restart the runner with the updated catalog and calibration files to activate this integration.

Deployed to https://playground.blupe.io/ on 2026-09-14. Verified the catalog, per-robot browser session, and all four public camera endpoints (HTTP 200). Hosted runs use the existing OpenAI API-key flow; local Codex mode remains at localhost:8788. Backup: `/home/ubuntu/yam-web/bimanual-backup-1789376378`. No model or physical run started during rollout. Operator routing now uses the persistent Jetson-to-cloud SSH tunnel; no Mac relay is required.

For wrist-only Astra input, add `"policy_cameras":["left","right"]` to this
robot's catalog entry. The viewer retains all configured `cameras`; both the
OpenAI and Codex policies capture only the selected wrist views and tell Astra
that no overhead view is supplied. Restart the runner and submit a new run to
apply the change. This setting does not change the physical controller's separate
camera-readiness requirements.

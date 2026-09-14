# Robot selection

1. `./run-codex.sh` loads `config/robots.json`: YAM, Andrew’s single SO101, and MakerMods Bimanual SO101. The top-right selector switches cameras, conversation, and the destination queue. All episodes remain shared. Switching robots does not move a previously queued request.
2. Override `YAM_DASHBOARD_ROBOTS` with a JSON array for another installation. Each entry supplies `id`, `name`, `hardware`, `joint_counts`, and `cameras`. A bimanual pair is one robot with joint counts `[5, 5]`.
3. Andrew’s single SO101 (`robot-abecb4cd868ab24b`) has bundled IK joint limits in [`models/so101/joint_profiles.json`](../models/so101/joint_profiles.json). The runner uses those normalized degree limits and the LeRobot midpoint-centered convention; no separate local calibration file is needed for this robot. The profile is derived from the controller’s published `dice_06.json` and includes its source URL and SHA-256. It is specific to that arm, not a default for every SO101. Other robot IDs must have their own reviewed profile or an explicit local calibration at `.local/robot-calibrations/<SHA256-of-robot-id>.json` inside `codex-runner/`. An explicit local calibration takes precedence. Bimanual SO101 still requires a JSON mapping `{"left":"left.json","right":"right.json"}` to the two calibrations in the same directory. Missing limits block execution, not viewing. If calibration changes on the controller, update the corresponding runner profile before Cartesian use; profiles are currently bundled configuration, not live controller discovery.
4. SO101 Cartesian bounds are X `[-0.40, 0.40]`, Y `[-0.44, 0.40]`, and Z `[0.02, 0.50]` meters in each arm’s base frame. Forward is -Y. Calibrated joint limits and IK still apply. These workspace checks are not arm-to-arm collision checking.

To compute the calibration filename:

```bash
python3 -c 'import hashlib; print(hashlib.sha256(b"YOUR_ROBOT_ID").hexdigest() + ".json")'
```

Robot entries are public connection metadata. Account credentials and local calibration files are not included in the catalog. The controller must be connected and the operator must permit queued execution before a task can run.

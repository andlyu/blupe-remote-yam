# Robot selection

1. `./run-codex.sh` loads `config/robots.json`: YAM, Andrew’s single SO101, and MakerMods Bimanual SO101. The top-right selector switches cameras, conversation, and the destination queue. All episodes remain shared. Switching robots does not move a previously queued request.
2. Override `YAM_DASHBOARD_ROBOTS` with a JSON array for another installation. Each entry supplies `id`, `name`, `hardware`, `joint_counts`, and `cameras`. A bimanual pair is one robot with joint counts `[5, 5]`.
3. SO101 Cartesian execution needs the selected robot’s calibration on this runner. Place it at `.local/robot-calibrations/<SHA256-of-robot-id>.json` inside `codex-runner/`. For a pair, that JSON contains `{"left":"left.json","right":"right.json"}` pointing to each arm’s calibration in the same directory. Missing calibration blocks execution; it does not prevent viewing or selecting the robot. Andrew’s single-arm file is published in the [controller repository](https://github.com/andlyu/blupe-playground-robot-controller/blob/main/examples/so101/dice_06.json); it is specific to his arm.
4. SO101 Cartesian bounds are X `[-0.40, 0.40]`, Y `[-0.44, 0.40]`, and Z `[0.02, 0.50]` meters in each arm’s base frame. Forward is -Y. Calibrated joint limits and IK still apply. These workspace checks are not arm-to-arm collision checking.

To compute the calibration filename:

```bash
python3 -c 'import hashlib; print(hashlib.sha256(b"YOUR_ROBOT_ID").hexdigest() + ".json")'
```

Robot entries are public connection metadata. Account credentials and local calibration files are not included in the catalog. The controller must be connected and the operator must permit queued execution before a task can run.

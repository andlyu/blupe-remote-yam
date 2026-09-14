# SO101 kinematic model

Derived from the existing workspace model `assets/so101/so101_new_calib.xml`,
originally from TheRobotStudio/SO-ARM100, `Simulation/SO101/so101_new_calib.xml`.
Source: https://github.com/TheRobotStudio/SO-ARM100/tree/main/Simulation/SO101
Generated 2026-09-13. Input SHA-256: `33d32cf819443159a2ea4ca8722bd8a55cf85eb3942605e7b7598354afb522a2`.

`kinematics.xml` retains body transforms, joints, limits, inertials and sites;
mesh assets and geoms are removed. No mesh downloads are required for FK/IK.
This model performs no collision checking. The target is `gripperframe`, not
`gripper_link` (the wrist body). The site lies on the fixed gripper assembly;
it is not recomputed as the moving jaw opens.

The solver assumes LeRobot new-calibration degree coordinates and uses radians
internally. Verify real calibration offsets and the model frame before physical
Cartesian use. Recorded home/zero names do not establish model calibration.

Runtime motor limits override the model defaults from the per-robot calibration.
For each STS3215 joint, degree bounds are ±(range_max-range_min)*180/4095,
matching the installed LeRobot degree normalization. Homing offsets are already
reflected in calibrated servo positions and are not applied a second time.

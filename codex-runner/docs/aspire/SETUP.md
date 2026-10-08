# Robo-house ASPIRE setup

The shared [robot catalog](../../config/robots.json) lists each playground's
hardware type, joint counts, and camera roles. See [multiple robots](../MULTI-ROBOT.md)
for selection and configuration, and the [RGB-D contract](../api-depth-contract.md)
for calibration and camera payloads.

## Hardware and station model

These are the Robo-house integration's configured assumptions; query the
station's current information and published calibration before using them.

| Item | Robo-house setup |
| --- | --- |
| Arms | Two YAM arms, six joints per arm |
| Camera roles | Top, left wrist, right wrist; no bottom camera |
| Grippers | Installed I2RT `linear_4310`; station model and mesh hashes were checked |
| Gripper mapping | Normalized 0 closed / 1 open; each installed positive slide travels `0.0475 * g` meters |
| World frame | `blupe_base_midpoint`: X forward, Y left, Z up, with Z at base height |
| Base spacing | Configured 0.62 meters |
| Tool frame | `aspire_grasp`; native opening X corresponds to API grasp Y through the adapter's local-Z conversion |
| Units | ASPIRE joints in radians; paired API packets in degrees; quaternions xyzw |
| Execution | Existing Session API controller owns hardware; native ASPIRE plans become paired 10 Hz packets |

The geometry mapping is implemented in [the native planning adapter](../../src/remote_yam/aspire_planning.py)
and [the gripper model adapter](../../src/remote_yam/aspire_gripper_model.py).
Published tool poses already include the grasp-frame conversion. Applying it
again or adding a second model offset changes the target incorrectly.

Model/mesh agreement is not measured physical contact calibration. Camera-to-arm
calibration, object dimensions, and pad contact remain subject to their actual
published quality and each skill's validation scope. Historical successful
tasks do not establish universal station accuracy.

## Current scene and calibration

Use `get_station_info()` inside the configured ASPIRE harness, together with
the selected robot's public calibration and measured robot-state feedback.
Use paired RGB plus metric aligned depth and that capture's intrinsics and
transform. Independent JPEG previews are useful for inspection; wrist images
need the wrist pose associated with their capture.

For the selected robot, the Session API exposes:

- `/v1/robots/{robot_id}/calibration` for the current calibration package.
- `/v1/robots/{robot_id}/cameras/{role}.rgbd.npz` for paired RGB-D.
- `/v1/robots/{robot_id}/cameras/{role}.jpg` for independent preview images.

The [native depth contract](../native-depth-contract.md) describes the optional
native camera transport. Neither the static robot catalog nor an old program
replaces current measurements.

## Runtime configuration

The configured station supplies the pinned ASPIRE checkout, compatible Python
environment, station harness, perception backend, gripper geometry, and API
robot identity. The public catalog supplies task code and learned recipes;
it does not install camera drivers, own robot motors, or commission calibration.

Keep new program and topic libraries in persistent writable storage outside
disposable run outputs. Keep credentials in the existing subscription/runtime
credential store. See the [ASPIRE quick start](README.md) for launching the local UI.

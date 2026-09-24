# MakerMods MakerArm integration

The runner supports MakerArm XYZ position requests, deterministic IK and 10 Hz
joint waypoints, through OpenAI or local Codex. Orientation is unconstrained.
Model/actuator alignment must be measured before physical Cartesian execution.
See [source findings](refs/maker-arm/INDEX.md).

## Register and select

Create a robot of type `makerarm` in the operator console. Use its actual ID,
then append this entry to the existing `YAM_DASHBOARD_ROBOTS` JSON on both local
and hosted runners (preserve existing entries):

```json
{"id":"ACTUAL_REGISTERED_ID","name":"MakerMods MakerArm","hardware":"makerarm","joint_counts":[6,0],"cameras":["front"]}
```

Configure camera roles to match the controller. The selector switches the
existing per-robot session/conversation, cameras and API queue. All Episodes is
unchanged. Before registration the selector shows a disabled setup-pending entry;
no placeholder identity is sent to the cloud.

## Motor/model alignment

Install the private mapping as `.local/robot-calibrations/<sha256(robot_id)>.json`
relative to the runner root. Required fields:

- `verified`: true only after matching measured poses to the URDF.
- `signs`: six values, each +1 or -1.
- `offsets_rad`: six measured model offsets, in URDF joint order link_002..007.
- `joint_limits_deg`: six [low, high] bounds in the controller SDK coordinates.
  These intersect with mapped URDF limits, never expand the SDK profile.
- `workspace_m`: three [low, high] bounds in native base_link coordinates (Y-up).

The relationship is `q_model = signs * radians(controller_degrees) + offsets_rad`.
Do not copy SO101 or LeRobot resting-zero calibration into this file. Validate
multiple distinct measured poses and axis directions. Leave verified=false until
that check is complete. Missing/unverified mapping rejects model runs.

The unmodified vendor model supplies grasp_center; no calibrated jaw-width
transmission or collision model is claimed. Unit tests use synthetic mappings,
not a physical MakerArm calibration.

## Deploy

Ship makerarm_policy.py, makerarm_trajectory.py, models/makerarm/robot.urdf,
hosted.py, multi_robot.py and static/hosted.js together with the existing runner.
The model uses existing NumPy only. Restart the runner in an idle window; install
its private mapping and catalog entry separately. The controller, registry and
operator portal changes must also be installed for the new hardware type.
No hardware commands or deployments are performed by the tests.

## Hosted rollout — 2026-09-13

Deployed to playground.blupe.io and the operator portal. The server keeps its
existing SO101 policy selection; MakerArm and required common policy/transport
hooks were added as a scoped release. No physical controller was restarted.
Backup: `/home/ubuntu/makerarm-backup-1789355877` (manifest records new files and
originals). Staged release and hardware-free smoke test: `/tmp/makerarm-deploy`.
The real browser showed YAM, SO101, and disabled MakerMods MakerArm — setup pending.
A registered identity, verified mapping, and Jetson installation are still needed.

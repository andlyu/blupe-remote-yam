# Robo-house runtime interface for the original ASPIRE skills

Generate actual Python defining `build_task(tools)` and `evaluate(tools, task)`.
Use the original installed `skill_library` source supplied in this context.
Return the requested action/source/summary/lesson/queries JSON schema. Do not
run task code at module import. Numpy, scipy and skill_library are importable.

This station has exactly top, left and right cameras, no bottom camera. Older
NVIDIA examples describe another station. Midpoint world is X forward, Y left,
Z up, with arm bases 0.62 m apart. Query station metadata and actual measured
start state. Camera calibration and selected original gripper geometry retain
their published provenance; do not invent or double-apply an offset.

The hardware is I2RT linear_4310, NOT the gripper in the original NVIDIA/ABC
station. Installed XML and housing/left-tip/right-tip mesh hashes were verified
against Robo-house. The adapter replaces those native meshes and jaw joints,
while preserving the published API grasp site. Both installed fingers use
positive 0.0475*g metre slides, g=0 closed and g=1 open. The old opposing
0.0376*g mapping and NVIDIA pad-midpoint geometry are invalid for this station.
API grasp Y is the hardware opening axis; the adapter's local Z rotation makes
this native planner grasp X. Query native_gripper_meshes in planner_validation
for CAD bounds and opening-axis planar facet candidates at open/closed jaws.
CAD surfaces are not measured physical contact. Do not use the old 16x2x50mm
NVIDIA pad profile or its midpoint shift, or assume a zero-offset revision is
already physically verified. The installed API site retains its 134.7mm
reference; the newer upstream I2RT 144.65mm site is another model revision.
Plan with the corrected installed collision meshes and derive task grasp and
placement from those contact surfaces and current measured block/support.

All tool poses already use the native ASPIRE grasp frame: get_planning_state
ee_quat, sample_topdown_geometric display RPY, freespace target display RPY,
native predicted endpoints and native_gripper_meshes contact coordinates.
Decode display RPY directly to R_world_aspire. Transform native contact points
with that rotation directly. Native X is the jaw-opening axis. Build a desired
native opening basis directly, and tilt about native X to preserve that axis.
Do not multiply these tool rotations by API_TO_ASPIRE_GRASP again, or convert a
desired native rotation back to API before supplying a native tool. The adapter
already applies that conversion to external API poses once. The matrix is
provenance for API/native translation, not an extra transform for native tools.

`tools` is a dictionary of callables, not an object with methods. Call
`tools['get_station_info']()`, `tools['get_camera_rgbd']('top')`, and similarly
for the other names below. Read-only callables during task construction and
final evaluation:

- `get_station_info()` returns frame/calibration/gripper/native-model metadata.
- `get_camera_rgbd(camera)` returns paired uint8 RGB, metric `depth_m`, K,
  T_world_camera (OpenCV optical XYZ to midpoint world), and capture metadata.
- `segment_camera_rgb(frame['rgb'], query)` returns a boolean mask, image hash,
  status and proposal evidence. Declare each query in the response query list.
  The station's existing Astra service proposes all queries for one image.
- `get_planning_state()` returns `arms[side]` containing joint_pos radians,
  gripper_pos normalized, ee_pos metres and ee_quat xyzw. Idle missing jaws can
  be labelled assumed-open geometry only in an offline preview.
- `sample_topdown_geometric(object_name, detection=..., camera='top', **kwargs)`
  calls the ORIGINAL `skill_library.grasp_geometry.sample_topdown_geometric`.
  Supply a current measured detection dictionary with position_3d in world
  metres and optional score. It returns original candidates with position,
  rpy, score, width (metres) and trajectory_cache_key. Original arguments yaws,
  pitches, z_offsets, z_offset_m, width, max_grasps and clip_min_z are available.
  Its detector callback receives this explicit measured detection, rather than
  connecting to NVIDIA's unavailable BundleSDF server. No position is guessed.
- `estimate_drop(target_name, detection=..., z_offset=..., camera='top')` calls
  ORIGINAL `skill_library.pick_place.estimate_drop`. Supply the measured target
  position_3d and an explicit offset derived from object/grasp/support geometry;
  NVIDIA's fixed basket/plate drop offsets are not this station's task geometry.
- `birdseye_pose(side, left_home_xyz=..., right_home_xyz=...,
  home_view_z_offset=..., left_birdeye_view_rpy=..., right_birdeye_view_rpy=...)`
  calls ORIGINAL `skill_library.pick_place.birdseye_pose`. All station pose
  arguments are explicit; use measured start/scene poses, not NVIDIA constants.
- `plan_freespace_sequence(segments)` tests original native ASPIRE planning,
  without motion, using successive predicted endpoints and jaw states. A
  segment contains stage, arguments, and gripper_state for both arms. Each
  segment needs an arm target pose; jaw-only arguments are not a motion
  segment. While converting task steps to preview segments, accumulate open
  or close into gripper_state for the next move, then emit that move with its
  pose arguments. The execution runtime performs this same conversion before
  its complete sequence plan. The result is a dictionary with success, status,
  stages, failing_stage and reason; inspect success and preserve actual failed
  stage details. Native failures remain failures.

The operator explicitly requires native planner/controller admission without
extra generated-program safety rails. Finger/support distance calculations may
be saved as diagnostics, but do not impose a separate 2 mm or other bespoke
preplanning clearance cutoff. Send proposed pose configurations to the native
planner and report its actual joint-limit/collision outcomes. Keep native
checks intact; do not invent calibration offsets or release heights.

The detection callback bindings and pure pose helpers issue no robot commands.
Whole upstream `pick_object`/`pick_and_place` and one-shot loops issue commands
and own retries/home/default drop poses. Use their code as the upstream starting
reference while assembling the complete sequence through this runtime interface.
AnyGrasp's model/server is not installed by the station adapter; do not claim
its neural sampler ran or silently substitute another sampler under that name.
The upstream topdown_geom mode is available explicitly.

`build_task` returns a nonempty steps list plus measured geometry needed for
evaluation. Move step: `{stage, kind:'move', arguments:{...}}`. Jaw step:
`{stage, kind:'open' or 'close', side:'left' or 'right'}`. Names are unique.
Arguments are native freespace_move keywords left/right_target_pos and
left/right_target_rpy, planning_speed, optional normalized gripper target
widths. Native display RPY degrees correspond to
Rotation.from_euler('xyz',[-rpy[1],rpy[0],-rpy[2]-90],degrees=True).
Keep idle arm fixed unless explicitly planned. Plan jaw state changes too.

The runtime fully plans pickup, lift, transport, placement, release, withdrawal
and returns before the first task command. The existing runner owns a fresh
five-minute queue session, Home, measured completion, stop and normal parking.
Execution captures a fresh leased scene and replans; recorded poses/cache keys
are not live plans. No physical retry loop or lease/controller API belongs in
generated source. Motion admission remains in the existing controller and native
planner. The held object is not attached as a native collision body.

Derive source and target from actual current masks/depth. The canonical task identifies the current objects and target; historical examples do not override it. Reuse source patterns after adapting station assumptions, never old geometry or path caches. evaluate receives fresh read-only tools and returns {success:bool,evidence} from final images/depth. Completed commands do not establish physical task success.

# Public YAM RGB-D read contract

The example reads the existing public Session API without a camera driver or
robot-control credential. Default origin:
`https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com`.

| Read route | Result |
| --- | --- |
| `/v1/robots/yam-1/calibration` | JSON package: schema version 1, robot/calibration identity, cameras and quality |
| `/v1/robots/yam-1/cameras/{role}.rgbd.npz` | Latest paired RGB-D; roles are top/left/right; 503 when unavailable or stale |
| `/v1/robots/yam-1/cameras/{role}.jpg` | Independent JPEG, not synchronized to a depth bundle |

Public `rgbd-npz-v1` has exactly these NumPy arrays:

| Array | Type/shape | Meaning |
| --- | --- | --- |
| `rgb` | uint8 H×W×3 | Paired RGB pixels |
| `depth_m` | float32 H×W | Color-camera optical Z in meters; zero/NaN are missing |
| `metadata` | scalar Unicode JSON | Capture and calibration identity |

Required metadata: `schema_version: 1`, `robot_id: "yam-1"`,
`camera: "top"|"left"|"right"`, `calibration_id`, finite Unix `captured_at`,
`depth_units: "meters"`, `depth_aligned_to: "color"`,
`depth_semantics: "optical_z"`, `depth_coordinate_frame: "color_optical"`.
Dimensions match camera `image_size: [W,H]`. Public freshness requires synchronized
clocks and an age from zero to two seconds at receipt. Different GETs may return
different frames. The client disables pickle, bounds compressed/decompressed
payloads to 16 MB, validates NPY sizes before loading, and follows no redirects.

Each camera has a 3×3 `K`, five `distortion` coefficients and
`distortion_model: "opencv_brown_conrady"`. Overhead `T_base_camera` maps optical
coordinates (x right, y down, z forward) into each named arm base in meters.
Wrist `T_grasp_camera` is a mounting transform combined with current measured
settled-joint FK, not itself a current base pose. Optional
`base_geometry.spacing_m` binds the two bases before a run.

Optional `depth_intrinsics` contains `width`, `height`, `fx`, `fy`, `ppx`, `ppy`,
`model` and five `coeffs`. It must match metadata `factory_color_intrinsics`,
with `sensor_serial` matching calibration `depth_sensor_serial`. The adapter uses
this factory profile instead of board-fitted RGB intrinsics when present.
Supported factory models: `distortion.none`, `distortion.brown_conrady`, and
`distortion.inverse_brown_conrady`.

Factory rays follow [`rs2_deproject_pixel_to_point` in librealsense 2.58.1](https://github.com/realsenseai/librealsense/blob/v2.58.1/src/rs.cpp).
The synthetic [SDK oracle](../tests/fixtures/realsense-deprojection-2.58.1.json)
checks corners and representative pixels. RealSense inverse Brown and legacy
OpenCV Brown use distinct tangential conventions.

The full `quality` accompanies observations and measurements. Rejected reports
or reports with failures require `operator_selected: true`; that permits an
explicit experiment and does not certify accuracy. Calibration identity must
match the bundle and stay fixed throughout a run. A query describes a captured
surface point, not an action target or a new capture at decision time.

See the [example guide](astra-depth-example.md) and optional
[operator transport](compact-depth-contract.md).

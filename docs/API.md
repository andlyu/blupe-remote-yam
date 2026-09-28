# BluPe Robot API

Connect your own model to real robot arms. Your code reads camera images and
robot feedback, calls your model, and submits its next motion through the API.
The playground UI is a client of this same service; using the API does not
require the UI or a particular model provider.

## Start with a read-only request

Base URL: `https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com`

```bash
export BLUPE_API="https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com"
curl -fsS "$BLUPE_API/v1/robots"
curl -fsS "$BLUPE_API/v1/robots/yam-1/queue"
curl -fsS "$BLUPE_API/v1/robots/yam-1/observation"
```

These calls do not join the queue or move the arms. The queue reports station
availability; the observation contains measured robot state and camera URLs.
Fetch the returned camera URLs for JPEG images. Unavailable or stale cameras can
return an error rather than an old frame.

[Live JSON request and response schemas](https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com/v1/contracts)
describe the exact payloads. This is a JSON-schema contract endpoint, not an
OpenAPI/Swagger page.

## Camera intrinsics and extrinsics

Camera calibration is available through public, read-only requests. No API key,
queue entry, or active session is required. Start with `GET /v1/robots`: robots
with calibration advertise `calibration_url` and `camera_poses_url`, relative to
the Session API origin. A robot without configured calibration returns `404`.

```bash
export BLUPE_API="https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com"
curl --fail-with-body "$BLUPE_API/v1/robots/yam-1/calibration"
curl --fail-with-body "$BLUPE_API/v1/robots/yam-1/camera-poses"
```

### Which parameters to use

The `/calibration` response has a `calibration_id` and a `cameras` object:

| Field | Meaning |
| --- | --- |
| `cameras.{top,left,right}.K` | 3×3 intrinsic matrix in pixels: `[[fx,0,cx],[0,fy,cy],[0,0,1]]` |
| `cameras.{top,left,right}.distortion` | Lens coefficients; read `distortion_model` and the response's `distortion_order` |
| `cameras.{top,left,right}.image_size` | `[width, height]` for these intrinsics; use the matching image resolution |
| `cameras.top.T_base_camera.left` | Fixed top-camera pose relative to the **left arm base** |
| `cameras.top.T_base_camera.right` | Fixed top-camera pose relative to the **right arm base** |
| `cameras.left.T_grasp_camera` | Fixed left-camera mount relative to the left gripper's grasp frame |
| `cameras.right.T_grasp_camera` | Fixed right-camera mount relative to the right gripper's grasp frame |
| `cameras.{top,left,right}.image_url` | Relative URL for the corresponding RGB JPEG |
| `quality` | Fit status, validation residuals, failures, and operator selection |

The left and right bases are different coordinate frames. Use `.left` when
planning for the left arm and `.right` for the right arm; do not average matrices
expressed in different bases. Each `T_base_camera` is a 4×4 matrix mapping a
camera-coordinate point to the named arm's base:

```text
[x_base, y_base, z_base, 1] = T_base_camera @ [x_camera, y_camera, z_camera, 1]
```

Matrices are nested row-major arrays and act on column vectors. Translations
are in **meters**. Camera optical axes are **x right, y down, z forward**;
arm-base axes are **x forward, y left, z up**. Read the returned
`transform_convention`, `camera_axes`, and `base_axes` with each calibration.

The current YAM RGB calibration uses `opencv_brown_conrady`, with coefficients
`[k1, k2, p1, p2, k3]`, and reports `rectified: false`. Account for distortion
when projecting or unprojecting pixels. Cropping or resizing an image requires
adjusting its intrinsics. Calibration maps a pixel to a ray; determining a 3D
point also requires depth, triangulation, or a known surface.

### Moving wrist cameras

`GET /v1/robots/yam-1/camera-poses` returns the current wrist extrinsics:

- `cameras.left.T_base_camera.left`: left wrist camera → left arm base.
- `cameras.right.T_base_camera.right`: right wrist camera → right arm base.

The API computes these from measured joints and the fixed mount:
`T_base_camera = T_base_grasp(measured_joints) @ T_grasp_camera`.
Refresh them after the arm moves. The response includes `joints_deg`,
`observed_at`, `age_s`, and the matching `calibration_id`; refetch calibration
if its ID changes. The static report also includes `kinematics` for clients
that need to reconstruct poses from recorded joint observations.

Pose requests require settled hardware feedback no more than two seconds old.
If feedback is absent, moving, stale, or simulated, the endpoint returns
`503` with error code `camera_pose_unavailable`. The static `/calibration`
endpoint still works when the arms are off. A failed pose request is not a
reason to reuse an earlier wrist pose as current.

Image capture and joint feedback are **not hardware synchronized**:
`camera-poses.synchronized_with_images` is `false`. Its `observed_at` is the
joint-feedback timestamp, while camera responses provide `X-Captured-At`.
Check both timestamps and use settled captures for geometric measurements.

### Python: retrieve calibration without moving the robot

This example uses only Python's standard library:

```python
import json
from urllib.error import HTTPError
from urllib.request import urlopen

API = "https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com"
ROBOT = "yam-1"


def get_json(path):
    with urlopen(API + path, timeout=10) as response:
        return json.load(response)


calibration = get_json(f"/v1/robots/{ROBOT}/calibration")
top = calibration["cameras"]["top"]
K = top["K"]
distortion = top["distortion"]
T_left_base_top = top["T_base_camera"]["left"]
T_right_base_top = top["T_base_camera"]["right"]
print("Calibration:", calibration["calibration_id"])
print("Top image size:", top["image_size"], "K:", K)
print("Fit quality:", calibration["quality"])

try:
    poses = get_json(f"/v1/robots/{ROBOT}/camera-poses")
except HTTPError as error:
    if error.code != 503:
        raise
    print("Current wrist poses unavailable:", error.read().decode())
else:
    if poses["calibration_id"] != calibration["calibration_id"]:
        raise RuntimeError("Calibration changed; fetch calibration and poses again")
    T_left_base_wrist = poses["cameras"]["left"]["T_base_camera"]["left"]
    T_right_base_wrist = poses["cameras"]["right"]["T_base_camera"]["right"]
    print("Joint feedback timestamp:", poses["observed_at"])
    print("Left wrist to left base:", T_left_base_wrist)
    print("Right wrist to right base:", T_right_base_wrist)
```

### Optional depth and calibration quality

Depth availability is **per camera**: check `cameras.{role}.depth.available`
on each calibration response. When true, `depth.bundle_url` provides paired
RGB and depth as `rgbd-npz-v1`: an NPZ with `rgb` (uint8 H×W×3), `depth_m`
(float32 H×W), and `metadata` (a scalar JSON string). Load it with
`numpy.load(..., allow_pickle=False)` and check its `calibration_id` and
`captured_at`. Depth is aligned to color and measures optical Z in meters;
zero means missing. `depth.image_url` provides a 16-bit PNG in millimeters.
Requests return `503` when no fresh depth frame is available. Use the bundle
when RGB/depth pairing matters; separate image requests may return different
captures. An RGB-only camera can still provide intrinsics and extrinsics.

Inspect `quality.status`, `quality.failures`, and the validation residuals
before relying on a calibration. `operator_selected: true` records an explicit
selection for experiments; it does not mean a rejected fit passed validation.
Calibration and pose responses use `Cache-Control: no-store`.

## Connect a model

The easiest starting point is the [shared runner](../codex-runner/README.md).
Adapt a [provider](../codex-runner/src/remote_yam/providers.py) to call your model
while retaining the runner's camera handling, queue lifecycle, motion validation,
feedback checks and Stop handling. A custom model integration requires code;
the UI does not currently offer an arbitrary model-upload field.

For a separate client, use the existing
[Python HTTP client](../codex-runner/src/remote_yam/session.py) as a reference.
Keep model inference in your own process or service. The robot API receives
validated motion requests, not your model's credentials or weights.

## Session lifecycle

1. Create a session with `POST /v1/sessions`. This requests an actual turn in the
   shared robot queue. Example JSON:
   ```json
   {"schema_version": 1, "robot_id": "yam-1", "prompt": "Place the green block on the plate", "run_duration_s": 300}
   ```
   The default duration is 300 seconds; the supported range is 60–600 seconds.
2. Keep the returned `session_id` and `session_capability` private. Authenticate
   subsequent session HTTP requests with
   `Authorization: Bearer <session_capability>`. The capability also authenticates
   the session event WebSocket; the Python client demonstrates its headers.
3. Follow the returned `events_url` or poll the session until it is active. Being
   queued does not authorize motion. Use the active session's episode and lease
   identifiers, and wait for fresh, ready robot feedback before issuing commands.
4. Read observations and images, call your model, then submit a trajectory. Use
   the live `joint_trajectory_request` schema and the Python client's
   `submit_trajectory` method for field layout and joint counts. Trajectories use
   a 10 Hz cadence; robot-specific limits and gateway validation still apply.
   Wait for execution feedback before planning the next motion.
5. End the session with `POST /v1/sessions/{id}/stop` and JSON
   `{"reason":"user_requested"}` (or `policy_complete` when the task is finished).
   Stop also cancels a queued session.

## Endpoint reference

All paths below are relative to the base URL. Session routes require the capability.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/contracts` | JSON payload schemas |
| GET | `/v1/robots` | Registered robot IDs |
| GET | `/v1/robots/{robot_id}/queue` | Public queue and station readiness |
| GET | `/v1/robots/{robot_id}/observation` | Current station feedback and camera URLs |
| GET | `/v1/robots/{robot_id}/calibration` | Public camera intrinsics, fixed extrinsics, mount transforms, and quality |
| GET | `/v1/robots/{robot_id}/camera-poses` | Public current camera-to-base poses from settled hardware feedback |
| GET | `/v1/robots/{robot_id}/cameras/{role}.jpg` | Public latest RGB image (`top`, `left`, or `right`) |
| GET | `/v1/robots/{robot_id}/cameras/{role}.rgbd.npz` | Public paired RGB-D, when this camera has fresh depth |
| GET | `/v1/robots/{robot_id}/cameras/{role}.depth.png` | Public 16-bit depth image in millimeters, when available |
| POST | `/v1/sessions` | Create a session and request a queue position |
| GET | `/v1/sessions/{id}` | Session state, position and active lease |
| WebSocket | `/v1/sessions/{id}/events` | Session lifecycle and execution events |
| GET | `/v1/sessions/{id}/observations?after_step_id=-1&limit=100` | Recorded observation page |
| POST | `/v1/sessions/{id}/trajectories` | Submit a validated joint trajectory |
| POST | `/v1/sessions/{id}/stop` | Stop or cancel the session |
| GET | `/v1/sessions/{id}/episode` | Episode metadata |
| GET | `/v1/sessions/{id}/episode/trace` | Episode trace |

The legacy `/actions` route is rejected by the current service; use trajectories.
Different robots have different joint layouts. Select the matching profile in the
runner instead of assuming every robot has two six-joint arms.

## Streaming model messages to public viewers

The local playground publishes readable model messages automatically. Use its
**Share conversation on the public playground** setting or launch with
`--no-share-conversation` to opt out. This setting affects conversation text,
not robot recordings or the public queue/task.

For your own policy loop, send snapshots after each visible model message:

```http
POST /v1/sessions/{session_id}/public-conversation
Authorization: Bearer <session_capability>
Content-Type: application/json

{"public_conversation":{"model":"My model","messages":[{"role":"user","content":"Stack the blocks"},{"role":"assistant","content":"Picking up the green block."}]}}
```

Each snapshot replaces the previous one; retries do not duplicate messages.
`GET /v1/robots/{robot_id}/queue` returns it as `public_run` once the session is
assigned, and the public playground renders it. Use only public plain text:
never include API keys, private context, image bytes or raw provider payloads.
Limits: 64 messages, 8,000 characters per message, 64,000 total characters, and
an 80-character model label. Roles: `user`, `assistant`, `tool`, `system`, or
`developer`. Send `{"public_conversation":null}` to withdraw the shared snapshot.
Custom API clients opt in explicitly; omitting this field does not publish text.

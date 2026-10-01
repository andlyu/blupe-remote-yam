# Astra RGB-D example with the public YAM API

[Runnable example](../examples/astra_depth.py) · [API contract](api-depth-contract.md)

The task is to pick up the red block with YAM (`yam-1`), place it fully on the
black towel, withdraw, and verify stable placement. Either arm may be used.
The example uses the existing Session API, local playground queue, measured
feedback, trajectory validation and robot gateway. It opens no camera or motor
devices. Robo-house YAM is a separate station and is not selected.

## Setup and read-only probes

```sh
git clone https://github.com/andlyu/blupe-remote-yam.git
cd blupe-remote-yam/codex-runner
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python examples/astra_depth.py --help
.venv/bin/python examples/astra_depth.py --probe --output outputs/astra-depth-probe
```

`--probe` reads paired RGB-D and calibration without queue admission or motion.
It writes RGB, a depth preview, raw paired NPZ, calibration and a receipt locally.
The receipt labels physical execution `UNVERIFIED`. `--api https://YOUR_SESSION_API`
overrides the default public API. A failed capture produces an error, without
substituting saved images or synthesized depth. Live API availability and
calibration can change independently of this repository.

For a read-only model probe, set up your own Codex subscription using the
[launcher instructions](../../README.md), then run:

```sh
.venv/bin/python examples/astra_depth.py --model-probe --output outputs/astra-model-probe
```

This asks Astra to identify the red block and towel and query their depth.
It accepts only `done` or `give_up`, has no execution controller, and consumes
model usage. It does not test physical placement. Credentials remain in your
normal local Codex login; no API key or copied credentials are needed.

## Local playground

```sh
.venv/bin/python examples/astra_depth.py --port 8793
```

Open `http://127.0.0.1:8793/`. The task is preset. Choose Astra through your local
Codex subscription and high reasoning effort. **Use depth from the API starts
unchecked**, in this example and the standard local YAM playground. Check it
to select the depth adapter; leaving it unchecked uses the ordinary RGB adapter.
The standard `./run-playground.sh` launcher exposes the same option for local
YAM Astra runs, using all three public RGB-D routes when selected.

Clicking Run enters the usual robot queue. Normal preparation can home both
arms. When depth is selected, capture validation and calibration binding happen
before queue admission; an unavailable or invalid pair prevents admission.
`--calibration-id EXPECTED_ID` additionally requires the exact package before
admission when depth is selected. Public conversation sharing is checked by
default; uncheck it in run setup to keep the conversation private.

Each depth observation contains paired overhead RGB and depth, left/right wrist
RGB, full API calibration, capture metadata and current measured state. The depth
preview uses a fixed 0–2 m scale: white is near, blue is far, and magenta is missing.
Saturated blue may exceed 2 m. It is a visualization, not a numeric lookup.

`measure_depth` accepts integer image coordinates `u`, `v` and `radius` (0–5).
The adapter returns optical Z, surface points in each arm base, sample count,
patch spread, capture identity and calibration quality. It corrects distortion
and applies the named transforms. Missing and mixed-surface patches are rejected.
There are at most four queries before each movement decision.

A surface point is not a gripper target: finger offsets, object size, support and
clearance still matter. Astra must query source and destination, reobserve after
each completed move, verify retention after lifting, release after observed
support, and verify stable placement after withdrawal. It returns validated
`move_to`, `done` or `give_up` decisions. It cannot execute arbitrary Python, shell
commands or web tools. An unnamed arm retains its measured joints and gripper
command; `--active-arm left|right` optionally restricts the allowed arm.

The client checks robot/camera identity, calibration identity, dimensions, dtype,
alignment, optical-Z units and a two-second freshness bound. Calibration stays
fixed within a run. The depth client retries observation reads only and submits
no motion itself. Captures can age while Astra reasons. Wrist RGB and measured
joints are not hardware synchronized with overhead depth.

## All-camera RGB-D and operator overrides

Use all three paired cameras through the public API:

```sh
.venv/bin/python examples/astra_depth.py --all-depth-origin https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com --probe
.venv/bin/python examples/astra_depth.py --all-depth-origin https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com --port 8793
```

Astra receives top/left/right paired RGB followed by three aligned depth previews.
Pixel queries also name `camera: top|left|right`; movement and terminal decisions
use null pixel/camera fields. Current measured settled-joint FK and
`T_grasp_camera` reconstruct wrist poses separately in each arm base. The API's
selected base spacing is bound before admission. Existing motion pacing and
validation remain in place. Each pair is atomic; cameras are not synchronized.
All three pairs must remain fresh at observation assembly. All-camera inference
uses the UI; `--model-probe` supports overhead mode only.

Operators with an existing authenticated camera tunnel can explicitly set
`--depth-url http://127.0.0.1:PORT/PATH.rgbd.npz`,
`--rgb-origin http://127.0.0.1:PORT`, or
`--all-depth-origin http://127.0.0.1:PORT`. These require the
[operator transport contract](compact-depth-contract.md). They do not set up a
tunnel or install an exporter. Queue/controller traffic and calibration still
use `--api`. Credentials in URLs, queries, fragments and redirects are rejected.
No private host, key or deployment script is required for the public examples.

## Calibration limits

The complete calibration quality accompanies every observation and measurement.
A rejected calibration requires explicit operator selection in the report;
selection does not certify accuracy. The October 1 experimental report was
operator-selected and `rejected`, with provisional extrinsics, approximately
3.2 cm left held-out translation error and approximately 8 cm disagreement between
overhead estimates in the two arm bases. These are historical report values;
inspect the full `quality` of the currently served package.

Board-fitted RGB intrinsics and factory aligned-depth intrinsics are distinct.
When supplied, `depth_intrinsics` must match the bundle's sensor serial and factory
color profile. Factory deprojection follows the SDK convention. Physical
verification remains necessary: capture or inference PASS does not establish
successful manipulation. Give up when scene, geometry or motion limits cannot
support a defensible move.

## Relation to Agent as Policy

Reference: Mengzhao Jia et al., [Agent as Policy for Robotic Manipulation](https://github.com/agent-as-policy-2026/agent-as-policy),
reference commit `c6875d0457358dd54dd23f61355d21149d63ca64`.
This example adapts its observation, measurement and feedback pattern to the YAM
public API. It uses bounded numeric queries and structured decisions rather than
the paper's autonomous programming workspace. It is AGP-inspired, not a
reproduction of the complete policy or reported results.

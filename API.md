# BluPe Remote YAM API

Use this API to build your own policy client for BluPe’s shared YAM arms.
For the ready-made Astra UI, follow the [README quickstart](README.md#download-launch-run).
You do not need to implement this protocol to use `./run.sh`.

This reference covers the v1 runner-facing API. The live schema bundle was
checked on September 7, 2026; station availability and deployment limits can change.

## Addresses and credentials

| Service | Address / purpose |
| --- | --- |
| Session API | `https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com` |
| Live JSON schemas | [GET /v1/contracts](https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com/v1/contracts) |
| Robot identifier for monitoring | `yam-1` |
| Local runner UI | `http://127.0.0.1:8787` on your computer |

Your **OpenAI API key** authenticates model calls made by your local runner.
Never send it to the Session API. BluPe does not provide model credits.

Public monitoring and session creation require no BluPe credential. Creating a
session returns a **session capability**: an opaque token that authorizes that
session’s REST requests and WebSocket connection. Keep it in backend process
memory; do not put it in URLs, browser pages, source control, or logs. The runner’s
[HttpSessionAPI](src/remote_yam/session.py) stores and attaches it automatically.

Authenticated requests use:

```http
Authorization: Bearer <session_capability>
Content-Type: application/json
```

A **lease ID** is separate from that token. It identifies the current grant of
robot control and must accompany motion requests, together with the episode ID.
A queued session may have neither until a station becomes ready.

## Endpoint reference

Paths below are relative to the Session API base URL. `sid` means `session_id`.

| Method | Path | Authorization | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | Public | Service health; does not establish robot readiness |
| GET | `/v1/contracts` | Public | JSON Schema bundle for v1 messages |
| GET | `/v1/queue` | Public | FIFO entries and station availability |
| GET | `/v1/robots/yam-1/observation` | Public | Latest station state and camera references |
| WS | `/v1/robots/yam-1/events` | Public | Station monitoring stream |
| POST | `/v1/sessions` | Public | Join the queue; returns session capability (HTTP 201) |
| GET | `/v1/sessions/{sid}` | Session capability | Status, position, lease, episode, active trajectory |
| WS | `/v1/sessions/{sid}/events` | Session capability | Session snapshots, observations, execution events |
| GET | `/v1/sessions/{sid}/observations?after_step_id=-1&limit=100` | Session capability | Paginated recorded observations |
| POST | `/v1/sessions/{sid}/trajectories` | Session capability | Submit a waypoint packet (HTTP 202) |
| POST | `/v1/sessions/{sid}/stop` | Session capability | Leave the queue or stop the active session |
| GET | `/v1/sessions/{sid}/episode` | Session capability | Episode metadata |
| GET | `/v1/sessions/{sid}/episode/trace` | Session capability | Episode execution trace |

WebSockets use `wss://` with the same host. Supply the capability in the
`Authorization` handshake header for session events. Use a backend WebSocket
client, as the runner does; the browser WebSocket API cannot set that header.

The legacy `POST .../actions` route is rejected by the current server; use
`/trajectories`. Although the runner has a `call_operator()` helper, the current
server router does not implement `POST .../operator`. Do not depend on that
helper as a working takeover endpoint; `/stop` is the supported stop request. The
UI’s **Call Operator** button displays contact information (7033443837 or
andrew@blupe.io); it does not call this endpoint or change robot control.

## 1. Inspect the station without joining

These commands are read-only and require no API key:

```bash
BLUPE_API="https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com"
curl --fail-with-body "$BLUPE_API/health"
curl --fail-with-body "$BLUPE_API/v1/queue"
curl --fail-with-body "$BLUPE_API/v1/robots/yam-1/observation"
```

The queue response contains `entries` with one-based positions and `stations`
with `connected`, `available`, `source`, `mode`, and `observed_at`. Public entries
omit prompts and session capabilities. An empty queue does not guarantee that
the arms are ready.

## 2. Create a session

Send `POST /v1/sessions` with:

```json
{
  "schema_version": 1,
  "prompt": "place green block on plate"
}
```

This is an actual queue entry, not a dry run. The creation body does not contain
an OpenAI key, model name, robot selector, or preselected lease. The runner owns
model selection and inference; BluPe assigns an available station.

The HTTP 201 response includes `session_id`, `session_capability`, `status`,
`position`, `episode_id`, `lease_id`, `lease_expires_at`,
`latest_observation_step`, and `events_url`. Episode and lease fields can be
null while queued. Save the capability from this response; later status reads
do not return it.

## 3. Wait for control and fresh observations

Open the authenticated `events_url` and follow state changes. A typical flow is
`queued → preparing → running → complete → resetting`. Runs can also end as
`stopped`, `timed_out`, `disconnected`, or `safety_aborted`.

Each session event has this envelope; the example values are illustrative:

```json
{
  "schema_version": 1,
  "event_id": "evt_example",
  "type": "snapshot",
  "timestamp": 1788825600,
  "session_id": "sess_example",
  "episode_id": null,
  "payload": {"status": "queued", "position": 1}
}
```

| Event type | What the client should track |
| --- | --- |
| `snapshot`, `queue_update` | Session status, queue position, episode, and lease |
| `lifecycle` | `payload.state` and transition reason |
| `observation` | Measured joint state and camera references in `payload` |
| `trajectory_result` | Packet `accepted`, `rejected`, `completed`, or `aborted` |
| `trajectory_progress` | Executed waypoint `step_id` for a trajectory |
| `heartbeat` | Gateway lease activity; this is a server-to-client event |
| `episode`, `error` | Episode results or structured failure information |

A subscription begins with a snapshot and may replay the latest observation and
trajectory result (`payload.replayed: true`). Correlate episode, lease,
trajectory, and step IDs; a replay must not cause duplicate motion. The stream
is not a replay of the full history; use the observation page or episode trace
for retained history.

Session observations include `step_id`, `observed_at`, explicit six-element
`left_joints_deg` and `right_joints_deg`, optional independent grippers, and
`images.left/top/right.url`. Station feedback also exposes readiness/safety
information. Use fresh measured state; commanded targets are not observations.
The included runner checks freshness, settled feedback, hardware Home, and
safety before starting its policy. The gateway separately enforces readiness
and motion limits.

The station gateway renews its lease with BluPe. There is no runner-facing REST
heartbeat endpoint. On client connection failure, stop producing commands and
attempt `/stop` when reachable; do not rely on a closed WebSocket alone to end
the server session. The supplied runner handles shutdown and stop requests.

## 4. Submit a trajectory and wait for completion

Your policy performs inference and inverse kinematics locally. Submit a packet
only after your session has control and its previous packet has completed.
The following shows the wire format only: the zero values are placeholders,
not a trajectory to run on hardware.

```json
{
  "schema_version": 1,
  "episode_id": "ep_example",
  "lease_id": "lease_example",
  "trajectory_id": "trajectory_example_0",
  "cadence_hz": 10.0,
  "waypoints": [
    {
      "step_id": 0,
      "left_joints_deg": [0, 0, 0, 0, 0, 0],
      "right_joints_deg": [0, 0, 0, 0, 0, 0],
      "left_gripper": 0.5,
      "right_gripper": 0.5
    }
  ]
}
```

Current contract rules:

- Both arms require exactly six finite joint values, in **degrees**. Do not
  mirror an omitted arm. Grippers are independent, optional, and normalized
  from `0` (closed) to `1` (open).
- `cadence_hz` must be exactly `10.0`. Packets contain 1–300 waypoints, with at
  most 3,000 accepted waypoints across the session.
- Waypoint `step_id` starts at `0`, is contiguous within each packet, and
  continues across packets. The server permits only one active packet.
- Give each new packet a unique `trajectory_id`. For a retry, reuse the exact
  same ID and body. Reusing an ID with a different body produces a conflict.
- The gateway currently imposes a 180-second policy runtime, including
  inference and settling. The waypoint budget does not extend that deadline.

HTTP 202 with `accepted: true` means the API dispatched the packet. It is **not
confirmation of physical execution**. Match the subsequent `trajectory_result`
and `trajectory_progress` events to your packet. Wait for `completed` and its
correlated final measured observation before planning the next packet. Rejected
or aborted packets end normal execution; an aborted packet may already have
executed some waypoints. Inspect its progress and error details.

Completing one packet retains the session for subsequent model calls. When your
policy finishes, send `/stop` to release the session; there is no public `/done`
endpoint, and a model’s reported success is not independent task verification.

## 5. Stop and retrieve results

Using your own session ID and capability, send:

```http
POST /v1/sessions/<session_id>/stop
Authorization: Bearer <session_capability>
Content-Type: application/json

{}
```

Read `/episode` for metadata and `/episode/trace` for the session trace.
These are not the local model-conversation archive or an immediate video
endpoint. The runner’s **Save log** exports local request/response and execution
logs; **Watch replay** and **Save video** use available recordings. Physical
training episodes are also published to
[Public-YAM-runs](https://huggingface.co/datasets/andlyu/Public-YAM-runs).
The service can expire retained episodes; download needed results promptly.

## Errors and retries

Errors use this shape:

```json
{
  "error": {
    "code": "invalid_session_state",
    "message": "a trajectory is already active for this session",
    "status": 409
  }
}
```

| HTTP status | Typical cause |
| --- | --- |
| 400 | Invalid request fields, shape, values, or protocol version |
| 403 | Missing or invalid session capability |
| 404 | Unknown session, robot, or route |
| 409 | Invalid session state, stale control context, or idempotency conflict |
| 410 | Episode retention expired |
| 429 | Rate limit exceeded |
| 502 / 503 / 504 | Service contact problem; execution state may be uncertain |

The bundled transport retries reads and stable-ID trajectory submissions up to
three total attempts, with 1- and 2-second pauses, for transient contact failures.
It does not automatically retry session creation: a lost creation response
could otherwise produce a second queue entry. Do not replace a packet ID after
an uncertain submission, and do not treat a transport error as proof that no
motion occurred. Consult measured feedback and the session trace.

## Cameras and the local UI API

Camera URLs in observations must be reachable from the runner’s computer.
Currently the station advertises private-network camera URLs. The public
Session API does not proxy those frames, so an off-site Astra client still
needs public camera transport. Changing `--session-api` does not solve that.

The local UI’s `/api/*` routes belong to the process started by `./run.sh`;
they are not paths on the hosted Session API. Local control/download requests
use `X-YAM-Runner-Token`, a separate local token. For an independent integration,
use the v1 endpoints above or the bundled [Python transport](src/remote_yam/session.py).

## Python: read-only example

From this repository, after installing dependencies with the launcher:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from remote_yam.session import HttpSessionAPI

api = HttpSessionAPI(
    "https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com",
    supports_trajectories=True,
)
queue = api.get_queue_snapshot()
print("Queued sessions:", len(queue["entries"]))
print("Stations:", queue["stations"])
PY
```

This creates no session and sends no robot commands. For the full policy loop,
see [RunnerController](src/remote_yam/controller.py); it coordinates session
state, feedback, retries, model calls, and stop handling.

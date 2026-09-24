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

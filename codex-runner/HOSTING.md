# Browser-only BYOK runner

`hosted.py` serves the shared multi-visitor UI. `run.py` now launches it locally
through `local_playground.py`, adding the local Codex subscription provider and
public video-route bridge. `--legacy-ui` retains the old terminal-key monitor.
The deployed hosted service does not enable access to a local Codex account.
Visitors to the hosted UI need only a browser.

## Behavior

- Watching requires no provider key and creates no robot queue entry.
- Each browser gets a random HttpOnly, Secure, SameSite=Strict session cookie.
  The server creates a separate controller, Session API client, and private
  recording directory for that visitor. Reload reconnects to the same session.
- A key submitted over HTTPS with **Join queue & run** is retained per provider
  in that visitor's server-memory session after a successful launch. Later runs
  with an empty key field reuse it; entering a new key replaces it. Reload keeps
  the same session. Keys never intentionally enter disk, logs or browser storage.
  Paid service credentials are not stored in the visitor's saved-key collection.
- Completion, failure, Stop and operator handoff clear the active adapter's key
  but keep the visitor's saved key for future launches. Forget, disconnect,
  expiry and server restart clear the saved key. Forget also cancels inference
  and requests robot Stop.
  An already-issued HTTP request can retain its credential until the bounded
  transport call returns (45-second timeout); Python does not promise secure
  erasure of immutable strings. Provider-side revocation remains authoritative.
- Idle browser sessions expire after 30 minutes, all sessions after two hours.
  Closing a tab does not instantly cancel a run. Use Stop before leaving.
  The Jetson's existing execution deadline and gateway checks still apply.
- Defaults: 32 concurrent visitors, one active run per visitor, five seconds
  between launches and 20 launches per visitor session. These are capacity
  bounds, not per-person quotas or protection against determined abuse.
- Model logs/images are private to that visitor and deleted on session cleanup.
  Physical episode recordings still enter the existing public robot dataset;
  the launch page discloses this. Download logs before ending the session.
- The built-in raise/lower policy needs no key. OpenAI uses the existing
  RoboCurve adapter. Astra is shown only when the administrator sets its HTTPS
  endpoint. Visitors cannot supply endpoints; redirects are rejected.

## Local development (robot mock, no real motion)

Install the existing runner requirements and `uvicorn==0.52.4` in a virtualenv.
From this directory:

```sh
YAM_WEB_DEVELOPMENT=1 uvicorn hosted:create_app --factory \
  --host 127.0.0.1 --port 8790 --workers 1 --no-access-log
```

Open `http://127.0.0.1:8790`. This explicit development mode permits HTTP only
on localhost; it uses the existing in-process mock when no Session API is set.
Mock camera images are not provided by this hosted service. Use the published
HTTPS Session API for read-only camera verification; do not click Run when
checking the real station without intending to enter its queue.

## Deployment

Use a dedicated HTTPS hostname and **one process / one replica**. Multiple
replicas would need a new session-routing architecture; do not enable automatic
horizontal scaling. Restarts intentionally lose visitor credentials/sessions.
An HTTPS reverse proxy must preserve Host. Keep the backend port private. Never
enable request-body/header tracing or proxy logging of cookies/keys, and disable
core dumps and swap for this service. Terminate TLS at a trusted proxy and route
only the configured hostname. The app validates Host, Origin and CSRF tokens.

Environment variables contain service configuration only:

| Variable | Value |
| --- | --- |
| `YAM_WEB_ORIGIN` | Exact public HTTPS origin, e.g. `https://run.example.com` |
| `YAM_SESSION_API` | Existing Session API HTTPS origin |
| `YAM_CAMERA_ORIGIN` | Optional; defaults to Session API origin |
| `YAM_WEB_ASTRA_ENDPOINT` | Optional administrator-controlled HTTPS Responses endpoint |
| `YAM_WEB_HARDWARE_CONTROL` | `1` to allow existing gateway-checked physical sessions; default disabled |
| `YAM_WEB_MAX_SESSIONS` | Optional visitor capacity; default `32` |

Do **not** set `OPENAI_API_KEY` or `ASTRA_API_KEY` for the hosted service.

Container build from the repository root:

```sh
docker build -f docker/hosted-runner/Dockerfile -t yam-web .
docker run --rm --name yam-web --read-only \
  --mount type=volume,src=yam-web-data,target=/app/data \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m,mode=1777 \
  --memory=1g --memory-swap=1g --ulimit core=0 \
  -p 127.0.0.1:8790:8000 --env-file /path/to/nonsecret-web-config.env yam-web
```

The Dockerfile copies only source, canonical models and hosted UI assets; it
never copies local recordings, `.env` files, credentials or the rest of the repo.
The in-memory `/tmp` bounds recordings and removes them on restart. Exhausted
recording space is reported by the existing journal without bypassing robot
safety. For larger pilots, add accounts/quotas and durable private artifact
ownership before expanding beyond this supervised single-station service.

`deploy/systemd/yam-web.service` is the equivalent non-container service for the
existing Ubuntu test server. It expects code/virtualenv in `/home/ubuntu/yam-web`
and nonsecret configuration in `/home/ubuntu/yam-web.env`. Its private 512 MiB
temporary filesystem, disabled core dumps/swap, and 1 GiB service memory limit
give the same credential/recording lifetime. Put an HTTPS proxy in front.

`GET /health` is liveness only. Deployment verification must also load the page,
create two independent browser sessions, fetch all three current cameras, and
check read-only station/queue status. A real model/robot run is a separate check.
Keep the existing AWS test server's automatic shutdown timer unless its lifetime
and hosting budget are explicitly changed.

## Verification

### Public past runs

The scrollable **Past runs** panel sits beside **Set up your run** (below it on
mobile). It lists public participant name, prompt and video. Clicking a card opens
a large keyboard-dismissable player. Arm-only raise/lower checks are excluded;
mixed object tasks remain. The robot queue stays beside the live conversation.

`GET /api/past-runs?offset=0` reads public dataset metadata in pages of 12, with a
60-second shared cache. It never reads visitors' model logs or API keys. Original
recordings remain available while previews are being generated; the player labels
those as 10× playback with pauses retained. Published previews play at their encoded
10× rate, with idle intervals removed. Legacy recordings have three views; new
previews add the side camera when recorded. Missing side frames are labeled.

Set `YAM_RUN_NAMES_DATABASE` to a persistent writable SQLite path (default
`data/run-names.sqlite3`). Only episode ID and the already-public runner name are
stored. Configure a writable data directory for a hardened service/container;
back it up with service data. Missing historical names are displayed explicitly.
Name-storage failures never block robot Stop. Private social handles are not
included in the public response.

The robot's existing publisher generates `previews/ep_*.mp4` alongside the original
archive. The recorder reads side-camera snapshots from the existing relay at
`127.0.0.1:8090/18`, asynchronously. Side-camera failures do not block capture or
change the three-camera training-validity definition. Neither training-camera
ordering nor model inputs change.

Backfill retained finalized robot recordings with the publisher's existing Python
environment, HF configuration and FFmpeg setting:

```sh
python -m scripts.publish_yam_previews --root outputs/yam-training
```

This adds previews and preview metadata only. It checks source hashes and uses
compare-and-swap publication; rerun after a concurrent-commit conflict. It cannot
recover side views or names that were never recorded. Existing uploaded archives
and training data are preserved.

```sh
PYTHONPATH=src python -m pytest tests -q
node --check static/hosted.js
```

`tests/test_hosted.py` covers independent visitors, controls and artifact ownership,
CSRF/Host/Origin checks, malformed/oversized requests, key handling, expiry,
duplicate launches, camera origin enforcement and revoked in-flight error
redaction. Tests use mock Session APIs; they never command physical hardware.

Shared visitor chat is served by GET/POST `/api/chat`, using the existing browser
session and CSRF checks. It holds the latest 100 messages in memory, accepts a
32-character display name and 1,000-character message, and limits each browser
session to one message every two seconds. Display names are self-chosen; names
grant no operator privileges. Session IDs stay internal to the chat UI.
Messages render as plain text. Chat never invokes robot controls or model APIs.
The sidebar polls every two seconds while visible; history clears on restart.

Chat moderation uses the isolated `yam-chat-moderation.service` on loopback8791.
Caddy sends only GET `/api/chat` through it; POST chat and all runner traffic stay
on8790. The upstream still performs Host/cookie/origin checks. Removal records
in `/home/ubuntu/yam-web/chat-removals.json` match message ID, timestamp and text
SHA-256, so a reused ID cannot remove a different message. Records are changed
by SSH only; there is no public delete/admin endpoint. The route filters matched
messages from every public response, preserving the original process and all
visitors' sessions. The original in-memory entry lasts until its normal history
eviction or runner restart. This is a soft deletion, not a retained chat archive.
Malformed/unavailable moderation state returns503 instead of exposing a removed
message. The browser reconciles whole chat snapshots so deletions appear on the
next poll; tabs loaded before this update need a refresh. Test coverage is in
`tests/test_chat_moderation.py`.

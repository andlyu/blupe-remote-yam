# Remote YAM Local Runner

For MakerMods MakerArm setup, its Astra prompt, XYZ IK, and the robot selector,
see [MakerArm integration](docs/MAKERARM.md).

This repository owns the shared local playground. BluPe-specific browser hosting
lives in `blupe-evals/apps/blupe_web`; see [ownership](../docs/REPOSITORY-OWNERSHIP.md).

Developer-focused local runner for model inference and YAM Session API commands. Provider credentials remain in the local process and are never sent to YAM Session or BluPe.

The UI starts an authenticated read-only monitor for `yam-1` at launch and renders camera frames and arm joint state from Session API feedback even while idle. Monitoring never creates a queue entry, lease, or command capability, and the UI never substitutes model commands for observed state.

The UI also polls the public FIFO snapshot, shows station availability and existing anonymous session rows, and marks the local runner's row as **YOU** after Join. Queue responses are sanitized locally and never expose prompts, provider configuration, or session capabilities.

Camera URLs must be reachable from the runner host. The current simulation publisher emits LAN URLs under `http://andrew-desktop.local:8089`, which work on that LAN but require Session API proxy/upload or externally reachable signed URLs for remote runners.

## Launch

From the repository root, run `./run-playground.sh` for normal installation and
startup. It installs the playground dependencies without requiring a provider
login. The UI checks local Codex and Claude readiness; a Run click rechecks the
selected provider before queue admission. Missing or outdated installations and
missing subscription logins show a copyable setup/update prompt. **Check again**
sends a small live model request without starting a robot run. Run performs the
same verification before queue admission. These checks use subscription usage;
page loading performs only local setup checks. Expired logins get reconnect
guidance even when the CLI reports a saved login.


The local launcher now serves the same playground UI and queue backend as the
remote site: prompt/settings, queue position, synchronized live views, conversation
and public past runs. Codex subscription is available only on localhost. The
Session API and queue protocol are unchanged. Use `--legacy-ui` to open the old
developer monitor. The local service bridges the public viewer routes normally
provided by the remote reverse proxy; it does not operate the station's controls.

### Astra through your Codex subscription

Run from this directory:

```bash
./run.sh --codex-login --provider codex --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com
```

The launcher checks the installed Codex version, installs a pinned Codex 0.154.0
under `.codex-runtime/` if needed (Node.js/npm required), and reuses your existing
ChatGPT login. If sign-in is needed, Codex opens its browser login flow. Later
launches can omit `--codex-login`. This does not change your global Codex install.

Select **Codex subscription** and keep model **gpt-6-astra**. This routes Astra
decisions through local Codex and your ChatGPT subscription limits. It does not
use the separately billed Responses API. No API key is requested, and there is
no automatic API-key fallback. **Check Codex connection** checks local setup,
sign-in and session-storage write access, then verifies a small live model request
using your subscription without joining the robot queue. A readable login alone is insufficient: the process launching
the runner must also be able to write `CODEX_HOME` (normally `~/.codex`). When
launching from a sandboxed coding agent, grant that directory write access through
its permission flow, or launch from your own terminal. The runner reports this
separately from login and subscription failures; it does not change permissions
or copy credentials automatically.

The launcher enables command submission by default. Clicking Run joins the queue;
execution still requires operator Home/Run authorization and passes the existing
feedback, trajectory, and gateway safety checks. No extra customer launch flag is
needed. Use `--read-only` when deliberately launching a monitor that cannot send
hardware commands. The older `--allow-hardware-control` flag remains accepted.
Codex launch enables the existing waypoint transport; when selecting Codex in a
runner launched with another provider, include `--waypoint-packets` at launch.

Test the subscription before connecting to a robot:

```bash
./run.sh --provider codex --check-provider
./run.sh --provider codex --check-provider-sim
```

These checks consume a small amount of Codex usage. The first sends three
synthetic color images and requests completion without movement. The second
runs two Codex turns through real MuJoCo IK and the in-process Session API mock:
one simulated gripper movement, measured mock feedback, then completion. They
cannot connect to physical hardware. Synthetic images validate attachment
transport, not real-scene perception or manipulation performance.

Each robot run has one Codex conversation. Every movement uses three newly
captured images, then waits for the existing controller's completion feedback.
Stop cancels inference; timeout, invalid output, or exhausted subscription usage
halts the run. Codex produces JSON decisions; the existing local IK and gateway
validate and execute them. Local runner recordings still contain the conversation;
Codex also retains its own session history under its normal local storage.

This provider is local-only. Browser-only hosted visitors continue to use their
own API keys; their Codex subscription would require a local companion runner.

### Opus through your Claude subscription

The same no-demo RoboCurve policy, driven by Claude Code on this computer instead
of Codex. Run from this directory:

```bash
./run.sh --claude-login --provider claude --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com
```

The launcher checks the installed Claude Code version and reuses your existing
Claude account login; if sign-in is needed, `claude auth login` opens its browser
flow. Later launches can omit `--claude-login`. Requires Claude Code 2.1.263 or newer
on `PATH`, in `~/.local/bin`, or in `~/.npm-global/bin` — the launcher does not
install it. The latter locations also work when a desktop launcher supplies a
minimal `PATH`. On macOS the launcher needs normal Keychain access to read your
subscription login; a sandboxed launch may incorrectly report that sign-in is needed.

In the UI the prompt row shows **Run with Astra** and **Run with Opus** side by
side; either joins the same robot queue with the same task. **Run with Opus**
selects provider **Claude subscription · Opus** and model `claude-opus-5-5`.
**Check Claude connection** verifies a small request through the Claude
subscription. It never displays the account email, organization, or plan owner.

Three things this provider does deliberately:

* **The subscription is the only credential.** `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` are withheld from the subprocess, because either one
  silently overrides the subscription login and bills API credit instead. A
  Claude Code signed in with an API key is reported as not ready.
* **The model is pinned, never defaulted.** Claude Code prints
  `unrecognized_model` and falls back to another model; the run is refused
  instead, so an eval never reports a model it did not use. If you see this,
  update Claude Code or pass `--model` with one it lists.
* **A blind decision never reaches the arms.** Claude Code has no image flag, so
  each observation's three camera frames are written into a per-run temporary
  workspace and read back with the Read tool. Read is the only tool granted,
  `--restricted` confines it to that workspace, and any permission denial fails
  the turn rather than letting Claude decide without seeing the cameras.

Each robot run has one Claude conversation, resumed by session id between
decisions. Stop cancels inference; timeout, invalid output, or exhausted
subscription usage halts the run. As with Codex, Claude produces JSON decisions
and the existing local IK and gateway validate and execute them. Token counts and
reported cost are recorded with the run for the eval report.

This provider is local-only, on the same terms as Codex above.

### API-key or built-in provider

```bash
cd codex-runner
./run.sh
```

The launcher opens `http://127.0.0.1:8787/` immediately and starts read-only monitoring without a provider key. When **Join Queue & Run** is invoked, a missing provider key is requested through a hidden prompt in the launch terminal and retained only in process memory.

Alternatively, provide a launch-time environment variable:

```bash
OPENAI_API_KEY="..." python3 run.py
# or
ASTRA_API_KEY="..." python3 run.py --provider astra
```

Connect to the live v1 API without a BluPe or provider credential:

```bash
./run.sh --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com
```

Monitoring and session creation are public; creation returns an opaque in-memory per-session capability for session REST/WSS calls. Select **Built-in policy: Raise both arms 20 cm and return**, then **Join Queue & Run** for the provider-free observation-relative MuJoCo policy. It waits for a fresh safe settled 6+6 observation, additionally requires calibrated Home on hardware, validates the complete adaptive trajectory, returns exactly to its observed baseline, then stops. OpenAI/Astra keys are requested only if selected.

## Observation-relative built-in policy

Launch read-only monitoring without a YAM or provider credential:

```bash
./run.sh --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com
```

Select **Built-in policy: Raise both arms 20 cm and return** to generate a provider-free trajectory from the first fresh, safe, settled observation. The runner uses the vendored canonical MuJoCo model, moves both end effectors independently 20 cm along world Z, adaptively subdivides toward at most `0.03 rad` per joint while retaining the `0.06 rad` rejection ceiling, reuses the exact reverse path, and submits one explicit 6+6 waypoint only after the prior waypoint settles. Hardware additionally requires explicit top-level `homed: true` and `settled: true`; missing or conflicting state sends zero commands.

## Camera monitoring

The browser loads only same-origin `/api/monitor/cameras/left|top|right` routes. The local backend resolves each semantic name from the latest station observation and streams MJPEG only when its origin exactly matches `--camera-origin` (default `http://10.1.10.185:8089`); it accepts no caller-supplied upstream URL and returns `NO SIGNAL` on validation, timeout, or disconnect failure.

## Astra with RoboCurve's no-demo policy

The UI's OpenAI/Astra providers use `move_to`, `done` and `give_up` tools with
RoboCurve's embodiment prompt, per-arm base coordinates, measured end-effector
state and three fresh camera images. Each call retains the complete conversation
and previous tool results. Cartesian moves become joint-waypoint packets at
10 Hz through the existing API and gateway safety checks.

Launch from this directory with your key available in the terminal environment:

```bash
./run.sh --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com
```

With `OPENAI_API_KEY` configured, the default is `gpt-6-astra` with the task
`place green block on plate`. Without a key, the default is the built-in policy.
After a code update, relaunch this process; browser refresh alone is insufficient.

The UI reports Astra's latest note, call count, outcome and local recording path.
Exact request/response bodies and image blobs are saved under
`recordings/robocurve_<id>/`; no authorization headers or provider key are saved.
`done` is model-reported success. Gateway rejection still stops and holds the
arms. This adapter has passed the full-stack simulated-driver tests, with model
HTTP and camera pixels supplied as fixtures; a live run remains separate.

Reference and intentional differences: `../../docs/refs/robocurve/INDEX.md`.

## Interaction history

The **Interaction log** panel shows each model request and reply, requested
targets, joint-packet submission, AWS dispatch acknowledgement, gateway
acceptance, waypoint progress, measured endpoint feedback and stop reason.
Expand **Details** for the underlying values. While inference is pending, the
panel shows the call number and elapsed wait; during motion it shows progress.

Local validation errors include a code distinguishing `target_out_of_bounds`
from `packet_waypoint_budget` and `session_waypoint_budget`. Gateway rejection,
model/network failure and the model choosing `give_up` are separate events.
An aborted packet can have executed some waypoints; its error includes reported
progress. A local validation failure has sent no motion.

Each run appends `interactions.jsonl` under `recordings/robocurve_<id>/` (Astra)
or `recordings/run_<id>/` (built-in policy). The file retains every progress event
and all submitted joint waypoints; the UI condenses progress into one advancing
row per packet. Use the run selector for earlier recordings and the download
buttons for the journal or exact model/waypoint JSONL. Older `calls.jsonl` logs
also appear, but cannot reconstruct gateway events that were never recorded.
Credentials are not recorded. A recording-write failure is shown in the panel
without interrupting motion. Reload the local runner to enable this panel.

## Waypoint limits

A policy session can submit 3,000 joint waypoints in packets of at most 300.
Execution remains 10 Hz (up to 30 seconds per packet). The Jetson enforces three minutes total from policy handoff, including
inference and settling. Timeout fails the run,
cancels playback, parks at joint zero through the guardrails, and disables torque. Each completed packet permits
the next model request; all existing gateway safety checks still apply.
Restart the runner after updating its Python code; refreshing the page alone
does not load new policy limits.

## Server connection recovery

Transient Session API connection failures (including TLS handshake timeouts)
and HTTP 502/503/504 responses receive up to three attempts, with 1- and 2-second
pauses. The runner shows **Server contact issue** while retrying and clears it
after recovery; exhausted retries leave that error visible and end the run.
The interaction journal records `server_contact_issue` and
`server_contact_recovered` events without credentials or raw exception text.

Retries cover reads and motion submissions with stable IDs. A packet retry
reuses its exact ID and body so server deduplication prevents repeated motion.
Session creation is not retried because an ambiguous response could create a
second queue entry. Authentication, certificate verification, and validation
failures are not retried. Astra inference also retries transient SSL/network failures and HTTP
408/500/502/503/504 up to three attempts with 1/2s pauses. Each attempt reuses
the same prompt, history and images; only one received response proceeds to
waypoint generation. The UI identifies the issue as `Server contact issue — Astra`.
Retries check cancellation between attempts and discard replies received after
Stop. Authentication, exhausted credits and certificate verification errors
fail immediately. The Jetson's three-minute total runtime limit still applies.
Relaunch the local runner to load this update; AWS and the Operator UI need no restart.

### Public physical training episodes

The Jetson automatically records policy execution to the public
[Public-YAM-runs dataset](https://huggingface.co/datasets/andlyu/Public-YAM-runs).
Each run adds an episode to the same repository: nominal 10 Hz camera images,
measured joints/grippers, and actual driver targets with timestamps and validity
flags. The Operator UI shows recording/upload status. Public uploads exclude
local model conversation archives; those remain in the runner's `recordings/`.
See [dataset schema](../../docs/PUBLIC-YAM-DATASET-CARD.md).

In **Interaction log**, choose a run and use **Save log** for a ZIP containing
model inputs/outputs, input images, tool results and execution/error events.
**Save video** downloads the selected episode's left | top | right MP4 once the
Jetson upload completes. **HF dataset** opens Public-YAM-runs. Earlier runs may
have logs but no video; a video from another episode is never substituted.
After updating code, relaunch the runner process and refresh its browser tab.

### Watch a replay

In Interaction log, click **Watch replay**. The selected run plays directly in
the page with play/pause, seeking and fullscreen. When there is no current run,
the latest saved run is selected automatically. Choose another run to replay it.
**Save video** remains a separate download action. Playback streams the public
MP4 directly; it does not wait for the full-file download proxy.

The managed UI on http://127.0.0.1:8787 can replay Astra runs recorded by :8788,
since both use the same local recordings directory. A runner process started
before the replay update needs relaunch for its new media security policy.

### Immediate browser replay

The runner UI records its existing same-origin left/top/right camera images with
Canvas captureStream and MediaRecorder while the run is `running` (1440×294,
10 fps, target 2.5 Mbps). Watch replay and Save video prefer this local recording
as soon as the runner observes the run end; neither waits for training dataset
assembly or upload. The hardware recorder and HF archive remain independent.
Older runs and unsupported browsers use the existing archive.

Reload an existing runner page once to load this feature. Keep that page open
and visible for subsequent runs. Opening midway, missing cameras, background
pauses, or the ten-minute/128 MiB capture limit produce a partial replay. Closing
the page during capture loses the unfinished local recording. Local replays
reflect the displayed camera streams, not the training recorder's synchronized
frames. The newest three finished replays are retained in memory and IndexedDB
when browser storage is available; they are not uploaded. Downloads may be WebM
or MP4 depending on browser codec support.

Run browser-recorder lifecycle regressions without hardware:

```sh
node tests/test_local_replay.cjs
```

## Optional: share your model conversation

We encourage API callers to share the visible conversation on the playground,
but it is not required. Add `public_conversation: {"model":"Astra","messages":[{"role":"assistant","content":"Locating the block."}]}`
to session creation, or send it to `POST /v1/sessions/{session_id}/public-conversation`
with the session capability as the bearer token. Each update replaces the shared
snapshot. Omit it to keep conversation private; send `null` to stop displaying it.
Only include text intended for public display, never API keys or private context.
See `docs/YAM-PUBLIC-CONVERSATION.md` in the BluPe API repository for limits and examples.

For two SO101 followers, see [bimanual SO101 prompt, IK, and runner configuration](docs/BIMANUAL-SO101.md).

## Online model choices

**Run online** opens the model selector: ChatGPT (OpenAI API key), Claude
(Anthropic API key, Opus 5.5), or No AI — just wiggle arms. Claude API control
currently supports YAM; other robot profiles retain their supported providers.
Subscription logins remain in the separate local Codex/Claude launch flow.
The shared runner keeps API keys in browser-session server memory and applies
the same image freshness, trajectory validation and feedback checks to both
API providers. BluPe hosting adds Stripe as a funding choice under ChatGPT;
it does not offer Stripe-funded Claude inference.

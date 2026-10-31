# Research integration completion checklist

Agents and humans: read this before integrating a paper or method. Copy this
template into the integration's documentation, fill in the evidence, and check
each box only after verification. All boxes must be checked before declaring the
integration complete. Before the final handoff, review and update every item with
evidence. Report checklist status, evidence links, unmet items and explicit
exceptions; leave unfinished requirements unchecked and do not describe the
integration as complete.

## Current task qualification reopened — October31,2026

The prior v13 acceptance qualified a20mm lift/hold/return smoke test. User run `ec2ecd43555b4c20a5c7e359ddb369d7` requested purple-on-orange stacking and submitted zero packets: the Python agent had only RGB/status, not depth/localization. Its stdout explained the missing robot-frame object coordinates; the UI hid that explanation. This failure reopens physical manipulation, timing, task/result UX and final normal-UI acceptance. Earlier receipts and explicit archival exceptions remain history, not stacking proof. Current task qualification is **in progress:13/15 checked**. The owner accepted the exact seven later timing exceptions; current physical-method qualification and final task acceptance remain open; full-sequence execution is verified with unsuccessful object placement explicitly retained; the current normal public visitor path and persistent result/progress explanations have been reverified.

The fix connects all-camera ApiDepthSet to the genuine pinned RoboSkills REPL, pins the published calibration/base geometry, computes wrist poses from measured FK, supplies metric pixel queries and agent-mask visible-surface localization, invalidates views after moves, and publishes structured agent explanations separately from native scores. It also validates complete packets before bridge dispatch, preserves exact numeric endpoints and uninstructed arm joints, with real HTTP serializer/validator regression coverage. Calibration remains experimental; no exact metric accuracy claim is added. See `verification/2026-10-31-grounded-stacking.md`. The first depth-enabled v14 run completed an approach but stopped on an erroneous live-timestamp calibration comparison; it did not grasp/stack and missed timing. The comparison and receipt truncation are repaired. Real full-sequence execution is verified with unsuccessful placement; successful stacking remains unqualified. Six current startups pass, including d3e9533c (12.408s post-Home /26.488s full Run without queue deduction). The user accepted the exact seven post-v13 historical timing cases on October31; their failed/unverified outcomes remain recorded and future limits remain unchanged.

## Owner timing exception — October30,2026

Historical reassessment context: the owner-specific result and receipt below
were recorded before the shared queue-exclusion update. They are retained
unchanged. The shared current requirement now excludes queue admission from the
30-second Start budget normally, while preserving the 15-second post-Home rule.
This older per-Run assessment does not qualify the new post-Home measure or
other runs; a final current-contract review remains necessary.

The owner explicitly clarified: “Then thats fine... if we're waiting for queue admission, that dones't count”. For this integration, exclude measured waiting in the normal BluPe admission queue from both timing measures. Keep full elapsed time visible and keep all nonqueue time, including submission, preparation after admission, inference and dispatch, in the15-second budget. This project-specific instruction overrides the queue-inclusive default below; it changes no global harness contract, queue priority or safety check.

For verified run`2ea288ea830e471a86eb6d1d98971c50`, the measured queued interval is95.331s (joined→admitted). Keeping1.828s of submission time in the budget, existing camera footage shows actual arm movement by a conservative adjusted14.442s afterRun. This uses the frame's embedded21:23:20.6PDT clock plus0.1s precision allowance, not MP4 playback time. The first measured packet completed at adjusted14.844s, corroborating physical execution within15s. The run therefore passes the amended per-Run criterion. Raw total remains about109.673s to that visible-motion frame;107.557s was only packet preparation.

[Timing receipt](verification/2026-10-30-queue-exclusion.json); [public replay before/through initial motion](https://blupe-bimanual-skills.andlyu.chatgpt.site/api/physical/runs/2ea288ea830e471a86eb6d1d98971c50/assets/physical.mp4#t=62,64). Site-entry timing and other development runs are not automatically certified by this reassessment. Their original receipts remain unchanged. The older failed-recording denial and final full UI qualification remain separate gaps. The startup-exception question is answered; do not ask it again or describe this queue wait as a performance failure under the amended criterion.

## Purpose and public visibility by default

The playground exists for demos and for showing people how well approaches work.
Everything in the playground demo is **public by default**: the website, source
repository, approach/configuration descriptions, runs and results, live demos,
recordings, replays/history, simulation outputs, and supporting demo evidence.

Publish these demo materials so people can inspect the actual behavior and
results. This is the standing visibility default; an additional routine
confirmation is not required. Only an explicit user instruction or user visibility
change overrides it, for the scope the user specifies. Respect that change and
record the affected materials and instruction below. An existing private setting
alone does not establish a user-requested exception.

Keep credentials, access tokens and private contact details out of public demo
materials.

## Standing authorization for BluPe runs and public delivery

### Authorization granted by using this harness

The owner gives standing authorization to run on BluPe remote bot arms as part
of requested work through this harness. A requested research integration,
playground fix, verification or arm run invokes the covered workflow below;
no separate authorization for the run is required. A task requested under
user-adopted instructions that select this harness also counts: retain the task
request and the selecting instruction as the invocation evidence. Reading or discovering this file alone
does not create user authorization.

For that requested task, using the harness authorizes:

- Joining the normal robot queue and submitting the runner's already-confirmed
  name and email to the task's integration website and its existing BluPe
  queue/run backend for queue admission, run attribution and normal run
  notifications.
- Starting the selected method on the task's physical robots/arms and performing
  bounded actual arm movement within the existing run limits.
- Capturing and retaining camera recordings, checking live footage and
  replay/history, and using Stop and normal parking/lease release.
- Creating and publishing the integration's public source repository, committing
  and pushing its source, and deploying or updating its publicly hosted website.
  Use the normal repository and hosting visibility controls to make both public;
  the intended audience is anyone on the internet. Publish the demo's approach
  and configuration, runs/results, live demos, recordings, replays/history,
  simulation outputs and supporting evidence. Keep credentials, access tokens
  and private contact details out of public source and demo materials.

These actions are part of the invoked workflow. Do not ask for separate
permission for covered identity submission, runs, movement, recording, public
hosting or source publication. Public delivery is an authorized requirement of
the integration request; it does not require another publication confirmation.
Reuse confirmed identity/defaults; ask only for missing or ambiguous identity
or scope outside this contract. Keep execution mode separate from human
identity. The grant covers the task's integration and existing queue/run
destinations and the integration's public hosting/source destinations. Keep
private email out of public materials and do not send it to unrelated recipients.
Do not invent a person's name or email. Preserve any explicit user limits or
revocation and copy this contract into the integration's checklist.

### Make the invocation and scope visible before approval review

At task start, read the target repository's `AGENTS.md` and its integration copy
of this checklist, even when the chat starts outside the repository. Repository
and global `AGENTS.md` entries should route the workflow to this contract; keep
the permission grant here rather than adding an independent global permission.
A checklist link alone does not load the instructions. Verify that changed
global discovery instructions have loaded into the chat; start a fresh session
if needed.

Before a physical test, give a brief progress notice citing the user's harness
invocation and this contract. Name the website and queue/run backend origins,
robot/station and arms, method/task, run duration, expected motion,
recording/replay destination and confirmed identity fields being submitted.
State that the invocation covers these scoped side effects. This is a notice,
not a new confirmation step; proceed without waiting for a reply.

For an action requiring tool approval, repeat the invocation, contract and
concrete scope in the escalation justification or the tool's supported approval
context. Include the user request/selecting instruction and the contract's source
path/heading or commit URL with the applicable wording; do not supply only a link.
The reviewer sees a compact transcript and the exact request, so do not rely on
hidden reasoning, an earlier chat, or an unread repository file. Fill this example
with the actual scope before the first approval request:

```text
User invocation: <task request; adopted instruction selecting the harness, if used>.
Harness contract: <source path/heading or commit URL and applicable wording>.
That invocation authorizes this task's queue entry and confirmed runner
name/email submission, bounded physical motion, and camera recording/replay.
The integration request also authorizes its publicly hosted website and public
source repository; no separate run or publication confirmation is required.
Action: <UI control or supported API operation>; site <origin>;
queue/run backend <origin>; station/arms <IDs>; method/task <name>;
duration <seconds>; motion <bounded behavior>; recording/replay <destination>.
Identity: <confirmed fields submitted to those operational destinations>.
Execution mode: <mode>, separate from human identity.
Safeguards: existing queue/lease, readiness, validation, hardware limits, Stop
and normal parking/release remain enabled; no automatic timed-out-motion retry.
```

Use the normal playground UI or its supported run API with existing queue/lease,
readiness, command validation, hardware limits and Stop behavior intact. Keep
tests within the task's robots, method and existing run limits, respect other
users' active runs, and never automatically retry a timed-out movement. This
contract does not authorize unrelated robots or disabling safeguards.

The harness adds no authorization gate for covered BluPe arm runs or public
delivery. Enforced sandbox, managed-policy and tool permissions still apply;
use their normal approval mechanism with the existing owner authorization and
concrete action scope. If a platform restriction blocks work, report its exact
action and reason, keep unverified acceptance items unchecked, and continue
unaffected work. Attribute that restriction to the platform rather than this
harness; do not ask the user to grant the same covered permission again.

References: [Codex instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md#how-codex-discovers-guidance)
and [Auto-review context and denials](https://learn.chatgpt.com/docs/sandboxing/auto-review).


Invocation evidence: user requested “please use the harness” and connection through BluPe remote arms, then “Then lets continue”. Confirmed runner/defaults remain the same as prior physical website runs; execution mode is automated verification, separate from human identity.

## Completion requirements

- [x] A usable website exists, with an associated repository containing the
  website's source code. Record both links and the deployed source revision.
- [x] Everything in the playground demo is public by default, including the
  website, source repository, runs/results, live demos, recordings, replays/history,
  simulation outputs and supporting evidence. Any nonpublic exception reflects
  an explicit user instruction or visibility change, recorded below.
- [x] The website clearly names the actual approach/method being run. Use the
  executed method's name; Spotter is an example, not a shared hardcoded label.
  The **Model** field identifies the approach being tested, as specified below.
- [x] When a human joins, collect or confirm their name and email. Prefill known
  identity/default values where available and let the runner edit both fields
  before joining.
- [x] Run context includes the actual approach and confirmed runner identity;
  the website makes clear which method is running and whose run it is.
- [x] Policy/model tests initiated by a person retain that actual user's
  confirmed name as runner. Use available identity and editable name/email
  defaults; require the real name to be provided/confirmed if missing before
  testing or running. Do not substitute `Anonymous`, `Autonomous` or `Automated`
  for that person. Record execution mode (such as `automated test`) and actual
  approach separately from human identity. Genuinely unattended/system activity
  has an explicit system identity; never fabricate a person's name or email.
- [ ] A person can use the website to run the integrated paper's method on
  physical YAM robots/arms. Record a verified physical execution of that method.
- [x] The actual physical arms are visible live on the website while the method
  runs, so a visitor can see that it is executing on real hardware.
- [x] Immediately after a run finishes, the person can replay that just-completed
  physical YAM execution through the website, with retained footage showing the
  actual arms executing the integrated method.
- [x] The person can browse/select previous runs on the website and replay their
  retained physical arm footage, associated with the executed method and run.
  Mock animations do not satisfy either replay requirement.
- [x] If the integrated method executes anything in simulation, those executions
  are reviewable within the integration website and tied to the corresponding
  run. A reviewer can inspect what ran and its results using suitable
  replay/visualization and relevant outputs, as appropriate to the simulation.
  Clearly label simulation content to distinguish it from physical-arm footage.
  If nothing executes in simulation, record an explicit not-applicable
  explanation instead of claiming simulation review.
- [x] The website is intuitive: a new visitor, without coaching, can find how to
  run the method. Record the visitor path and starting conditions; site entry
  has no separate 15-second pass/fail deadline.
- [ ] **Mandatory for every run:** first observed actual method-driven physical
  YAM motion occurs both within **15 seconds after the startup move to Home
  completes** and within **30 seconds after the Run/Start click excluding
  evidenced queue-admission waiting**, using the boundaries below (including a
  documented readiness timestamp when Home is unnecessary). Homing/preparation
  motion does not count. Record full elapsed times, queue entry/admission,
  excluded queue seconds and the queue-adjusted Run time for every run; a sampled
  demonstration is insufficient. Apply only the documented GPU-launch or
  research-grounded exceptions below.
- [x] Every meaningful wait after a website action is immediately explained in
  a persistent inline status/progress area at that action (below Run in the run
  panel for launches). Show the current stage, a plain-language reason and real
  stage completion, with honest failure/stall status and available recovery.
  Verify the detailed wait-stage behavior below.
- [ ] Before marking the integration complete or reporting it done, open the
  deployed website and perform a final end-to-end physical YAM run through its
  normal user interface. Verify every completion requirement against that
  deployment and record evidence; unmet or unverified requirements block
  completion.

## Model identifies the tested approach

Set the harness's **Model** field to the actual research approach/method being
tested. For example, a HarnessVLA run uses `HarnessVLA` as its Model value.
Resolve this value from the approach that actually executes and carry it
consistently through the website's model label/selection, run context and stored
run records, queue/email notifications, and replay/history attribution.

Record underlying foundation, planner or VLA model IDs and checkpoint names as
separate configuration metadata. Keep confirmed human runner identity and
execution mode in their own fields too.

Verify a run's Model value against the executed approach and retain evidence of
the website label, stored run record, notification and replay/history attribution.

## Verify the visitor path

Record initial navigation (`t_entry`), the visitor's starting state and test
conditions, including browser/session/network, identity entry or confirmation,
queue and robot readiness. Keep the website usable without coaching. Site entry
is contextual evidence, not a separate timed pass/fail gate.

Keep a timed screen recording showing the visitor's actions, the Run click,
startup Home completion (or no-Home readiness) and live physical arms beginning
the integrated method, linked to the matching run. Correlate recorded UI/physical
events with lifecycle logs on a common timeline; document any clock offset or
measurement uncertainty. Backend timing alone cannot verify physical onset.

## Verify every Run and startup Home

For every run, record these fixed boundaries:

- `t_run`: the actual Run/Start click. Retain all subsequent wall-clock time,
  including queue waiting, preparation, coding, model loading and inference.
  Exclude only evidenced queue-admission waiting from the 30-second calculation
  below; do not restart the clock at admission, acknowledgement, dispatch, GPU
  readiness or a later status change.
- `t_home`: completion of that run's startup move to Home, using matched
  settled/ready completion for all participating arms, corroborated by live
  footage. Starting/submitting Home or reporting it accepted is insufficient.
  If no Home movement is necessary, record why and use the earliest documented
  per-run robot-ready confirmation after Run and normal queue/lease admission,
  before method preparation/execution. If already admitted and robot-ready at
  Run, use `t_home = t_run` with that readiness evidence. Readiness here concerns
  the robot, not GPU/model/code availability. Do not choose an arbitrary later
  event, repeat Home, or delay the readiness timestamp to reset this clock.
- `t_motion`: first observed actual physical arm motion executing the integrated
  method. Home and other preparation/parking motion do not count. An accepted or
  queued request, submitted packet, UI "running" status, inference start or
  backend-only timestamp is insufficient.

### Queue-admission wait in the 30-second budget

Record `t_queue` when this run's request actually enters the normal robot queue
and `t_admit` when that same request is first admitted/granted its robot
session/lease. Match queue/request and session/lease identifiers to the run and
retain actual lifecycle evidence. An accepted/queued submission is not admission.
Time between Start and queue entry still counts. Post-admission hardware
preparation, readiness checks, Home, coding, model loading/inference and other
setup still count; they must not be relabeled queue waiting. Later preparation
status changes or reacquisition cannot move the initial admission boundary.

Let `Q_run` be the duration of the evidenced queue-admission wait interval
`[t_queue, t_admit]` overlapping `[t_run, t_motion]`. Clip any queue waiting that
began before Start to `t_run`. If already admitted or there is no queue wait,
record `Q_run = 0` with matching admission/no-queue evidence. For a queued
request, missing entry or admission evidence makes the adjusted Run measure
unverified; do not assume zero or infer admission from Home, method readiness
or eventual motion.

Ordinary runs must satisfy **both** `t_motion - t_home <= 15 seconds` and
`t_motion - t_run - Q_run <= 30 seconds`. The queue exclusion is a normal part of
the 30-second budget, not a GPU or research exception; a full wall time over
30 seconds may still be a normal pass when the evidenced queue-adjusted time
meets its target. It does not change or pause the 15-second post-Home measure.

Retain full Run-to-motion and post-Home-to-motion times, excluded queue seconds
and queue-adjusted Run seconds even for failures and exceptions. Record every
run below (add rows), with visual evidence of Run and physical onset plus
matched Home/readiness and queue-admission evidence. Mark each target as pass,
missed target, unverified or exception. A normal pass requires the queue-adjusted
Run time and full post-Home time within their limits. Missing visual evidence or
an unmatched Home/readiness boundary leaves the affected timing unverified.

### GPU-launch exception

A run genuinely requiring new GPU launch/provisioning (for example on RunPod)
may claim an exception only for the evidenced time that launch blocks progress.
Show the **entire launch period** in persistent inline UI: actual launch start,
ongoing launch reason/stage and actual GPU-ready completion, with matching
provider/instance/job lifecycle evidence. If launch starts before Run, retain its
full start/ready interval and show its ongoing state when the run panel opens.
Warm/already available GPUs do not qualify. Record why a new GPU was necessary.

Keep the original clocks and full elapsed times. Record `t_gpu_start`,
`t_gpu_ready`, the full launch duration, and the evidenced GPU-launch blocking
seconds overlapping each measured interval (`B_run` and `B_home`). Only blocking
time inside both the actual launch interval and the relevant Run/Home-to-motion
interval can be excused; do not deduct concurrent time spent on unrelated work.
For the Run clock, also record `O_run`, the duration shared by the evidenced
queue-wait and GPU-blocking intervals inside `[t_run, t_motion]`. Exclude their
union, `Q_run + B_run - O_run`, never the overlapping seconds twice. For a GPU
exception, require `t_motion - t_run - Q_run - B_run + O_run <= 30 seconds` and
`t_motion - t_home - B_home <= 15 seconds`. Label a queue-adjusted Run target miss
or full post-Home target miss excused by GPU launch as **exception**, not normal
pass. Queue exclusion alone remains normal budget accounting. Keep raw queue and
GPU intervals, overlap, full timings and both adjusted calculations separately.
If GPU readiness precedes Home completion, `B_home = 0`: the full 15-second post-Home target still
applies. If Home completes during launch, only its evidenced remaining launch
blocking time can be excused; do not silently restart either clock at GPU ready.
Missing lifecycle or persistent UI evidence leaves the exception unverified.

Only the evidenced initial queue-admission interval is excluded from the
30-second budget; coding, model loading, inference, post-admission preparation
and other infrastructure delays outside actual GPU provisioning remain counted.
A warm GPU, a generic "loading" message or a slow model call is not a launch
exception.
Unrelated delays remain missed targets if they exceed the remaining limits.

Other exceptions require a specific justification grounded in the integrated
paper/research, explaining why it applies to that run and which target it affects,
plus a supporting source citation/link and relevant section/page. Slow coding,
loading, setup, post-admission delay or infrastructure delay is not in itself a
paper/research-grounded exemption; there is no permanent coding exemption.
Queue-admission waiting uses the normal exclusion above, not this mechanism.

| Run reference | Run/Start click (`t_run`) | Queue entry / admission (`t_queue` / `t_admit`), matching lifecycle evidence; or evidenced no-wait | Excluded queue seconds (`Q_run`) | Startup Home settled completion / no-Home readiness (`t_home`) and evidence | First observed method motion (`t_motion`) / visual evidence | Full Run-to-motion seconds | Queue-adjusted Run seconds | Full post-Home-to-motion seconds | Run / post-Home outcomes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | |

- Exception record (repeat for each affected run):
- Exception category (GPU launch or paper/research-grounded):
- Affected run reference and timing target(s):
- Specific reason and why it applies to this run:
- GPU-launch evidence (if applicable: provider, instance/job reference,
  `t_gpu_start`, `t_gpu_ready`, full launch seconds, lifecycle log/link and
  persistent inline UI evidence covering the entire launch period):
- GPU-launch blocking evidence (`B_run`, `B_home`), queue/GPU shared seconds
  (`O_run`), excluded union (`Q_run + B_run - O_run`), both adjusted calculations
  and separate outcome for each target:
- Supporting paper/research source (for other exceptions: citation/link and
  relevant section/page):

## Visible progress while waiting

Use a compact persistent inline status area directly below Run in the existing
run panel; keep other meaningful waits beside their initiating control. Show the
current stage and why it is taking time immediately, keep it readable throughout
the wait, and visibly mark completed stages as actual lifecycle state advances.
A transient toast alone or a generic spinner is insufficient.

While actually awaiting queue admission, show `Waiting for robot admission` (or
clear equivalent), with the real queue reason/state and entry timestamp. Retain
its completed admission/grant state and timestamp, then show actual hardware
preparation/Home/model stages separately. Keep queue and GPU launch stages
distinct when concurrent; do not leave the queue stage pending after admission
or label all startup as queue waiting. Retain matching per-run lifecycle evidence
for the queue exclusion; no invented queue positions, times or completion.

When a model requires a new GPU, show only the stages that actually exist:
`Starting GPU` / "We're launching a GPU on RunPod for this model" -> `GPU ready`
-> `Loading model` -> `Running`. GPU startup must be explicit from its actual
launch start and throughout the launch period (immediately after Run when Run
initiates it); visibly confirm actual GPU readiness before subsequent
loading/running and retain completed-stage confirmation with start/ready
timestamps tied to lifecycle evidence. Use the applicable provider/model wording.
On failure or a stalled stage, state the known condition honestly and show an
available recovery action, such as retry or cancel where supported. Do not invent
percentages, ETA or completion, or expose irrelevant infrastructure internals.
UI transparency alone grants no exemption; only the documented GPU-launch
blocking interval qualifies under the exception above, with neither clock reset.

## Final verification before completion

Open the deployed website and its associated source repository. Use the normal
visitor flow to join, run the integrated method on physical YAMs and watch the
actual arms live. Check the unchanged 15-second post-Home measure and the
30-second Run/Start-to-motion budget excluding evidenced queue-admission wait.
Keep fixed boundaries, full elapsed times, queue entry/admission/excluded seconds
and queue-adjusted time; verify any GPU-launch exception uses the queue/GPU
union without double subtraction. Check actual queue-to-admitted-to-preparation
inline stages; site entry remains context only. Check approach/runner identities,
replay of the just-completed run and selection/replay of previous runs. Review
every checklist item against current evidence, including public visibility and
actual user attribution in policy/model tests, with execution mode separate.
Check the website, repository and each demo artifact type as a logged-out visitor;
record working public links or the explicit user-directed visibility exception.
Verify that Model identifies the executed approach consistently in the website,
run records, notifications and replay/history, with underlying model/checkpoint
configuration recorded separately.
Verify wait status appears immediately, keeps a readable reason, and visibly
confirms real stage completion (GPU ready before subsequent loading/running).
Check failure/stall status and available recovery evidence too. Record the
tested deployment, run and evidence below.
Leave unmet or unverified items unchecked and report gaps and explicit exceptions
before any completion claim.

## Integration and evidence

- Paper / method (link and version):
- Final verification (reviewer/date, tested website URL and deployed revision,
  physical run reference):
- Final checklist review (evidence for every item, replay links/timings, unmet
  items and explicit exceptions):
- Actual approach name / website label evidence:
- Model-as-approach evidence (executed approach, website Model value, stored run
  record, queue/email notification and replay/history attribution; separate
  underlying model/checkpoint configuration):
- Human join evidence (name/email collection or confirmation, prefilled defaults
  and edits to both fields):
- Run context evidence (run ID, approach and runner identity; website attribution):
- User-initiated test evidence (run ID, confirmed human runner, actual approach,
  separate execution mode; editable defaults and missing-name confirmation):
- Unattended/system activity evidence (explicit system identity, execution mode
  and actual approach; no fabricated person):
- Wait-status evidence (action/run reference, immediate inline visibility,
  ongoing stage/reason and real completion transitions):
- GPU-startup UI evidence (startup shown after Run, actual readiness confirmed
  before loading/running, completed stages and matching lifecycle evidence):
- Failure/stall status and available recovery evidence:
- Website URL:
- Website source repository URL / deployed revision:
- Public-default evidence (logged-out access checks and links for website,
  repository, runs/results, live demos, recordings, replay/history, simulation
  outputs and supporting demo evidence; explain any artifact type not produced):
- User-directed visibility exceptions (write "none", or affected materials/scope,
  current visibility, explicit user instruction/change, date and reference):
- Physical-test authorization/scope (standing owner authorization or task
  reference, website, station/arms, confirmed runner, method/task, duration,
  expected motion and recording; permission-review result if applicable):
- Physical execution (date, method/configuration, YAM station/arm IDs, run ID):
- Live physical arm footage (website location and evidence link):
- Just-completed replay URL / immediate availability evidence (same physical run):
- Previous-run browser URL / selected prior run and replay evidence:
- Simulation review (corresponding run reference, website review link, what ran
  and its results, suitable replay/visualization/outputs, clear simulation label;
  or explicit not-applicable explanation if nothing executes in simulation):
- Per-run timing verification (tester/date, browser/session/network and robot
  readiness; evidence for every run in the timing table above):
- Visitor's starting state, `t_entry` and entry-to-motion steps (context only):
- `t_run` / `t_home` / `t_motion`, full Run-to-motion and post-Home-to-motion
  seconds, queue-adjusted Run seconds and separate target outcomes:
- Queue evidence (`t_queue` / `t_admit`, matched run/request/session/lease IDs,
  lifecycle links and persistent inline stages), excluded `Q_run` seconds; or
  evidenced already-admitted/no-wait with zero exclusion:
- Matched settled Home completion evidence for participating arms, or no-Home
  reason and earliest robot-ready timestamp/evidence (already ready: Run time):
- GPU launch start/ready/full interval and inline UI/lifecycle evidence, `B_run`
  / `B_home`, queue/GPU overlap `O_run`, excluded union, adjusted calculations and
  exception outcome if applicable (otherwise "none"):
- Timed screen recording / matching physical run evidence and clock correlation:
- Remaining gaps:


## Timing contract update — October 30, 2026

The maintained requirements above now use 15 seconds from settled startup Home
completion (or documented no-Home robot readiness) and 30 seconds from Run/Start
to first observed method-driven physical motion, excluding evidenced initial
queue-admission waiting from the 30-second budget only. Site entry is context
only. The historical evidence below retains its original 15-second entry/Run assessments;
those entries are not current gates or new verification. Existing runs are not
reclassified as passes: full visual onset and matched Home/readiness evidence
remain required, and no GPU/research exception is newly claimed. The recorded
107.56-second Run-to-packet-preparation and approximately 97 seconds described
as queue/readiness do not establish a queue-adjusted pass: exact queue admission,
visual onset and matched Home/readiness evidence remain necessary. Both current
timing measures are not newly certified by this contract edit. The owner-specific
per-Run reassessment above is preserved; post-Home and other-run qualification
remain separate. Historical assessments and their exact receipts are unchanged.

## RoboSkills evidence — October30,2026

Status: INCOMPLETE / physical retest and recording-publication approval required.

- Website: https://blupe-bimanual-skills.andlyu.chatgpt.site/
- Website source: integrations/roboskills/site in this public repository; Sites production revision will be recorded in its VERIFICATION.md. Shared provider: src/remote_yam/roboskills_policy.py; launcher: roboskills_playground.py.
- Executed approach: RoboSkills; underlying inference model:gpt-6-astra. The provider uses pinned upstream core.agent.run with a BluPe-specific tool adapter, not the complete SAM/RoboKit tool surface.
- Human attribution: actual Andrew Lyubovsky from existing account context, editable name/email in the deployed UI. Public API omits email/capabilities. Physical test453a9a037e874840ad4382597c25e8c1 / ep_4518061f91c24d0d records Model=RoboSkills and the actual runner; automated verification is separate execution mode.
- First physical test:60-second maximum,20mm paired fingertip lift/return with grippers unchanged, normal queue/lease/readiness/Stop. Packet submission was followed by a status-lifecycle KeyError; it was not retried. Read-only native harness reconciliation confirmed parked=true and torque_off_verified=true. A complete method-driven lift/return is unverified.
- Corrected provider:17 tests and8 subtests pass. Pending-packet status is exercised. The corrected physical UI retest was explicitly rejected by automatic approval review (specific second motion test authorization missing). Public upload of the local real-camera recording was separately rejected (public disclosure authorization missing). Neither action was rerouted.
- Live physical camera evidence: deployed /#physical-yam loads actual top/left/right YAM-1 cameras through fixed public Session API paths; public camera request200. Queue and hardware_not_initialized/disabled state were shown honestly.
- Replay/history: physical history selector and receipt work; no physical recording upload was performed after the rejection. Existing footage stays in the user's local review output, not this repository. Immediate and prior physical replay requirements remain unchecked.
- Simulation review: /#native-verification, reference native-lift-20261030. Public native receipt/task/replay retained. MuJoCo demo solved8/8, overall build FAILED(robot_at_rest=false;scene_in_frame=false); fresh solver not run.
- Wait state: deployed Run immediately shows submission, then real BluPe preparation/running state; receipt and Stop remain inline. No GPU provisioning is used. Failed test is retained, not fabricated as success.
-15-second entry and per-run method-motion targets: UNVERIFIED / unmet. No paper/GPU exception is claimed. First run's method completion failed; the corrected test is approval-blocked.
- Public scope: site, source, live camera monitoring, public run metadata and native simulation evidence. Private email and capabilities are excluded. Anonymous shared compute was rejected earlier and remains disabled. Physical recording publication awaits explicit approval after tool denial.
- Standing physical-test authorization from this canonical checklist was retained. Enforced tool review remains controlling; no bypass was attempted.
- Remaining gaps: corrected physical method execution, timed entry/Run-to-motion evidence, immediate and historical physical replay, and successful native build/frozen-task solve.

Final code/evidence update: public source PR164; final Sites source d272543c470daaeed3c3f8b56f9ee77efd494116. All mirrored source bytes match. Corrected provider17tests+8subtests passed0.81s. Final TypeScript/build passed; SQLite migration equivalence and Drizzle no-change checks passed. New detailed model-wait wording is implemented but its corrected physical lifecycle remains unverified because the retest was denied; that checkbox stays unchecked.


## October 30 follow-up: standing authorization applied

The owner reaffirmed that physical tests are covered by the harness and asked
to resolve the approval mismatch. The same normal website Run action was
re-submitted with that clarification and concrete bounded scope. Review allowed
it; no robot command or safeguard was bypassed.

- Deployed source remains `d272543c470daaeed3c3f8b56f9ee77efd494116`.
- Website run `249450342a5e4286b9d406d3e8738d34`, episode
  `ep_a59c7a3174ef44c7`, was created by the actual website Run button at
  `2026-10-31T03:37:20.120Z` (October 30, 8:37:20 PM Pacific).
- The upstream RoboSkills Python agent generated one checked paired lift packet.
  Measured left/right fingertip rises were 14.04 / 14.38 mm against a 20 mm
  request. Its 3 mm tolerance check failed, so it ended without a hold/return
  attempt. This is verified method-driven physical motion, not a successful
  lift-hold-return task. Task success remains unscored. No uncertain motion was
  retried.
- The controller accepted the packet and matched measured completion. Normal
  `policy_complete` Stop was accepted. The documented YAM-control-harness
  read-only status subsequently returned `DISABLED`, `parked=true`,
  `torque_off_verified=true`, with auto queue still enabled.
- Live actual-arm footage remained visible beside the run controls. The new
  physical replay appeared immediately after this run and played in the browser.
  Clearing and reselecting this retained run in physical history reloaded its
  recording. Anonymous replay access returned HTTP 206 for bytes 0-1023 of the
  570,939-byte MP4, with no authentication supplied.
- Public replay: [actual YAM footage](https://blupe-bimanual-skills.andlyu.chatgpt.site/api/physical/runs/249450342a5e4286b9d406d3e8738d34/assets/physical.mp4).
- Public receipt: [measured execution](https://blupe-bimanual-skills.andlyu.chatgpt.site/api/physical/runs/249450342a5e4286b9d406d3e8738d34/assets/result.json).
- Name/email defaults remained editable. The website, retained run and local
  queue submission journal name Andrew Lyubovsky; Model is RoboSkills, with
  gpt-6-astra separate. The remote public station feed nevertheless showed
  `API caller`. Cross-surface attribution is not fully qualified. No email or
  visitor/lease capability is published in this documentation.
- The website visibly transitioned from submitting to readiness/queue, model
  wait, and finished/replay. Completed-stage retention and all failure/recovery
  paths are not fully qualified; the wait-stage requirement stays unchecked.
- The Run-to-motion target was missed: the checked packet was prepared at
  `03:38:01.716Z`, already 41.6 seconds after Run, before dispatch/physical motion.
  Planning took 27.28 s including 21.56 s model time; hardware execution was
  0.79 s. These are measured controller timings, not a claimed visual
  first-motion timestamp. There was no GPU startup and no timing exception.
  Site-entry timing remains unqualified.
- Automatic review still denied publishing the older failed-test recording,
  citing missing trusted authorization for that specific existing recording.
  It remains local; no alternate upload route was used. The pending specific
  authorization request covers that recording. Public-by-default qualification
  remains unchecked because this enforced denial is not a user visibility
  exception.

| Run reference | Run click | First observed method motion / evidence | Elapsed | Outcome |
| --- | --- | --- | --- | --- |
| 249450342a5e4286b9d406d3e8738d34 | 03:37:20.120Z | Real footage and measured packet completion; precise visual onset not separately measured | Packet not prepared until +41.6 s; actual onset later | Missed 15 s target; partial physical task |

Remaining qualification gaps: all-public older recording, full identity
consistency, site-entry and every-Run latency, completed-stage/failure UX, and
full final checklist qualification. The native simulation build remains rejected
with the frozen-task solver not run. This is not a complete research integration.


## Final lift/hold/return verification

- Tested website source `e983572f99c93cf41f4999f397bfb16e556548f2` (version9),
  deployed2026-10-31T04:17:24Z. User invocation and authorization-by-use contract
  were surfaced with exact Site/local runner/backend origins, YAM-1 both arms,
  confirmed Andrew Lyubovsky/name-email defaults,60s run limit,20mm task and
  new footage retained on the same Site. Execution mode: automated verification.
- Run `2ea288ea830e471a86eb6d1d98971c50` completed the lift, hold and return.
  The model generated the Python program in one upstream agent turn. Measured
  rises17.80/20.97mm satisfy the existing3mm target tolerance. Lift Cartesian
  errors2.21/0.98mm; return2.14/0.08mm. Grippers unchanged within0.0001.
  The measured interval from lift completion to return packet preparation was
  1.74s, including the program's1s hold and fresh feedback/preparation.
- Four packets matched measured completion. The first three formed the lift
  with bounded accumulated tracking correction; the fourth returned. No
  rejected/failed/timed-out/uncertain packet was retried. Model`RoboSkills`,
  underlying inference`gpt-6-sol`, actual human runner`Andrew Lyubovsky`.
  Native physical success score remains unset; this is a measured primitive
  verification, not the upstream paper's benchmark success score.
- [Real recording](https://blupe-bimanual-skills.andlyu.chatgpt.site/api/physical/runs/2ea288ea830e471a86eb6d1d98971c50/assets/physical.mp4)
  appeared after completion, played and seeked through the website. Anonymous
  HTTP206/1024-byte range verified. Selecting previous retained physical runs
  was verified earlier; new and prior runs remain available in the same list.
- The website visibly retains completed queue/readiness, scene, model, motion
  and recording stages. The earlier accuracy-failed runs showed persistent
  exact measured failure reasons, retained footage and a new Run control. Stop
  was available during active execution. GPU startup not applicable: this
  subscription planner runs without provisioning a GPU.
- Native YAM-control-harness read-only status confirmed`parked=true`,
  `torque_off_verified=true`, mode`DISABLED`, automatic queue still enabled.
- Run click04:21:30.927Z; first packet prepared04:23:18.484Z (+107.56s).
  Queue/readiness consumed about97s; model6.31s, planning8.38s. This misses
  the15s requirement, with no GPU or paper-grounded exception. Exact visual
  first-motion time and new-visitor entry path remain unqualified.
- The earlier apparent handoff mismatch was not proven: the request later
  admitted normally. An unavailable station can reflect another active run.
  No other run was interrupted or readiness gate changed.
- Source-level tests: final focused22passed+8subtests/0.60s; broader shared
  suite1621passed,4skipped,254subtests/124.05s with CI-pinned ASPIRE+mink
  dependencies; Node358passed/0.695s. The additional accumulated-offset
  regression failed before its fix and passed after. Final CI records the
  complete current source. TypeScript and production build passed.
- The backend's legacy public conversation projection still labels the caller
  `API caller`; website, local submission journal and retained receipts use the
  actual confirmed runner. That cross-surface presentation issue is recorded,
  not hidden or claimed corrected.
- Automatic review still blocks uploading the original failed clip, citing
  missing trusted authorization for that specific existing recording. Its
  pending specific request is unchanged; no further upload attempt was made.
- An optional product question asks whether the owner explicitly waives the
  strict startup target for this version. Until answered, no waiver is assumed.
  Timing and full completion remain unchecked.


## Worker continuity recovery — October30,2026

- Interruption reconciliation found both task workers absent and both public readiness endpoints offline, with no active/queued Site runs. No interrupted run was replayed.
- Site version11/source `ca4ab9c843c49135e8845c87cbb00bf5cf6e42d8` deployed successfully at2026-10-31T04:41:14.680359Z, environment revision3. Separate `PHYSICAL_WORKER_TOKEN` and `SIMULATION_WORKER_TOKEN` isolate the two roles; existing operator-only control and public viewing remain. Secrets are not in source or output.
- `worker-agent/launch.py` starts a detached loopback provider plus configured workers, using stdin for secrets and persistent local logs/PID metadata. It refuses duplicate live services and occupied ports; it never kills, automatically restarts or replays a job. It requires this runner machine to remain awake; it is not an always-on hosting/reboot service.
- Provider/physical/simulation PIDs32026/32029/32030 remained alive after the launcher exited. Public Site `/api/config` returned online=true and `/api/physical/station` runnerOnline=true, queueCount0. This recovery verification initiates no Run/motion/recording.
- Two process regression tests pass (0.105s), including actual child survival after parent exit and duplicate rejection. TypeScript and production build pass. CI now also runs these launcher regressions.
- The final physical evidence remains run`2ea288ea830e471a86eb6d1d98971c50` against version9; version11's worker authentication/launch changes were verified without a duplicate motion test. Full checklist qualification remains incomplete: strict entry/per-Run15s timings, legacy backend caller-label consistency, original explicitly denied recording publication, and final all-requirements review. No startup waiver or visibility exception is inferred from “please proceed”.

Role authentication verified after deployment: each dedicated credential reaches its own worker handler (nonmutating unknown action404); cross-role requests reject401. No readiness heartbeat was fabricated and no run was claimed by these probes.


## Backend attribution correction — October30,2026

- The Session API validated but discarded `runner_name`, then hardcoded `API caller` in the public conversation projection. The three-line correction retains the validated name and projects it only when a conversation is already shared. Missing-name fallback is unchanged; email and capabilities remain excluded. The appended model field preserves positional constructor compatibility.
- The regression failed against the deployed image before the patch, then passed. Seven existing Session API/queue suites plus four new regressions pass51tests against both deployed-image and clean-main patch variants (7.628/7.676s). Source correction merged as20d9f95a5b104beabed5a426e509ca3a3d26b573. The unrelated private-repository MolmoAct2 CI job did not start because of account billing/spending limits; it ran no code. No required branch checks/reviews were configured or bypassed.
- Minimal runtime image57 overlays only the two tested source files on active image56. Source hashes and inherited environment/command/user/ports were verified; no other source or runtime settings changed. Deployment55 requested only after empty queue, no active Site run, and connected YAM-1 DISABLED were verified. It was observed ACTIVE at2026-10-31T05:10:42Z (activation upper bound228.613s). The exact existing containers/endpoint configuration was verified unchanged except the image.
- Both local task workers survived a chat turn and remained online. The managed source-worktree tool stalled with no Git process/worktree/artifact; its wait was cancelled. Supported GitHub tree/commit APIs published only the three source/test files without touching the user's dirty checkout/index. CLI JSON transport failures happened locally before any AWS deployment; the installed official AWS SDK validated parameters and submitted exactly one rollout.
- No new physical policy test or recording was initiated for this presentation correction. Earlier physical method/replay evidence is retained. A new real run is still needed to observe the corrected live caller projection, alongside the strict startup and final UI qualification; historical missed timings remain missed. Explicit denial for publishing the original failed clip is unchanged. All four unfinished checklist requirements remain unchecked.

Terminal verification: backend health200, both workers online, live top camera200 and retained successful replay206. Controller reconnect initially latched STOPPED/queue paused; the normal guarded native zero completed in3.925s, verified parked/torque-off, then documented auto_queue_on restored its prior setting. A final printing error after that queue action was reconciled read-only without resending it. Native status confirmed DISABLED, parked=true, torque_off_verified=true, auto_queue_enabled=true; public queue_ready=true/queue0. No research policy Run or new recording occurred. Fresh browser automation was unavailable in this turn (`browser.user is unavailable`); current UI end-to-end qualification stays unchecked.


## Final website recovery — October 30, 2026

The user resumed with “lets finish”. The former temporary source checkouts were missing and all three recorded worker processes had exited. No physical run was active in public history. Restored the exact hosted source and official RoboSkills revision into persistent task directories, preserving the existing run history. Scoped application worker credentials were rotated because their former in-memory values were unavailable; owner identity and public audience remain unchanged. No authentication bypass or anonymous shared execution was added.

The release separates recoverable connection errors from action failures, adds Refresh connection, exposes supported Operator sign-in, and distinguishes queue admission from arm preparation with retained observed lifecycle timestamps. TypeScript and six focused lifecycle-projection tests passed before packaging. Current harness adoption and timing text are reconciled with the maintained shared copy; historical results and explicit-denial records remain intact.

Browser sign-in returned to the platform-owned callback as HTTP404; the workspace remains anonymous. The final normal-UI physical run and post-deployment live runner-name qualification are not verified. No new test was submitted and no prior explicitly denied recording was published. The historical successful run and queue-exclusion evidence remain valid for their recorded scope. Current completion remains unchecked pending final UI qualification.

Website v12 source3226ea05cff35dfe8e80bb9e136011e614468776 deployed2026-10-31T05:56:34.743875Z, environment revision4. Final physical UI test remains unverified; operator browser is anonymous after callback404.


## Public physical flow verified — October 30, 2026

The owner explicitly rejected the operator sign-in requirement and confirmed “yes, remove it”. The physical form now accepts a confirmed human name/email directly and uses normal BluPe queue admission. Same-origin validation, a private browser Stop capability, worker-role authentication and the existing single-active-run constraint remain. Shared simulation authorization is unchanged. This user instruction supersedes the earlier physical anonymous-admission restriction for this exact flow; no auth bypass was used. The older recording-publication denial remains separate.

- Final deployed Site: version13, source`01bac7438e6284716b0f85fd2ce158f2f20ca744`, published2026-10-31T06:01:32.292573Z.
- Fresh anonymous visitor path: reload/#physical-yam → Live YAM arms → editable name/email → Run on physical YAMs. No operator sign-in. Entry is context only.
- Actual Run click bracket06:02:27.070–06:02:27.330Z. Run`b1d79ff0fe8944e8ab47342bdcffbad2`, episode`ep_a41fd6cf5ebd402e`, session`sess_b0e6374a10c54309`. Model/approach`RoboSkills`; underlying`gpt-6-sol`; actual runner`Andrew Lyubovsky`; execution mode automated verification. Name/email went only to the Site and existing BluPe backend; email/capabilities stay outside public evidence. The live backend public run now shows the actual runner and RoboSkills.
- The real20mm lift/1s hold/return completed: measured rises18.064/22.063mm; lift errors1.936/2.141mm; return errors2.789/0.974mm. All within3mm tolerance. Hold-to-return preparation2.027s includes the program's1s hold. Gripper changes<0.00015. Four matched packets, including two bounded feedback corrections; no uncertain/rejected/timed-out movement was retried. Native physical score remains unset.
- Actual arms visible live beside Run and Stop. UI retained queue entry, observed completed admission, preparation, scene, model, motion and recording stages with timestamps. Final status and prior accuracy failures retained honest result/recovery.
- [Just-completed real replay](https://blupe-bimanual-skills.andlyu.chatgpt.site/api/physical/runs/b1d79ff0fe8944e8ab47342bdcffbad2/assets/physical.mp4) appeared after completion and played through the normal website. Anonymous range request206, bytes0–1023/660400. Selecting and playing previous run2ea288ea830e471a86eb6d1d98971c50, then reselecting the new run, worked.
- Timing uses original Run, matched queued response/initial grant, first episode-matched step0 homed+settled observation for both arms, and unchanged camera frames41/42 with23:03:06.0/23:03:06.8PDT clocks. Independent robot/backend clock offsets were bounded with read-only request/response receipts. Count all submission, preparation and inference. Deduct only the guaranteed queue interval≥25.949s, retaining uncertain boundary time. Conservative upper bounds: full Run→visible method motion39.813s; queue-adjusted13.865s≤30s (also≤owner's earlier15s rule); post-Home13.134s≤15s. No GPU/paper exception. This run passes both current timing measures; historical runs are not silently reclassified.
- Native status after normal policy completion: DISABLED, parked=true, torque_off_verified=true, auto_queue_enabled=true. No other run stopped.
- Validation: seven route tests passed for public admission, confirmed identity, same-origin rejection, private Stop ownership, redaction, one active run and protected worker APIs; eight launcher/progress tests passed; TypeScript and production build passed.
- Local UI capture sequence, lifecycle observations and clock receipts retained under the task's work/final-ui-proof. UI images containing private email stay local. The public replay screenshot and original physical timing frames contain no contact details. Public verification receipt and images are in codex-runner/docs/roboskills/verification/2026-10-30-no-signin-*.

Final audit: website's current public physical flow is verified. Historical every-run timing is still not fully qualified and the explicitly denied older failed clip remains local; those acceptance items stay unchecked. The native simulation failure remains honestly reviewable rather than claimed as a solved benchmark.

The public verification folder also includes a screenshot-sampled UI recording (`2026-10-30-no-signin-ui.mp4`) with original inter-capture wall times and a full-height redaction band over the contact-input column. Sparse UI captures are paired with the continuous retained physical camera samples and matched lifecycle/clock receipts (`2026-10-30-no-signin-lifecycle.json`); the unredacted UI file remains local. Camera onset is measured from original physical frames, not interpolated UI frames.


## Every-run historical audit — October30,2026

[Full six-run matrix](verification/2026-10-30-historical-timing.md) and [structured receipts](verification/2026-10-30-all-runs-audit.json) recover the original Run-click markers/brackets and matched queue requests/grants. Two archived runs provably missed both current limits even after deducting the maximum possible queue duration:453a9a nonqueue≥30.720s/post-Home≥29.380s;249450 nonqueue≥30.551s/post-Home≥28.160s. Other old missing Home/clock evidence is retained as unverified; the current v13 run remains a normal pass. No retrospective clock assumption, GPU/paper waiver, new arm test or denied old-recording upload was used. The every-run and all-demo-public requirements remain unchecked pending explicit scope/exception decisions.

Owner acceptance decision: the user explicitly answered “Accept historical exceptions” for pre-version-13 timing failures and missing measurements. This is a release-specific owner instruction for the fixed five archived run IDs in the matrix; preserve their failed/unverified outcomes. Current v13 remains a normal verified pass and all future runs retain15s post-Home and30s queue-adjusted Run limits. The remaining original-recording publication question is unanswered; no upload is authorized by this timing exception.


## Final release acceptance — October30,2026

**15/15 checked for the current release.** The user accepted the fixed pre-version-13 timing failures/missing measurements as historical exceptions, and then explicitly dismissed the original failed clip as a release concern: “so why is this an issue? This just simply doesn't matter. All that we care about is that the method is reflected and that the 15 reqs pass”. Record that clip as outside the required archive; keep its local file and prior specific denial. No upload, new privacy claim, future timing waiver or physical-test confirmation is needed. Current method fidelity, genuine v13 physical execution, live/replay, simulation review, identity, source/public visibility and honest progress are verified. The current Site source remains01bac7438e6284716b0f85fd2ce158f2f20ca744. See the final15-row audit and original measurements in codex-runner/docs/roboskills/verification/2026-10-30-final-acceptance.md; no required work remains after source documentation is landed.


Current depth-fix audit: [2026-10-31-depth-fix-acceptance.md](verification/2026-10-31-depth-fix-acceptance.md),13/15 checked. Real full-sequence object execution is verified, not successful stacking; current task performance remains unqualified. Seven later historical timing cases require an explicit archive decision; no extension of the pre-v13 exception is inferred.


## Explicit vertical-grasp capability — October31 continuation

Current live v18/runtimef60495a remains the prior depth-enabled experiment. Its full sequence missed placement, and the final run timed out; no successful stack is claimed. This candidate changes command generation and reopens physical method qualification until matching normal-UI verification;12/15 currently checked. Existing archived evidence and pending seven-case timing decision remain unchanged.

Retained Home joints from runf4346b reconstruct tool Z `[0.86047,0.00201,-0.50949]`,59.37deg from base-Z-down, while the old API reports pitch/roll0 relative to Home and accepts only0. It therefore cannot express a vertical pinch. Native cube-planner code uses an explicit base-Z-down attitude. This is an API capability/frame mismatch; it does not prove tilt alone caused the complete observed placement offset.

RoboSkills now exposes `bot.move_to(..., attitude='base_vertical')` for an above-object Cartesian approach. Only named Cartesian arms adopt the declared base-Z-down reference; yaw0 has tool-X toward base-X-negative. Later moves retain that reference. Omitted attitude keeps the existing behavior, including ordinary lift/hold/return. Gripper-only alignment rejects locally. The status/configuration names each arm's reference and reports its actual measured-FK approach axis. No gravity-sensor or independently surveyed calibration claim is added.

The same native IK, joint/workspace/pacing limits, full packet validator,3mm position criterion,8mm correction cap, queue/lease/readiness/Stop and uncertain-motion latch remain. Untargeted solver poses and serialized joints remain measured. Invalid target or canonical-packet rejection rolls the requested reference back before dispatch.

Regression: actual sanitized Home fixture, both arms, constructor and station-binding paths through RobotTools→generator→canonical validator→HTTP JSON serializer. Original f60495a rejects the explicit request; earlier red pose checks retained59deg tilt. Final28dedicated tests pass2.792s;25broader IK/RoboCurve/workspace/model tests pass6.656s. Tests include post-alignment close preserving both measured joint sets, all<=0.035rad waypoint increments, invalid/gripper-only requests and canonical-rejection zero-dispatch/rollback. The earlier attempted implicit-all-moves change failed10existing cases and was replaced with this explicit API; no compatibility regression is hidden. CI and actual original-object scenario remain required before qualification.


## Local transfer planning and historical decision — October31 continuation

The user explicitly answered “Accept the seven historical timing exceptions” for exactly these post-v13 development cases: ec2ecd43555b4c20a5c7e359ddb369d7, f7b751b7a15240a8a71e5d88206104ea,899e4cee34eb48308989c58014956edc,2a97205b588f40b089ca4a420bc264f4,3e3c56a9d77d486086aa8e13b5d61d69,e3426b9a67e74877a6df5794c55d21a5,44ae94bf6daa4f3ba8a1470487faa070. This supersedes only the pending timing decision in earlier audit text. Preserve original failed/unverified measurements, the older fixed pre-v13 exceptions and all task failures. It grants no future timing waiver. Zero-admission cancelled540a remains N/A. The specifically denied original453a recording remains local/outside the required archive.

PR189 merged5a607b1; immutable70a8837 ran through the normal public v18 website as d3e9533c00af4883afe73c359d254442, genuine pinned RoboSkills, confirmed human attribution,300s limit. The first turn measured both blocks and requested explicit vertical approach. Actual right tool-Z was[-.00428,.00237,-.999988]. Descent, close and retained lift executed; high transfer requests were locally rejected before dispatch.106waypoints/8packets,11turns,146.627s native execution; task_success_reported=false. Replay played through normal UI. Normal parking/torque-off verified. No aligned supported release/stable stack was verified.

Camera frame26 at03:08:07.7PDT retains the prior pose; frame27 at03:08:08.7 is clearly moved. Using+0.1s display uncertainty, matched Home1793441276.392256 and current measured robot-host bounds[.001720,.007564]s gives conservative12.408s post-Home and26.488s full Run upper bounds. Full Run already passes30s with no queue deduction; observed admission receipts are retained separately. Both startup limits pass normally.

Offline reconstruction of the actual step106 reproduces rejection at the requested(.4296,.2145,.1320)m vertical transfer. With unchanged joint/workspace/IK/pacing/packet checks, the same XY atZ=.055m generates18waypoints; all tested yaws atZ>=.08m reject. This establishes a height-dependent reach limitation and an incomplete agent plan, not certified scene clearance or physical task success.

Candidate adds bot.plan_move with fresh same-lease settled feedback and a deep copy of the exact active geometry, including station-constrained limits, measured reference and MuJoCo state. It invokes the actual generator and canonical serializer/validator without dispatch, cursor/move consumption, attitude adoption or active-solver mutation. It reports local reachability only; move_to always revalidates fresh state. Guidance compares measured safe transfer heights and held-object bottom clearance; raising Z can reduce horizontal reach. No target coordinates are embedded in the agent prompt, no IK/limit/transport change, no motion retry. Actual task verification remains required;13/15 current items checked until then.


## Native calibrated joint contract — October31 continuation

The local planner candidate executed publicly as dc54f9d2ab40471796028d43d991cde3 on v18/immutablead8bd6a. It completed measured approach/descent/close/lift and the locally planned lower transfer. A subsequent alignment packet was rejected by the native joint_9_outside_limits guard; no uncertain packet replay.131submitted waypoints/9submitted packets (the final6-waypoint packet rejected),13turns,207.992s native execution. No supported release/stable stack; normal parking/torque-off verified. Website retains the actual native error and replay. Actual startup upper14.533s post-Home/27.420s full Run passes normally, with no queue deduction. Seven current measured startups now pass; the exact seven older timing exceptions remain accepted.

The native guard uses calibrated encoder bounds already present in the hash-pinned controller-workspace safety snapshot. RoboSkills applied that snapshot's XYZ bounds but omitted its joint_lower_rad/joint_upper_rad. Right joint4 (zero-based global9) therefore planned down to canonical-90deg instead of native-87.70256deg. The exact rejected six-waypoint packet reaches-89.89771deg. Fresh read-only SSH safety.json hash9a305c10a44e1093ebd21d138e992443263edb8dc5c06c3004c62371f2d3c69f matches the existing packaged snapshot. No controller safety file, calibration, FK offset or native range was changed.

Candidate binds the existing IK's intersection of canonical and saved native joint bounds when RoboSkills binds its station, including perception geometry rebuilds. Local planning copies retain them. A final command-space check includes every serialized waypoint and measured uninstructed arm.1e-10rad tolerance applies only to degree/radian conversion roundoff;1e-8rad genuine violations remain rejected. Complete canonical schema/range validation still occurs before the bridge. Exact recorded rejection is red under old code (passes canonical HTTP serialization), green as local zero-dispatch rejection; a new valid gripper request retains the unused cursor. Tests cover all12joint lower/upper endpoints, invalid neighbours, no additional FK offsets, held-arm rejection and copied station bounds. Method/task qualification remains reopened pending CI and matching normal-UI task verification;13/15 current checks.


## Camera read recovery after confirmed motion — October31 continuation

Native-bound source02a2205/PR191merged7431cd6 ran as cd7316b5a15847a49769a456c300c54d through the normal public v18 UI, selecting the isolated small loose purple block and the same central orange support. Source selection clarifies an actual purple block, retaining full pick/place acceptance; larger-block/cloth failures remain history.3matched packets/43waypoints completed, including descent to(.2572,.0571,.0185)m with1.555mm measured error. A subsequent observe() calibration GET raised RemoteDisconnected; the agent misclassified that read failure as uncertain movement and ended before closing. No native error or pending packet; no stack. Normal parking/torque-off verified.

Actual camera frame26 at03:38:16.6PDT retains the prior pose; frame28 at03:38:17.9 is clearly moved. Matched Home1793443086.3448489 and fresh robot-host bounds[.059508,.064860]s, plus0.1s clock-display uncertainty, yield11.655s post-Home/24.161s full Run upper bounds. Both startup limits pass with zero queue deduction. Eight current measured startups now pass; exact seven accepted historical exceptions and no future waiver remain unchanged.

Initial replacement launcher blocked reading iCloud Documents services.json and was interrupted before spawning children (trace retained). The task's new live logs/recordings now use local cache live-work-02a2205; provider21775/physical21780, simulation14674unchanged. This is setup delay, not robot queue or inference. Prior public history remains retained.

The calibration GET was outside ApiDepthSet's existing bounded camera recovery. Exact RemoteDisconnected regression fails old code at that read. Candidate routes transient calibration connection/timeouts and selected temporary HTTP failures through the existing10-attempt/60s whole-set camera recovery, with persistent retry/failure stage reasons.401/403/404 fail once without retry; cancellation/native motion failure remain terminal. No Start, motion, Stop or upload is retried. Snapshots remain invalidated on failure and fresh paired depth/calibration/settle checks remain unchanged.

RobotTools status now exposes concise last measured move/error and the terminal motion latch. Camera-only exceptions explicitly state observe sent no motion and never undo recorded completion. A later image cannot clear failed/uncertain motion. Regression covers whole-set fresh recovery with no actuator call, bounded exhaustion, denial non-retry, stale-view invalidation, native Stop/motion latch, and inline camera stage precedence over a prior local rejection.35dedicated tests3.664s and16worker tests0.105s pass. Broader camera/depth/workspace suite71tests/21subtests passes8.13s; its first sandbox attempt could not bind8loopback fixture servers (63other tests passed), rerun with normal loopback permission passed. Actual task verification/CI remain required;13/15 current checks, no successful stack.


## Explicit live-image object binding — October31,2026

Site19 source `4d76cfb3df3a4feceaef0ed07ce7e63458dc51df` adds source/target marks on the actual top-camera picture. Responsive pointer coordinates and accessible keyboard selection map to native image pixels, excluding viewer letterboxing. Both marks are required by default before queue submission; freeform tasks can turn marks off. Submitted instructions retain the exact reference pixels/image dimensions and require fresh-scene confirmation, depth measurement and re-localization after admission. Run history exposes that task text; successful submission clears marks. This supplies operator object binding, not a prewritten grasp or trajectory: the unchanged pinned RoboSkills agent still generates and verifies the entire physical manipulation.

Evidence: six coordinate/keyboard/input-binding regressions plus seven existing anonymous admission/Stop/privacy checks pass (13/13,320.53ms), TypeScript passes, and the production build passes. Normal local browser keyboard selection produced source421195/target330109. One retained-scene first-decision check using the exact new formatter and unchanged gpt-6-sol/none selected both pixels in6.134s; emitted Python was not executed. Earlier explicit-mark samples passed3/3, while unmarked variants selected the intended source3/6. These samples establish input binding only; general object identification and full stacking remain unqualified.

The already retained physical run `900123781dec40b49bb223e6c91a4283` timed out after grasp/lift/transfer, without verified supported release/stable stack. It also introduced a new startup miss: conservative first motion27.163s after matched Home; full Run40.800s with exact admission exclusion not established. That run is outside all accepted historical exceptions and reopens the every-run timing requirement. The seven exceptions explicitly accepted by the owner remain accepted and do not cover this new run. New Site19 full-task/timing verification is pending; no15/15 claim.


## Site19 final UI verification — current12/15, still open

See [the current full15-item audit](verification/2026-10-31-depth-fix-acceptance.md). Public source/target marks, actual task/history binding, anonymous admission/Stop, genuine agent, live cameras, retained replay and native safeguards work. Runfae84cbc passes both startup budgets and executes grasp/lift/transfer/alignment, but times out before release/withdrawal/stable stack. Items7and15 remain open. Item13 is reopened for900123 and2437, outside the exact seven owner-accepted exceptions. The earlier camera-run admission interval has now been recovered as10.291855s; its queue-adjusted Run interval is29.156–30.508s and its postHome26.163–27.163s still fails. No acceptance of these new cases is inferred.


## Candidate orientation and latency repair — not yet physically qualified

Later website run `e15b4276205b41f082f0c0303e1a21fe` independently requested the original purple-on-orange task and submitted zero packets. Both vertical approaches were locally rejected. Its measured source lies at right-base(.496568,.269295,.007984), beyond the tested vertical wrist reach. This is another unresolved no-motion startup/task case outside all accepted historical exceptions; it is not silently waived.

The RoboSkills adapter now exposes `rotation={arm: R}` in `move_to` and `plan_move`, with an explicit Cartesian target. R is an absolute3x3 proper rotation in that arm's base frame; its columns are jaw-opening X, side Y and approach Z. Numeric entries must be finite real numbers in[-1,1]; shape, orthogonality and determinant+1 are checked with1e-8 matrix-arithmetic tolerance, without normalization or clamping. Reflections, malformed matrices, incompatible relative rotation fields, gripper-only frame changes and invalid targets reject before dispatch. Frame changes roll back on local failure. The existing RoboCurve API and all native joint/workspace/pacing/gateway/Stop limits remain unchanged. Full native serialized packets preserve every inactive arm and gripper.

A new read-only `plan_approach(points_base_m, clearance=.05)` accepts the actual measured arm-frame point mapping. Clearance is metres along negative tool-Z, with tested endpoints[.01,.10] and strict invalid neighbours. It checks both supplied arms with0/35/45/55degree downward approaches and level jaw-opening axes through the actual generator/serializer. Paired arm coordinates must agree with the active calibrated base geometry. It returns feasible local candidates and specific rejections, never motion; failed samples do not establish that all poses are unreachable. Scene/finger clearance and grasp success remain unverified until the agent checks fresh views. The genuine upstream agent still generates grasp, transfer, supported release, withdrawal and outcome checks.

Explicit rotation commands additionally require measured FK orientation within2degrees, with the existing3mm Cartesian check. The2degree threshold is a provisional engineering completion tolerance, not an independent calibration/gravity accuracy claim or a relaxed native limit. Matched completion may use the existing bounded feedback correction; failed/uncertain movement stays terminal. Requested matrices and angular errors appear in receipts.

Red regression: old adapter rejects the new command and cannot expose the recorded tilted candidate (2failures/2errors, exit1). Green:13orientation/approach contracts cover both arms, native HTTP serialization, matrix extrema/invalid neighbours, measured angular drift and terminal latch, inactive-arm hold, read-only frame/cursor preservation, paired geometry and clearance bounds. Full affected gate:85tests+209subtests pass in6.29s, including existing RoboSkills/RGB-D/native workspace checks. The fixture pins actual calibration9773d518... and its published0.5m model base spacing; the native controller's conservative0.7m guard geometry remains separate and unchanged.

Latency evidence from one reconstructed recorded placement history: all24images/medium13.704,13.599s; newest3/medium9.732,18.105s gave no consistent benefit, so upstream image history is retained. Same24images/text withlow effort8.471,9.894s. The model remains gpt-6-sol, initialnone; subsequent effort changes tolow with matching configuration metadata. This small offline sample does not prove general quality or physical success. Two new first-turn samples on the actual later Home scene choose the correct source and destination and use the approach query in6.471/9.674s; emitted Python was not executed.

Candidate runtime deployment and the normal-UI full-task test are still required. Current physical stacking remains unqualified and the15-item review remains12/15; no completion or future timing waiver is claimed.


## Actual tilted approach result and bounded feedback repair — October31

PR195 merged as669abc9; all six push/PR gates passed, including actual execution of the new rotation contracts in the dedicated51test suite. Immutableebfaf69 became the live provider/physical worker; simulation and upstream agent.py remained unchanged. Normal Site19 run`63f7924023834731b5afce3e066a59a0` measured source31184/target330113 and chose the locally validated left35degree level-jaw approach. It submitted67waypoints in6matched completed packets, then terminated at5.876mm left position error (0.663degree rotation) against unchanged3mm/2degree completion checks. No grasp, lift or stack was performed. Normal native park/DISABLED/torque-off was independently verified. Matching32s replay played and sought through the website, with the actual failed result shown.

[Timing receipt](verification/2026-10-31-tilted-task-timing.json): first actual method movement21.737–22.737s after matched Home fails15s; full Run35.053–36.100s, initial queue10.540787s, adjusted24.512–25.559s passes30s. These are embedded camera wall clocks and measured clock correlation, not playback time. A [timed normal-UI capture](verification/2026-10-31-tilted-timed-ui.mp4) and [original cadence/hash manifest](verification/2026-10-31-tilted-ui-frames.json) retain88frames/102capture attempts over40.454s. Contact field was blank before/after each retained capture. The independent latere15b vertical-only refusal remains a zero-packet unsuccessful case; its Run-click time was not measured. Neither case is covered by the exact seven previously accepted timing exceptions.

The matched commands and canonical FK show the residual response changed branch after a direction reversal: commandX/Z decreased while measured errors increased (incremental gains−0.324/−0.410). The previous secant routine retained the original opposite-sign bracket and kept shrinking toward it despite the contradictory response. The candidate repair discards bracket history before a measured reversed or flat response, retaining the existing0.25mm command/0.1mm response deadband criteria and bounded half-residual step at that event. Future interpolation uses only the consistent suffix. Original3mm/2degree thresholds,8mm offsets, maximum6packets, native limits and terminal uncertain-motion latch remain unchanged. No failed packet was replayed.

A separate measured startup defect was eight sequential0.77–0.86s feedback reads during a read-only approach search. The candidate search now obtains one fresh settled snapshot for all candidates, runs the same generator/canonical validation on independent geometry copies, and still obtains fresh feedback before actual movement. No candidate, native check, frame or arm hold is removed.

New regressions fail on the old source (three failures: obsolete bracket, modeled reverse response terminal4.86mm, eight feedback reads) and pass after the repair. The reverse-travel continuation is explicitly a unit-slope bias model after the first three actual samples, not physical ground truth or a physical success claim. All older approach/deadband regressions pass. A simple half-residual-only alternative failed the older transfer model and was rejected. Affected gate:88tests+209subtests pass6.47s. Full suite initially could not collect five unrelated reset tests because the live minimal runtime lacks OpenCV; the pinned dependency is installed only in an isolated test directory for the full gate, without changing the live environment. Required CI, deployed repair and fresh normal-UI full stacking qualification remain pending. Review stays12/15, items7/13/15 open.

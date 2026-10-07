# ASPIRE task progress and skill lineage

The local ASPIRE station uses the ordinary prompt and Run flow. Its left panel keeps the task visible and shows a task work log, New skills added, Skills reused, and expandable Built from records. No execution-mode choice was added.

Current launch routing matches the complete prompt against the verified executable
registry before constructing a perception provider. A match reports “Program
found,” binds the exact source/inputs and enters normal queue/lease/Home without
pre-queue capture or planning. One fresh live harness pass measures state/RGB-D,
segments the scene, fully plans all native paths, then executes that plan in the
same pass. Candidate search is bounded to 64 native proposals or 30 seconds,
checked between calls. Exact repeated IK failures with the same initial state,
predicted stage seed and requested prefix are retained and skipped after two
rejections; RRT failures and successes are not cached. All candidate evidence is
saved, and a final complete native plan remains mandatory.

On the web, a saved-program failure before task motion hands the same robot session to the
ordinary Astra adapter with the exact failure and fresh observations. This is a
separate linked attempt; it does not rewrite the saved source or create a new
queue entry. Cancellation, controller faults and failures after motion do not
trigger that live handoff. Astra completion is a model report, with physical
success unknown until observed verification.

Locally, a recoverable saved-program code or planning failure finishes the normal
stop and enters ASPIRE Astra code diagnosis, offline validation and fresh
admission. Local execution never hands control to the ordinary Astra adapter.

After normal parking, an UNVERIFIED result can start a bounded offline code
repair. It supplies the latest actual source, failed or unknown checks, labeled parked images
and native evidence. The repair must diagnose placement versus observation or
metric uncertainty and validate the whole revised plan without task commands.
Each failed and revised source remains in history. Only a native-plan-validated
revision becomes an exact-task candidate, bound to the current base-core hash,
complete input configuration and immutable source hash. The original physical
outcome is preserved.
Each subsequent failed retry receives a new diagnosis and validated candidate
from its own evidence. Coding-request counts and the physical retry limit carry
forward from the original submission. A code fix must meaningfully change behavior or effective inputs/strategy. Identical or cosmetic edits are no progress. An explicit `action=rerun` response instead records `RERUN_VALIDATED` / `offline_rerun_diagnosis` with its evidence-based reason; it is a justified unchanged-code retry, not a new repair. It still needs fresh corrective planning and the original budgets. Without a justified candidate, recovery stops with its evidence blocker. Zero-command live planning
failures consume the coding budget without consuming a physical retry.

When station automatic recovery is enabled, Run settings expose an automatic
retry limit from 0 to 3, defaulting to 1. Zero disables automatic retries. The
browser remembers the setting per robot, and each submitted run records its
effective limit. Editing settings applies to the next submission and never
replays history. Each automatic retry checks fresh geometry and a complete
native corrective plan before ordinary queue/Home admission, then recaptures
and replans before task motion. After-parking verification determines physical
success. An observation-only repair or unreliable target stops before queue
admission and reports a concrete blocker. Stop, controller faults, changed
source bindings and exhausted limits prevent further retries. Durable attempt
links retain the exact source, preflight evidence and original physical outcome;
restarting the launcher never replays an interrupted recovery.
Read-only preflight accepts the station's normal stopped, queue-ready
`hardware_not_initialized` state with no emergency stop; ordinary controller
initialization and motion safety checks still apply after admission. Other
station faults stop preflight. An operator can explicitly recheck a resolved
station fault before admission, retaining the run's limit and each failed check.
Recovery uses the station harness's actual protocol: read-only `observe` writes
a new paired snapshot directory, and `plan` receives that directory, the exact
repair source and named query mapping. This snapshot is scoped to that check;
execution still captures and plans again after Home. Launcher argument errors,
other process failures and missing artifacts remain infrastructure failures,
separate from native solver rejection and unknown geometry. Each station
subprocess records its argument vector, exit status and raw log; historical
retry disclosures link those diagnostics without putting usage text in the
task-level answer.

The task-level answer distinguishes verified success, active recovery and an
unresolved outcome with its next action. Offline PLAN_VALIDATED is never shown
as physical success. A latest historical run with a validated repair can be
explicitly checked through its recovery button; browsing history does not start
recovery.

Shared routing and configured-policy entry points accept an explicit
`execution_environment='local'|'web'`, with `web` as the default. The local launcher
always passes `local`. Exact-task repair lookup is independent of template
grammar; source, full task, base core and configuration must match. A compatible
bound generic template also counts as a usable saved program.

Locally, an unmatched prompt generates ASPIRE Python and validates the complete
native plan before queue/Home. On the web, an unmatched prompt runs through the existing normal Astra robot adapter with
the full original request. The route notice explicitly names Astra and links
optional local ASPIRE program development. Provider configuration, public progress
and run history retain the actual route. Web launch selection does not generate ASPIRE code, and a web physical failure is reported without ASPIRE repair or retry. Local linked post-parking
repair and explicit development retain their own code-generation records and scope.

The work log preserves three summary items per update: what happened, what changed, and the next action or verification result. Updates come from policy activity and harness results. Detailed code and evidence remain expandable. A stable stage display shows elapsed queue/Home, capture, segmentation, native planning, execution and repair time, with candidate rejections and errors. Polling preserves the existing task log, scroll position and expanded source records.

Linked recovery and offline repair attempts also expose their own public activity
trace. It includes the provider's public summaries/status, explicit diagnosis and
lesson, and runner tool/validation activity as available. While the model has not
published a summary, the panel says it is waiting. Resumed coding revisions stream
these summaries too. This is separate from private wire captures and excludes raw
or encrypted reasoning, prompts, source payloads, images and tool arguments.
Attempt IDs, parent IDs and coding revision numbers keep attribution explicit;
child lineage cannot replace the original executed program's lineage. Sanitized
rows are durable under `recovery-traces/<attempt-id>.jsonl` and appear under the
same attempt in recorded history. Existing repair journals are read conservatively
within their own request window; ambiguous overlapping model work is omitted.
Incremental updates patch keyed rows and preserve disclosure and scroll state.

A new subscription coding response records retrieved recipe/program IDs and content-addressed versions, actual unchanged/adapted use with original and target source lines, and new behavior with its code lines. Missing lineage in older responses remains unknown. Exact recipe text matches and model-declared adaptations have different proof labels. Retrieval alone is not use, and source matches do not establish semantic dependence.

Authorship requires recorded transport or author evidence. The UI distinguishes Generated by Codex from Reusing code generated by Codex. Model selection alone does not establish authorship. Reuse refines or generates code with frozen models; it is not model-weight training. New skills remain pending until code creation is recorded. Planning and physical outcomes remain separate.

The localhost-only read endpoint `/api/aspire-lineage?robot_id=...` reads the configured skill library, manifest and recorded runs. It never starts a model, GPU worker, policy, queue entry or physical command. Artifact URLs are opaque keys registered from permitted evidence roots. Foreign hosts, cross-site requests, writes, unknown artifact IDs and paths outside those roots are rejected. Private evidence is not bundled into shared static JavaScript.

Historical executable proof is computed from the immutable bound program body and bound-program hash. A historical source link opens that run's recorded program, never a subsequently repaired shared core under an older hash. A changed or unavailable original path is labelled explicitly.

Recorded episodes distinguish original native status, automated after-parking checks and a scoped visual review. Red-chip visual retention remains visible alongside its failed centering and dimension checks. Development references are labelled as references; they are not silently promoted into exact ancestors. A saved program without an episode review remains a program record, with physical evidence unknown.

Implementation: `src/remote_yam/aspire_lineage.py`, `aspire_lineage_catalog.py`, `aspire_codex_policy.py`, `local_playground.py`, `playground.py`, and the shared `static/hosted.js` / `hosted.css`. The shared executable core and manifest remain intact; validated exact-task repairs are stored separately under `.repaired-programs`. The launcher retains its existing configuration and URL.

Validation: provenance/policy tests, local/public status regression tests, all Node UI tests, and the actual served browser panel. Browser checks include familiar/novel prompt previews, exact reuse, repaired-core history, old unknown-use history, loaded review images and immutable historical source hashing. Physical live-test results are recorded separately by the robot owner.

## Live verification and deployment state (2026-10-06)

Local UI runs now keep sparse camera evidence through the controller's normal stop and parking. The controller worker performs a read-only, fresh post-parking evaluation before transport cleanup, with the exact executed source and live task definition. No new lease, task command, recovery move or physical retry is created. The task log and images show the after-parking result separately from the original native result. Missing parking evidence or failed evaluator checks remain UNVERIFIED; release-time success alone does not promote a physically successful skill. Runs are retained under the configured skill directory's sibling `runs` directory so visitor cleanup does not delete the evidence.

The red-block/green-chip revision transport failure was reproduced with its saved request: 1,321,674 characters exceeded the CLI's 1,048,576-character turn-input limit. Coding requests now retain actual code, source citations and validation scope once, rather than recursively embedding previous retrievals and lineage. Full provenance stays in the recorded retrieval artifacts. Oversized requests get a specific error before CLI invocation, and a transport failure preserves the last native failure and source for the next human-submitted preparation. High reasoning and Standard inference remain unchanged.

The ordinary Run submission for “Stack two blocks.” displayed scene capture and saved-skill matching beneath the exact submitted prompt while preparing, before a robot session ID existed. The task log then recorded code generation with Codex. That first request failed at the subscription transport before queue admission; it sent zero task-motion commands. Physical repair and retry remain the robot owner’s responsibility. The captured submitting-client screenshot is `/Users/andrew/Projects/blupe-evals/outputs/aspire-stack-ui-20261006/preparation-submitted.jpg`.

A spectator polling the running launcher could receive an older terminal station record in place of the new local preparation, because preparation has no robot-run start timestamp. The backend now records the launch-request timestamp for precedence; the robot owner loaded this change during a verified idle launcher restart. The served frontend also protects spectators by showing the current preparing task and stating that detailed updates are available in the submitting client when that older record is received. Existing tabs must load the revised frontend before that fallback applies. The separately opened viewer was then observed showing the exact retry prompt, its real syntax-failure repair, fresh scene capture, skill matching, and code-generation summaries while preparing. No active preparation or robot execution was interrupted to apply these changes.


## Recovery verification (2026-10-06)

The green-block/green-towel run `aspire-059b4bc91f9d483ca839c494601799a2`
spent about 100 seconds in 185 native planner calls before task motion. Most
rejections were IK non-convergence, including repeated source-only prefixes;
this is not proof of geometric unreachability. Its parked result remains
UNVERIFIED with `finite_towel_support` and `fresh_cloth_surrounds_block` false.
The recorded final footprint was about 14% larger and shifted about 4 mm; one
surrounding-cloth sample was 33.77 mm away.

A local Codex repair ranks fitting measured towel patches by support margin,
skips destination-only retries after a source-only prefix failure, and tries the
previously admitted short-span grasp first. It preserves the evaluator checks.
Revision 1 failed its offline native budget after 64 candidates in 23.18 seconds.
Revision 2 passed the complete native plan with two candidates in 1.557 seconds
using recorded initial RGB-D and exact saved SAM 3 proposals with network access
disabled. These timings do not establish live timing or physical success. Both
sources, plans, diffs and the original outcome are retained in linked history.
A separate Astra repair process did not run for this historical case.

Validation: the full Python suite passed 1,114 tests and 216 subtests before the
final history/setup-error assertions; affected tests were rerun after those
edits. Focused UI checks passed. The complete Node suite has nine existing
fixture failures, reproduced against the unchanged code in those test regions.
No new robot task or motion was submitted to validate this change.


The read-only `/api/aspire-lineage?robot_id=...&prompt=...` preview calls the same
`resolve_api_depth_launch` method as Run. It returns the actual route, selected
source hash and provenance without constructing a provider or creating a queue
session. The green/towel next Run selects the complete second repair source
`7dd36572806bce2f7795b13d9740395e03a3dcd39c3b79e9befc4d77e19dec5c`,
including the original green-rod context input. Exact-task repair lineage checks
the whole program, while generic core lineage still checks its two-line binder.
An offline real-policy construction/preparation check accepted the same hash,
with `SAVED_EXECUTABLE_FRESH_PLAN_REQUIRED`, zero motion and no session. The final
loaded backend (PID 17172 at activation) confirmed this through the localhost
preview, alongside linked Astra recovery, offline repair and stage-progress
capabilities. Final affected verification passed 261 tests and 102 subtests.

## Public repair-trace verification (2026-10-06)

The black-block/green-chip run `aspire-d067e22b169145608412b19d706301cc`
already had five public model summaries in its runner journal. The ASPIRE panel
hid the generic conversation stream and did not render those events. Its offline
repair completed naturally in 240.02 seconds with PLAN_VALIDATED while this UI
work was underway. The original 13-command physical attempt and parked outcome
remain UNVERIFIED for `centered_over_chip`; offline validation is separate.

The loaded localhost backend now returns 30 public activity rows, including all
five summaries and the recorded diagnosis, beneath `astra-repair-1` following
`aspire-1`. Browser history displays them and preserves its expanded disclosure
and scrolled trace through polling. The existing exact black/chip repair hash
`c929012c0b3993a784391c1a795bc5e58ceaf436f4726dc12b14ee76d1f17ac3`
and green/towel repair hash are still selected by the actual read-only launch
resolver. Original/repaired sources, completion, review and coding-history file
hashes did not change. The same port/configuration was reloaded only after idle
UI, completed-repair and worker-process checks; PID 35550 was loaded at activation.
No robot task or motion was submitted for this verification.

Affected Python verification passed 266 tests and 100 subtests; the final trace,
recovery and history checks passed 26 tests and 11 subtests after the small journal
robustness adjustment. Focused UI verification passed 45 tests, including active
repair summaries, honest waiting, revision updates, historical attribution and
unchanged DOM/scroll/disclosures. Activation evidence and the browser screenshot
are saved in that run's `repair-trace-ui/` directory.

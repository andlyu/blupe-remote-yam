# Shared ASPIRE skills

This versioned library was recovered from the deployed Robo-house ASPIRE worker
on October 8, 2026. It contains **32 saved task programs**, **13 learned topic
patterns**, and the **pick-and-place executable**. Four saved programs retain
scoped historical `PHYSICAL_SUCCESS` labels; 28 are `FULL_PLAN_ONLY`. Those
labels describe the original runs, not a new execution or general robustness.

Start with [the ASPIRE quick start](../../docs/aspire/README.md). Give Codex your
current task and ask it to retrieve these skills, inspect the actual scene, and
adapt the code rather than transfer old poses or object dimensions.

- [Program index](programs.json): tasks, lessons, historical validation, and exact source hashes.
- [Topic recipes](patterns.json): extracted source lines, scopes, limits, and failure caveats.
- [Executable manifest](executable/pick_place.json): input-bound current pick-and-place core.
- [Export manifest](manifest.json): hashes for the distributed catalog and recovery provenance.

## Topic index

| Topic | Recipe or caveat | Evidence status |
| --- | --- | --- |
| grasp | Use the already-converted native grasp frame | observed_recipe |
| transport | Carry commanded jaw state into native sequence planning | observed_recipe |
| localize | Use paired metric depth and its captured transform | observed_recipe |
| localize | Use concise SAM3 instance descriptions | observed_recipe |
| localize | Separate instance depth from the upward layer | observed_recipe |
| grasp | Keep unsupported contact-height checks unresolved | failure |
| localize | Measure XY pose from the current instance footprint | observed_recipe |
| transport | Choose visible finite fabric clear of occupied objects | failure, observed_recipe |
| grasp | Try both measured grasp spans after native carry failure | observed_recipe |
| transport | Search measured chip poses clear of observed context | observed_recipe |
| grasp | Retain chip placement pose and dimension mismatch | failure |
| transport | Search bounded chip offsets and yaw with measured obstacle checks | failure, provisional |
| transport | Keep collision-invalid transfer goals rejected despite small IK residuals | failure |

## Reuse and learning

Configured ASPIRE coding runs retrieve this catalog alongside their persistent
local libraries. Local entries with the same pattern identity take precedence.
The executable is selected only for supported complete task requests and always
uses fresh measurements and native full planning. Historical programs are coding
context; loading the catalog does not execute them or contact the robot.

Keep writable local program/topic libraries outside disposable outputs. A run
can stage findings and promote scoped lessons locally; publishing an updated
catalog is a separate reviewed repository change. No run pushes to GitHub.

## Provenance

The complete recovery archive is retained privately. Before export, the recovered
topic library was checked against its original promotion ledger, finding hashes,
patches, and before/after snapshots. Every published snippet was checked against
its retained original source and line range. The public metadata preserves
evidence roles and limits, with record hashes in place of private run paths,
conversation identifiers, and native diagnostic dumps. Original development
recordings are not bundled.

Sources are historical snapshots, including any task-specific assumptions.
Failures and provisional recipes stay labelled as such. Contact geometry,
calibrated dimensions, and cross-task reliability remain limited by each entry.

The integration uses NVIDIA ASPIRE at the revision recorded in the manifest.
NVIDIA ASPIRE is copyright NVIDIA Corporation and affiliates and licensed under
[Apache 2.0](ASPIRE-LICENSE). Original upstream callable skills remain in the
separate pinned ASPIRE checkout; this directory contains our deployed task code
and extracted development lessons.

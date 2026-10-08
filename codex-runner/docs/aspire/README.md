# Try ASPIRE with BluPe

ASPIRE writes and reuses Python robot programs that execute through the YAM
Session API. Use Codex locally to develop skills and programs, then run them
through the local UI or API launcher in this repository.

For the arms, cameras, grippers, coordinate frames, and calibration sources,
see [Robo-house hardware and station setup](SETUP.md).

## How it works

1. **View the scene.** Look at the playground's camera views, or access camera
   images through the YAM Session API. Choose the objects and task from what is
   actually visible.
2. **Ask Codex to write the program using ASPIRE skills.** Give Codex the task,
   constraints, and success criteria. Ask it to read the [shared skill library](../../skills/aspire/README.md),
   reuse compatible programs and recipes, and retain their validation limits.
   The library includes 32 historical programs, 13 learned patterns, and an
   input-bound pick-and-place executable. Configured ASPIRE coding runs retrieve
   these alongside locally learned skills.
3. **Execute the program.** Use your configured station's local UI to run the
   task and watch it live, or launch through the [API runner](API.md) from Codex and review
   the recordings afterward. The UI selects registered programs or invokes its
   coding loop; a Python file written separately is not automatically registered.
   Both routes use the existing runner/controller, fresh measurements, and
   complete native planning before task motion.
4. **Iterate and save reusable skills.** Ask Codex to inspect the outcome, logs,
   and recorded videos, diagnose failures, and refine the program. Run the revision
   and check the outcome again. Save validated, reusable lessons so later tasks
   build on the evidence. Planning success alone does not establish physical success.

ASPIRE skill development is a local option. The public playground does not
offer an ASPIRE model chooser for developing your own programs.

## What happens

The coding runner combines the versioned shared catalog with your writable
local program and topic libraries. Current local entries take precedence over
matching published recipes. The runner then uses the station's ASPIRE harness
to measure the scene, generate or reuse code, and validate native plans. The
existing controller owns queue admission, robot commands, Stop, and parking.

The shared catalog contains source hashes, extracted snippets, historical
validation labels, and unresolved failure caveats. It does not contain recorded
trajectories to replay or a claim that an old scene is still valid. New learning
belongs in a persistent local directory outside disposable run outputs. Review
and publish those changes deliberately; a hardware run does not push to GitHub.

## Launch the local UI

You need Python 3, Git, Node.js/npm, [uv](https://docs.astral.sh/uv/), and a
Codex subscription with Astra access. From the BluPe Remote YAM repository root:

```sh
./setup-aspire.sh
./run-aspire.sh
```

Run setup once, and sign in through your browser only if Codex isn’t already signed in.

## Code structure

```text
setup-aspire.sh / run-aspire.sh  Setup and local UI entry points
codex-runner/aspire/            Native launcher, scene capture, program contract
  assets/linear_4310/           Licensed installed gripper model and meshes
codex-runner/src/remote_yam/    API bridge, planning, coding, learning, retrieval
codex-runner/skills/aspire/     Published programs, patterns, executable, provenance
codex-runner/docs/aspire/       Quick start and station hardware setup
```

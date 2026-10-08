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
   task and watch it live, or launch through its API runner from Codex and review
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

Setup fetches the clean pinned NVIDIA ASPIRE source, installs its API-backed
Python runtime, verifies the planner and bundled gripper assets, and writes
`~/.config/blupe/aspire.json`. Programs and learned topics are saved under
`~/.local/share/blupe/aspire/skills/`, separately from run recordings. Setup
does not access the robot. It preserves an existing configuration.

Complete the browser login if requested, open the printed localhost address,
select the local ASPIRE option, then enter the task. Subscription use needs no
OpenAI API key. Add `--port 8792` to the launch command if the default is occupied.
Use `./run-aspire.sh --config /path/to/station.json` for another configured station.
The default is Robo-house with Astra perception; RunPod SAM3 is optional and
requires your own endpoint and credentials.

## Code structure

```text
setup-aspire.sh / run-aspire.sh  Setup and local UI entry points
codex-runner/aspire/            Native launcher, scene capture, program contract
  assets/linear_4310/           Licensed installed gripper model and meshes
codex-runner/src/remote_yam/    API bridge, planning, coding, learning, retrieval
codex-runner/skills/aspire/     Published programs, patterns, executable, provenance
codex-runner/docs/aspire/       Quick start and station hardware setup
```

## Run from Codex through the API

Use the same runner without the UI. First generate and validate a program from
a fresh scene; it can reuse the shared catalog or your local skills:

```sh
~/.local/share/blupe/aspire/venv/bin/python codex-runner/aspire/run_prompt.py \
  --prompt 'Pick up the green block and place it on the blue poker chip.' \
  --plan-only --output /path/to/new-plan-run
```

To use code you wrote in Codex, supply `--program-response /path/to/response.json`.
The response must follow the [program contract](../../aspire/CODE-GENERATION.md)
and the runner's [program response schema](../../src/remote_yam/aspire_codex_policy.py),
including actual Python source and declared perception queries.

Execute a passing plan through the normal queue with the same prompt,
`--execute --feedback /path/to/new-plan-run/receipt.json`, and a new output
directory. Execution measures the live scene and replans; the saved receipt
does not replay old trajectories. Review the receipt, recorded videos, and
post-parking outcome, then iterate with feedback. `--config` selects a different
station configuration.

Check setup without contacting hardware with `./setup-aspire.sh --check`.
To inspect the published library without installing the native runtime:

```sh
cd codex-runner
PYTHONPATH=src python3 -m remote_yam.aspire_published_skills --query 'pick and place'
```

The bundled executable is enabled by default. An explicit
`executable_skills.manifest` selects your own executable; `published_skills: false`
disables bundled coding context. Explicit upstream-baseline runs exclude
accumulated learning.

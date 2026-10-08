# Run ASPIRE through the API

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

Return to the [ASPIRE quick start](README.md).

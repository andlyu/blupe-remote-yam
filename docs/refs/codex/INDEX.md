# Codex runner integration

Verified 2026-09-13 against `codex-cli 0.154.0` (Apache-2.0).
The system 0.149.0 CLI was rejected by the service for `gpt-6-astra`:
the model requires a newer Codex version. The runner installs pinned 0.154.0
locally when needed and leaves the global CLI unchanged. Release reference:
https://learn.chatgpt.com/docs/changelog (2026-09-09, GPT-6-Astra support).

The local robot runner uses `codex exec` and explicit `exec resume <thread-id>`.
This is the CLI transport used for the integration, with no Python SDK
dependency. Authentication stays with Codex; the runner never reads auth.json.

- Subscription login, JSONL events, schema output and resume: [noninteractive.md](noninteractive.md).
- Installed flag names, images on resume: [cli-help.txt](cli-help.txt).
- Config: https://developers.openai.com/codex/config-reference/ (`forced_login_method`,
  `features.shell_tool`, `features.multi_agent`, `web_search`, `approval_policy`).
- SDK alternative: https://developers.openai.com/codex/sdk/.

Each robot decision is a schema-constrained final response, translated locally
to the existing RoboCurve move_to/done/give_up contract. It is not a native Codex
tool invocation. Only the latest camera frames are attached on each turn;
the explicit resumed conversation receives measured movement results.

Use `--ignore-user-config`, read-only sandbox, disabled shell/apps/hooks/multi-agent,
and forced ChatGPT authentication. Strip API-key environment overrides. Missing
login, timeout, cancellation or malformed output must never fall back to API billing.
Run from an isolated temporary directory so repo instructions do not alter policy.

Live smoke check: Astra correctly identified top=red, left=green, right=blue
synthetic images using the existing ChatGPT login. No API key or hardware used.

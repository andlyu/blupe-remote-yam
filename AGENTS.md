# BluPe launch instructions

When the user asks to run BluPe with a Codex or ChatGPT subscription:

- Run `./run-codex.sh` from this repository. It installs the local dependencies,
  signs into Codex when needed, and connects to the public BluPe queue.
- Do not request an OpenAI API key for subscription use.
- Let the user complete the OpenAI browser login. Do not copy credentials.
- Codex needs write access to `CODEX_HOME` (normally `~/.codex`) for session
  files, even when login status succeeds. If your sandbox denies this, request
  write access using its normal approval flow or have the user launch from their
  own terminal. Do not work around it by copying credentials or changing ownership.
- Keep the launcher process running and give the user its localhost URL.
- If the port is occupied, use `./run-codex.sh --port 8792` (or another free port).
- Launching opens the playground; the user enters a prompt and clicks Run.
- Hardware command submission is enabled by default. Preserve the existing
  command validation and robot gateway safety checks.

Python 3 and Node.js/npm must be installed. The account needs Astra access.
The subscription implementation lives in `codex-runner/`; the root `run.sh`
is the original API-key runner.

# Code ownership

Shared runner/UI/model work belongs in this repository's codex-runner directory.
BluPe-only hosted billing, analytics, moderation and deployment belong in
blupe-evals/apps/blupe_web. Do not reintroduce an editable bundled copy there.
See docs/REPOSITORY-OWNERSHIP.md. Claude users launch ./run-claude.sh and complete
their own account login; never request an API key for subscription use.

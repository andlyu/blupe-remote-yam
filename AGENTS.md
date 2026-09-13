# BluPe launch instructions

When the user asks to run BluPe with a Codex or ChatGPT subscription:

- Run `./run-codex.sh` from this repository. It installs the local dependencies,
  signs into Codex when needed, and connects to the public BluPe queue.
- Do not request an OpenAI API key for subscription use.
- Let the user complete the OpenAI browser login. Do not copy credentials.
- Keep the launcher process running and give the user its localhost URL.
- If the port is occupied, use `./run-codex.sh --port 8792` (or another free port).
- Launching opens the playground; the user enters a prompt and clicks Run.
- Hardware command submission is enabled by default. Preserve the existing
  command validation and robot gateway safety checks.

Python 3 and Node.js/npm must be installed. The account needs Astra access.
The subscription implementation lives in `codex-runner/`; the root `run.sh`
is the original API-key runner.

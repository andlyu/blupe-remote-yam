# Repository ownership

`andlyu/blupe-remote-yam` is the source of truth for the shared runner, general
playground UI, model adapters, session/queue handling and camera display. The
current playground is in `codex-runner/`; the root API-key runner remains legacy.

`andlyu/blupe-evals` owns BluPe's hosted application in `apps/blupe_web`: payment
processing, analytics configuration, moderation, partnership pages and deployment.
It consumes this repository as a Git submodule pinned to a reviewed commit.

## Changing shared behavior

1. Work on a branch in this repository, including when opened as a submodule.
2. Run the Python tests in `codex-runner/tests` and `node --test codex-runner/tests/*.cjs`.
3. Review and merge here first.
4. In blupe-evals, check out that merged commit in `public-projects/blupe-remote-yam`,
   run hosted integration tests, and commit the updated submodule pointer.

Never copy a runner directory back into blupe-evals. Its `public-projects/Remote-yam`
path is a compatibility symlink into this repository, not another source tree.

## Hosted extensions

`playground.HostedRunner` is the shared ASGI application. Despite its historical
class name, it contains no Stripe implementation or production analytics script.
Applications may override `page`, `public_extension`, `session_extension`,
`action_extension`, and `validate_launch`. Public extensions receive validated
Host but must implement their own endpoint authentication. Action extensions run
only after visitor, Origin, JSON and CSRF checks. Keep robot admission and motion
validation in the shared runner/gateway.

The shared HTML has an `application-head` marker. An application can load a
script defining optional `window.yamApplication` hooks for provider presentation,
submission, session setup and robot-specific navigation, without forking the UI.
No application script is loaded by the standalone local runner.

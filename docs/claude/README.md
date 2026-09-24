# Set up Claude for the BluPe playground

Use your Claude subscription to run Opus through Claude Code on this computer.
The playground requires Claude Code **2.1.263 or newer** and access to
`claude-opus-5-5`. It uses your Claude account login, not an Anthropic API key.

## Install or update

Check the installed version:

```sh
claude --version
```

If Claude Code is missing, follow the [official installation guide](https://code.claude.com/docs/en/setup).
If it is outdated, update it:

```sh
claude update
```

The playground finds `claude` on `PATH`, in `~/.local/bin`, or in
`~/.npm-global/bin`. If your terminal cannot find it but it is installed in one
of those locations, use its full path for these commands.

## Connect your subscription

```sh
claude auth login
```

Complete the browser sign-in yourself and select your Claude subscription
account. OAuth is the credential created by this sign-in; it does not by itself
mean API billing. The runner requires the `claude.ai` login method and excludes
API-key environment variables from its Claude subprocess.

`claude auth status` checks the saved login. It can report signed in even when
the access token has expired. If the playground reports an expired or rejected
subscription login, run `claude auth login` again even if status says logged in.
The `./run-claude.sh` launcher can skip sign-in when it finds a saved login, so
use the explicit login command to reconnect an expired session.

## Launch and verify

From the repository root:

```sh
./run-playground.sh
```

Keep an already-running playground open, or restart it if needed. Refresh its
browser page, select **Claude subscription · Opus** in Settings, and click
**Check Claude connection**. If a setup prompt is already visible, use
**Check again** instead.

The check sends a small live request through your subscription to verify
authentication and model access. It consumes a small amount of subscription
usage and never joins the robot queue or sends motion commands. Page loading
only checks local setup. Run performs live verification again before queue
admission; authentication and available usage can still change later.

For an explicit Claude launch after setup, use `./run-claude.sh`. If port 8787
is occupied, use `./run-playground.sh --port 8792`.

## Troubleshooting

- **Update required:** run `claude update`. The runner needs the restricted-mode
  and noninteractive permission flags supplied by the supported CLI version.
- **Subscription login expired or rejected:** run `claude auth login`, complete
  sign-in, and check again.
- **API-key login:** sign out with `claude auth logout`, then sign in with your
  Claude subscription using `claude auth login`.
- **Usage limit reached:** wait for the subscription limit to reset, then check
  again. Reinstalling or signing in again does not reset usage limits.
- **Local storage or Keychain access denied:** launch from your normal terminal,
  or grant the launcher the required access through its permission flow.
- **Live verification still fails:** check network access and whether your
  account can use the requested model. Keep the exact model; do not silently
  substitute another one.

## For a coding assistant following the setup prompt

Inspect this repository's launch instructions and the installed CLI before
changing anything. Install or update Claude Code as needed, let the user complete
browser sign-in, and verify the live subscription connection. Never request an
API key, copy credentials, or bypass filesystem permissions. Leave the playground
running, but do not join the robot queue or send motion commands as part of setup.

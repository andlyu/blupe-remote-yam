"""Subscription-backed Claude Code CLI transport for the existing RoboCurve policy.

Protocol/flags verified with claude 2.1.263; `claude --help` is the reference.
Claude produces structured decisions; only the runner executes robot commands.

Mirrors codex_policy.py. Two transport differences from Codex are deliberate:
  * Claude Code has no `--image` flag, so camera frames are written into the
    per-run workspace and read back with the Read tool. Read is the only tool
    granted, `--restricted` confines it to that workspace, and any permission
    denial fails the turn rather than letting a blind decision reach the arms.
  * The model is pinned, never defaulted. Claude Code prints
    `unrecognized_model` and silently falls back, which would mean an eval run
    reporting a model it did not use.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

from .codex_policy import DECISION_SCHEMA, decision_response as _shared_decision_response
from .robocurve_policy import RoboCurveResponsesAdapter

DEFAULT_MODEL = 'claude-opus-5-5'
MIN_VERSION = (2, 1, 0)
INSTALL_HELP = ('Install Claude Code (https://claude.com/claude-code) so the `claude` command is on PATH, '
                'then retry ./run.sh --provider claude.')
LOGIN_HELP = 'Run ./run.sh --claude-login --provider claude in the launch terminal.'
API_KEY_HELP = ('Claude Code is authenticated with an Anthropic API key, which bills per token instead of '
                'using your Claude subscription. Run `claude auth logout` then ./run.sh --claude-login '
                '--provider claude to sign in with your Claude account.')


def claude_binary():
    binary = shutil.which('claude')
    if binary:
        return binary
    # Desktop launchers may not inherit the user's shell PATH.
    for directory in ('.local/bin', '.npm-global/bin'):
        binary = shutil.which(str(Path.home() / directory / 'claude'))
        if binary:
            return binary
    return None


def claude_environment():
    """Do not pass runner API keys, lease capabilities or parent-agent overrides.

    ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are withheld on purpose: either
    one silently overrides the subscription login and bills the account's API
    credit instead, which is the opposite of what this provider exists for.
    """
    allowed = {'HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR', 'TEMP', 'TMP',
               'SYSTEMROOT', 'WINDIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
               'XDG_CACHE_HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               'NODE_EXTRA_CA_CERTS', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY',
               'NO_PROXY', 'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'}
    return {key: value for key, value in os.environ.items() if key in allowed}


def claude_status():
    """Only return setup state, never the account email, org or raw auth output."""
    binary = claude_binary()
    if not binary:
        return {'ready': False, 'state': 'missing', 'message': INSTALL_HELP}
    try:
        version = subprocess.run([binary, '--version'], capture_output=True, text=True,
                                 timeout=10, env=claude_environment())
        match = re.search(r'(\d+)\.(\d+)\.(\d+)', version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < MIN_VERSION:
            return {'ready': False, 'state': 'upgrade_required', 'message': INSTALL_HELP}
        auth = subprocess.run([binary, 'auth', 'status'], capture_output=True,
                              text=True, timeout=15, env=claude_environment())
        try:
            report = json.loads(auth.stdout)
        except ValueError:
            report = {}
        if auth.returncode or not report.get('loggedIn'):
            return {'ready': False, 'state': 'login_required', 'version': '.'.join(match.groups()),
                    'message': 'Claude Code is not signed in. ' + LOGIN_HELP}
        if report.get('authMethod') != 'claude.ai':
            return {'ready': False, 'state': 'api_key_login', 'version': '.'.join(match.groups()),
                    'message': API_KEY_HELP}
        plan = report.get('subscriptionType')
        return {'ready': True, 'state': 'ready', 'version': '.'.join(match.groups()),
                'plan': plan if plan in ('pro', 'max', 'team', 'enterprise') else None,
                'message': 'Signed in with your Claude account. Uses your Claude subscription limits.'}
    except (OSError, subprocess.SubprocessError):
        return {'ready': False, 'state': 'unavailable',
                'message': 'Could not check Claude Code. Run `claude auth status` in the launch terminal.'}


def login_claude():
    binary = claude_binary()
    if not binary:
        raise RuntimeError(INSTALL_HELP)
    setup = claude_status()
    if setup['ready']:
        return
    result = subprocess.run([binary, 'auth', 'login'], env=claude_environment())
    if result.returncode or not claude_status()['ready']:
        raise RuntimeError('Claude account sign-in did not complete. ' + LOGIN_HELP)


def decision_response(value, names=None):
    """Reuse the transport-independent contract, relabelled for this provider."""
    try:
        return _shared_decision_response(value) if names is None else _shared_decision_response(value, names)
    except RuntimeError as exc:
        raise RuntimeError(str(exc).replace('Codex', 'Claude')) from None


def claude_failure(raw):
    """Actionable categories without reflecting raw account/server diagnostics."""
    text = raw.lower()
    if any(term in text for term in (b'permission denied', b'operation not permitted', b'read-only file system')):
        return ('Claude Code cannot write its local session storage. Launch the runner from your own terminal, '
                'or grant the launching agent write access to ~/.claude, then restart; no motion sent.')
    if b'unrecognized_model' in text:
        return ('This Claude Code build does not recognize the requested model and would silently run a '
                'different one. Update Claude Code, or pick a model it lists; no motion sent.')
    if any(term in text for term in (b'usage limit', b'usage_limit', b'rate limit', b'rate_limit', b'quota')):
        return ('Claude subscription usage limit reached. Wait for the limit to reset, then start a new run; '
                'no motion sent.')
    if any(term in text for term in (b'oauth', b'unauthorized', b'authentication', b'not logged in',
                                     b'token_expired', b'session expired')):
        return 'Claude Code needs sign-in. ' + LOGIN_HELP
    return ('Claude request failed. Check subscription limits, model access and `claude auth status`; '
            'no motion sent.')


class ClaudeAdapter(RoboCurveResponsesAdapter):
    provider_name = 'claude'

    def __init__(self, model=DEFAULT_MODEL, *, camera_source=None, recording_root=None,
                 timeout_s=180):
        setup = claude_status()
        if not setup['ready']:
            raise RuntimeError(setup['message'])
        if not model.strip():
            raise ValueError('A Claude model is required')
        self.model = model
        self._initialize_observation(camera_source)
        self._initialize_policy(recording_root)
        self._binary = claude_binary()
        self._workspace = tempfile.TemporaryDirectory(prefix='yam-claude-')
        self._session_id = None
        self._sent_items = 0
        self._timeout_s = timeout_s
        self._usage = None

    def public_config(self):
        return {**super().public_config(), 'api_key_configured': False,
                'authentication': 'claude_subscription', 'transport': 'claude_print',
                'claude_connected': True, 'usage': self._usage}

    def _post_json(self, payload):
        root = Path(self._workspace.name)
        history = payload['input']
        if self._sent_items > len(history):
            raise RuntimeError('Claude conversation history moved backwards')
        system = (
            'You are the decision component of the YAM local runner. Return only the JSON decision '
            'matching the output schema. The runner translates your action into move_to/done/give_up '
            'and performs validation. Use null for every unchanged target dimension; use empty strings '
            'for unused text fields. For done/give_up all targets must be null. Never claim motion '
            'executed until measured feedback says completed.'
        )
        lines = []
        if hasattr(self, '_decision_instructions'):
            system = self._decision_instructions
            lines.append(payload.get('instructions', ''))
        images = []
        for item in history[self._sent_items:]:
            content = item.get('content')
            if isinstance(content, list):
                for part in content:
                    if part.get('type') == 'input_image':
                        suffix = 'png' if part['image_url'].startswith('data:image/png;') else 'jpg'
                        name = f'camera-{len(images)}.{suffix}'
                        path = root / name
                        path.write_bytes(base64.b64decode(part['image_url'].split(',', 1)[1], validate=True))
                        path.chmod(0o600)
                        images.append(name)
                    elif part.get('type') == 'input_text':
                        lines.append(part['text'])
            else:
                lines.append(json.dumps(item, allow_nan=False))
        expected = getattr(self, '_expected_camera_count', 3)
        if len(images) != expected:
            raise RuntimeError(f'Claude requires exactly {expected} fresh camera images')
        lines.append(
            'The current observation is these image files in the working directory, ordered '
            + ', '.join(getattr(self, '_camera_names', ('top', 'left', 'right'))) + ': '
            + ', '.join(images) + '. Read every one of them with the Read tool before deciding. '
            'Use no other tool.'
        )
        if self._session_id is None:
            lines.append('Action contract: ' + json.dumps(payload.get('tools', [])))
        command = [self._binary, '--print',
                   '--output-format', 'json',
                   '--json-schema', json.dumps(getattr(self, '_decision_schema', DECISION_SCHEMA)),
                   '--model', self.model,
                   '--system-prompt', system,
                   '--tools', 'Read',
                   '--restricted',
                   '--strict-mcp-config',
                   '--disable-slash-commands',
                   '--setting-sources', '',
                   '--permission-mode', 'acceptEdits',
                   '--permission-prompts', 'none',
                   '--add-dir', str(root)]
        if self._session_id:
            command += ['--resume', self._session_id]
        else:
            self._pending_session = str(uuid.uuid4())
            command += ['--session-id', self._pending_session]
        try:
            report = self._execute(command, '\n'.join(lines), root)
        finally:
            for name in images:
                (root / name).unlink(missing_ok=True)
        if self.cancelled():
            raise RuntimeError('Claude inference cancelled; no motion sent')
        if report.get('is_error') or report.get('subtype') not in (None, 'success'):
            raise RuntimeError(claude_failure(json.dumps(report).encode()))
        denials = report.get('permission_denials')
        if denials:
            raise RuntimeError('Claude could not read the camera images for this observation; no motion sent')
        session_id = report.get('session_id')
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError('Claude did not return a conversation ID; no motion sent')
        if self._session_id is not None and session_id != self._session_id:
            raise RuntimeError('Claude resumed the wrong conversation')
        final = report.get('result')
        if not isinstance(final, str):
            raise RuntimeError('Claude did not complete a structured decision; no motion sent')
        try:
            result = getattr(self, '_decision_response', decision_response)(json.loads(final))
        except (ValueError, TypeError):
            raise RuntimeError('Claude returned malformed JSON; no motion sent') from None
        self._session_id = session_id
        usage = report.get('usage')
        self._usage = {'input_tokens': usage.get('input_tokens'),
                       'output_tokens': usage.get('output_tokens'),
                       'cost_usd': report.get('total_cost_usd')} if isinstance(usage, dict) else None
        # Skip the synthetic function call that RoboCurve adds after this return.
        self._sent_items = len(history) + 1 if getattr(self, '_incremental_history', True) else 0
        return result

    def _execute(self, command, prompt, root):
        if self.cancelled():
            raise RuntimeError('Claude inference cancelled; no motion sent')
        # Files avoid pipe deadlocks. Raw diagnostics remain private and are discarded.
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            stdin.write(prompt.encode())
            stdin.seek(0)
            process = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr,
                                       cwd=root, env=claude_environment(), start_new_session=True)
            deadline = time.monotonic() + self._timeout_s
            try:
                while process.poll() is None:
                    if self.cancelled():
                        raise RuntimeError('Claude inference cancelled; no motion sent')
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Claude inference timed out; no motion sent')
                    time.sleep(.05)
                stdout.seek(0)
                stderr.seek(0)
                raw = stdout.read(8_000_001)
                diagnostics = stderr.read(1_000_000)
                if len(raw) > 8_000_000:
                    raise RuntimeError('Claude output exceeded the response limit')
                if process.returncode:
                    raise RuntimeError(claude_failure(raw + diagnostics))
                # Claude Code reports an unknown model on stderr and then answers
                # with a different one. Refuse rather than mislabel the run.
                if b'unrecognized_model' in raw + diagnostics:
                    raise RuntimeError(claude_failure(b'unrecognized_model'))
                try:
                    report = json.loads(raw.decode().strip().splitlines()[-1])
                except (ValueError, UnicodeError, IndexError):
                    raise RuntimeError('Claude returned malformed output; no motion sent') from None
                if not isinstance(report, dict):
                    raise RuntimeError('Claude returned an invalid result object; no motion sent')
                return report
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=2)
                    except ProcessLookupError:
                        process.wait()
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()

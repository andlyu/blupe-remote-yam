"""Subscription-backed Codex CLI transport for the existing RoboCurve policy.

Protocol/flags verified with codex-cli 0.154.0; docs/refs/codex/INDEX.md.
Codex produces structured decisions; only the runner executes robot commands.
"""
from __future__ import annotations

import base64
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

from .robocurve_policy import RoboCurveResponsesAdapter
from .robocurve_trajectory import NAMES

DEFAULT_MODEL = 'gpt-6-astra'
MIN_VERSION = (0, 154, 0)
PINNED_VERSION = '0.154.0'
RUNTIME_ROOT = Path(__file__).resolve().parents[2] / '.codex-runtime'
INSTALL_HELP = 'Run ./run.sh --provider codex to install the compatible local Codex runtime (Node.js/npm required).'
LOGIN_HELP = 'Run ./run.sh --codex-login --provider codex in the launch terminal.'
STORAGE_HELP = ('Codex cannot write its local session storage. Launch the runner from your own terminal, '
                'or grant the launching agent write access to CODEX_HOME (normally ~/.codex), then restart. '
                'Signing in again will not fix a write-permission problem.')


def check_codex_storage():
    """Probe real writes, including today's session directory, before queue admission."""
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    paths = [home, home / 'tmp', home / 'sessions' / datetime.now().strftime('%Y/%m/%d')]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(prefix='.blupe-write-check-', dir=path) as probe:
            probe.write(b'check')
            probe.flush()
    # Existing databases may have different permissions from their directory.
    for database in home.glob('*.sqlite*'):
        if database.is_file():
            with database.open('r+b'):
                pass


def codex_binary():
    local = RUNTIME_ROOT / 'node_modules' / '.bin' / 'codex'
    return str(local) if local.is_file() else shutil.which('codex')


def ensure_codex_runtime():
    """Explicit Codex launch installs a pinned local runtime only when needed."""
    if codex_status()['state'] not in ('missing', 'upgrade_required'):
        return
    npm = shutil.which('npm')
    if not npm:
        raise RuntimeError('Install Node.js (including npm), then retry ./run.sh --provider codex.')
    print(f'Installing Codex {PINNED_VERSION} in {RUNTIME_ROOT}…', flush=True)
    result = subprocess.run([npm, 'install', '--prefix', str(RUNTIME_ROOT), '--no-audit',
                             '--no-fund', '--save-exact', '@openai/codex@' + PINNED_VERSION],
                            env=codex_environment())
    if result.returncode or codex_status()['state'] in ('missing', 'upgrade_required', 'unavailable'):
        raise RuntimeError('Local Codex installation failed. ' + INSTALL_HELP)


def codex_environment():
    # Do not pass runner API keys, lease capabilities or parent-agent overrides.
    allowed = {'HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR', 'TEMP', 'TMP',
               'SYSTEMROOT', 'WINDIR', 'CODEX_HOME', 'XDG_CONFIG_HOME',
               'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'LANG', 'LC_ALL',
               'SSL_CERT_FILE', 'SSL_CERT_DIR', 'CODEX_CA_CERTIFICATE',
               'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY',
               'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'}
    return {key: value for key, value in os.environ.items() if key in allowed}


def codex_status():
    """Only return setup state, never raw auth output or account identifiers."""
    binary = codex_binary()
    if not binary:
        return {'ready': False, 'state': 'missing', 'message': INSTALL_HELP}
    try:
        version = subprocess.run([binary, '--version'], capture_output=True, text=True,
                                 timeout=5, env=codex_environment())
        match = re.search(r'codex-cli (\d+)\.(\d+)\.(\d+)', version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < MIN_VERSION:
            return {'ready': False, 'state': 'upgrade_required', 'message': INSTALL_HELP}
        login = subprocess.run([binary, 'login', 'status'], capture_output=True,
                               text=True, timeout=5, env=codex_environment())
        ready = login.returncode == 0 and 'Logged in using ChatGPT' in login.stdout + login.stderr
        if ready:
            try:
                check_codex_storage()
            except OSError:
                return {'ready': False, 'state': 'storage_unwritable', 'message': STORAGE_HELP}
        return {'ready': ready, 'state': 'ready' if ready else 'login_required',
                'version': '.'.join(match.groups()),
                'message': 'Signed in with ChatGPT. Uses your Codex subscription limits.' if ready else LOGIN_HELP}
    except (OSError, subprocess.SubprocessError):
        return {'ready': False, 'state': 'unavailable',
                'message': 'Could not check Codex. Run codex login status in the launch terminal.'}


def login_codex():
    binary = codex_binary()
    if not binary:
        raise RuntimeError(INSTALL_HELP)
    setup = codex_status()
    if setup['ready']:
        return
    if setup.get('state') == 'storage_unwritable':
        raise RuntimeError(setup['message'])
    result = subprocess.run([binary, 'login'], env=codex_environment())
    if result.returncode or not codex_status()['ready']:
        raise RuntimeError('Codex ChatGPT sign-in did not complete. ' + LOGIN_HELP)


DECISION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'action': {'type': 'string', 'enum': ['move_to', 'done', 'give_up']},
        'targets': {'type': 'object', 'additionalProperties': False,
                    'properties': {name: {'type': ['number', 'null']} for name in NAMES},
                    'required': list(NAMES)},
        **{name: {'type': 'string'} for name in ('note', 'summary', 'reason', 'hindsight')},
    },
    'required': ['action', 'targets', 'note', 'summary', 'reason', 'hindsight'],
}


def decision_response(value, names=NAMES):
    """Validate locally even though Codex also receives an output schema."""
    if not isinstance(value, dict) or set(value) != set(DECISION_SCHEMA['required']):
        raise RuntimeError('Codex returned an invalid decision object; no motion sent')
    if any(not isinstance(value[name], str) for name in ('note', 'summary', 'reason', 'hindsight')):
        raise RuntimeError('Codex decision text must be strings; no motion sent')
    targets = value['targets']
    if not isinstance(targets, dict) or set(targets) != set(names):
        raise RuntimeError('Codex returned invalid target dimensions; no motion sent')
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, (float, int))
                             or not math.isfinite(v)) for v in targets.values()):
        raise RuntimeError('Codex targets must be finite numbers or null; no motion sent')
    action = value['action']
    if action == 'move_to':
        args = {'targets': {k: v for k, v in targets.items() if v is not None}, 'note': value['note']}
    elif action in ('done', 'give_up'):
        if any(v is not None for v in targets.values()):
            raise RuntimeError('Codex completion must not include movement targets')
        field = 'summary' if action == 'done' else 'reason'
        args = {field: value[field], 'hindsight': value['hindsight']}
    else:
        raise RuntimeError('Codex returned an unknown action; no motion sent')
    return {'output': [{'type': 'function_call', 'call_id': 'codex_' + uuid.uuid4().hex,
                        'name': action, 'arguments': json.dumps(args, allow_nan=False)}]}


def codex_failure(raw):
    """Actionable categories without reflecting raw account/server diagnostics."""
    text = raw.lower()
    if any(term in text for term in (b'permission denied', b'operation not permitted', b'read-only file system')):
        return STORAGE_HELP
    if b'newer version of codex' in text:
        return 'Astra requires a newer Codex runtime. ' + INSTALL_HELP
    if any(term in text for term in (b'usage_limit', b'rate_limit', b'quota', b'usage limit', b'rate limit')):
        return 'Codex subscription usage limit reached. Wait for the limit to reset, then start a new run; no motion sent.'
    if any(term in text for term in (b'unauthorized', b'authentication', b'not logged in', b'token_expired')):
        return 'Codex needs ChatGPT sign-in. ' + LOGIN_HELP
    return 'Codex request failed. Check subscription limits, model access and codex login status; no motion sent.'


class CodexAdapter(RoboCurveResponsesAdapter):
    provider_name = 'codex'
    reasoning_effort = 'low'

    def __init__(self, model=DEFAULT_MODEL, *, camera_source=None, recording_root=None,
                 timeout_s=120):
        setup = codex_status()
        if not setup['ready']:
            raise RuntimeError(setup['message'])
        if not model.strip():
            raise ValueError('A Codex model is required')
        self.model = model
        self._initialize_observation(camera_source)
        self._initialize_policy(recording_root)
        self._binary = codex_binary()
        self._workspace = tempfile.TemporaryDirectory(prefix='yam-codex-')
        self._thread_id = None
        self._sent_items = 0
        self._timeout_s = timeout_s

    def public_config(self):
        return {**super().public_config(), 'api_key_configured': False,
                'authentication': 'chatgpt', 'transport': 'codex_exec',
                'codex_connected': True, 'reasoning_effort': self.reasoning_effort}

    def _post_json(self, payload):
        root = Path(self._workspace.name)
        schema = root / 'decision-schema.json'
        schema.write_text(json.dumps(getattr(self, "_decision_schema", DECISION_SCHEMA)))
        history = payload['input']
        if self._sent_items > len(history):
            raise RuntimeError('Codex conversation history moved backwards')
        lines = [
            'You are the decision component of the YAM local runner. Return only the JSON decision',
            'matching the output schema. Do not use shell, web, files, or other tools.',
            'The runner translates your action into move_to/done/give_up and performs validation.',
            'Use null for every unchanged target dimension; use empty strings for unused text fields.',
            'For done/give_up all targets must be null. Never claim motion executed until measured',
            'feedback says completed. Attached images are the current observation, ordered top, left, right.',
        ]
        if hasattr(self, '_decision_instructions'):
            lines = [self._decision_instructions, payload.get('instructions', '')]
        images = []
        for item in history[self._sent_items:]:
            content = item.get('content')
            if isinstance(content, list):
                for part in content:
                    if part.get('type') == 'input_image':
                        suffix = 'png' if part['image_url'].startswith('data:image/png;') else 'jpg'
                        path = root / f'camera-{len(images)}.{suffix}'
                        path.write_bytes(base64.b64decode(part['image_url'].split(',', 1)[1], validate=True))
                        path.chmod(0o600)
                        images.append(path)
                    elif part.get('type') == 'input_text':
                        lines.append(part['text'])
            else:
                lines.append(json.dumps(item, allow_nan=False))
        if len(images) != getattr(self, '_expected_camera_count', 3):
            raise RuntimeError('Codex requires exactly three fresh camera images')
        if self._thread_id is None:
            lines.append('Action contract: ' + json.dumps(payload.get('tools', [])))
        command = [self._binary, 'exec']
        if self._thread_id:
            command += ['resume', self._thread_id]
        command += ['--ignore-user-config', '--skip-git-repo-check', '--json',
                    '--model', self.model, '--output-schema', str(schema),
                    '-c', 'forced_login_method="chatgpt"', '-c', 'model_provider="openai"',
                    '-c', f'model_reasoning_effort="{self.reasoning_effort}"',
                    '-c', 'sandbox_mode="read-only"', '-c', 'approval_policy="never"',
                    '-c', 'web_search="disabled"', '-c', 'features.shell_tool=false',
                    '-c', 'features.multi_agent=false', '-c', 'features.apps=false',
                    '-c', 'features.hooks=false']
        for path in images:
            command += ['--image', str(path)]
        command += ['-']
        try:
            events = self._execute(command, '\n'.join(lines), root)
        finally:
            for path in images:
                path.unlink(missing_ok=True)
        if self.cancelled():
            raise RuntimeError('Codex inference cancelled; no motion sent')
        thread_id, final = self._thread_id, None
        completed = False
        for event in events:
            kind = event.get('type')
            if kind == 'thread.started':
                candidate = event.get('thread_id')
                try:
                    uuid.UUID(candidate)
                except (ValueError, TypeError, AttributeError):
                    raise RuntimeError('Codex returned an invalid conversation ID') from None
                if thread_id is not None and candidate != thread_id:
                    raise RuntimeError('Codex resumed the wrong conversation')
                thread_id = candidate
            elif kind in ('error', 'turn.failed'):
                raise RuntimeError(codex_failure(json.dumps(event).encode()))
            elif kind == 'turn.completed':
                completed = True
            elif kind == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                final = event['item'].get('text')
        if not completed or not thread_id or not isinstance(final, str):
            raise RuntimeError('Codex did not complete a structured decision; no motion sent')
        try:
            result = getattr(self, "_decision_response", decision_response)(json.loads(final))
        except (ValueError, TypeError):
            raise RuntimeError('Codex returned malformed JSON; no motion sent') from None
        self._thread_id = thread_id
        # Skip the synthetic function call that RoboCurve adds after this return.
        self._sent_items = len(history) + 1 if getattr(self, "_incremental_history", True) else 0
        return result

    def _execute(self, command, prompt, root):
        if self.cancelled():
            raise RuntimeError('Codex inference cancelled; no motion sent')
        # Files avoid pipe deadlocks. Raw diagnostics remain private and are discarded.
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            stdin.write(prompt.encode())
            stdin.seek(0)
            process = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr,
                                       cwd=root, env=codex_environment(), start_new_session=True)
            deadline = time.monotonic() + self._timeout_s
            try:
                while process.poll() is None:
                    if self.cancelled():
                        raise RuntimeError('Codex inference cancelled; no motion sent')
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Codex inference timed out; no motion sent')
                    time.sleep(.05)
                if process.returncode:
                    stdout.seek(0)
                    stderr.seek(0)
                    raise RuntimeError(codex_failure(stdout.read(1_000_000) + stderr.read(1_000_000)))
                stdout.seek(0)
                raw = stdout.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise RuntimeError('Codex output exceeded the response limit')
                try:
                    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
                except (ValueError, UnicodeError):
                    raise RuntimeError('Codex returned malformed events; no motion sent') from None
                if any(not isinstance(event, dict) for event in events):
                    raise RuntimeError('Codex returned invalid events; no motion sent')
                return events
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

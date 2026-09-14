"""Read-only, bounded public dataset catalog. No visitor artifacts or credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import sqlite3
from pathlib import Path
import threading
import time
from urllib.request import urlopen

BASE = 'https://huggingface.co/datasets/andlyu/Public-YAM-runs/resolve/main/'


def run_timed_out(state):
    if state.get('status') == 'timed_out':
        return True
    if (state.get('safety_error') or {}).get('code') in {'session_timeout', 'policy_runtime_timeout'}:
        return True
    text = str(state.get('error') or '').lower()
    if any(marker in text for marker in ('runtime limit', 'session_timeout', 'policy_runtime_timeout', 'selected duration limit')):
        return True
    return any(event.get('kind') == 'lifecycle' and
               (event.get('details') or {}).get('reason') in {'session_timeout', 'policy_runtime_timeout'}
               for event in (state.get('interactions') or {}).get('events', []))


def merge_run_result(row, stored):
    """A catalog timeout is robot evidence; stale generic runner errors cannot erase it."""
    timed_out = row.get('result_reason') == 'Run reached time limit'
    row.update(stored)
    diagnostic = public_runner_diagnostic({'error': row.get('errors')})
    if diagnostic and row.get('result') == 'Failure' and not timed_out:
        row['result_reason'] = diagnostic
    if timed_out and row.get('result_reason') in {'Runner or model error', 'Robot safety check stopped the run', 'Run reached time limit'}:
        row.update(result='Failure', result_reason='Run reached time limit', errors='Run reached time limit')
        if row.get('ending_reason') in {None, 'Runner or model error', 'Robot safety check stopped the run'}:
            row['ending_reason'] = 'Run reached time limit'
    return row


def run_result(meta):
    # Completion is the runner's report, not an independent task evaluation.
    outcomes = {
        'policy_complete': ('Success', 'Runner reported task complete'),
        'user_requested': ('Stopped', 'Run stopped; no specific result recorded'),
        'session_timeout': ('Failure', 'Run reached time limit'),
        'policy_runtime_timeout': ('Failure', 'Run reached time limit'),
        'lease_expired': ('Failure', 'Runner connection expired'),
        'physical_execution_failure': ('Failure', 'Robot execution failed'),
        'runner_failed': ('Failure', 'Runner or model error'),
        'recorder_process_interrupted': ('Unknown', 'Recording interrupted'),
        'operator_stop': ('Stopped', 'Stopped by the operator'),
    }
    label, reason = outcomes.get(meta.get('outcome'), ('Unknown', 'Result was not recorded'))
    return {'result': label, 'result_reason': reason,
            'ending_reason': None if label == 'Unknown' else reason,
            'errors': reason if label == 'Failure' else None}


def public_runner_diagnostic(state):
    """Preserve exact feedback-guard errors; never include arbitrary provider text."""
    error = state.get('error')
    reasons = {
        'station_safety_status_missing': 'Robot safety status was missing',
        'station_emergency_stop_engaged': 'Robot emergency stop was engaged',
        'station_contact_reported': 'Robot reported contact',
        'station_controller_fault': 'Robot controller reported FAULT',
        'station_emergency_stop_status_unknown': 'Robot emergency-stop status was unknown',
        'station_disabled': 'Robot was disabled',
        'station_physical_controller_fault': 'Robot reported a physical controller fault',
        'station_joint_limit': 'Robot reported a joint outside its position limits',
        'station_hardware_not_initialized': 'Robot hardware was not initialized',
        'station_safety_not_confirmed': 'Robot safety.ok was not true; no recognized specific reason was supplied',
    }
    for code, message in reasons.items():
        if error in {f'Hardware feedback blocked: {code}', f'RuntimeError: Hardware feedback blocked: {code}'}:
            return message
    if not isinstance(error, str):
        return None
    reasons = (
        'station_unsafe', 'completion_timestamp_invalid_or_stale',
        'completion_not_explicitly_settled', 'completion_not_explicitly_homed',
        'station_source_not_hardware', 'station_timestamp_invalid',
        'station_precedes_completion_or_is_stale', 'station_not_explicitly_settled',
        'station_not_explicitly_homed',
    )
    if re.fullmatch(r'(?:RuntimeError: )?Hardware feedback blocked: (' + '|'.join(reasons) + r')', error):
        return error
    return None


def public_robot_error(state):
    """Expose only the known numeric joint mismatch, never arbitrary exceptions."""
    candidates = [(state.get('safety_error') or {}).get('message'), state.get('error')]
    candidates += [(e.get('details') or {}).get('context', {}).get('message')
                   for e in (state.get('interactions') or {}).get('events', []) if e.get('kind') == 'error']
    pattern = r'within ([0-9.]+)s: (left|right)_joint_([0-5]) residual_rad=([0-9.]+) measured_rad=(-?[0-9.]+) target_rad=(-?[0-9.]+); allowed_rad=([0-9]+(?:\.[0-9]+)?)'
    for candidate in candidates:
        gripper_delta = re.fullmatch(r'(?:ValueError: )?gripper delta limit at waypoint ([0-9]{1,6})', candidate or '')
        if gripper_delta:
            return f'ValueError: gripper delta limit at waypoint {int(gripper_delta.group(1))}'
        # This station-level message is already allowlisted by the gateway;
        # validate again before exposing it through the public hosted UI.
        number = r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'
        gripper = re.fullmatch(
            rf'(Left|Right) gripper feedback ({number}) is outside the accepted range '
            rf'\[({number}), ({number})\]\. Robot unavailable; operator attention needed\.',
            candidate or '')
        if gripper and all(math.isfinite(float(v)) for v in gripper.groups()[1:]):
            return gripper.group(0)
        match = re.search(pattern, candidate or '')
        if not match:
            continue
        seconds, arm, joint, residual, measured, target, allowed = match.groups()
        try:
            seconds, residual, measured, target, allowed = map(float, (seconds,residual,measured,target,allowed))
        except ValueError:
            continue
        if not all(math.isfinite(v) for v in (seconds,residual,measured,target,allowed)):
            continue
        return (f'{arm.capitalize()} arm joint {joint} (joint {int(joint)+1} of 6) did not reach its target within {seconds:g}s. '
                f'Measured {math.degrees(measured):.1f}°, target {math.degrees(target):.1f}°; '
                f'error {math.degrees(residual):.1f}° exceeds {math.degrees(allowed):.1f}° allowed. '
                'Motion stopped. Check for contact or obstruction before continuing.')
    return None


def model_ending(state):
    candidates = [(state.get('provider') or {}).get('outcome')]
    candidates += [(e.get('details') or {}).get('result')
                   for e in reversed((state.get('interactions') or {}).get('events', []))
                   if e.get('kind') == 'tool_result']
    for outcome in candidates:
        if not isinstance(outcome, dict) or outcome.get('status') not in {'done', 'give_up'}:
            continue
        reason = outcome.get('reason' if outcome['status'] == 'give_up' else 'summary')
        if isinstance(reason, str) and reason.strip():
            return outcome['status'], reason.strip()[:2000]
    return None


class RunNames:
    def __init__(self, path):
        self.path = Path(path)

    def remember(self, episode_id, name):
        if not re.fullmatch(r'ep_[A-Za-z0-9_-]+', episode_id or '') or not name:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS run_names (episode_id TEXT PRIMARY KEY, name TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO run_names VALUES (?, ?)', (episode_id, name[:32]))

    def remember_result(self, episode_id, state):
        if not re.fullmatch(r'ep_[A-Za-z0-9_-]+', episode_id or ''):
            return
        events = (state.get('interactions') or {}).get('events', [])
        stop_reasons = [(event.get('details') or {}).get('reason') for event in events
                        if event.get('kind') == 'stop_requested']
        ending = model_ending(state)
        errors = None
        specific = public_robot_error(state)
        if specific:
            result, reason = 'Failure', specific
            errors = reason
        elif run_timed_out(state):
            result, reason = 'Failure', 'Run reached time limit'
            errors = reason
        elif state.get('safety_error'):
            result, reason = 'Failure', 'Robot safety check stopped the run'
            errors = reason
        elif state.get('error'):
            result, reason = 'Failure', public_runner_diagnostic(state) or 'Runner or model error'
            errors = reason
        elif state.get('status') == 'timed_out':
            result, reason = 'Failure', 'Run reached time limit'
            errors = reason
        elif ending:
            result = 'Failure' if ending[0] == 'give_up' else 'Success'
            reason = ending[1]
        elif 'policy_complete' in stop_reasons:
            result, reason = 'Success', 'Runner reported task complete'
        elif 'user_requested' in stop_reasons:
            result, reason = 'Cancelled', 'Stopped by the visitor'
        else:
            return
        # Never expose provider exception text, URLs, or visitor credentials.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS run_results (episode_id TEXT PRIMARY KEY, result TEXT NOT NULL, reason TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS run_details (episode_id TEXT PRIMARY KEY, errors TEXT, ending_reason TEXT)')
            if not specific:
                previous_error = db.execute('SELECT errors FROM run_details WHERE episode_id=?', (episode_id,)).fetchone()
                if previous_error and previous_error[0] and (re.match(r'(Left|Right) arm joint [0-5] ', previous_error[0]) or re.fullmatch(r'ValueError: gripper delta limit at waypoint [0-9]{1,6}', previous_error[0])):
                    result, reason, errors = 'Failure', previous_error[0], previous_error[0]
            if not ending and not errors:
                previous = db.execute('SELECT r.result, r.reason FROM run_results r JOIN run_details d USING (episode_id) WHERE r.episode_id=? AND d.ending_reason IS NOT NULL', (episode_id,)).fetchone()
                if previous:
                    result, reason = previous
            db.execute('INSERT INTO run_details VALUES (?, ?, ?) ON CONFLICT(episode_id) DO UPDATE SET errors=COALESCE(excluded.errors,errors), ending_reason=COALESCE(excluded.ending_reason,ending_reason)',
                       (episode_id, errors, ending[1] if ending else None))
            previous = db.execute('SELECT reason FROM run_results WHERE episode_id=?', (episode_id,)).fetchone()
            if previous and previous[0] == 'Run reached time limit' and reason == 'Runner or model error':
                reason = errors = 'Run reached time limit'
            db.execute('INSERT INTO run_results VALUES (?, ?, ?) ON CONFLICT(episode_id) DO UPDATE SET result=excluded.result, reason=excluded.reason WHERE result != excluded.result OR reason != excluded.reason', (episode_id, result, reason))

    def results(self):
        if not self.path.exists():
            return {}
        with sqlite3.connect(self.path) as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE name='run_results'").fetchone():
                return {}
            result = {eid: {'result': label, 'result_reason': reason,
                            'ending_reason': reason, 'errors': reason if label == 'Failure' else None}
                      for eid, label, reason in db.execute('SELECT episode_id, result, reason FROM run_results')}
            if db.execute("SELECT name FROM sqlite_master WHERE name='run_details'").fetchone():
                for eid, errors, ending in db.execute('SELECT episode_id, errors, ending_reason FROM run_details'):
                    if eid in result:
                        result[eid]['errors'] = errors
                        if ending:
                            result[eid]['ending_reason'] = ending
            return result


    def names(self):
        if not self.path.exists():
            return {}
        with sqlite3.connect(self.path) as db:
            return dict(db.execute('SELECT episode_id, name FROM run_names'))


def fetch(path):
    with urlopen(BASE + path, timeout=8) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('Public catalog is too large')
    return body.decode()


class PastRuns:
    def __init__(self, reader=fetch, *, robot_id="yam-1"):
        self.robot_id = robot_id
        self.reader = reader
        self.lock = threading.Lock()
        self.rows = None
        self.updated = 0
        self.details = {}
        self.viewing = {}

    def page(self, offset=0):
        with self.lock:
            stale = False
            if self.rows is None or time.monotonic() - self.updated > 60:
                try:
                    sources = [json.loads(line) for line in self.reader('meta/blupe_source_episodes.jsonl').splitlines() if line]
                    tasks = {x['episode_index']: x.get('tasks', []) for x in
                             (json.loads(line) for line in self.reader('meta/episodes.jsonl').splitlines() if line)}
                    try:
                        motion = {r['episode_id']: r for r in (json.loads(line) for line in
                                  self.reader('meta/blupe_motion_classification.jsonl').splitlines() if line)}
                    except Exception:
                        motion = {}  # Never hide a run using its prompt or a guessed result.
                    try:
                        viewing = {r['episode_id']:r for r in (json.loads(line) for line in
                                   self.reader('meta/blupe_viewing.jsonl').splitlines() if line)
                                   if re.fullmatch(r'ep_[A-Za-z0-9_-]+', r.get('episode_id',''))}
                    except Exception:
                        viewing = self.viewing
                    self.viewing = viewing
                    rows = []
                    archived = {row['episode_id'] for row in sources}
                    for eid, item in viewing.items():
                        if eid not in archived:
                            rows.append({'episode_id':eid, 'episode_index':None,
                                         'started_at':item['started_at'], 'prompt':item.get('task') or 'Task unavailable',
                                         'original_available':item.get('original_available', False),
                                         'robot_id':item.get('robot_id', 'yam-1')})
                    for row in sources:
                        if not re.fullmatch(r'ep_[A-Za-z0-9_-]+', row.get('episode_id', '')):
                            continue
                        classification = motion.get(row['episode_id'], {})
                        if (classification.get('kind') == 'arm_check' and classification.get('version') in {1, 2}
                                and row.get('samples_sha256') and classification.get('samples_sha256') == row['samples_sha256']):
                            continue
                        prompts = tasks.get(row['episode_index'], [])
                        rows.append({'episode_id': row['episode_id'], 'episode_index': row['episode_index'],
                                     'robot_id':row.get('robot_id', 'yam-1'),
                                     'started_at': row['recorded_at'], 'prompt': '\n'.join(prompts) or 'Task unavailable'})
                    # Explicitly excluded by the operator, including incomplete recordings.
                    rows = [r for r in rows if (self.robot_id is None or r['robot_id'] == self.robot_id) and ' '.join(r['prompt'].split()).casefold().rstrip('.') != 'raise and lower both arms for three cycles']
                    self.rows = sorted(rows, key=lambda r: (r['started_at'], r['episode_index'] or -1), reverse=True)
                    self.updated = time.monotonic()
                except Exception:
                    if self.rows is None:
                        raise
                    stale = True
            selected = self.rows[offset:offset+12]
            now = time.monotonic()
            missing = [r for r in selected if r['episode_id'] not in self.details or now-self.details[r['episode_id']][0] > 60]
            with ThreadPoolExecutor(max_workers=4) as pool:
                for eid, detail in pool.map(self._detail, missing):
                    self.details[eid] = (now, detail)
            # Bound caches as the archive grows.
            if len(self.details) > 240:
                wanted = {r['episode_id'] for r in selected}
                self.details = {k: v for k, v in self.details.items() if k in wanted}
            result = [dict(r, **self.details[r['episode_id']][1]) for r in selected]
            return {'runs': result, 'next_offset': offset+len(selected) if offset+len(selected) < len(self.rows) else None,
                    'stale': stale}

    def _detail(self, row):
        eid = row['episode_id']
        try:
            meta = json.loads(self.reader(f'episodes/{eid}.json'))
            preview = meta.get('preview') or {}
        except Exception:
            meta = {}
            preview = {}
        viewing = self.viewing.get(eid)
        if viewing:
            meta = {**meta, **viewing}
            preview = viewing['preview']
        compressed = preview.get('speed') == 10
        video_meta = preview if compressed else (meta.get('video') or {})
        duration = video_meta.get('duration_s')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            duration = 0 if video_meta.get('frames') == 0 or meta.get('rows') == 0 else None
        return eid, {**run_result(meta), 'video_duration_s': duration, 'video_url': BASE + f'{"viewing" if viewing else "previews" if compressed else "videos"}/{eid}.mp4',
                     'original_video_url': BASE + f'videos/{eid}.mp4',
                     'original_available': row.get('original_available', True),
                     'compressed': compressed,
                     'camera_order': preview.get('camera_order', ['left', 'top', 'right']) if compressed else ['left', 'top', 'right']}

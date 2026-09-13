"""Private, persistent setup submissions. Never store provider credentials."""
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

FIELDS = ('runner_name', 'email', 'instagram_handle', 'twitter_handle',
          'provider', 'model', 'prompt', 'run_duration_s')


class SubmissionLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions '
                       '(id INTEGER PRIMARY KEY, timestamp REAL, submission_id TEXT, '
                       'session_id TEXT, event TEXT, details TEXT)')

    def record(self, submission_id, event, details, session_id=None):
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute('INSERT INTO submissions(timestamp,submission_id,session_id,event,details) VALUES (?,?,?,?,?)',
                       (time.time(), submission_id, session_id, event, json.dumps(details)))


class LoggedSessionAPI:
    def __init__(self, api, journal):
        self.api, self.journal = api, journal
        self.submission_id = None

    def __getattr__(self, name):
        return getattr(self.api, name)

    def __setattr__(self, name, value):
        if name in {"api", "journal", "submission_id"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self.api, name, value)

    def setup(self, payload, paid=False):
        self.submission_id = uuid.uuid4().hex
        self.journal.record(self.submission_id, 'submitted',
                            {'source': 'website', 'paid': paid,
                             'fields': {k: payload[k] for k in FIELDS if k in payload}})

    def note(self, event, details, session_id=None):
        try:
            self.journal.record(self.submission_id, event, details, session_id)
        except Exception as exc:
            print('[queue-log-error] '+type(exc).__name__, flush=True)

    def create_session(self, prompt, run_duration_s=300):
        self.note('queue_request', {'prompt': prompt, 'run_duration_s': run_duration_s})
        try:
            result = self.api.create_session(prompt, run_duration_s=run_duration_s)
        except Exception as exc:
            self.note('queue_request_failed', {'error_type': type(exc).__name__})
            raise
        self.note('queued', {k: result[k] for k in ('status', 'position', 'created_at') if k in result}, result['session_id'])
        return result

    def open_events(self, session_id):
        try:
            return LoggedEvents(self.api.open_events(session_id), self, session_id)
        except Exception as exc:
            self.note('event_stream_failed', {'error_type': type(exc).__name__}, session_id)
            raise

    def stop_session(self, session_id, *, reason='user_requested'):
        self.note('stop_requested', {'reason': reason}, session_id)
        try:
            result = self.api.stop_session(session_id, reason=reason)
        except Exception as exc:
            self.note('stop_failed', {'error_type': type(exc).__name__}, session_id)
            raise
        self.note('stop_accepted', {k: result[k] for k in ('status', 'episode_id') if k in result}, session_id)
        return result


class LoggedEvents:
    def __init__(self, events, owner, session_id):
        self.events, self.owner, self.session_id = events, owner, session_id

    def __getattr__(self, name):
        return getattr(self.events, name)

    def receive(self, *args, **kwargs):
        event = self.events.receive(*args, **kwargs)
        if isinstance(event, dict) and event.get('type') in {'lifecycle', 'queue_update'}:
            payload = event.get('payload') or {}
            self.owner.note(event['type'], {k: payload[k] for k in
                ('state', 'previous_state', 'reason', 'actor', 'episode_id', 'position', 'depth') if k in payload}, self.session_id)
        return event

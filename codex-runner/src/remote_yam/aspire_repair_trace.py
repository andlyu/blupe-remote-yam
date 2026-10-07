"""Attempt-scoped public summaries/activity, separate from private wire captures."""
import json
from pathlib import Path
import re
import threading
import time

from .public_conversation import project_events


def public_activity(kind, message, details):
    """Project explicit display fields only; never copy arbitrary provider data."""
    text = lambda value: str(value or '')[:4000]
    if kind == 'model_progress':
        projected = project_events([dict(kind=kind, message=message, details=details)], '')
        if not projected:return None
        return dict(kind=kind, message=projected[0]['message'],
            progress_type=projected[0]['progress_type'], item_id=text(details.get('item_id')))
    if kind not in ('model_request','model_response','model_error','model_timing',
                    'tool_request','tool_result','tool_error'):
        return None
    update = details.get('task_update')
    if isinstance(update, dict):
        return dict(kind='activity', message=text(update.get('happened')),
            changed=text(update.get('changed')), next_action=text(update.get('next_action')))
    row = dict(kind=kind, message=text(message))
    # A coding response exposes its explicit diagnosis and lesson, not source,
    # prompts, image bytes, encrypted reasoning, wire payloads or tool arguments.
    if kind == 'model_response' and isinstance(details.get('summary'), str):
        row.update(message=text(details['summary']), lesson=text(details.get('lesson')))
    if kind == 'model_timing' and type(details.get('elapsed_s')) in (int, float):
        row['elapsed_s'] = round(details['elapsed_s'], 2)
    return row


class AttemptTrace:
    def __init__(self, record, directory):
        self.record = record
        self.path = Path(directory)/'recovery-traces'/(record['id']+'.jsonl')
        self.lock = threading.RLock()
        self.sequence = self.revision = 0
        self.keys = {}
        record.update(trace=[], trace_schema=1)

    def append(self, kind, message, **details):
        row = public_activity(kind, message, details)
        if row is None:return
        with self.lock:
            if kind == 'model_request':self.revision = details.get('attempt', details.get('call', self.revision+1))
            key = (self.revision, row['item_id']) if kind == 'model_progress' and row.get('item_id') else None
            event_id = self.keys.get(key) if key else None
            if event_id is None:
                self.sequence += 1;event_id = self.sequence
                if key:self.keys[key] = event_id
            row.update(id=event_id, timestamp=time.time(), attempt_id=self.record['id'],
                parent_attempt_id=self.record.get('parent_attempt_id'), revision=self.revision)
            rows = self.record['trace']
            index = next((i for i,e in enumerate(rows) if e['id']==event_id), None)
            if index is None:rows.append(row)
            else:rows[index] = row
            self.record['trace'] = rows[-300:]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open('a') as stream:stream.write(json.dumps(row, allow_nan=False)+'\n')
            except (OSError, ValueError):
                self.record['trace_error'] = 'Local activity recording is unavailable.'


def recorded_trace(record, directory):
    """Read durable public rows, or conservatively recover an old repair journal."""
    def lines(path):
        try:
            with path.open() as stream:
                for line in stream:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):yield value
                    except (ValueError, TypeError):continue
        except OSError:return
    identity = record.get('id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity):return []
    path = Path(directory)/'recovery-traces'/(identity+'.jsonl')
    if path.is_file():
        latest = {}
        for row in lines(path):
            if row.get('attempt_id')==identity and type(row.get('id')) in (str, int):latest[row['id']] = row
        return list(latest.values())[-300:]
    if 'trace' in record:return record['trace'][-300:]
    started, ended = record.get('started_at'), record.get('ended_at', time.time())
    if type(started) not in (int, float):return []
    if type(ended) not in (int, float):ended = time.time()
    events = [e for e in lines(Path(directory)/'runner/interactions.jsonl')
        if type(e.get('timestamp')) in (int, float) and started <= e['timestamp'] <= ended]
    requests = [e for e in events if e.get('kind')=='model_request']
    # Older events have no attempt tag. Do not attribute overlapping model work
    # or prior development to this repair merely because the clock overlaps.
    if not requests or any(e.get('message')!='Generate/revise an ASPIRE program' for e in requests):
        events = []
    rows = []
    for event in events:
        details = event.get('details') or {}
        if not isinstance(details, dict) or 'id' not in event:continue
        if details.get('attempt_id', identity)!=identity:continue
        row = public_activity(event.get('kind'), event.get('message'), details)
        if row:
            rows.append(dict(row, id=event['id'], timestamp=event['timestamp'],
                attempt_id=identity, parent_attempt_id=record.get('parent_attempt_id'),
                source='recorded_repair_journal'))
    if record.get('diagnosis'):
        rows.append(dict(id='diagnosis', kind='model_response', message=record['diagnosis'][:4000],
            lesson=str(record.get('lesson') or '')[:4000], timestamp=ended,
            attempt_id=identity, parent_attempt_id=record.get('parent_attempt_id'),
            source='recorded_repair_response'))
    return rows[-300:]

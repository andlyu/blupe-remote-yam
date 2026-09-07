"""Readable local interaction journal alongside the exact provider wire capture."""
import copy
import json
from pathlib import Path
import re
import threading
import time


def compact(events):
    """Keep all durable progress samples; show one advancing row per packet."""
    result, progress = [], {}
    for event in events:
        if event['kind'] == 'packet_progress':
            key = event.get('details', {}).get('trajectory_id')
            if key in progress:
                result[progress[key]] = event
                continue
            progress[key] = len(result)
        result.append(event)
    result = copy.deepcopy(result[-300:])
    for event in result:
        points = event.get('details', {}).pop('waypoints', None)
        if points:
            event['details'].update(first=points[0], last=points[-1], waypoint_count=len(points))
    return result


class InteractionLog:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None
        self.events = []
        self.sequence = 0
        self.error = None
        self.lock = threading.RLock()
        if self.directory:
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as exc:
                self.error = f'Cannot create interaction recording: {type(exc).__name__}'

    def add(self, kind, message, **details):
        with self.lock:
            self.sequence += 1
            event = {'id': self.sequence, 'timestamp': time.time(), 'kind': kind,
                     'message': str(message), 'details': copy.deepcopy(details)}
            self.events.append(event)
            self.events = self.events[-2000:]
            if self.directory and self.error is None:
                try:
                    path = self.directory/'interactions.jsonl'
                    with path.open('a', encoding='utf-8') as handle:
                        handle.write(json.dumps(event, allow_nan=False)+'\n')
                    path.chmod(0o600)
                except (OSError, TypeError, ValueError) as exc:
                    # Logging trouble must be visible, never interrupt a move.
                    self.error = f'Interaction recording failed: {type(exc).__name__}'

    def snapshot(self):
        with self.lock:
            return {'run_id': self.directory.name if self.directory else None,
                    'path': str(self.directory) if self.directory else None,
                    'error': self.error, 'events': copy.deepcopy(compact(self.events))}


def recording_directory(root, run_id):
    if not re.fullmatch(r'(?:robocurve|run)_[a-f0-9]{32}', run_id):
        raise ValueError('Invalid recording ID')
    root = Path(root).resolve()
    path = root/run_id
    if path.is_symlink() or path.resolve().parent != root or not path.is_dir():
        raise ValueError('Unknown recording')
    return path


def list_recordings(root):
    runs = []
    for path in Path(root).glob('*'):
        try:
            path = recording_directory(root, path.name)
        except ValueError:
            continue
        files = [p for p in (path/'interactions.jsonl', path/'calls.jsonl') if p.is_file() and not p.is_symlink()]
        if files:
            runs.append({'run_id': path.name, 'updated_at': max(p.stat().st_mtime for p in files)})
    return sorted(runs, key=lambda r:r['updated_at'], reverse=True)[:50]


def wire_events(rows):
    """Make recordings from before the UI journal readable too; no invented ACKs."""
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind, call = row.get('kind'), row.get('call')
        details = {'call': call}
        if kind == 'request':
            request = row['request']
            message = f"Model call {call} requested · {request.get('model', '')}"
            kind = 'model_request'
            details['history_items'] = len(request.get('input', []))
            latest = request.get('input', [])[-1:]
            details['observation'] = '\n'.join(part.get('text','') for item in latest
                for part in item.get('content',[]) if isinstance(part,dict) and part.get('type')=='input_text')
        elif kind == 'response':
            response = row.get('response')
            output = response.get('output') if isinstance(response, dict) else None
            calls = [i for i in output if isinstance(i, dict) and i.get('type')=='function_call'] if isinstance(output, list) else []
            details['tools'] = [{k:i.get(k) for k in ('name','arguments','call_id')} for i in calls]
            notes = []
            for c in calls:
                try:
                    args = json.loads(c.get('arguments',''))
                    if isinstance(args,dict):
                        note = args.get('note') or args.get('summary') or args.get('reason') or c.get('name','')
                        if isinstance(note, str):
                            notes.append(note)
                except (TypeError, ValueError):
                    pass
            message = f"Model call {call} responded: " + (' '.join(notes) or 'See response details')
            kind = 'model_response'
        elif kind == 'joint_packet':
            points = row.get('waypoints',[])
            message = f"Prepared {len(points)} joint waypoints at {row.get('cadence_hz',10)} Hz"
            details.update(waypoint_count=len(points), first=points[0] if points else None, last=points[-1] if points else None)
            kind = 'packet_ready'
        elif kind == 'tool_result':
            try:
                result = json.loads(row['item']['output'])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            details.update(result, call_id=row['item'].get('call_id'))
            if result.get('ok') is False:
                message, kind = 'No motion sent: '+str(result.get('error','Tool rejected')), 'tool_error'
            elif result.get('status')=='completed':
                message, kind = f"Completed {result.get('steps')} waypoints; feedback ready for next model call", 'tool_result'
            else:
                message, kind = 'Policy outcome: '+str(result.get('status','result')), 'tool_result'
        else:
            continue
        events.append({'id':len(events)+1,'timestamp':row.get('time',0),'kind':kind,'message':message,'details':details})
    return events


def read_recording(root, run_id):
    directory = recording_directory(root, run_id)
    journal = directory/'interactions.jsonl'
    if journal.is_file() and not journal.is_symlink():
        path, legacy = journal, False
    else:
        path, legacy = directory/'calls.jsonl', True
    if path.is_symlink() or not path.is_file():
        raise ValueError('Recording file unavailable')
    rows = []
    with path.open() as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                # Writer may be appending the last line during a live read.
                continue
    events = wire_events(rows) if legacy else rows
    return {'run_id':run_id, 'path':str(directory), 'error':None, 'events':compact(events)}

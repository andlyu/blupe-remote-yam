"""Read the native harness's append-only events without changing its execution."""
from datetime import datetime
import json
from pathlib import Path
import threading
import time


class HarnessProgress:
    def __init__(self, directory, publish):
        self.directory, self.publish = Path(directory), publish
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True, name='aspire-progress')
        self.offset = 0
        self.candidates = self.rejected = 0
        self.last_failure = None
        self.stage = None

    def __enter__(self):
        self.publish(dict(stage='harness_startup', title='Starting the native harness',
            detail='Loading the runtime before fresh capture.', started_at=time.time()))
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.closed.set()
        self.thread.join(2)
        self.read()

    def _watch(self):
        while not self.closed.wait(.25):
            self.read()

    def read(self):
        path = self.directory/'debug_events.jsonl'
        try:
            with path.open() as stream:
                stream.seek(self.offset)
                while True:
                    line = stream.readline()
                    if not line or not line.endswith('\n'):
                        break
                    self.offset = stream.tell()
                    try:
                        self.event(json.loads(line))
                    except (ValueError, TypeError, KeyError):
                        continue
        except OSError:
            pass

    def event(self, event):
        name, kind = event.get('name'), event.get('type')
        stages = {
            'snapshot_context': ('capture', 'Capturing fresh state and RGB-D'),
            'get_camera_rgbd': ('capture', 'Capturing fresh state and RGB-D'),
            'segment_camera_rgb': ('segmentation', 'Segmenting the current scene'),
            'plan_freespace_sequence': ('native_planning', 'Testing native candidate plans'),
            'open_gripper': ('execution', 'Executing the admitted plan'),
            'close_gripper': ('execution', 'Executing the admitted plan'),
            'freespace_move': ('execution', 'Executing the admitted plan'),
        }
        if name not in stages or kind not in ('tool_start', 'tool_end'):
            return
        stage, title = stages[name]
        if stage == 'native_planning':
            if kind == 'tool_start':
                self.candidates += 1
            else:
                result = event.get('result') or {}
                if event.get('error') or result.get('success') is False:
                    self.rejected += 1
                    self.last_failure = str(event.get('error') or result.get('reason') or result.get('status'))
        if kind == 'tool_end' and stage != 'native_planning' and not event.get('error'):
            return
        stamp = datetime.fromisoformat(event['ts']).timestamp() if event.get('ts') else time.time()
        detail = ('Candidate '+str(self.candidates)+'; rejected '+str(self.rejected)+'. '
            'Rejected candidates are retained; task motion waits for a complete passing plan.'
            if stage == 'native_planning' else name)
        self.publish(dict(stage=stage, title=title, detail=detail, event_at=stamp,
            candidates=self.candidates, rejected_candidates=self.rejected,
            last_failure=self.last_failure, error=event.get('error')))

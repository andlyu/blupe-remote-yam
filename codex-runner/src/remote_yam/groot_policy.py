"""Queued GR00T inference. Only the leased controller submits robot commands."""
import base64
import io
import json
import math
import threading
import time
from urllib import request
from .cameras import NoRedirect
from .ik import IKSolver, IKCommand, ArmIKCommand
from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET

MODEL = 'groot-reviewed-step10000'
ENDPOINT = 'https://3xjhrdbhwcnux3.api.runpod.ai'


class GrootAdapter:
    provider_name = 'groot'
    model = MODEL

    def __init__(self, api_key, *, camera_source):
        if not api_key.strip():
            raise ValueError('GR00T service credential is missing')
        self._api_key = api_key.strip()
        self._camera_source = camera_source
        self.cancelled = lambda: False
        self._ready = threading.Event()
        self._failure = None
        self._warm_state = 'waiting_for_queue'
        self._thread = None
        self._opener = request.build_opener(NoRedirect)

    def public_config(self):
        return {'provider': self.provider_name, 'model': self.model,
                'warmup': self._warm_state, 'api_key_configured': bool(self._api_key)}

    def _json(self, path, payload=None):
        req = request.Request(ENDPOINT + path, data=None if payload is None else json.dumps(payload).encode(),
                              headers={'Authorization': 'Bearer '+self._api_key, 'Content-Type':'application/json'})
        try:
            with self._opener.open(req, timeout=12) as response:
                return json.loads(response.read(8*1024*1024))
        except Exception as exc:
            # No exception text or credential-bearing request objects escape.
            raise RuntimeError('GR00T request failed ('+type(exc).__name__+')') from None

    def start_preparation(self, cancelled):
        if self._thread is not None:
            return
        self._warm_state = 'warming'
        self._thread = threading.Thread(target=self._keep_ready, args=(cancelled,), daemon=True)
        self._thread.start()

    def _keep_ready(self, cancelled):
        deadline = time.monotonic()+600
        while not cancelled():
            try:
                h = self._json('/health')
                if not (h.get('ok') and h.get('cuda_available') and h.get('device') == 'cuda:0'
                        and h.get('checkpoint_step') == 10000 and h.get('action_dim') == 14
                        and h.get('num_actions') == 30 and h.get('norm_tag') == 'yam_reviewed_groot'):
                    raise RuntimeError('GR00T checkpoint contract mismatch')
                self._warm_state = 'ready'; self._ready.set(); deadline = time.monotonic()+600
                delay = 30
            except RuntimeError:
                self._ready.clear(); self._warm_state = 'warming'; delay = 2
                if time.monotonic() >= deadline:
                    self._failure = 'GR00T did not become ready within ten minutes'
                    self._warm_state = 'failed'; return
            until = time.monotonic()+delay
            while not cancelled() and time.monotonic() < until:
                time.sleep(.1)
        self._warm_state = 'released'

    def wait_ready(self, cancelled):
        waited = not self._ready.is_set()
        while not self._ready.wait(.1):
            if cancelled(): raise RuntimeError('GR00T preparation cancelled')
            if self._failure: raise RuntimeError(self._failure)
        if cancelled(): raise RuntimeError('GR00T preparation cancelled')
        return waited

    def build_trajectory(self, prompt, observation, first_step_id):
        if not self._ready.is_set():
            raise RuntimeError('GR00T is not ready')
        state = []
        for arm in ('left','right'):
            q = observation[arm+'_joints_deg']; g = observation[arm+'_gripper']
            if len(q)!=6 or not all(math.isfinite(v) for v in q) or not isinstance(g,(int,float)) or not 0<=g<=1:
                raise ValueError('GR00T requires measured 6+6 joints and both grippers')
            state.extend(math.radians(v) for v in q); state.append(g)
        frames = self._camera_source.capture_for_policy(observation, cancelled=self.cancelled)
        if {f.name for f in frames} != {'top','left','right'}:
            raise ValueError('GR00T requires top and both wrist cameras')
        from PIL import Image
        images = {}
        for frame in frames:
            output = io.BytesIO()
            with Image.open(io.BytesIO(frame.jpeg)) as image:
                image.convert('RGB').resize((640,360)).save(output,format='JPEG')
            images[frame.name+'_cam'] = {'encoding':'jpeg_base64','data':base64.b64encode(output.getvalue()).decode()}
        if self.cancelled(): raise RuntimeError('GR00T cancelled')
        result = self._json('/act', {'model':MODEL,'instruction':prompt,'state':state,'images':images,'action_units':'radians'})
        rows = result.get('actions')
        if (result.get('model') != MODEL or result.get('checkpoint_step') != 10000
                or result.get('action_units') != 'radians' or not isinstance(rows,list) or len(rows)!=30
                or any(not isinstance(r,list) or len(r)!=14 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in r) for r in rows)
                or any(not 0<=r[i]<=1 for r in rows for i in (6,13))):
            raise ValueError('Invalid GR00T action chunk')
        if self.cancelled(): raise RuntimeError('GR00T cancelled')
        # One 30 Hz chunk covers one second. Sample at the gateway's 10 Hz
        # deadlines without inserting ramps that stretch the learned trajectory.
        # Preserve the existing .01 rad / 100 ms bound by rejecting oversize
        # steps, not by silently retiming model actions.
        points = []; current = dict(observation); solver = IKSolver()
        for row in rows[2::3]:
            command = IKCommand(ArmIKCommand('joints',tuple(math.degrees(v) for v in row[:6])),
                                ArmIKCommand('joints',tuple(math.degrees(v) for v in row[7:13])),row[6],row[13])
            resolved = solver.resolve(command)
            point = {'step_id': first_step_id+len(points)}
            for arm in ('left', 'right'):
                joints = list(getattr(resolved, arm+'_joints_deg'))
                if any(abs(target-start) > math.degrees(.01)+1e-9
                       for target, start in zip(joints, current[arm+'_joints_deg'])):
                    raise ValueError('GR00T action exceeds the gateway motion limit; chunk rejected without retiming')
                point[arm+'_joints_deg'] = joints
                point[arm+'_gripper'] = getattr(resolved, arm+'_gripper')
            if len(points)+1>MAX_WAYPOINTS_PER_PACKET or first_step_id+len(points)+1>MAX_COMMANDS:
                raise ValueError('GR00T trajectory exceeds gateway budget')
            points.append(point); current.update(point)
        return points

    def trajectory_completed(self, observation, waypoint_count):
        pass

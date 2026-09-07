"""Stateful, no-demo RoboCurve Responses policy over the public YAM gateway.

Wire history follows inspect-robots-agent 0.26.0 _responses.py. The model sees
Cartesian tools; only locally resolved joint-waypoint packets reach AWS.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import time
import uuid

from .session import MAX_COMMANDS
from .providers import ResponsesAdapter, PolicyComplete
from .robocurve_contract import SYSTEM_PROMPT, TOOLS
from .robocurve_trajectory import RoboCurveTrajectory, NAMES, InvalidMove


class CallRecorder:
    """Local wire JSON plus deduplicated images. Never receives HTTP headers."""
    def __init__(self, root):
        self.path = Path(root) / ('robocurve_' + uuid.uuid4().hex)
        self.path.mkdir(parents=True, mode=0o700)
        (self.path / 'blobs').mkdir(mode=0o700)

    def _pack(self, value):
        if isinstance(value, str) and value.startswith('data:image/') and ';base64,' in value:
            prefix, encoded = value.split(';base64,', 1)
            data = base64.b64decode(encoded, validate=True)
            digest = hashlib.sha256(data).hexdigest()
            blob = self.path / 'blobs' / digest
            if not blob.exists():
                blob.write_bytes(data)
                blob.chmod(0o600)
            return prefix + ';base64,$blob:' + digest
        if isinstance(value, list):
            return [self._pack(v) for v in value]
        if isinstance(value, dict):
            return {k: self._pack(v) for k, v in value.items()}
        return value

    def write(self, kind, **values):
        path = self.path / 'calls.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(self._pack({'kind': kind, 'time': time.time(), **values}), allow_nan=False) + '\n')
        path.chmod(0o600)


class RoboCurveResponsesAdapter(ResponsesAdapter):
    def __init__(self, api_key, model, endpoint, *, camera_source=None, recording_root=None):
        super().__init__(api_key, model, endpoint, camera_source=camera_source)
        self._geometry = RoboCurveTrajectory()
        self._history = []
        self._prompt = None
        self._episode = None
        self._pending = None
        self._calls = 0
        self._failures = 0
        self._note = None
        self._outcome = None
        self._recorder = CallRecorder(recording_root) if recording_root is not None else None
        self.cancelled = lambda: False
        self.interaction_sink = None

    def _interaction(self, kind, message, **details):
        if self.interaction_sink:
            self.interaction_sink(kind, message, call=self._calls, **details)

    def public_config(self):
        return {**super().public_config(), 'policy': 'robocurve_no_demo',
                'model_calls': self._calls, 'history_items': len(self._history),
                'note': self._note, 'outcome': self._outcome,
                'pending_tool': self._pending['call_id'] if self._pending else None,
                'recording_path': str(self._recorder.path) if self._recorder else None}

    def _record(self, kind, **values):
        if self._recorder:
            self._recorder.write(kind, **values)

    def _observation_message(self, prompt, observation, first_step_id):
        _, eef, joints = self._geometry.observe(observation)
        text = ('Current observation.\nInstruction: ' + prompt
                + '\nstate[joint_pos]: ' + str([round(float(v), 4) for v in joints])
                + '\nstate[eef_state]: ' + ' '.join(f'{k}={v:.4f}' for k, v in zip(NAMES, eef))
                + f'\nGateway waypoints remaining in this session: {MAX_COMMANDS-first_step_id}.')
        content = [{'type': 'input_text', 'text': text}]
        if self._camera_source is None:
            raise RuntimeError('RoboCurve model inference requires a camera frame source')
        self._vision_state = 'capturing'
        self._vision_frames = []
        try:
            frames = {frame.name: frame for frame in self._camera_source.capture(observation)}
            if set(frames) != {'top', 'left', 'right'}:
                raise RuntimeError('RoboCurve requires top, left, and right camera frames')
            for name in ('top', 'left', 'right'):
                frame = frames[name]
                self._vision_frames.append(frame.summary())
                content.extend([
                    {'type': 'input_text', 'text': f"camera '{name}_cam' (step {first_step_id}):"},
                    {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(frame.jpeg).decode('ascii'), 'detail': 'high'},
                ])
        except Exception:
            self._vision_state = 'camera_error'
            raise
        return {'role': 'user', 'content': content}

    def _tool_result(self, call_id, result):
        item = {'type': 'function_call_output', 'call_id': call_id,
                'output': json.dumps(result, allow_nan=False)}
        self._history.append(item)
        self._record('tool_result', item=item)
        message = 'Tool result recorded for the next model call'
        if result.get('ok') is False:
            message = 'Local validation rejected the move; no motion sent: '+result['error']
        elif result.get('status') == 'give_up':
            message = 'Astra gave up: '+result.get('reason','')
        elif result.get('status') == 'done':
            message = 'Astra reported done: '+result.get('summary','')
        self._interaction('tool_error' if result.get('ok') is False else 'tool_result',
                          message,
                          call_id=call_id, result=result)

    def trajectory_completed(self, observation, waypoint_count):
        """Controller calls only after acceptance, every progress event and settle."""
        if self._pending is None or waypoint_count != self._pending['waypoint_count']:
            raise RuntimeError('Completed trajectory does not match the pending model tool')
        if observation.get('step_id') != self._pending['last_step_id'] + 1:
            raise RuntimeError('Completed model tool has the wrong observation cursor')
        self._tool_result(self._pending['call_id'], {'ok': True, 'steps': waypoint_count,
                          'status': 'completed', 'cadence_hz': 10})
        self._pending = None
        self._failures = 0

    def infer(self, prompt, observation):
        raise RuntimeError('RoboCurve policy requires the joint-waypoint packet transport')

    def build_trajectory(self, prompt, observation, first_step_id):
        if self._pending is not None:
            raise RuntimeError('Cannot ask the model again before its motion completes')
        if self._outcome is not None:
            raise PolicyComplete(self._outcome['status'])
        identity = tuple(observation.get(k) for k in ('episode_id', 'lease_id'))
        if not self._history:
            self._prompt, self._episode = prompt, identity
            self._history = [{'role': 'system', 'content': SYSTEM_PROMPT},
                             {'role': 'user', 'content': 'Goal: ' + prompt}]
        elif prompt != self._prompt or identity != self._episode:
            raise RuntimeError('A new trial requires a fresh RoboCurve policy instance')
        while self._calls < 100:
            if self.cancelled():
                return []
            self._history.append(self._observation_message(prompt, observation, first_step_id))
            if self.cancelled():
                return []
            payload = {'model': self.model, 'input': copy.deepcopy(self._history),
                       'tools': copy.deepcopy(TOOLS), 'store': False,
                       'include': ['reasoning.encrypted_content']}
            self._calls += 1
            self._record('request', call=self._calls, request=payload)
            self._vision_state = 'requesting'
            self._interaction('model_request', f'Model call {self._calls}: waiting for {self.model}',
                              model=self.model, history_items=len(self._history),
                              observation=self._history[-1]['content'][0]['text'],
                              cameras=list(self._vision_frames))
            try:
                raw = self._post_json(payload)
            except Exception as exc:
                self._vision_state = 'request_error'
                self._interaction('model_error', str(exc))
                raise
            self._vision_state = 'response_received'
            self._record('response', call=self._calls, response=raw)
            from .interactions import wire_events
            summary = wire_events([{'kind':'response', 'call':self._calls,
                                    'time':time.time(), 'response':raw}])[0]
            self._interaction('model_response', summary['message'], tools=summary['details']['tools'])
            if self.cancelled():
                return []
            output = raw.get('output')
            if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
                raise RuntimeError('Model returned malformed Responses output')
            # Preserve the original encrypted reasoning + function-call items.
            # Never synthesize an assistant turn or discard tool-call identity.
            self._history.extend(copy.deepcopy(output))
            calls = [item for item in output if item.get('type') == 'function_call']
            if any(not isinstance(c.get('call_id'), str) or not c['call_id'] for c in calls):
                raise RuntimeError('Model tool call is missing its call_id')
            if len({c['call_id'] for c in calls}) != len(calls):
                raise RuntimeError('Model returned duplicate tool call IDs')
            try:
                if len(calls) != 1:
                    raise InvalidMove('Respond with exactly one tool call per turn')
                call = calls[0]
                try:
                    args = json.loads(call.get('arguments', ''))
                except (TypeError, ValueError) as exc:
                    raise InvalidMove('Tool arguments must be a JSON object') from exc
                if not isinstance(args, dict):
                    raise InvalidMove('Tool arguments must be a JSON object')
                name = call.get('name')
                if name in ('done', 'give_up'):
                    field = 'summary' if name == 'done' else 'reason'
                    if set(args) != {field, 'hindsight'} or any(not isinstance(v, str) for v in args.values()):
                        raise InvalidMove(f'{name} requires {field} and hindsight strings')
                    self._outcome = {'status': name, **args}
                    self._note = args[field]
                    self._tool_result(call['call_id'], self._outcome)
                    raise PolicyComplete(name)
                if name != 'move_to':
                    raise InvalidMove('Supported tools are move_to, done and give_up')
                if set(args) != {'targets', 'note'} or not isinstance(args.get('note'), str) or not args['note'].strip():
                    raise InvalidMove('move_to requires targets and a nonempty note')
                self._note = args['note']
                points = self._geometry.build(args['targets'], observation, first_step_id)
                self._pending = {'call_id': call['call_id'], 'waypoint_count': len(points),
                                 'last_step_id': points[-1]['step_id']}
                self._record('joint_packet', call=self._calls, cadence_hz=10, waypoints=points)
                self._interaction('packet_ready', f'Prepared {len(points)} joint waypoints at 10 Hz',
                                  waypoint_count=len(points), first=points[0], last=points[-1])
                return points
            except InvalidMove as exc:
                for call in calls:
                    self._tool_result(call['call_id'], {'ok': False, 'error': str(exc),
                                                       'code': exc.code, 'stage':'local_validation', 'steps': 0})
                if not calls:
                    self._history.append({'role': 'user', 'content': str(exc)})
                self._failures += 1
                if self._failures >= 3:
                    raise RuntimeError(f'Model failed three consecutive tool attempts: {exc}') from exc
        raise RuntimeError('RoboCurve model call budget of 100 exhausted')


class OpenAIAdapter(RoboCurveResponsesAdapter):
    provider_name = 'openai'

    def __init__(self, api_key, model, endpoint='https://api.openai.com/v1/responses', **kwargs):
        super().__init__(api_key, model, endpoint, **kwargs)


class AstraAdapter(RoboCurveResponsesAdapter):
    provider_name = 'astra'

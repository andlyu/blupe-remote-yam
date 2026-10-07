"""ASPIRE saved-script subprocess connected to the existing code-policy API flow.

The parent RunnerController retains the session capability/lease. An owner-only
Unix socket exposes observations and whole paired paths to ASPIRE's adapter.
Hardware and simulation use the same existing runner session flow.
It never creates a queue entry itself or implements another robot controller.
"""
from copy import deepcopy
import json
from pathlib import Path
from queue import Empty
import socket
import tempfile
import threading
import time

import numpy as np

from .aspire_adapter import AspireNotReady, ObservationEnvironment, paired_trajectory, vector
from .program_packet_bridge import ProgramPacketBridge
from .providers import PolicyComplete
from .session import MockSessionAPI

MAX_RPC_BYTES = 2_000_000


def _read_message(stream):
    raw = stream.readline(MAX_RPC_BYTES+1)
    if not raw.endswith(b'\n') or len(raw)>MAX_RPC_BYTES:
        raise ValueError('Invalid or oversized ASPIRE bridge message')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('ASPIRE bridge message must be an object')
    return data


def _write_message(stream, value):
    raw = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()+b'\n'
    if len(raw)>MAX_RPC_BYTES:
        raise ValueError('ASPIRE bridge response is oversized')
    stream.write(raw)
    stream.flush()


class BridgeClient:
    def __init__(self, path, timeout_s=90, *, on_timing=None):
        self.path, self.timeout_s = str(path), timeout_s
        self.on_timing = on_timing

    def call(self, operation, **arguments):
        # A timed-out motion request is never replayed.
        started_at, started = time.time(), time.perf_counter()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout_s)
            sock.connect(self.path)
            with sock.makefile('rwb') as stream:
                _write_message(stream, dict(operation=operation, arguments=arguments, sent_at=time.time()))
                reply = _read_message(stream)
        if self.on_timing is not None:
            self.on_timing(dict(operation=operation, started_at=started_at,
                duration_s=time.perf_counter()-started, server=reply.get('timing', {})))
        if reply.get('ok') is not True:
            raise AspireNotReady(reply.get('error', 'ASPIRE API bridge failed'))
        return reply['result']


class BridgeServer:
    def __init__(self, handler):
        self.handler = handler
        self._directory = tempfile.TemporaryDirectory(prefix='aspire-api-')
        self.path = str(Path(self._directory.name)/'bridge.sock')
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(self.path)
        Path(self.path).chmod(0o600)
        # Three camera workers can connect together before the serving thread
        # accepts the first request. On macOS a full Unix-socket backlog refuses
        # connect() immediately. Buffer read bursts; keep every RPC serial below.
        self._socket.listen(16)
        self._socket.settimeout(.1)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True, name='aspire-api-bridge')

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._closed.set()
        self._thread.join(2)
        self._socket.close()
        self._directory.cleanup()

    def _serve(self):
        while not self._closed.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(90)
                with conn.makefile('rwb') as stream:
                    try:
                        message = _read_message(stream)
                        handler_at, handler_started = time.time(), time.perf_counter()
                        result = self.handler(message['operation'], message.get('arguments', {}))
                        reply = dict(ok=True, result=result)
                        if type(message.get('sent_at')) in (int, float):
                            reply['timing'] = dict(handler_s=time.perf_counter()-handler_started,
                                send_to_handler_s=max(0., handler_at-message['sent_at']))
                    except Exception as exc:
                        reply = dict(ok=False, error=type(exc).__name__+': '+str(exc))
                    try:
                        _write_message(stream, reply)
                    except (OSError, ValueError):
                        pass


class AspireApiPolicy(ProgramPacketBridge):
    provider_name = 'aspire'
    model = 'ASPIRE official saved-script harness'

    def __init__(self, *, run_harness, calibration, robot_id, task, recording_path=None,
                 allow_hardware=None):
        self.run_harness = run_harness
        self.report = deepcopy(calibration)
        self.robot_id, self.task = robot_id, task
        self.recording_path = str(recording_path) if recording_path else None
        self.allow_hardware = allow_hardware
        self._expected_source = None if allow_hardware is None else 'hardware' if allow_hardware else 'simulation'
        self.cancelled = lambda: False
        self.refresh_observation = None
        self.interaction_sink = None
        self._init_packet_bridge()
        self._identity = None
        self._feedback_state = None
        self._finished = False
        self._outcome = None

    def public_config(self):
        return dict(provider=self.provider_name, model=self.model,
            recording_path=self.recording_path, task=self.task,
            implementation='upstream_harness_with_existing_code_policy_api_transport',
            physical_motion_enabled=self.allow_hardware, phase='complete' if self._finished else 'api_bridge',
            api_key_configured=False)

    def infer(self, *args):
        raise RuntimeError('ASPIRE yields complete paired paths, not model IK commands')

    def validate_session_admission(self, api):
        # Resolve transport mode only. Admission belongs to RunnerController/API;
        # no commissioning, preview receipt or calibration-match gate is added.
        if self.allow_hardware is None:
            self.allow_hardware = not isinstance(api, MockSessionAPI)
        self._expected_source = 'hardware' if self.allow_hardware else 'simulation'

    def _feedback(self, *, refresh=True):
        self._check_cancelled()
        state = deepcopy(self._feedback_state)
        if not state:
            raise AspireNotReady('Wait for the queue lease and ready observation')
        if state.get('source') != self._expected_source:
            raise AspireNotReady('ASPIRE feedback source does not match the selected execution mode')
        stamp = state.get('observed_at')
        age = time.time()-stamp if type(stamp) in (int, float) else float('inf')
        if refresh and not 0<=age<=2 and callable(self.refresh_observation):
            refreshed = self.refresh_observation()
            if refreshed.get('step_id') != state.get('step_id'):
                raise AspireNotReady('ASPIRE feedback changed step unexpectedly')
            self._feedback_state = state = deepcopy(refreshed)
            # A new status read establishes a new pose, not continuity with an
            # earlier completion. Only the owner's same-cursor, no-active-packet
            # reconciliation may explicitly preserve the old completion proof.
            if state.pop('_verified_capture_refresh', False) is not True:
                for field in ('settled_after', 'settled_pose', '_capture_context'):
                    state.pop(field, None)
        if tuple(state.get(k) for k in ('episode_id', 'lease_id')) != self._identity:
            raise AspireNotReady('ASPIRE observation changed episode/lease')
        if state.get('source') != self._expected_source:
            raise AspireNotReady('ASPIRE feedback changed source')
        if state.get('jetson_id', state.get('robot_id')) != self.robot_id:
            raise AspireNotReady('ASPIRE observation changed robot')
        context = state.get('_capture_context')
        if context is not None and context != [state.get(k) for k in
                ('episode_id', 'lease_id', 'step_id', 'jetson_id', 'source')]:
            raise AspireNotReady('ASPIRE settled capture changed lease/cursor/source')
        # Controller validates station freshness/safety; ProgramPacketBridge
        # correlates the actual settled completion. Do not add a shorter cutoff.
        for side in ('left', 'right'):
            vector(state[side+'_joints_deg'], 6, side+' measured joints')
            grip = state.get(side+'_gripper')
            if type(grip) not in (int, float) or not np.isfinite(grip) or not 0<=grip<=1:
                raise AspireNotReady('ASPIRE requires measured gripper feedback')
        return state

    def reconciled_observation(self, observation, station):
        """Use controller-validated state, retaining the correlated settle epoch.

        The controller calls this only after completion/lease/cursor and station
        reconciliation, with no local trajectory in flight. Later stationary
        telemetry is a newer pose sample, not a new movement completion.
        """
        if observation.get('source') != 'hardware':
            return observation
        if station.get('jetson_id') != self.robot_id or station.get('source') != 'hardware':
            raise AspireNotReady('Reconciled ASPIRE feedback changed robot/source')
        from .feedback import feedback_decision
        decision, reason = feedback_decision(observation, station, now=time.time(),
            require_home=observation.get('step_id') == 0)
        if decision != 'ready':
            raise AspireNotReady('Reconciled ASPIRE feedback is not ready: '+reason)
        for field in ('episode_id', 'lease_id', 'step_id'):
            # Public station telemetry omits lease/cursor fields. The
            # controller's normalized monitor includes them as None; they are
            # not a conflicting assignment. The leased completion remains the
            # authority, and every supplied station identity must still agree.
            if station.get(field) is not None and station[field] != observation.get(field):
                raise AspireNotReady('Reconciled ASPIRE feedback changed lease/cursor')
        state = deepcopy(observation)
        for field in ('observed_at', 'left_joints_deg', 'right_joints_deg',
                      'left_gripper', 'right_gripper'):
            state[field] = deepcopy(station[field])
        state['settled_after'] = observation['observed_at']
        # A frame after completion but before the newer telemetry must use the
        # completion's measured wrist pose, not a later pose paired retrospectively.
        state['settled_pose'] = {field: deepcopy(observation[field]) for field in
            ('observed_at', 'left_joints_deg', 'right_joints_deg', 'left_gripper', 'right_gripper')}
        state['_capture_context'] = [state.get(k) for k in
            ('episode_id', 'lease_id', 'step_id', 'jetson_id', 'source')]
        return state

    def refresh_reconciled_observation(self, previous, current):
        """Retain a completion only after the owner confirms no later packet.

        RunnerController checks the live lease/cursor and absence of an active
        trajectory before this hook. A bare status refresh provides no proof.
        """
        state = deepcopy(current)
        for field in ('settled_after', 'settled_pose', '_capture_context', '_verified_capture_refresh'):
            state.pop(field, None)
        context = previous.get('_capture_context')
        fields = ('episode_id', 'lease_id', 'step_id', 'jetson_id', 'source')
        if context is None or context != [current.get(k) for k in fields]:
            return state
        from .feedback import feedback_decision
        decision, _ = feedback_decision(previous, current, now=time.time(),
            require_home=current.get('step_id') == 0)
        if decision == 'ready':
            for field in ('settled_after', 'settled_pose', '_capture_context'):
                state[field] = deepcopy(previous[field])
            state['_verified_capture_refresh'] = True
        return state

    @staticmethod
    def _public_feedback(state):
        fields = ('observed_at', 'settled_after', 'source', 'mode', 'homed', 'settled', 'safety',
                  'left_joints_deg', 'right_joints_deg', 'left_gripper', 'right_gripper', 'images')
        result = {**{k: deepcopy(state[k]) for k in fields if k in state},
                  'robot_id': state.get('jetson_id', state.get('robot_id'))}
        if state.get('settled_pose') is not None:
            result['settled_pose'] = {k: deepcopy(state['settled_pose'][k]) for k in
                ('observed_at', 'left_joints_deg', 'right_joints_deg', 'left_gripper', 'right_gripper')}
        return result

    def _rpc(self, operation, arguments):
        started = time.perf_counter()
        # These operations carry no pose or command. Keep the local cancellation,
        # robot/source and lease checks, without hiding HTTP reads in every
        # camera/segmentation cancellation check. Observation/motion still refresh.
        state = self._feedback(refresh=operation not in ('configuration', 'calibration'))
        feedback_s = time.perf_counter()-started
        if operation == 'observation':
            return self._public_feedback(state)
        if operation == 'calibration':
            return deepcopy(self.report)
        if operation == 'configuration':
            return dict(physical_motion_enabled=self.allow_hardware,
                source=self._expected_source, robot_id=self.robot_id)
        if operation != 'trajectory':
            raise ValueError('Unknown ASPIRE bridge operation')
        measured = [np.r_[np.deg2rad(state[s+'_joints_deg']), state[s+'_gripper']]
                    for s in ('left', 'right')]
        points = paired_trajectory(arguments['timestamps'], arguments['left'], arguments['right'],
            measured_left=measured[0], measured_right=measured[1],
            playback_speed=arguments.get('playback_speed', 1.),
            start_interp_s=arguments.get('start_interp_s', 1.), first_step_id=state['step_id'])
        queued_at = time.perf_counter()
        feedback = self._execute(points)
        return dict(success=True, status='done', waypoint_count=len(points),
                    feedback=self._public_feedback(feedback),
                    timing=dict(feedback_s=feedback_s, prepare_packet_s=queued_at-started-feedback_s,
                                completion_wait_s=time.perf_counter()-queued_at))

    def _run_program(self):
        try:
            with BridgeServer(self._rpc) as bridge:
                self._outcome = self.run_harness(bridge.path)
            if not isinstance(self._outcome, dict):
                raise ValueError('ASPIRE harness did not return a result object')
        except Exception as exc:
            # Preserve a real packet/controller error over a secondary
            # exception from the stopped harness.
            self._failure = self._failure or type(exc).__name__+': '+str(exc)
            self._outcome = dict(status='FAILED', success=False, reason=self._failure)
        finally:
            self._finished = True

    def build_trajectory(self, prompt, observation, first_step_id):
        if prompt != self.task:
            raise ValueError('ASPIRE task changed during a run')
        self._check_cancelled()
        if self._pending is not None:
            raise RuntimeError('Wait for measured packet completion')
        identity = tuple(observation.get(k) for k in ('episode_id', 'lease_id'))
        if not all(isinstance(value, str) and value for value in identity):
            raise AspireNotReady('ASPIRE requires an assigned episode and lease')
        if self._identity is None:
            self._identity = identity
        elif self._identity != identity:
            raise AspireNotReady('A new episode/lease requires a new ASPIRE run')
        if observation.get('step_id') != first_step_id:
            raise AspireNotReady('ASPIRE packet cursor changed')
        self._feedback_state = deepcopy(observation)
        state = self._feedback()
        if self._completed is not None:
            if observation['step_id'] != self._completed['step_id']:
                raise AspireNotReady('ASPIRE completion changed before worker resume')
            self._replies.put_nowait(deepcopy(state))
            self._completed = None
        if self._thread is None:
            self._thread = threading.Thread(target=self._run_program, daemon=True, name='aspire-harness')
            self._thread.start()
        while True:
            self._check_cancelled()
            if self._finished and self._packets.empty():
                raise PolicyComplete('ASPIRE program ended; task outcome is in harness artifacts')
            try:
                points = self._packets.get(timeout=.05)
            except Empty:
                continue
            self._check_cancelled()
            self._feedback()
            if not points or points[0]['step_id'] != first_step_id:
                raise AspireNotReady('ASPIRE yielded the wrong packet cursor')
            self._pending = dict(waypoint_count=len(points), last_step_id=points[-1]['step_id'])
            return points

    def trajectory_failed(self, code, message, waypoint_count, *, feedback=None):
        self._failure = f'{code}: {message}'
        self._pending = None


class ApiBridgeEnvironment(ObservationEnvironment):
    execution_mode = 'simulation_api_bridge'
    def __init__(self, *, bridge_socket, **kwargs):
        super().__init__(**kwargs)
        def bridge_timing(timing):
            spans = getattr(self._local, 'capture_spans', None)
            if spans is not None:
                spans.append(dict(stage='bridge_rpc', **timing))
            elif timing['operation'] in ('observation', 'trajectory'):
                try:
                    self._save_json('api_rpc_timing', timing)
                except OSError:
                    pass  # Diagnostics cannot turn a completed command into a failure.
        self.bridge = BridgeClient(bridge_socket, on_timing=bridge_timing)
        configuration = self.bridge.call('configuration')
        def cancelled():
            # The owner checks its real runner cancellation and lease before
            # replying; a stopped/expired session aborts observation recovery.
            self.bridge.call('configuration')
            return False
        self.cancelled = cancelled
        self.physical_motion_enabled = configuration['physical_motion_enabled']
        if self.physical_motion_enabled:
            self.execution_mode = 'leased_hardware_api_bridge'

    def status(self):
        state = self.bridge.call('observation')
        if state.get('robot_id') != self.robot_id:
            raise AspireNotReady('ASPIRE bridge robot mismatch')
        return state

    def calibration(self):
        return self.bridge.call('calibration')

    def recording_status(self):
        # Recording must keep reading cameras during an in-flight trajectory.
        # It uses the public read-only endpoint, not the blocking completion RPC
        # or the control feedback accessor. Robot identity remains checked.
        return ObservationEnvironment.status(self)

    def _move_bimanual_joint_keypoints(self, timestamps, left_joint_positions,
            right_joint_positions, left_gripper_positions=None, right_gripper_positions=None,
            playback_speed=1., command_hz=60., start_interp_s=1.):
        # Convert complete upstream paths once; never stream the arms separately.
        state = self.status()
        targets = []
        for side, values, grip in [('left', left_joint_positions, left_gripper_positions),
                                   ('right', right_joint_positions, right_gripper_positions)]:
            arr = np.asarray(values, dtype=float)
            if arr.ndim != 2 or arr.shape not in ((len(timestamps), 6), (len(timestamps), 7)):
                raise ValueError('ASPIRE paths require (N,6) or (N,7)')
            if grip is None:
                grips = arr[:, 6] if arr.shape[1] == 7 else np.full(len(arr), state[side+'_gripper'])
            else:
                grips = np.asarray(grip, dtype=float).reshape(-1)
                if len(grips) != len(arr):
                    raise ValueError('ASPIRE gripper path length mismatch')
            targets.append(np.column_stack([arr[:, :6], grips]).tolist())
        return self.bridge.call('trajectory', timestamps=np.asarray(timestamps).tolist(),
            left=targets[0], right=targets[1], playback_speed=playback_speed,
            start_interp_s=start_interp_s)

    move_bimanual_joint_keypoints = _move_bimanual_joint_keypoints

    def set_gripper(self, side, pos, timeout=3., vel_limit=None, torque_limit=None):
        if not self.physical_motion_enabled:
            return self.deny_motion()
        if side not in ('left', 'right') or type(pos) not in (float, int) or not np.isfinite(pos) or not 0<=pos<=1:
            raise ValueError('Invalid normalized API gripper command')
        state = self.status()
        # Arms hold measured encoder positions; both jaws travel in one paired
        # path. API/gateway own motor limits, never upstream local motor gains.
        delta = abs(float(pos)-state[side+'_gripper'])
        # Encode at the existing 10 Hz / 0.3-per-step API contract, without
        # the integration's former 0.5/s ramp or minimum 0.5-second duration.
        steps = max(1, int(np.ceil(delta/.3)))
        count = steps+1
        timestamps = np.arange(count)/10.
        targets = []
        for arm in ('left', 'right'):
            q = np.deg2rad(vector(state[arm+'_joints_deg'], 6, arm+' joints'))
            g = np.linspace(state[arm+'_gripper'], float(pos), count) if arm == side else np.full(count, state[arm+'_gripper'])
            targets.append(np.column_stack([np.tile(q, (count, 1)), g]))
        self._save_json('api_gripper_request', dict(side=side, target=pos,
            requested_native_vel_limit=vel_limit, requested_native_torque_limit=torque_limit,
            limit_owner='Existing API/controller; native motor settings are not applied'))
        return self._move_bimanual_joint_keypoints(timestamps, targets[0], targets[1], start_interp_s=0.)

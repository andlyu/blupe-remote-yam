"""Authenticated ASPIRE packet worker; only RunnerController owns robot commands.

The worker receives measured observations and returns proposed paired packets.
It receives no Session API capability and cannot enqueue, dispatch, or Stop a
robot. Long calls use polling and a reverse observation-refresh handshake.
"""
from collections import deque
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import re
import secrets
import threading
import time
from urllib import request
from urllib.parse import urlsplit, parse_qs
import uuid

from .providers import PolicyComplete
from .remote_session_policy import NoRedirect
from .session import MockSessionAPI

LIMIT = 2_000_000
PUBLIC_FIELDS = {'provider', 'model', 'display_name', 'phase', 'launch_route',
    'implementation', 'reasoning_effort', 'response_speed', 'task_progress',
    'task_outcome', 'preparation_status', 'segmentation_backend',
    'segmentation_model', 'vision_ready', 'vision', 'server_contact_issue'}
METHODS = {'prepare_before_session', 'validate_session_admission', 'build_trajectory',
    'trajectory_completed', 'trajectory_failed', 'review_after_session_stop'}


def encode(value):
    raw = json.dumps(value, allow_nan=False).encode()
    if len(raw) > LIMIT:
        raise ValueError('ASPIRE worker message exceeds the transport limit')
    return raw


class WorkerRun:
    def __init__(self, provider=None):
        self.provider = None
        self.creation_complete = threading.Event()
        self.creation_error_type = None
        self.cancelled = threading.Event()
        self.closed = False
        self.last_seen = time.monotonic()
        self.lock = threading.RLock()
        self.calls = {}
        self.active = None
        self.refresh = None
        self.events = deque(maxlen=128)
        self.sequence = 0
        self.revision = 0
        if provider is not None:
            self._attach(provider)
            self.creation_complete.set()

    def _attach(self, provider):
        provider.cancelled = self.cancelled.is_set
        provider.refresh_observation = self.refresh_observation
        provider.interaction_sink = self.event
        self.provider = provider

    @staticmethod
    def _dispose(provider):
        try:
            vision = getattr(provider, 'vision_session', None)
            if vision:
                vision.close()
        finally:
            close = getattr(provider, 'close_transport', None)
            if callable(close):
                close()

    def create(self, factory, prompt):
        # Reserve the run identifier before construction, then return immediately.
        # CLI readiness and local model loading must not hold the HTTP/control lock.
        def work():
            provider = None
            try:
                if self.cancelled.is_set():
                    return
                provider = factory(prompt)
                with self.lock:
                    if not self.closed and not self.cancelled.is_set():
                        self._attach(provider)
                        provider = None
            except Exception as exc:
                # Never expose credentials, paths or arbitrary exception bodies.
                with self.lock:
                    self.creation_error_type = type(exc).__name__
            finally:
                if provider is not None:
                    try:
                        provider.cancelled = self.cancelled.is_set
                        self._dispose(provider)
                    except Exception as exc:
                        with self.lock:
                            self.creation_error_type = self.creation_error_type or type(exc).__name__
                self.creation_complete.set()
        threading.Thread(target=work, name='aspire-worker-create', daemon=True).start()

    def _await_provider(self):
        while not self.creation_complete.wait(.05):
            if self.cancelled.is_set():
                raise RuntimeError('ASPIRE provider creation cancelled')
        if self.creation_error_type:
            raise RuntimeError('ASPIRE provider creation failed')
        if self.provider is None:
            raise RuntimeError('ASPIRE provider creation cancelled')
        return self.provider

    def event(self, kind, message, **details):
        # Existing provider summaries are public; credentials are never inputs.
        with self.lock:
            self.sequence += 1
            self.events.append(dict(sequence=self.sequence, kind=kind,
                message=str(message)[:4000], details=details))

    def state(self, after=0):
        with self.lock:
            self.last_seen = time.monotonic()
            self.revision += 1
            creating = not self.creation_complete.is_set()
            creation = dict(status='running' if creating else 'failed' if self.creation_error_type
                else 'completed' if self.provider is not None else 'cancelled')
            if self.creation_error_type:
                creation['error_type'] = self.creation_error_type
            config = self.provider.public_config() if self.provider is not None else dict(
                provider='codex', model='gpt-6-astra', phase='creating_provider' if creating
                    else 'provider_creation_failed' if self.creation_error_type else 'stopped')
            result = dict(revision=self.revision, config={k:v for k,v in config.items() if k in PUBLIC_FIELDS},
                events=[e for e in self.events if e['sequence'] > after],
                busy=self.active is not None or creating, creation=creation)
            if self.refresh:
                result['refresh_id'] = self.refresh['id']
            return result

    def refresh_observation(self):
        item = dict(id=uuid.uuid4().hex, ready=threading.Event(), observation=None)
        with self.lock:
            if self.refresh is not None:
                raise RuntimeError('Only one observation refresh may be outstanding')
            self.refresh = item
        try:
            while not item['ready'].wait(.05):
                if self.cancelled.is_set():
                    raise RuntimeError('ASPIRE observation refresh cancelled')
            if self.cancelled.is_set():
                raise RuntimeError('ASPIRE observation refresh cancelled')
            return item['observation']
        finally:
            with self.lock:
                self.refresh = None

    def start(self, call_id, method, args, kwargs):
        if method not in METHODS or not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError('Unsupported ASPIRE worker call')
        with self.lock:
            self.last_seen = time.monotonic()
            if self.closed or (self.cancelled.is_set() and method not in ('review_after_session_stop','trajectory_failed')):
                raise ValueError('ASPIRE worker run stopped')
            body = hashlib.sha256(encode(dict(method=method, args=args, kwargs=kwargs))).hexdigest()
            if call_id in self.calls:
                if self.calls[call_id]['request'] != body:
                    raise ValueError('ASPIRE worker call identifier changed')
                return
            if self.active is not None:
                raise ValueError('Wait for the current ASPIRE worker call')
            # Keep only the latest result: old identifiers cannot replay calls.
            if len(self.calls) >= 4096:
                raise ValueError('ASPIRE worker call budget exhausted')
            for prior in self.calls.values():
                prior.pop('result', None)
                prior['status'] = 'expired'
            call = self.calls[call_id] = dict(status='running', request=body)
            self.active = call_id
        def work():
            try:
                provider = self._await_provider()
                if self.cancelled.is_set() and method not in ('review_after_session_stop','trajectory_failed'):
                    raise RuntimeError('ASPIRE worker run stopped')
                callback = getattr(provider, method, None)
                if method == 'validate_session_admission':
                    if kwargs or len(args) != 1 or type(args[0]) is not bool:
                        raise ValueError('Invalid execution mode')
                    value = callback(object() if args[0] else MockSessionAPI()) if callback else None
                else:
                    value = callback(*args, **kwargs) if callable(callback) else None
                encode(value)
                reply = dict(status='completed', result=value)
            except PolicyComplete:
                reply = dict(status='policy_complete')
            except Exception as exc:
                # Do not publish URLs, credentials or arbitrary exception bodies.
                reply = dict(status='failed', error_type=type(exc).__name__)
            with self.lock:
                call.update(reply)
                self.active = None
        threading.Thread(target=work, name='aspire-worker-call', daemon=True).start()

    def stop(self):
        self.cancelled.set()
        vision = getattr(self.provider, 'vision_session', None)
        if vision:
            vision.close()

    def close(self):
        self.stop()
        with self.lock:
            if self.closed:
                return
            self.closed = True
        close = getattr(self.provider, 'close_transport', None)
        if callable(close):
            close()


class AspireWorkerServer(ThreadingHTTPServer):
    """Bind to loopback and expose through an operator-managed TLS tunnel/VPN."""
    daemon_threads = True

    def __init__(self, address, token, robot_id, factory, *, idle_timeout_s=90):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('ASPIRE worker requires a service token of at least 32 characters')
        self.token, self.robot_id, self.factory = token, robot_id, factory
        self.runs, self.run_lock = {}, threading.RLock()
        self.idle_timeout_s = idle_timeout_s
        self.finished = threading.Event()
        super().__init__(address, WorkerHandler)
        threading.Thread(target=self.reap, name='aspire-worker-expiry', daemon=True).start()

    def reap(self):
        while not self.finished.wait(.5):
            with self.run_lock:
                expired = [key for key, run in self.runs.items()
                    if time.monotonic()-run.last_seen > self.idle_timeout_s]
                # A closed constructor may still be unwinding. Retain its ID and
                # capacity slot until late-provider cleanup finishes, so it cannot
                # be recreated concurrently after expiry.
                runs = [self.runs.pop(key) if self.runs[key].creation_complete.is_set()
                    else self.runs[key] for key in expired]
            for run in runs:
                run.close()

    def server_close(self):
        self.finished.set()
        with self.run_lock:
            runs, self.runs = list(self.runs.values()), {}
        for run in runs:
            run.close()
        super().server_close()


class WorkerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Authorization and task bodies never enter HTTP access logs.

    def reply(self, status, value):
        raw = encode(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        self.connection.settimeout(10)
        if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+self.server.token):
            return self.reply(401, {'error':'Service authentication required'})
        try:
            url = urlsplit(self.path)
            parts = url.path.strip('/').split('/')
            after = int(parse_qs(url.query).get('after', ['0'])[0])
            body = None
            if self.command == 'POST':
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= LIMIT:
                    return self.reply(413, {'error':'Invalid request size'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Request must be an object')
            if parts == ['health'] and self.command == 'GET':
                return self.reply(200, dict(ready=True, robot_id=self.server.robot_id, protocol=1))
            if len(parts) < 2 or parts[0] != 'runs' or not re.fullmatch('[a-f0-9]{32}', parts[1]):
                return self.reply(404, {'error':'Unknown route'})
            key = parts[1]
            with self.server.run_lock:
                run = self.server.runs.get(key)
                if len(parts) == 2 and self.command == 'POST':
                    if set(body) != {'prompt','robot_id'} or body['robot_id'] != self.server.robot_id:
                        raise ValueError('Worker robot does not match')
                    if not isinstance(body['prompt'], str) or not 1 <= len(body['prompt']) <= 4000:
                        raise ValueError('Invalid task')
                    if run is None:
                        if len(self.server.runs) >= 32:
                            return self.reply(503, {'error':'Worker capacity reached'})
                        # Selection/construction uses the existing polled call
                        # lifetime, before capture, queue admission or execution.
                        run = WorkerRun()
                        run.prompt = body['prompt']
                        self.server.runs[key] = run
                        run.create(self.server.factory, body['prompt'])
                    elif run.prompt != body['prompt']:
                        raise ValueError('Worker run identifier changed')
            if run is None:
                return self.reply(404, {'error':'Worker run expired'})
            if len(parts) == 2:
                return self.reply(200, run.state(after))
            if len(parts) == 3 and parts[2] in ('stop','close') and self.command == 'POST':
                if parts[2] == 'close':
                    run.close()
                else:
                    run.stop()
                return self.reply(200, run.state(after))
            if len(parts)==3 and parts[2]=='observation' and self.command=='POST':
                if not run.creation_complete.is_set() or run.provider is None:
                    raise ValueError('ASPIRE provider is not ready for observations')
                if set(body)!={'method','args'} or body['method'] not in (
                        'reconciled_observation','refresh_reconciled_observation'):
                    raise ValueError('Invalid observation hook')
                if not isinstance(body['args'],list) or len(body['args'])!=2:
                    raise ValueError('Invalid observation hook arguments')
                callback=getattr(run.provider,body['method'],None)
                result=callback(*body['args']) if callable(callback) else body['args'][0 if body['method']=='reconciled_observation' else 1]
                return self.reply(200, dict(observation=result))
            if len(parts) == 4 and parts[2] == 'refresh' and self.command == 'POST':
                with run.lock:
                    if not run.refresh or run.refresh['id'] != parts[3] or set(body) != {'observation'}:
                        raise ValueError('Observation refresh changed')
                    run.refresh['observation'] = body['observation']
                    run.refresh['ready'].set()
                return self.reply(200, {})
            if len(parts) == 4 and parts[2] == 'calls' and re.fullmatch('[a-f0-9]{32}', parts[3]):
                if self.command == 'POST':
                    if set(body) != {'method','args','kwargs'}:
                        raise ValueError('Invalid call')
                    run.start(parts[3], **body)
                state = run.state(after)
                with run.lock:
                    call = run.calls.get(parts[3])
                    if call is None:
                        return self.reply(404, {'error':'Unknown call'})
                    state['call'] = {k:v for k,v in call.items() if k != 'request'}
                return self.reply(200, state)
            return self.reply(404, {'error':'Unknown route'})
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error':'Invalid ASPIRE worker request'})
        except Exception:
            self.reply(503, {'error':'ASPIRE worker unavailable'})


class RemoteAspirePolicy:
    provider_name = 'codex'
    model = 'gpt-6-astra'
    share_conversation = True

    def __init__(self, origin, token, *, robot_id, poll_s=.2, timeout_s=650):
        u = urlsplit(origin)
        if (u.username or u.password or u.query or u.fragment or u.path not in ('','/')
                or not u.hostname or not (u.scheme == 'https' or
                    u.scheme == 'http' and u.hostname in ('127.0.0.1','localhost'))):
            raise ValueError('ASPIRE worker requires HTTPS or a loopback origin')
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('ASPIRE worker service credential is missing')
        self.origin, self._token, self.robot_id = origin.rstrip('/'), token, robot_id
        self.poll_s, self.timeout_s = poll_s, timeout_s
        self.cancelled = lambda: False
        self.refresh_observation = None
        self.interaction_sink = None
        self._open = request.build_opener(request.ProxyHandler({}), NoRedirect()).open
        self._run_id = uuid.uuid4().hex
        self._created = False
        self._closed = threading.Event()
        self._cursor = 0
        self._revision = 0
        self._state_lock = threading.RLock()
        self._config = dict(provider='codex', model=self.model, display_name='ASPIRE · Codex/Astra',
            phase='connecting', implementation='authenticated_aspire_packet_worker')
        # Fail before queue admission/quota acceptance if the configured worker
        # cannot be reached. This check starts neither a GPU nor a robot session.
        health = self._request('/health')
        if health.get('robot_id') != robot_id or health.get('protocol') != 1 or health.get('ready') is not True:
            raise ValueError('ASPIRE worker robot or protocol does not match')

    def _request(self, path, body=None):
        req = request.Request(self.origin+path, data=encode(body) if body is not None else None,
            headers={'Authorization':'Bearer '+self._token, 'Content-Type':'application/json'})
        try:
            with self._open(req, timeout=5) as response:
                raw = response.read(LIMIT+1)
            if len(raw) > LIMIT:
                raise ValueError('Oversized response')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Invalid response')
            return value
        except Exception:
            raise RuntimeError('ASPIRE worker is unavailable; this request was not replayed') from None

    def _accept(self, state):
        events=[]
        with self._state_lock:
            config = state.get('config', {})
            if state.get('revision',0) >= self._revision:
                self._revision=state.get('revision',0)
                self._config.update({k:v for k,v in config.items() if k in PUBLIC_FIELDS})
            for event in state.get('events', []):
                if event['sequence'] > self._cursor:
                    self._cursor = event['sequence']
                    events.append(event)
        # A controller callback can acquire its own lock. Never hold our state
        # lock while calling it: status reads acquire those locks in reverse order.
        if self.interaction_sink:
            for event in events:
                self.interaction_sink(event['kind'], event['message'], **event['details'])

    def public_config(self):
        with self._state_lock:
            return deepcopy(self._config)

    def prepare_before_session(self, prompt):
        if self.cancelled():
            raise RuntimeError('ASPIRE launch cancelled')
        self._created = True  # A lost response still requires idempotent Stop.
        try:
            self._accept(self._request('/runs/'+self._run_id, dict(prompt=prompt, robot_id=self.robot_id)))
            threading.Thread(target=self._watch, name='aspire-worker-heartbeat', daemon=True).start()
            return self._call('prepare_before_session', prompt)
        except BaseException:
            self.close_transport()
            raise

    def _watch(self):
        next_heartbeat = time.monotonic()+10
        while not self._closed.wait(.25):
            if self.cancelled():
                self._stop()
                return
            if time.monotonic() < next_heartbeat:
                continue
            next_heartbeat = time.monotonic()+10
            try:
                self._accept(self._request('/runs/'+self._run_id+'?after='+str(self._cursor)))
            except RuntimeError:
                self._stop()
                return

    def _stop(self):
        if self._created:
            try:
                self._accept(self._request('/runs/'+self._run_id+'/stop', {}))
            except RuntimeError:
                pass

    def _call(self, method, *args, ignore_cancel=False, **kwargs):
        path = '/runs/'+self._run_id+'/calls/'+uuid.uuid4().hex
        deadline = time.monotonic()+self.timeout_s
        try:
            if not ignore_cancel and self.cancelled():
                raise RuntimeError('ASPIRE worker call cancelled')
            state = self._request(path, dict(method=method, args=list(args), kwargs=kwargs))
            refreshed = set()
            while True:
                self._accept(state)
                if not ignore_cancel and self.cancelled():
                    raise RuntimeError('ASPIRE worker call cancelled')
                call = state['call']
                if call['status'] == 'completed':
                    return call.get('result')
                if call['status'] == 'policy_complete':
                    raise PolicyComplete('ASPIRE policy completed')
                if call['status'] in ('failed','expired'):
                    raise RuntimeError('ASPIRE worker call failed: '+call.get('error_type','unknown'))
                refresh_id = state.get('refresh_id')
                if refresh_id and refresh_id not in refreshed:
                    if not callable(self.refresh_observation):
                        raise RuntimeError('Runner observation refresh is unavailable')
                    observation = self.refresh_observation()
                    self._request('/runs/'+self._run_id+'/refresh/'+refresh_id, dict(observation=observation))
                    refreshed.add(refresh_id)
                if time.monotonic() >= deadline:
                    raise RuntimeError('ASPIRE worker call timed out; this request was not replayed')
                self._closed.wait(self.poll_s)
                state = self._request(path+'?after='+str(self._cursor))
        except PolicyComplete:
            raise
        except BaseException:
            self._stop()
            raise

    def validate_session_admission(self, api):
        return self._call('validate_session_admission', not isinstance(api, MockSessionAPI))

    def _observation_hook(self, method, *args):
        # These existing hooks reconcile data only and may run during a build
        # awaiting fresh feedback. They never dispatch or resume a packet.
        return self._request('/runs/'+self._run_id+'/observation',
            dict(method=method,args=list(args)))['observation']

    def reconciled_observation(self, observation, station):
        return self._observation_hook('reconciled_observation',observation,station)

    def refresh_reconciled_observation(self, previous, current):
        return self._observation_hook('refresh_reconciled_observation',previous,current)

    def build_trajectory(self, prompt, observation, first_step_id):
        return self._call('build_trajectory', prompt, observation, first_step_id)

    def trajectory_completed(self, observation, waypoint_count):
        return self._call('trajectory_completed', observation, waypoint_count)

    def trajectory_failed(self, code, message, waypoint_count, *, feedback=None):
        return self._call('trajectory_failed', code, message, waypoint_count, feedback=feedback,
            ignore_cancel=True)

    def review_after_session_stop(self):
        self._stop()
        try:
            deadline=time.monotonic()+30
            while self._request('/runs/'+self._run_id).get('busy'):
                if time.monotonic()>=deadline:
                    raise RuntimeError('ASPIRE worker did not finish cancellation before review')
                time.sleep(.05)
            return self._call('review_after_session_stop', ignore_cancel=True)
        finally:
            self._closed.set()

    def close_transport(self):
        self._closed.set()
        if self._created:
            try:
                self._accept(self._request('/runs/'+self._run_id+'/close', {}))
            except RuntimeError:
                pass

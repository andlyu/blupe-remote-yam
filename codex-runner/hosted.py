"""Browser runner with optional Stripe-funded runs. Run with uvicorn hosted:create_app --factory.

One process/replica owns all in-memory visitor sessions. No provider credentials
are loaded by default; paid mode explicitly loads a private server configuration.
See HOSTING.md for deployment.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit, parse_qs
from urllib import request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from remote_yam.submission_log import SubmissionLog, LoggedSessionAPI
from remote_yam.cameras import CameraFrameSource, CAMERA_NAMES, NoRedirect
from remote_yam.controller import RunnerController
from remote_yam.interactions import list_recordings, read_recording
from remote_yam.socials import social_handles, save_handles
from remote_yam.artifacts import log_archive, run_artifacts
from remote_yam.providers import RepeatingRaiseLowerAdapter
from remote_yam.robocurve_policy import OpenAIAdapter, AstraAdapter
from remote_yam.session import HttpSessionAPI, MockSessionAPI

ACTIVE = {"queued", "preparing", "running"}
COOKIE = "__Host-yam-visitor"


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


class HostedSessionAPI(HttpSessionAPI):
    def open_events(self, session_id):
        try:
            return super().open_events(session_id)
        except Exception:
            # create_session has already returned a capability. If attaching the
            # stream fails, remove this known queue entry before losing its ID.
            with suppress(Exception):
                self.stop_session(session_id)
            raise


class EphemeralController(RunnerController):
    """Release the adapter's retained credential after every worker exit."""
    def forget_key(self):
        self._stop_event.set()
        with self._lock:
            if self._provider is not None and hasattr(self._provider, "_api_key"):
                self._provider._api_key = ""

    def busy(self):
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def _run_loop(self, max_steps):
        try:
            super()._run_loop(max_steps)
        finally:
            self.forget_key()


@dataclass
class Visitor:
    controller: EphemeralController
    directory: Path
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    born: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    last_launch: float = -float("inf")
    chat_id: str = field(default_factory=lambda: secrets.token_hex(4))
    last_chat: float = -float("inf")
    last_stream_report: float = -float("inf")
    runner_task: str = ""
    runner_name: str = "Anonymous"
    runner_session_id: str | None = None
    launches: int = 0
    saved_keys: dict[str, str] = field(default_factory=dict, repr=False)
    retired: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self):
        with self.lock:
            self.retired = True
            self.saved_keys.clear()
            self.controller.forget_key()
            self.controller.disconnect("web_session_ended")
        # A cancelled provider HTTP request may take up to its transport timeout
        # to return. Do not remove its journal directory while it can still write.
        worker = self.controller._worker
        if worker is not None:
            worker.join(timeout=50)
        if not self.controller.busy():
            shutil.rmtree(self.directory, ignore_errors=True)


class HostedRunner:
    def __init__(self, *, public_origin: str, session_api: str | None,
                 camera_origin: str, astra_endpoint: str = "",
                 hardware_control: bool = False, development: bool = False,
                 max_sessions: int = 32, idle_seconds: float = 1800,
                 lifetime_seconds: float = 7200, api_factory=None,
                 provider_factory=None, payments=None, paid_model_key="", paid_model="gpt-6-astra", chat_database=None,
                 local_codex=False, default_provider="openai"):
        parsed = urlsplit(public_origin)
        if (not parsed.hostname or parsed.path not in {"", "/"} or parsed.query
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("YAM_WEB_ORIGIN must be an origin without a path or credentials")
        if parsed.scheme != "https" and not (
                development and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise ValueError("Public hosting requires HTTPS; HTTP is limited to explicit localhost development")
        if not session_api and not development:
            raise ValueError("YAM_SESSION_API is required outside localhost development")
        if astra_endpoint:
            endpoint = urlsplit(astra_endpoint)
            if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("The configured Astra endpoint must use HTTPS without credentials or a query")
        self.origin = public_origin.rstrip("/")
        if local_codex and not (development and parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'}):
            raise ValueError('Codex subscription access is restricted to the local runner')
        self.local_codex = local_codex
        self.default_provider = default_provider
        self.authority = parsed.netloc
        self.secure = parsed.scheme == "https"
        self.simulation = not session_api
        self.cookie_name = COOKIE if self.secure else "yam-local-visitor"
        self.astra_endpoint = astra_endpoint
        self.hardware_control = hardware_control
        self.max_sessions = max_sessions
        self.idle_seconds, self.lifetime_seconds = idle_seconds, lifetime_seconds
        self.api_factory = api_factory or (lambda: HostedSessionAPI(session_api, supports_trajectories=True) if session_api else MockSessionAPI())
        self.payments = payments
        self.paid_model_key = paid_model_key
        self.paid_model = paid_model
        self.provider_factory = provider_factory
        self.camera_source = CameraFrameSource(camera_origin)
        self.visitors: dict[str, Visitor] = {}
        self.monitor_api = self.api_factory()
        self.observation = None
        self.queue = None
        self.operator_auto_queue = None
        from remote_yam.past_runs import PastRuns
        self.past_runs = PastRuns()
        self.task = None
        self.cleanups = set()
        self.frame_cache = {}
        self.frame_locks = {name: threading.Lock() for name in CAMERA_NAMES}
        self.root = Path(tempfile.mkdtemp(prefix="yam-web-"))
        self.root.chmod(0o700)
        from remote_yam.chat_history import ChatHistory
        self.chat_history = ChatHistory(chat_database or self.root / "chat.sqlite3")
        self.social_database = Path(os.environ.get('YAM_SOCIAL_DATABASE', ROOT / 'data/run-socials.sqlite3'))
        from remote_yam.past_runs import RunNames
        self.run_names = RunNames(os.environ.get('YAM_RUN_NAMES_DATABASE', ROOT / 'data/run-names.sqlite3'))
        self.submission_log = SubmissionLog(os.environ.get("YAM_SUBMISSION_DATABASE", ROOT / "data/queue-submissions.sqlite3"))
        self.remembered_names = set()

    def remember_runner(self, visitor):
        state = visitor.controller.status()
        eid = state.get('episode_id')
        if eid and state.get('session_id') == visitor.runner_session_id:
            try:
                self.run_names.remember_result(eid, state)
            except (OSError, sqlite3.Error):
                pass
        if eid and eid not in self.remembered_names and state.get('session_id') == visitor.runner_session_id:
            # History storage must never prevent Stop or other robot controls.
            try:
                self.run_names.remember(eid, visitor.runner_name)
            except (OSError, sqlite3.Error):
                return
            self.remembered_names.add(eid)

    def new_visitor(self):
        if len(self.visitors) >= self.max_sessions:
            raise RequestError(503, "All browser sessions are busy. Please try again shortly.")
        capability = secrets.token_urlsafe(32)
        directory = self.root / secrets.token_hex(16)
        directory.mkdir(mode=0o700)
        visitor = Visitor(EphemeralController(
            LoggedSessionAPI(self.api_factory(), self.submission_log), hardware_control_enabled=self.hardware_control,
            recording_root=directory), directory)
        self.visitors[capability] = visitor
        return capability, visitor

    def refresh_monitor(self):
        from remote_yam.operator_status import read_auto_queue
        self.operator_auto_queue = read_auto_queue() if self.hardware_control else None
        try:
            self.observation = self.monitor_api.get_robot_observation("yam-1")
        except Exception:
            self.observation = None
        try:
            self.queue = self.monitor_api.get_queue_snapshot()
        except Exception:
            self.queue = None

    def camera_frame(self, name, url):
        # Share a short-lived frame between viewers without making model capture
        # depend on the browser cache. Never serve a cached frame after failure.
        with self.frame_locks[name]:
            cached = self.frame_cache.get(name)
            if cached and cached[0] == url and time.monotonic() - cached[1] < .25:
                return cached[2]
            self.frame_cache.pop(name, None)
            frame = self.camera_source.fetch(name, url)
            self.frame_cache[name] = (url, time.monotonic(), frame)
            return frame

    async def maintenance(self):
        while True:
            await asyncio.to_thread(self.refresh_monitor)
            for visitor in list(self.visitors.values()):
                with suppress(Exception):
                    await asyncio.to_thread(self.remember_runner, visitor)
            now = time.monotonic()
            expired = [key for key, item in self.visitors.items()
                       if now - item.touched > self.idle_seconds or now - item.born > self.lifetime_seconds]
            for key in expired:
                visitor = self.visitors.pop(key)
                self.retire(visitor)
            await asyncio.sleep(1)

    def retire(self, visitor):
        visitor.retired = True
        visitor.saved_keys.clear()
        visitor.controller.forget_key()
        task = asyncio.create_task(asyncio.to_thread(visitor.close))
        self.cleanups.add(task)
        task.add_done_callback(self.cleanups.discard)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    self.task = asyncio.create_task(self.maintenance())
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    if self.task:
                        self.task.cancel()
                        with suppress(asyncio.CancelledError):
                            await self.task
                    visitors = list(self.visitors.values())
                    self.visitors.clear()
                    await asyncio.gather(*(asyncio.to_thread(v.close) for v in visitors))
                    if self.cleanups:
                        await asyncio.gather(*self.cleanups)
                    if not any(v.controller.busy() for v in visitors):
                        shutil.rmtree(self.root, ignore_errors=True)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        try:
            await self.http(scope, receive, send)
        except RequestError as exc:
            await self.json(send, exc.status, {"error": exc.message})
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:
            # No raw exceptions: upstream errors can contain URLs or credentials.
            await self.json(send, 503, {"error": "The request could not be completed. Please try again."})

    async def body(self, receive):
        async def read():
            value = bytearray()
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    raise ConnectionError("Browser disconnected")
                value.extend(event.get("body", b""))
                if len(value) > 24000:
                    raise RequestError(413, "Request is too large")
                if not event.get("more_body"):
                    break
            try:
                payload = json.loads(value or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError()
                return payload
            except (ValueError, UnicodeError):
                raise RequestError(400, "Expected a JSON object") from None
        try:
            return await asyncio.wait_for(read(), 10)
        except asyncio.TimeoutError:
            raise RequestError(408, "Request timed out") from None

    async def http(self, scope, receive, send):
        path, method = scope["path"], scope["method"]
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        if path == "/health" and method == "GET":
            await self.json(send, 200, {"ok": True})
            return
        if headers.get("host") != self.authority:
            raise RequestError(421, "Unrecognized website host")
        if path == '/api/stripe/webhook' and method == 'POST':
            if self.payments is None:
                raise RequestError(503, 'Payments are not configured')
            async def webhook_body():
                raw = bytearray()
                while True:
                    part = await receive()
                    if part['type'] == 'http.disconnect':
                        raise RequestError(400, 'Incomplete webhook')
                    raw.extend(part.get('body', b''))
                    if len(raw) > 1048576:
                        raise RequestError(413, 'Webhook too large')
                    if not part.get('more_body'):
                        return bytes(raw)
            raw = await asyncio.wait_for(webhook_body(), 10)
            try:
                await asyncio.to_thread(self.payments.webhook, raw, headers.get('stripe-signature', ''))
            except Exception:
                raise RequestError(400, 'Webhook could not be verified or processed') from None
            await self.json(send, 200, {'received':True})
            return
        if headers.get("sec-fetch-site") == "cross-site" and not (path == "/" and method == "GET"):
            raise RequestError(403, "Open the runner directly to continue")
        if method == "GET" and path in {"/", "/static/hosted.js", "/static/hosted.css", "/static/analytics.js", "/static/stream-health.js", "/static/hls.light.min.js"}:
            asset = ROOT / "static" / ("hosted.html" if path == "/" else path.rsplit("/", 1)[1])
            kind = "text/html" if path == "/" else "text/javascript" if path.endswith(".js") else "text/css"
            await self.respond(send, 200, asset.read_bytes(), kind + "; charset=utf-8")
            return
        if method == 'GET' and path == '/api/past-runs':
            try:
                offset = int(parse_qs(scope.get('query_string', b'').decode()).get('offset', ['0'])[0])
                if not 0 <= offset <= 100000:
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise RequestError(400, 'Invalid page') from None
            try:
                result = await asyncio.to_thread(self.past_runs.page, offset)
                names = await asyncio.to_thread(self.run_names.names)
                outcomes = await asyncio.to_thread(self.run_names.results)
                for row in result['runs']:
                    row['runner_name'] = names.get(row['episode_id'], 'Name not recorded')
                    from remote_yam.past_runs import merge_run_result
                    merge_run_result(row, outcomes.get(row['episode_id'], {}))
            except Exception:
                raise RequestError(503, 'Past runs are temporarily unavailable. Please retry.') from None
            await self.json(send, 200, result)
            return
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except CookieError:
            pass
        pay_cookie_name = '__Host-yam-payment' if self.secure else 'yam-payment'
        payment_cookie = cookie.get(pay_cookie_name)
        payment_owner = payment_cookie.value if payment_cookie else ''
        token = cookie.get(self.cookie_name)
        capability = token.value if token else ""
        visitor = self.visitors.get(capability)
        if visitor and (time.monotonic() - visitor.born > self.lifetime_seconds
                        or time.monotonic() - visitor.touched > self.idle_seconds):
            self.visitors.pop(capability)
            self.retire(visitor)
            visitor = None
        if method == "POST":
            if headers.get("origin") != self.origin:
                raise RequestError(403, "Request must come from this website")
            if headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise RequestError(415, "Use application/json")
        if method == "POST" and path == "/api/session":
            await self.body(receive)
            if visitor is None:
                capability, visitor = self.new_visitor()
            visitor.touched = time.monotonic()
            cookie_value = f"{self.cookie_name}={capability}; Path=/; HttpOnly; SameSite=Strict"
            if self.secure:
                cookie_value += "; Secure"
            payment_headers = []
            if self.payments is not None and not payment_owner:
                payment_owner = secrets.token_urlsafe(32)
                value = f'{pay_cookie_name}={payment_owner}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000'
                if self.secure:
                    value += '; Secure'
                payment_headers.append((b'set-cookie', value.encode()))
            local_setup = {}
            if self.local_codex:
                from remote_yam.codex_policy import codex_status
                local_setup = {'local_runner': True, 'default_provider': self.default_provider,
                               'codex': await asyncio.to_thread(codex_status)}
            await self.json(send, 200, {**local_setup, "paid_runs": self.payments is not None, "csrf": visitor.csrf, "astra_enabled": bool(self.astra_endpoint),
                                      "simulation": self.simulation,
                                      "recover_purchase": await asyncio.to_thread(self.payments.recover, payment_owner) if self.payments and payment_owner else None,
                                      "expires_in": int(self.lifetime_seconds - (time.monotonic() - visitor.born))},
                            [(b"set-cookie", cookie_value.encode())] + payment_headers)
            return
        if visitor is None or visitor.retired:
            raise RequestError(401, "Your browser session ended. Reload to start a new one.")
        visitor.touched = time.monotonic()
        if method == "POST":
            if not secrets.compare_digest(headers.get("x-yam-runner-token", ""), visitor.csrf):
                raise RequestError(403, "Invalid browser session token")
            payload = await self.body(receive)
            if path == "/api/stream-health":
                from remote_yam.stream_health import public_report, log_report
                try:
                    report = public_report(payload)
                except ValueError:
                    raise RequestError(400, 'Invalid stream health report') from None
                if time.monotonic() - visitor.last_stream_report >= 1:
                    log_report(report)
                    visitor.last_stream_report = time.monotonic()
                result = {'ok': True}
            elif path == "/api/chat":
                name, text = payload.get("name", ""), payload.get("text", "")
                if not isinstance(name, str) or not isinstance(text, str):
                    raise RequestError(400, "Enter a name and message")
                name, text = name.strip(), text.strip()
                if not 1 <= len(name) <= 32 or not 1 <= len(text) <= 1000:
                    raise RequestError(400, "Use a name up to 32 characters and a message up to 1,000 characters")
                if time.monotonic() - visitor.last_chat < 2:
                    raise RequestError(429, "Please wait a moment before sending another message")
                visitor.last_chat = time.monotonic()
                await asyncio.to_thread(self.chat_history.append, visitor.chat_id, name, text)
                result = {"ok": True}
            elif path in {'/api/checkout', '/api/purchase/redeem'}:
                from remote_yam.payments import PaymentError
                if self.payments is None or not self.paid_model_key or not payment_owner:
                    raise RequestError(503, 'Paid runs are not configured')
                if visitor.controller.busy() or visitor.controller.status()['status'] in ACTIVE:
                    raise RequestError(409, 'Finish your current run first')
                try:
                    if path == '/api/checkout':
                        result = await asyncio.to_thread(self.payments.checkout, payment_owner, payload)
                    else:
                        def paid_launch(saved):
                            self.launch(visitor, {**saved, 'provider':'openai', 'model':self.paid_model,
                                                   'api_key':self.paid_model_key}, paid=True)
                            return visitor.runner_session_id
                        result = await asyncio.to_thread(self.payments.redeem, payment_owner,
                                                         payload.get('order', ''), paid_launch)
                except PaymentError as exc:
                    confirmed = False
                    if path == '/api/purchase/redeem':
                        try:
                            confirmed = self.payments.get(payment_owner, payload.get('order', ''))['state'] in {'paid', 'dispatching', 'used', 'review'}
                        except PaymentError:
                            pass
                    await self.json(send, 409, {'error': str(exc), 'payment_confirmed': confirmed})
                    return
                except Exception:
                    raise RequestError(503, 'Payment service unavailable. Your purchase has not been retried.') from None
            elif path == "/api/run":
                result = await asyncio.to_thread(self.launch, visitor, payload)
            elif path == '/api/codex/check' and self.local_codex:
                from remote_yam.codex_policy import codex_status
                result = await asyncio.to_thread(codex_status)
            elif path in {"/api/stop", "/api/credentials/clear", "/api/disconnect", "/api/operator"}:
                result = await asyncio.to_thread(self.control, visitor, path)
                if path == "/api/disconnect":
                    self.visitors.pop(capability, None)
                    self.retire(visitor)
            else:
                raise RequestError(404, "Unknown action")
            await self.json(send, 200, result)
            return
        if method != "GET":
            raise RequestError(405, "Method not allowed")
        if path == "/api/chat":
            await self.json(send, 200, {"messages": await asyncio.to_thread(self.chat_history.messages), "visitor": visitor.chat_id})
        elif path == "/api/status":
            controller = visitor.controller
            if self.observation is not None:
                controller.update_monitor_observation(self.observation)
            if self.queue is not None:
                controller.update_queue_snapshot(self.queue)
            state = controller.status()
            from remote_yam.operator_status import fresh_auto_queue
            state['robot_auto_queue_enabled'] = fresh_auto_queue(self.operator_auto_queue)
            # Station faults can happen before any visitor is assigned a run.
            from remote_yam.past_runs import public_robot_error
            observation = self.observation or {}
            observation = observation.get('observation') or observation.get('payload') or observation
            state['robot_fault'] = public_robot_error({
                'error': (observation.get('safety') or {}).get('reason')
            }) if observation.get('mode') == 'FAULT' else None
            state['runner_name'] = visitor.runner_name
            state['whats_running'] = []
            candidates = []
            for other in list(self.visitors.values()):
                current = other.controller.status()
                running = current.get('status') in {'preparing', 'running'}
                if running:
                    state['whats_running'].append({'runner_name': other.runner_name,
                        'task': other.runner_task, 'status': current['status']})
                if other.runner_session_id and current.get('status') != 'queued':
                    candidates.append((running, other.last_launch, other, current))
            state['public_run'] = None
            if candidates:
                _, _, owner, current = max(candidates, key=lambda item: item[:2])
                from remote_yam.public_conversation import project_events
                from remote_yam.run_errors import public_run_error
                from remote_yam.past_runs import public_robot_error
                events = project_events((current.get('interactions') or {}).get('events', []),
                                        (current.get('interactions') or {}).get('run_id'))
                state['public_run'] = {'run_id': (current.get('interactions') or {}).get('run_id'),
                    'runner_name': owner.runner_name, 'task': owner.runner_task,
                    'status': current['status'], 'events': events, 'error': public_run_error(current),
                    **{k: current.get(k) for k in ('run_duration_s', 'run_started_at', 'run_ended_at', 'run_elapsed_s')}}
            shared_run = (state.get('queue_snapshot') or {}).get('public_run')
            if shared_run and (shared_run.get('status') in {'preparing', 'running'} or not state['public_run']):
                state['public_run'] = shared_run
            names = {v.runner_session_id: v.runner_name for v in list(self.visitors.values()) if v.runner_session_id}
            for entry in (state.get('queue_snapshot') or {}).get('entries', []):
                entry['runner_name'] = names.get(entry.get('session_id'), 'Anonymous')
            # Camera URLs, filesystem paths, and transport details aren't UI data.
            observation = state.get("last_observation")
            if isinstance(observation, dict):
                observation = dict(observation)
                observation["images"] = {name: {"url": f"/api/monitor/cameras/{name}"} for name in CAMERA_NAMES}
                state["last_observation"] = observation
            if isinstance(state.get("provider"), dict):
                state["provider"].pop("recording_path", None)
                state["provider"].pop("endpoint", None)
                state["provider"]["api_key_configured"] = bool(getattr(controller._provider, "_api_key", ""))
            if isinstance(state.get("interactions"), dict):
                state["interactions"].pop("path", None)
            state.pop("lease_id", None)
            state["saved_key_providers"] = sorted(visitor.saved_keys)
            state["key_configured"] = bool(visitor.saved_keys)
            await self.json(send, 200, state)
        elif path.startswith("/api/monitor/cameras/"):
            name = path.rsplit("/", 1)[1]
            if name not in CAMERA_NAMES or not self.observation:
                raise RequestError(503, "NO SIGNAL")
            images = self.observation.get("images", {})
            spec = images.get(name, {})
            url = spec.get("url") if isinstance(spec, dict) else None
            if not url:
                raise RequestError(503, "NO SIGNAL")
            try:
                frame = await asyncio.to_thread(self.camera_frame, name, url)
            except Exception:
                raise RequestError(503, "NO SIGNAL") from None
            await self.respond(send, 200, frame.jpeg, "image/jpeg")
        elif path.startswith('/api/public-images/'):
            import re
            from remote_yam.interactions import recording_directory
            parts = path.split('/')
            if len(parts) != 5 or not re.fullmatch('[a-f0-9]{64}', parts[4]):
                raise RequestError(404, 'Image unavailable')
            run_id, digest = parts[3:]
            for owner in list(self.visitors.values()):
                snapshot = owner.controller.status().get('interactions') or {}
                if snapshot.get('run_id') != run_id:
                    continue
                permitted = any(image.get('digest') == digest
                    for event in snapshot.get('events', []) if event.get('kind') == 'model_request'
                    for image in (event.get('details') or {}).get('images', []))
                if not permitted:
                    continue
                directory = recording_directory(owner.directory, run_id)
                blob = directory / 'blobs' / digest
                if blob.is_symlink() or not blob.is_file() or blob.resolve().parent != (directory / 'blobs').resolve():
                    break
                await self.respond(send, 200, await asyncio.to_thread(blob.read_bytes), 'image/jpeg')
                return
            raise RequestError(404, 'Image unavailable')
        elif path == "/api/recordings":
            await self.json(send, 200, {"runs": await asyncio.to_thread(list_recordings, visitor.directory)})
        elif path.startswith("/api/recordings/"):
            parts = path.split("/")
            if len(parts) != 5:
                raise RequestError(404, "Recording unavailable")
            run_id, action = parts[3:]
            try:
                if action in {"interactions", "artifacts"}:
                    reader = read_recording if action == "interactions" else run_artifacts
                    result = await asyncio.to_thread(reader, visitor.directory, run_id)
                    result.pop("path", None)
                    await self.json(send, 200, result)
                elif action == "log.zip":
                    archive = await asyncio.to_thread(log_archive, visitor.directory, run_id)
                    with archive:
                        size = archive.seek(0, 2)
                        archive.seek(0)
                        await self.start_response(send, 200, "application/zip", size,
                                                  [(b"content-disposition", b'attachment; filename="yam-run-log.zip"')])
                        while True:
                            chunk = await asyncio.to_thread(archive.read, 1024 * 1024)
                            await send({"type": "http.response.body", "body": chunk, "more_body": bool(chunk)})
                            if not chunk:
                                break
                else:
                    raise ValueError()
            except (ValueError, OSError):
                raise RequestError(404, "Recording unavailable") from None
        else:
            raise RequestError(404, "Not found")

    def launch(self, visitor, payload, *, paid=False):
        self.remember_runner(visitor)
        if self.payments is not None and not paid and payload.get('provider') not in {'local_raise_lower', 'openai'}:
            raise RequestError(402, 'Choose a built-in run, bring your API key, or pay through Checkout')
        with visitor.lock:
            if visitor.retired:
                raise RequestError(401, "Browser session ended")
            if visitor.controller.busy() or visitor.controller.status()["status"] in ACTIVE:
                raise RequestError(409, "A run is already active. Stop it before starting another.")
            if time.monotonic() - visitor.last_launch < 5:
                raise RequestError(429, "Please wait a few seconds before starting another run.")
            if visitor.launches >= 20:
                raise RequestError(429, "This browser session reached its 20-run limit. End the session to begin another.")
            name, key, prompt, model = (payload.get(k, "") for k in ("provider", "api_key", "prompt", "model"))
            if not paid and key == "" and isinstance(name, str):
                key = visitor.saved_keys.get(name, "")
            duration = payload.get("run_duration_s", 300)
            if type(duration) is not int or not 60 <= duration <= 600:
                raise RequestError(400, "Choose a run duration from 1 to 10 minutes")
            runner_name = payload.get('runner_name', 'Anonymous')
            try:
                handles = social_handles(payload)
            except ValueError as exc:
                raise RequestError(400, str(exc)) from None
            if not isinstance(runner_name, str) or not 1 <= len(runner_name.strip()) <= 32 or any(ord(c) < 32 for c in runner_name):
                raise RequestError(400, 'Enter a name of 1–32 characters')
            if name not in ({"local_raise_lower", "openai", "astra", "codex"} if self.local_codex else {"local_raise_lower", "openai", "astra"}):
                raise RequestError(400, "Choose a supported provider")
            if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
                raise RequestError(400, "Enter a task of up to 4,000 characters")
            if not isinstance(model, str) or len(model) > 128 or any(ord(c) < 32 for c in model):
                raise RequestError(400, "Invalid model name")
            if name not in {"local_raise_lower", "codex"} and (not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
                raise RequestError(400, "Enter your provider API key")
            if payload.get("endpoint"):
                raise RequestError(400, "Provider endpoints are configured by the service")
            if name == "astra" and not self.astra_endpoint:
                raise RequestError(400, "Astra is not configured on this service")
            if name == "local_raise_lower":
                prompt = "Raise and lower both arms for three cycles."
            if self.provider_factory:
                provider = self.provider_factory(name, key, model, visitor.directory)
            elif name == 'codex':
                from remote_yam.codex_policy import CodexAdapter
                try:
                    provider = CodexAdapter(model or 'gpt-6-astra', camera_source=self.camera_source,
                                            recording_root=visitor.directory)
                except RuntimeError as exc:
                    raise RequestError(400, str(exc)) from None
            elif name == "local_raise_lower":
                provider = RepeatingRaiseLowerAdapter(cycles=3)
            else:
                kwargs = {"camera_source": self.camera_source, "recording_root": visitor.directory}
                provider = (OpenAIAdapter(key, model or "gpt-6-astra", **kwargs) if name == "openai"
                            else AstraAdapter(key, model or "astra-default", self.astra_endpoint, **kwargs))
                provider._urlopen = request.build_opener(NoRedirect).open
            visitor.last_launch = time.monotonic()
            visitor.launches += 1
            try:
                visitor.controller._session_api.setup(payload, paid=paid)
                visitor.controller.join_and_run(provider, prompt.strip(), run_duration_s=duration)
                with visitor.controller._lock:
                    analytics_run = getattr(visitor.controller, "_analytics_run", None)
                    if analytics_run:
                        analytics_run["run_route"] = "paid" if paid else "direct"
                visitor.runner_task = prompt.strip()
                visitor.runner_name = runner_name.strip()
                visitor.runner_session_id = visitor.controller.status().get('session_id')
                visitor.controller._interactions.add('runner', 'Run submitted by ' + visitor.runner_name, runner_name=visitor.runner_name)
            except Exception:
                if hasattr(provider, "_api_key"):
                    provider._api_key = ""
                visitor.controller.disconnect("launch_failed")
                raise RequestError(503, "Could not join the robot queue. Please try again.") from None
            # Optional metadata must never cancel an already accepted session.
            try:
                save_handles(self.social_database, visitor.runner_session_id, visitor.runner_name, handles)
            except Exception as exc:
                print('[contacts] save_failed error_type=' + type(exc).__name__, flush=True)
                visitor.controller._session_api.note(
                    'contact_save_failed', {'error_type': type(exc).__name__}, visitor.runner_session_id)
            if not paid and name not in {"local_raise_lower", "codex"}:
                visitor.saved_keys[name] = key
            return {"ok": True, "saved_key_providers": sorted(visitor.saved_keys)}

    def control(self, visitor, path):
        self.remember_runner(visitor)
        with visitor.lock:
            if visitor.retired:
                raise RequestError(401, "Browser session ended")
            controller = visitor.controller
            # Forget means cancel future inference as well as clearing the key.
            controller.forget_key()
            if path in {"/api/credentials/clear", "/api/disconnect"}:
                visitor.saved_keys.clear()
            if path == "/api/operator":
                controller.call_operator()
            elif path == "/api/disconnect":
                controller.disconnect("browser_disconnect")
            else:
                controller.stop("browser_stop_or_forget")
            return {"ok": True, "key_configured": bool(visitor.saved_keys), "saved_key_providers": sorted(visitor.saved_keys)}

    async def start_response(self, send, status, content_type, size, extra=()):
        headers = [(b"content-type", content_type.encode()), (b"content-length", str(size).encode()),
                   (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"referrer-policy", b"no-referrer"),
                   (b"content-security-policy", b"default-src 'none'; script-src 'self' https://www.googletagmanager.com; style-src 'self'; connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://www.googletagmanager.com; img-src 'self' https://*.google-analytics.com https://www.googletagmanager.com; media-src https://huggingface.co https://*.huggingface.co https://*.hf.co; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
        if self.secure:
            headers.append((b"strict-transport-security", b"max-age=31536000"))
        await send({"type": "http.response.start", "status": status, "headers": headers + list(extra)})

    async def respond(self, send, status, body, content_type, extra=()):
        await self.start_response(send, status, content_type, len(body), extra)
        await send({"type": "http.response.body", "body": body})

    async def json(self, send, status, payload, extra=()):
        await self.respond(send, status, json.dumps(payload, allow_nan=False).encode(), "application/json", extra)


def create_app():
    development = os.environ.get("YAM_WEB_DEVELOPMENT") == "1"
    session_api = os.environ.get("YAM_SESSION_API")
    payment_service = None
    model_key = ''
    if os.environ.get('YAM_PAID_RUNS') == '1':
        from remote_yam.payments import Payments
        config = json.loads(Path(os.environ['YAM_PAYMENT_CONFIG']).read_text())
        model_key = config['model_key']
        if not model_key or not config['stripe_key'] or not config['webhook_secret']:
            raise ValueError('Payment credentials are incomplete')
        payment_service = Payments(os.environ['YAM_PAYMENT_DB'], config['stripe_key'],
                                   config['webhook_secret'], os.environ['YAM_WEB_ORIGIN'])
    return HostedRunner(
        payments=payment_service, paid_model_key=model_key,
        chat_database=os.environ.get("YAM_CHAT_DATABASE", str(ROOT / "data/chat.sqlite3")),
        public_origin=os.environ.get("YAM_WEB_ORIGIN", "http://127.0.0.1:8790" if development else ""),
        session_api=session_api,
        camera_origin=os.environ.get("YAM_CAMERA_ORIGIN", session_api or "http://127.0.0.1:8089"),
        astra_endpoint=os.environ.get("YAM_WEB_ASTRA_ENDPOINT", ""),
        hardware_control=os.environ.get("YAM_WEB_HARDWARE_CONTROL") == "1",
        development=development,
        max_sessions=int(os.environ.get("YAM_WEB_MAX_SESSIONS", "32")),
    )

"""Canonical v1 Session API transport and deterministic runner-side mock."""

from __future__ import annotations

import json
import math
import queue
import ssl
import http.client
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib import error, parse, request

SCHEMA_VERSION = 1
MAX_COMMANDS = 3000  # Total accepted waypoints in one policy session.
MAX_WAYPOINTS_PER_PACKET = 300
TERMINAL_STATES = frozenset(
    {"resetting", "stopped", "timed_out", "disconnected", "safety_aborted"}
)


class SessionAPIError(RuntimeError):
    def __init__(self, message: str, *, code: str = "session_api_error", status: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SessionEventStream(Protocol):
    def receive(self, timeout_s: float | None = None) -> dict[str, Any]: ...
    def close(self) -> None: ...


class SessionAPI(Protocol):
    def get_queue_snapshot(self) -> dict[str, Any]: ...
    def get_robot_observation(self, jetson_id: str) -> dict[str, Any]: ...
    def create_session(self, prompt: str) -> dict[str, Any]: ...
    def get_session(self, session_id: str) -> dict[str, Any]: ...
    def open_events(self, session_id: str) -> SessionEventStream: ...
    def submit_action(
        self,
        session_id: str,
        episode_id: str,
        lease_id: str,
        step_id: int,
        idempotency_key: str,
        left_joints_deg: Sequence[float],
        right_joints_deg: Sequence[float],
        left_gripper: float | None = None,
        right_gripper: float | None = None,
    ) -> dict[str, Any]: ...
    def submit_trajectory(
        self,
        session_id: str,
        episode_id: str,
        lease_id: str,
        trajectory_id: str,
        cadence_hz: float,
        waypoints: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]: ...
    def stop_session(self, session_id: str) -> dict[str, Any]: ...
    def call_operator(self, session_id: str, reason: str) -> dict[str, Any]: ...
    def get_episode(self, session_id: str) -> dict[str, Any]: ...
    def get_episode_trace(self, session_id: str) -> dict[str, Any]: ...


class WebSocketEventStream:
    def __init__(self, socket: Any, timeout_error: type[BaseException]) -> None:
        self._socket = socket
        self._timeout_error = timeout_error
        self._closed = False

    def receive(self, timeout_s: float | None = None) -> dict[str, Any]:
        if self._closed:
            raise ConnectionError("Session event stream is closed")
        if timeout_s is not None:
            self._socket.settimeout(timeout_s)
        try:
            raw = self._socket.recv()
        except self._timeout_error as exc:
            raise TimeoutError("No session event received") from exc
        except Exception as exc:
            raise ConnectionError(f"Session event stream failed: {type(exc).__name__}") from exc
        if raw in {None, "", b""}:
            raise ConnectionError("Session event stream disconnected")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise SessionAPIError("Session event must be a JSON object")
        return payload

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._socket.close()


class HttpSessionAPI:
    """HTTPS/WSS runner client for Thread 02's canonical v1 API."""

    # The deployed v1 API supports individual actions, not /trajectories.
    # Never discover write capabilities by attempting physical dispatch.
    supports_trajectories = False

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 10.0,
        websocket_factory: Callable[..., Any] | None = None,
        websocket_timeout_error: type[BaseException] = TimeoutError,
        supports_trajectories: bool = False,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Session API base URL must be HTTP(S)")
        self.supports_trajectories = supports_trajectories
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._websocket_factory = websocket_factory
        self._websocket_timeout_error = websocket_timeout_error
        self._session_capabilities: dict[str, str] = {}
        self._contact_lock = threading.Lock()
        self._contact_issues: dict[str, dict[str, Any]] = {}
        self._contact_listener = None

    def get_queue_snapshot(self) -> dict[str, Any]:
        return self._request("GET", "/v1/queue")

    def create_session(self, prompt: str) -> dict[str, Any]:
        if self._websocket_factory is None:
            try:
                import websocket  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "Control transport is not installed; relaunch with `./run.sh --session-api <base-url>`"
                ) from exc
        created = self._request("POST", "/v1/sessions", {"schema_version": 1, "prompt": prompt})
        session_id = str(created.get("session_id", ""))
        capability = created.get("session_capability")
        if not session_id or not isinstance(capability, str) or not capability:
            raise SessionAPIError("Session creation did not return a session capability")
        self._session_capabilities[session_id] = capability
        return created

    def get_robot_observation(self, jetson_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/robots/{parse.quote(jetson_id, safe='')}/observation"
        )

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/sessions/{parse.quote(session_id, safe='')}",
            bearer=self._capability(session_id),
        )

    def open_events(self, session_id: str) -> SessionEventStream:
        factory = self._websocket_factory
        timeout_error = self._websocket_timeout_error
        if factory is None:
            try:
                import websocket
            except ImportError as exc:
                raise RuntimeError(
                    "Remote Session API requires `pip install websocket-client`"
                ) from exc
            # websocket-client 1.9 synchronous API; see docs/refs/websocket-client/INDEX.md.
            factory = websocket.create_connection
            timeout_error = websocket.WebSocketTimeoutException
        headers = [f"Authorization: Bearer {self._capability(session_id)}"]
        url = self._websocket_url(f"/v1/sessions/{parse.quote(session_id, safe='')}/events")
        socket = factory(url, timeout=self._timeout_s, header=headers)
        return WebSocketEventStream(socket, timeout_error)

    def submit_action(
        self,
        session_id: str,
        episode_id: str,
        lease_id: str,
        step_id: int,
        idempotency_key: str,
        left_joints_deg: Sequence[float],
        right_joints_deg: Sequence[float],
        left_gripper: float | None = None,
        right_gripper: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "episode_id": episode_id,
            "lease_id": lease_id,
            "step_id": step_id,
            "idempotency_key": idempotency_key,
            "left_joints_deg": _joint_vector(left_joints_deg, "left_joints_deg"),
            "right_joints_deg": _joint_vector(right_joints_deg, "right_joints_deg"),
        }
        if left_gripper is not None:
            payload["left_gripper"] = float(left_gripper)
        if right_gripper is not None:
            payload["right_gripper"] = float(right_gripper)
        return self._request(
            "POST", f"/v1/sessions/{parse.quote(session_id, safe='')}/actions", payload,
            bearer=self._capability(session_id),
        )

    def submit_trajectory(
        self,
        session_id: str,
        episode_id: str,
        lease_id: str,
        trajectory_id: str,
        cadence_hz: float,
        waypoints: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not trajectory_id:
            raise ValueError("trajectory_id must be non-empty")
        if isinstance(cadence_hz, bool) or float(cadence_hz) != 10.0:
            raise ValueError("cadence_hz must equal 10.0")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": episode_id,
            "lease_id": lease_id,
            "trajectory_id": trajectory_id,
            "cadence_hz": 10.0,
            "waypoints": _trajectory_waypoints(waypoints),
        }
        return self._request(
            "POST",
            f"/v1/sessions/{parse.quote(session_id, safe='')}/trajectories",
            payload,
            bearer=self._capability(session_id),
        )

    def stop_session(self, session_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/sessions/{parse.quote(session_id, safe='')}/stop", {},
            bearer=self._capability(session_id),
        )

    def call_operator(self, session_id: str, reason: str = "user_requested") -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/sessions/{parse.quote(session_id, safe='')}/operator",
            {"schema_version": 1, "reason": reason},
            bearer=self._capability(session_id),
        )

    def get_episode(self, session_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/sessions/{parse.quote(session_id, safe='')}/episode",
            bearer=self._capability(session_id),
        )

    def get_episode_trace(self, session_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/sessions/{parse.quote(session_id, safe='')}/episode/trace",
            bearer=self._capability(session_id),
        )

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None,
        *, bearer: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self._url(path), data=body, headers=headers, method=method)
        # Reads and stable-ID packet/action submissions can safely be retried.
        # Session creation has no idempotency key: an ambiguous reply must not
        # create a second queue entry. Reuse the exact body/ID on every retry.
        safe_retry = method == "GET" or (
            method == "POST" and payload is not None and (
                (path.endswith("/trajectories") and bool(payload.get("trajectory_id")))
                or (path.endswith("/actions") and bool(payload.get("idempotency_key")))
            )
        )
        attempts = 3 if safe_retry else 1
        contact_id = method + " " + path
        for attempt in range(1, attempts + 1):
            failure = None
            try:
                with request.urlopen(req, timeout=self._timeout_s) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                if exc.code in {502, 503, 504}:
                    failure = exc
                    exc.close()
                else:
                    self._clear_contact_issue(contact_id)
                    try:
                        detail = json.loads(exc.read().decode("utf-8"))["error"]
                    except Exception:
                        detail = {"code": "http_error", "message": f"Session API returned HTTP {exc.code}"}
                    finally:
                        exc.close()
                    raise SessionAPIError(
                        str(detail.get("message", "Session API request failed")),
                        code=str(detail.get("code", "http_error")), status=exc.code,
                    ) from exc
            except (error.URLError, TimeoutError, ConnectionError, ssl.SSLError,
                    http.client.IncompleteRead) as exc:
                failure = exc
            if failure is None:
                self._clear_contact_issue(contact_id)
                if not isinstance(decoded, dict):
                    raise SessionAPIError("Session API returned a non-object response")
                return decoded
            cause = getattr(failure, "reason", failure)
            retrying = attempt < attempts and not isinstance(cause, ssl.SSLCertVerificationError)
            detail = {
                "message": "Server contact issue",
                "state": "retrying" if retrying else "failed",
                "attempt": attempt, "max_attempts": attempts,
                "cause_type": type(cause).__name__,
                "http_status": failure.code if isinstance(failure, error.HTTPError) else None,
            }
            self._report_contact_issue(contact_id, detail)
            if not retrying:
                raise SessionAPIError(
                    "Server contact issue — unable to reach the Session API" +
                    (f" after {attempt} attempts" if attempt > 1 else ""),
                    code="server_contact_issue", status=503,
                ) from failure
            time.sleep(float(attempt))
        raise AssertionError("unreachable")

    @property
    def contact_issue(self) -> dict[str, Any] | None:
        with self._contact_lock:
            return dict(next(reversed(self._contact_issues.values()))) if self._contact_issues else None

    def set_contact_listener(self, listener) -> None:
        self._contact_listener = listener

    def _report_contact_issue(self, contact_id, detail):
        with self._contact_lock:
            self._contact_issues[contact_id] = detail
            while len(self._contact_issues) > 32:
                self._contact_issues.pop(next(iter(self._contact_issues)))
        if self._contact_listener is not None:
            self._contact_listener("server_contact_issue", detail)

    def _clear_contact_issue(self, contact_id):
        with self._contact_lock:
            issue = self._contact_issues.pop(contact_id, None)
        if issue is not None and self._contact_listener is not None:
            self._contact_listener("server_contact_recovered", {"message": "Server contact restored"})

    def _capability(self, session_id: str) -> str:
        capability = self._session_capabilities.get(session_id)
        if capability is None:
            raise SessionAPIError("No capability is available for this session", status=401)
        return capability

    def _url(self, path: str) -> str:
        if self._base_url.endswith("/v1") and path.startswith("/v1/"):
            return self._base_url + path[3:]
        return self._base_url + path

    def _websocket_url(self, path: str) -> str:
        http_url = self._url(path)
        parts = parse.urlsplit(http_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return parse.urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


class _MockEventStream:
    def __init__(self, events: queue.Queue[dict[str, Any]]) -> None:
        self._events = events
        self._closed = False

    def receive(self, timeout_s: float | None = None) -> dict[str, Any]:
        if self._closed:
            raise ConnectionError("Session event stream is closed")
        try:
            return deepcopy(self._events.get(timeout=timeout_s))
        except queue.Empty as exc:
            raise TimeoutError("No session event received") from exc

    def close(self) -> None:
        self._closed = True


class MockSessionAPI:
    """Event-driven v1 mock with leases, recorded observations, and deduplication."""

    supports_trajectories = True

    DEFAULT_OBSERVATIONS = (
        {"left_joints_deg": [0.0, -14.0, 28.0, 0.0, 11.0, 0.0], "right_joints_deg": [0.0, -14.0, 28.0, 0.0, 11.0, 0.0], "left_gripper": 0.5, "right_gripper": 0.5, "images": {"left": {"url": "/static/mock-camera-0.svg"}, "top": {"url": "/static/mock-camera-1.svg"}, "right": {"url": "/static/mock-camera-2.svg"}}},
        {"left_joints_deg": [3.0, -11.0, 25.0, 0.0, 11.0, 3.0], "right_joints_deg": [-3.0, -11.0, 25.0, 0.0, 11.0, -3.0], "left_gripper": 0.5, "right_gripper": 0.4, "images": {"left": {"url": "/static/mock-camera-0.svg"}, "top": {"url": "/static/mock-camera-1.svg"}, "right": {"url": "/static/mock-camera-2.svg"}}},
        {"left_joints_deg": [5.0, -9.0, 23.0, 1.0, 10.0, 5.0], "right_joints_deg": [-5.0, -9.0, 23.0, -1.0, 10.0, -5.0], "left_gripper": 0.4, "right_gripper": 0.3, "images": {"left": {"url": "/static/mock-camera-0.svg"}, "top": {"url": "/static/mock-camera-1.svg"}, "right": {"url": "/static/mock-camera-2.svg"}}},
    )

    def __init__(
        self,
        observations: Sequence[Mapping[str, Any]] | None = None,
        fail_after_commit_once: set[int] | None = None,
        auto_activate: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._observations = list(observations or self.DEFAULT_OBSERVATIONS)
        self._fail_after_commit_once = set(fail_after_commit_once or set())
        self._failed_steps: set[int] = set()
        self._auto_activate = auto_activate
        self._sessions: dict[str, dict[str, Any]] = {}
        self.create_requests: list[dict[str, Any]] = []
        self.action_attempts: list[dict[str, Any]] = []
        self.action_log: list[dict[str, Any]] = []
        self.trajectory_attempts: list[dict[str, Any]] = []
        self.trajectory_log: list[dict[str, Any]] = []

    def create_session(self, prompt: str) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        with self._lock:
            self.create_requests.append({"schema_version": 1, "prompt": prompt})
            session_id = f"sess_{uuid.uuid4().hex[:10]}"
            record: dict[str, Any] = {
                "session_id": session_id,
                "episode_id": None,
                "lease_id": None,
                "status": "queued",
                "position": 1,
                "next_command_step": 0,
                "next_observation": 0,
                "events": queue.Queue(),
                "receipts": {},
                "trajectory_receipts": {},
                "active_trajectory": None,
                "episode": None,
                "return_to_rest_requested": False,
                "operator_requested": False,
                "created_at": time.time(),
            }
            self._sessions[session_id] = record
            self._event(record, "snapshot", self._public_state(record))
            self._event(record, "queue_update", {"position": 1, "state": "queued"})
            if self._auto_activate:
                self.activate(session_id)
            return {
                "schema_version": 1,
                "session_id": session_id,
                "session_capability": f"cap_{uuid.uuid4().hex}",
                "status": "queued",
                "position": 1,
                "episode_id": None,
                "events_url": f"/v1/sessions/{session_id}/events",
            }

    def get_robot_observation(self, jetson_id: str) -> dict[str, Any]:
        payload = deepcopy(self.DEFAULT_OBSERVATIONS[0])
        payload.update({
            "schema_version": 1,
            "jetson_id": jetson_id,
            "source": "simulation",
            "mode": "API_WAITING",
            "observed_at": time.time(),
            "safety": {"ok": True, "estop_engaged": False, "reason": None},
        })
        return payload

    def get_queue_snapshot(self) -> dict[str, Any]:
        with self._lock:
            queued = sorted(
                (record for record in self._sessions.values() if record["status"] == "queued"),
                key=lambda record: record["created_at"],
            )
            busy = any(
                record["status"] in {"queued", "preparing", "running"}
                for record in self._sessions.values()
            )
            return {
                "schema_version": 1,
                "type": "queue_snapshot",
                "generated_at": time.time(),
                "entries": [
                    {
                        "position": position,
                        "session_id": record["session_id"],
                        "status": "queued",
                        "created_at": record["created_at"],
                    }
                    for position, record in enumerate(queued, start=1)
                ],
                "stations": [{
                    "jetson_id": "yam-1", "connected": True, "available": not busy,
                    "source": "simulation", "mode": "AVAILABLE" if not busy else "BUSY",
                    "observed_at": time.time(),
                }],
            }

    def activate(self, session_id: str) -> None:
        with self._lock:
            record = self._record(session_id)
            record["episode_id"] = f"ep_{uuid.uuid4().hex[:10]}"
            record["lease_id"] = f"lease_{uuid.uuid4().hex[:10]}"
            self._transition(record, "preparing", {"lease_id": record["lease_id"]})
            self._transition(record, "running")
            self._next_observation(record)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_state(self._record(session_id))

    def open_events(self, session_id: str) -> SessionEventStream:
        with self._lock:
            return _MockEventStream(self._record(session_id)["events"])

    def submit_action(
        self,
        session_id: str,
        episode_id: str,
        lease_id: str,
        step_id: int,
        idempotency_key: str,
        left_joints_deg: Sequence[float],
        right_joints_deg: Sequence[float],
        left_gripper: float | None = None,
        right_gripper: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._record(session_id)
            attempt = {
                "schema_version": 1,
                "session_id": session_id,
                "episode_id": episode_id,
                "lease_id": lease_id,
                "step_id": step_id,
                "idempotency_key": idempotency_key,
                "left_joints_deg": _joint_vector(left_joints_deg, "left_joints_deg"),
                "right_joints_deg": _joint_vector(right_joints_deg, "right_joints_deg"),
            }
            if left_gripper is not None:
                attempt["left_gripper"] = float(left_gripper)
            if right_gripper is not None:
                attempt["right_gripper"] = float(right_gripper)
            self.action_attempts.append(deepcopy(attempt))
            if record["status"] != "running":
                raise SessionAPIError("Session is not running", code="invalid_session_state", status=409)
            if episode_id != record["episode_id"] or lease_id != record["lease_id"]:
                raise SessionAPIError("Stale episode or lease", code="stale_lease", status=409)
            receipts: dict[str, tuple[dict[str, Any], dict[str, Any]]] = record["receipts"]
            if idempotency_key in receipts:
                original, receipt = receipts[idempotency_key]
                if original != attempt:
                    raise SessionAPIError("Idempotency key body changed", code="idempotency_conflict", status=409)
                duplicate = deepcopy(receipt)
                duplicate["duplicate"] = True
                return duplicate
            if step_id >= MAX_COMMANDS:
                raise SessionAPIError("Command limit reached", code="command_limit", status=409)
            if step_id != record["next_command_step"]:
                raise SessionAPIError("Command step is out of order", code="step_out_of_order", status=409)
            receipt = {
                "schema_version": 1,
                "command_id": f"cmd_{uuid.uuid4().hex[:10]}",
                "step_id": step_id,
                "status": "accepted",
                "duplicate": False,
            }
            receipts[idempotency_key] = (deepcopy(attempt), deepcopy(receipt))
            record["next_command_step"] += 1
            self.action_log.append(deepcopy(attempt))
            self._event(
                record,
                "action_result",
                {**receipt, "episode_id": episode_id, "lease_id": lease_id},
            )
            self._event(record, "heartbeat", {"lease_id": lease_id})
            if record["next_observation"] < len(self._observations):
                self._next_observation(record)
            else:
                self._complete(record)
            if step_id in self._fail_after_commit_once and step_id not in self._failed_steps:
                self._failed_steps.add(step_id)
                raise ConnectionError("simulated lost acknowledgement after commit")
            return receipt

    def submit_trajectory(
        self,
        session_id: str,
        episode_id: str,
        lease_id: str,
        trajectory_id: str,
        cadence_hz: float,
        waypoints: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            record = self._record(session_id)
            if not trajectory_id:
                raise ValueError("trajectory_id must be non-empty")
            if isinstance(cadence_hz, bool) or float(cadence_hz) != 10.0:
                raise ValueError("cadence_hz must equal 10.0")
            normalized = _trajectory_waypoints(waypoints)
            attempt = {
                "schema_version": SCHEMA_VERSION,
                "episode_id": episode_id,
                "lease_id": lease_id,
                "trajectory_id": trajectory_id,
                "cadence_hz": 10.0,
                "waypoints": normalized,
            }
            self.trajectory_attempts.append(deepcopy(attempt))
            if record["status"] != "running":
                raise SessionAPIError("Session is not running", code="invalid_session_state", status=409)
            if episode_id != record["episode_id"] or lease_id != record["lease_id"]:
                raise SessionAPIError("Stale episode or lease", code="stale_lease", status=409)
            receipts: dict[str, tuple[dict[str, Any], dict[str, Any]]] = record["trajectory_receipts"]
            if trajectory_id in receipts:
                original, receipt = receipts[trajectory_id]
                if original != attempt:
                    raise SessionAPIError(
                        "Trajectory ID body changed", code="idempotency_conflict", status=409
                    )
                duplicate = deepcopy(receipt)
                duplicate["duplicate"] = True
                return duplicate
            if record["active_trajectory"] is not None:
                raise SessionAPIError(
                    "A trajectory is already active", code="trajectory_active", status=409
                )
            first_step = normalized[0]["step_id"]
            last_step = normalized[-1]["step_id"]
            if first_step != record["next_command_step"]:
                raise SessionAPIError(
                    "Trajectory step is out of order", code="step_out_of_order", status=409
                )
            if last_step >= MAX_COMMANDS:
                raise SessionAPIError("Command limit reached", code="command_limit", status=409)
            dispatched_at = time.time()
            receipt = {
                "schema_version": SCHEMA_VERSION,
                "accepted": True,
                "duplicate": False,
                "session_id": session_id,
                "episode_id": episode_id,
                "lease_id": lease_id,
                "trajectory_id": trajectory_id,
                "first_step_id": first_step,
                "last_step_id": last_step,
                "waypoint_count": len(normalized),
                "dispatched_at": dispatched_at,
            }
            receipts[trajectory_id] = (deepcopy(attempt), deepcopy(receipt))
            record["next_command_step"] = last_step + 1
            record["active_trajectory"] = {
                "trajectory_id": trajectory_id,
                "state": "dispatched",
                "first_step_id": first_step,
                "last_step_id": last_step,
                "waypoint_count": len(normalized),
                "progress_count": 0,
                "last_progress_step_id": None,
                "final_observation_received": False,
                "dispatched_at": dispatched_at,
            }
            self.trajectory_log.append(deepcopy(attempt))
            self.action_log.extend(deepcopy(normalized))
            result_base = {
                "episode_id": episode_id,
                "lease_id": lease_id,
                "trajectory_id": trajectory_id,
                "step_id": None,
                "code": None,
                "message": None,
                "details": {},
            }
            record["active_trajectory"]["state"] = "accepted"
            self._event(record, "trajectory_result", {
                **result_base, "status": "accepted", "reported_at": time.time(),
            })
            for waypoint in normalized:
                step_id = waypoint["step_id"]
                record["active_trajectory"]["progress_count"] += 1
                record["active_trajectory"]["last_progress_step_id"] = step_id
                self._event(record, "trajectory_progress", {
                    "episode_id": episode_id,
                    "lease_id": lease_id,
                    "trajectory_id": trajectory_id,
                    "step_id": step_id,
                    "executed_at": time.time(),
                })
            final = normalized[-1]
            self._event(record, "observation", {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "episode_id": episode_id,
                "lease_id": lease_id,
                "step_id": last_step + 1,
                "observed_at": time.time(),
                "source": "simulation",
                "mode": "API_ACTIVE",
                "homed": True,
                "settled": True,
                "safety": {"ok": True, "estop_engaged": False, "contact_count": 0},
                "left_joints_deg": deepcopy(final["left_joints_deg"]),
                "right_joints_deg": deepcopy(final["right_joints_deg"]),
                "left_gripper": final.get("left_gripper"),
                "right_gripper": final.get("right_gripper"),
                "images": {},
            })
            record["active_trajectory"]["final_observation_received"] = True
            self._event(record, "trajectory_result", {
                **result_base,
                "step_id": last_step,
                "status": "completed",
                "reported_at": time.time(),
            })
            record["active_trajectory"] = None
            return receipt

    def stop_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(session_id)
            if record["status"] not in TERMINAL_STATES:
                record["return_to_rest_requested"] = True
                self._transition(record, "stopped")
            return self._public_state(record)

    def call_operator(self, session_id: str, reason: str = "user_requested") -> dict[str, Any]:
        with self._lock:
            record = self._record(session_id)
            if not record["operator_requested"]:
                record["operator_requested"] = True
                record["operator_reason"] = reason
                if record["status"] not in TERMINAL_STATES:
                    self._transition(record, "stopped", {"operator_requested": True})
            return self._public_state(record)

    def get_episode(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(session_id)
            if record["episode"] is None:
                return {"schema_version": 1, "status": record["status"], "episode_id": record["episode_id"], "downloads": {}}
            return deepcopy(record["episode"])

    def get_episode_trace(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(session_id)
            return {"schema_version": 1, "session_id": session_id, "commands": deepcopy(self.action_log), "status": record["status"]}

    def emit_heartbeat(self, session_id: str) -> None:
        with self._lock:
            record = self._record(session_id)
            self._event(record, "heartbeat", {"lease_id": record["lease_id"]})

    def state(self, session_id: str) -> dict[str, Any]:
        return self.get_session(session_id)

    def _next_observation(self, record: dict[str, Any]) -> None:
        index = record["next_observation"]
        source = dict(self._observations[index])
        payload = {
            "schema_version": 1,
            "session_id": record["session_id"],
            "episode_id": record["episode_id"],
            "lease_id": record["lease_id"],
            "step_id": index,
            "observed_at": time.time(),
            "source": source.get("source", "simulation"),
            "mode": source.get("mode", "API_ACTIVE"),
            "homed": source.get("homed", True),
            "settled": source.get("settled", True),
            "safety": deepcopy(source.get("safety", {
                "ok": True, "estop_engaged": False, "contact_count": 0,
            })),
            "left_joints_deg": list(source.get("left_joints_deg", [])),
            "right_joints_deg": list(source.get("right_joints_deg", [])),
            "left_gripper": source.get("left_gripper"),
            "right_gripper": source.get("right_gripper"),
            "images": deepcopy(source.get("images", {})),
        }
        record["next_observation"] += 1
        self._event(record, "observation", payload)
    def _complete(self, record: dict[str, Any]) -> None:
        self._transition(record, "complete")
        episode = {
            "schema_version": 1,
            "status": "complete",
            "episode_id": record["episode_id"],
            "downloads": {"trace": f"/v1/sessions/{record['session_id']}/episode/trace"},
        }
        record["episode"] = episode
        self._event(record, "episode", episode)
        self._transition(record, "resetting")

    def _transition(self, record: dict[str, Any], state: str, extra: Mapping[str, Any] | None = None) -> None:
        previous = record["status"]
        record["status"] = state
        payload = {"state": state, "previous_state": previous}
        payload.update(extra or {})
        self._event(record, "lifecycle", payload)

    def _event(self, record: dict[str, Any], event_type: str, payload: Mapping[str, Any]) -> None:
        record["events"].put({
            "schema_version": 1,
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": event_type,
            "timestamp": time.time(),
            "session_id": record["session_id"],
            "episode_id": record["episode_id"],
            "payload": deepcopy(dict(payload)),
        })

    @staticmethod
    def _public_state(record: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            "schema_version": 1,
            "session_id": record["session_id"],
            "status": record["status"],
            "position": record["position"],
            "episode_id": record["episode_id"],
            "return_to_rest_requested": record["return_to_rest_requested"],
            "operator_requested": record["operator_requested"],
        }
        if record.get("active_trajectory") is not None:
            result["active_trajectory"] = deepcopy(record["active_trajectory"])
        return result

    def _record(self, session_id: str) -> dict[str, Any]:
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionAPIError("Unknown session", code="session_not_found", status=404)
        return record


def _joint_vector(values: Sequence[float], name: str) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != 6:
        raise ValueError(f"{name} must contain exactly 6 values")
    return result


def _trajectory_waypoints(
    waypoints: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(waypoints, (str, bytes)) or not 1 <= len(waypoints) <= MAX_WAYPOINTS_PER_PACKET:
        raise ValueError(f"waypoints must contain between 1 and {MAX_WAYPOINTS_PER_PACKET} entries")
    required = {"step_id", "left_joints_deg", "right_joints_deg"}
    allowed = required | {"left_gripper", "right_gripper"}
    result: list[dict[str, Any]] = []
    first_step: int | None = None
    for index, raw in enumerate(waypoints):
        if not isinstance(raw, Mapping) or set(raw) - allowed or not required.issubset(raw):
            raise ValueError("Each waypoint must use the exact trajectory waypoint schema")
        step_id = raw["step_id"]
        if isinstance(step_id, bool) or not isinstance(step_id, int) or step_id < 0:
            raise ValueError("waypoint step_id must be a non-negative integer")
        if first_step is None:
            first_step = step_id
        if step_id != first_step + index:
            raise ValueError("waypoint step_ids must be contiguous")
        item: dict[str, Any] = {
            "step_id": step_id,
            "left_joints_deg": _joint_vector(raw["left_joints_deg"], "left_joints_deg"),
            "right_joints_deg": _joint_vector(raw["right_joints_deg"], "right_joints_deg"),
        }
        for arm in ("left", "right"):
            key = f"{arm}_gripper"
            if key in raw:
                value = float(raw[key])
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(f"{key} must be finite and between 0 and 1")
                item[key] = value
        if not all(math.isfinite(value) for name in ("left_joints_deg", "right_joints_deg") for value in item[name]):
            raise ValueError("trajectory joint values must be finite")
        result.append(item)
    return result

"""Lease-aware event loop for the canonical YAM Session API v1 contract."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
import uuid
from typing import Any, Mapping

from .ik import IKSolver, parse_observation
from .feedback import feedback_decision, feedback_fields
from .providers import PolicyComplete, ProviderAdapter
from .interactions import InteractionLog
from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET, SCHEMA_VERSION, TERMINAL_STATES, SessionAPI, SessionAPIError, SessionEventStream


class RunnerController:
    def __init__(
        self,
        session_api: SessionAPI,
        ik_solver: IKSolver | None = None,
        submit_attempts: int = 2,
        robot_id: str = "yam-1",
        hardware_control_enabled: bool = True,
        recording_root: Path | None = None,
    ) -> None:
        self._session_api = session_api
        self._ik_solver = ik_solver or IKSolver()
        self._submit_attempts = max(1, submit_attempts)
        self._robot_id = robot_id
        self._hardware_control_enabled = hardware_control_enabled
        self._execution_blocked_reason = None
        self._lock = threading.RLock()
        self._join_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._events: SessionEventStream | None = None
        self._provider: ProviderAdapter | None = None
        self._prompt = ""
        self._session_id: str | None = None
        self._episode_id: str | None = None
        self._lease_id: str | None = None
        self._command_step_id = 0
        self._status = "idle"
        self._queue_position: int | None = None
        self._last_event_type: str | None = None
        self._heartbeat_seen = False
        self._episode: dict[str, Any] | None = None
        self._last_model_command: dict[str, Any] | None = None
        self._last_observation: dict[str, Any] | None = None
        self._monitor_observation: dict[str, Any] | None = None
        self._queue_snapshot: dict[str, Any] | None = None
        self._return_to_rest_requested = False
        self._operator_requested = False
        self._error: str | None = None
        self._safety_error: dict[str, Any] | None = None
        self._run_events: list[dict[str, Any]] = []
        self._submitted_step_ids: list[int] = []
        self._packets_submitted = 0
        self._trajectory: dict[str, Any] | None = None
        self._latency: dict[str, dict[str, float | int]] = {}
        self._last_action_submitted_monotonic: float | None = None
        self._last_latency_log_monotonic = time.monotonic()
        self._feedback_checks: list[dict[str, Any]] = []
        self._recording_root = recording_root
        self._interactions = InteractionLog()
        listener = getattr(self._session_api, 'set_contact_listener', None)
        if callable(listener):
            listener(self._record_server_contact)

    def join(self, provider: ProviderAdapter, prompt: str) -> dict[str, Any]:
        with self._join_lock:
            with self._lock:
                if self._status in {"queued", "preparing", "running"}:
                    return self.status()
            return self._join_new(provider, prompt)

    def _join_new(self, provider: ProviderAdapter, prompt: str) -> dict[str, Any]:
        with self._lock:
            if self._status in {"queued", "preparing", "running"}:
                raise RuntimeError("Runner is already active")
            if not prompt.strip():
                raise ValueError("Prompt is required")
            self._stop_event.clear()
            self._error = None
            self._safety_error = None
        created = self._session_api.create_session(prompt)
        session_id = str(created["session_id"])
        events = self._session_api.open_events(session_id)
        with self._lock:
            self._provider = provider
            self._prompt = prompt
            self._session_id = session_id
            self._episode_id = created.get("episode_id")
            self._lease_id = None
            self._command_step_id = 0
            self._packets_submitted = 0
            self._status = str(created.get("status", "queued"))
            self._queue_position = created.get("position")
            self._last_event_type = None
            self._heartbeat_seen = False
            self._episode = None
            self._last_model_command = None
            self._last_observation = None
            self._return_to_rest_requested = False
            self._operator_requested = False
            self._run_events = []
            self._submitted_step_ids = []
            self._trajectory = None
            self._feedback_checks = []
            self._execution_blocked_reason = None
            self._latency = {}
            self._last_action_submitted_monotonic = None
            self._last_latency_log_monotonic = time.monotonic()
            self._events = events
            directory = provider.public_config().get('recording_path')
            if not directory and self._recording_root:
                directory = self._recording_root/('run_'+uuid.uuid4().hex)
            self._interactions = InteractionLog(directory)
            if hasattr(provider, 'interaction_sink'):
                provider.interaction_sink = self._interactions.add
            self._interactions.add('joined', 'Joined the policy queue',
                                   session_id=session_id, task=prompt,
                                   provider=provider.provider_name)
        return self.status()

    def join_and_run(
        self, provider: ProviderAdapter, prompt: str, max_steps: int | None = None
    ) -> dict[str, Any]:
        with self._join_lock:
            with self._lock:
                if self._status in {"queued", "preparing", "running"} and self._worker is not None and self._worker.is_alive():
                    return self.status()
            return self._start_worker(provider, prompt, max_steps)

    def _start_worker(self, provider, prompt, max_steps):
        if max_steps is not None and not 1 <= max_steps <= MAX_COMMANDS:
            raise ValueError(f"max_steps must be between 1 and {MAX_COMMANDS}")
        result = self.join(provider, prompt)
        with self._lock:
            self._worker = threading.Thread(
                target=self._run_loop,
                args=(max_steps,),
                name="public-yam-runner",
                daemon=True,
            )
            self._worker.start()
        return result

    def process_next_event(self, timeout_s: float | None = 0.5) -> dict[str, Any]:
        with self._lock:
            events = self._events
            if events is None or self._stop_event.is_set():
                raise RuntimeError("Runner event stream is not active")
        event = events.receive(timeout_s)
        self._handle_event(event)
        return event

    def stop(self, reason: str = "user_requested") -> dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            session_id = self._session_id
            if session_id is None or self._is_terminal(self._status):
                self._close_events()
                return self.status()
            self._return_to_rest_requested = True
            self._interactions.add('stop_requested', 'Stop requested', reason=reason)
        result = self._session_api.stop_session(session_id)
        with self._lock:
            self._status = str(result.get("status", "stopped"))
            self._interactions.add('stopped', 'Session stopped', status=self._status)
        self._close_events()
        return self.status()

    def disconnect(self, reason: str = "runner_disconnected") -> dict[str, Any]:
        self._interactions.add('disconnect', 'Runner disconnect requested', reason=reason)
        with self._lock:
            self._stop_event.set()
            session_id = self._session_id
            should_stop = session_id is not None and not self._is_terminal(self._status)
        if should_stop and session_id is not None:
            try:
                self._session_api.stop_session(session_id)
            except Exception:
                pass
        self._close_events()
        with self._lock:
            self._status = "disconnected"
            self._provider = None
            self._prompt = ""
            self._lease_id = None
        return self.status()

    def call_operator(self, reason: str = "user_requested") -> dict[str, Any]:
        with self._lock:
            if self._operator_requested:
                return self.status()
            session_id = self._session_id
            if session_id is None:
                raise RuntimeError("No session is available for operator handoff")
            self._interactions.add('operator_requested', 'Operator handoff requested', reason=reason)
            self._stop_event.set()
        try:
            self._session_api.call_operator(session_id, reason)
        except Exception as exc:
            try:
                self._session_api.stop_session(session_id)
            except Exception:
                pass
            self._close_events()
            with self._lock:
                self._status = "stopped"
                self._error = f"Operator request failed: {type(exc).__name__}"
            raise
        self._close_events()
        with self._lock:
            self._status = "operator_requested"
            self._operator_requested = True
            self._provider = None
            self._prompt = ""
        return self.status()

    def _record_server_contact(self, kind, detail):
        self._interactions.add(kind, detail['message'], **{k:v for k,v in detail.items() if k != 'message'})

    def status(self) -> dict[str, Any]:
        with self._lock:
            provider_config = self._provider.public_config() if self._provider else None
            contact_issue = getattr(self._session_api, "contact_issue", None)
            if not isinstance(contact_issue, dict):
                contact_issue = (provider_config or {}).get("server_contact_issue")
            return {
                "status": self._status,
                "hardware_control_enabled": self._hardware_control_enabled,
                "execution_blocked_reason": self._execution_blocked_reason,
                "session_id": self._session_id,
                "episode_id": self._episode_id,
                "lease_id": self._lease_id,
                "queue_position": self._queue_position,
                "next_step_id": self._command_step_id,
                "commands_submitted": self._command_step_id,
                "packets_submitted": self._packets_submitted,
                "command_transport": "trajectory" if getattr(self._session_api, "supports_trajectories", False) is True else "single_action",
                "provider": provider_config,
                "prompt_configured": bool(self._prompt),
                "heartbeat_sent": self._heartbeat_seen,
                "last_event_type": self._last_event_type,
                "last_model_command": self._last_model_command,
                "last_observation": self._display_observation(),
                "queue_snapshot": self._queue_for_status(),
                "return_to_rest_requested": self._return_to_rest_requested,
                "operator_requested": self._operator_requested,
                "episode": self._episode,
                "error": self._error,
                "server_contact_issue": contact_issue,
                "safety_error": deepcopy_dict(self._safety_error) if self._safety_error else None,
                "feedback_checks": [deepcopy_dict(item) for item in self._feedback_checks],
                "latency": self._latency_status(),
                "run_summary": {
                    "events": [deepcopy_dict(item) for item in self._run_events],
                    "action_step_ids": list(self._submitted_step_ids),
                },
                "trajectory": self._trajectory_status(),
                "interactions": self._interactions.snapshot(),
            }

    def sanitized_episode_trace(self) -> dict[str, Any]:
        """Fetch the current capability-scoped trace and return no authority or commands."""
        with self._lock:
            session_id = self._session_id
            episode_id = self._episode_id
            terminal_status = self._status
            local_events = [deepcopy_dict(item) for item in self._run_events]
            local_steps = list(self._submitted_step_ids)
            local_error = self._error
        if session_id is None:
            raise RuntimeError("No session trace is available")
        raw = self._session_api.get_episode_trace(session_id)

        def timestamp(item: Mapping[str, Any]) -> float | None:
            value = item.get("timestamp", item.get("created_at", item.get("occurred_at")))
            return float(value) if isinstance(value, (int, float)) else None

        def event_summary(item: Mapping[str, Any]) -> dict[str, Any]:
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            event: dict[str, Any] = {
                "type": str(item.get("type", "unknown")),
                "timestamp": timestamp(item),
            }
            step_id = item.get("step_id", payload.get("step_id"))
            if isinstance(step_id, int):
                event["step_id"] = step_id
            if isinstance(payload.get("replayed"), bool):
                event["replayed"] = payload["replayed"]
            return event

        raw_events = raw.get("events", raw.get("trace", []))
        events = [event_summary(item) for item in raw_events if isinstance(item, Mapping)]
        if not events:
            events = local_events

        raw_actions = raw.get("actions", raw.get("commands", raw.get("action_step_ids", [])))
        action_step_ids: list[int] = []
        for item in raw_actions if isinstance(raw_actions, list) else []:
            step_id = item if isinstance(item, int) else item.get("step_id") if isinstance(item, Mapping) else None
            if isinstance(step_id, int):
                action_step_ids.append(step_id)
        if not action_step_ids:
            action_step_ids = local_steps

        candidates = raw.get("observations", [])
        if not isinstance(candidates, list):
            candidates = []
        candidates = list(candidates) + [
            item.get("payload", {}) for item in raw_events
            if isinstance(item, Mapping) and item.get("type") == "observation"
        ]
        observations: list[dict[str, Any]] = []
        seen_observations: set[tuple[Any, Any]] = set()
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            left, right = item.get("left_joints_deg"), item.get("right_joints_deg")
            if not isinstance(left, list) or not isinstance(right, list) or len(left) != 6 or len(right) != 6:
                continue
            key = (item.get("step_id"), item.get("observed_at"))
            if key in seen_observations:
                continue
            seen_observations.add(key)
            observations.append({
                "step_id": item.get("step_id"),
                "observed_at": item.get("observed_at"),
                "left_joints_deg": [float(value) for value in left],
                "right_joints_deg": [float(value) for value in right],
                "left_gripper": item.get("left_gripper"),
                "right_gripper": item.get("right_gripper"),
            })

        raw_error = raw.get("error")
        raw_errors = raw.get("errors")
        if raw_error is None and isinstance(raw_errors, list) and raw_errors:
            raw_error = raw_errors[-1]
        error_summary: dict[str, Any] | None = None
        if isinstance(raw_error, Mapping):
            error_summary = {
                key: str(raw_error[key])[:500] for key in ("type", "category", "code", "message")
                if raw_error.get(key) is not None
            } or None
            if error_summary is not None:
                for key in ("terminal", "step_id", "observed_at"):
                    if raw_error.get(key) is not None:
                        error_summary[key] = raw_error[key]
                if isinstance(raw_error.get("details"), Mapping):
                    error_summary["details"] = safe_context(raw_error["details"])
        elif raw_error is not None or local_error:
            error_summary = {"message": str(raw_error or local_error)[:500]}
        episode = raw.get("episode") if isinstance(raw.get("episode"), Mapping) else {}
        return {
            "session_id": session_id,
            "episode_id": episode_id,
            "terminal_status": raw.get("terminal_status", raw.get("status", terminal_status)),
            "events": events,
            "action_step_ids": action_step_ids,
            "observations": observations,
            "error": error_summary,
            "episode_terminal_state": episode.get("status", raw.get("episode_terminal_state")),
            "episode_ended_at": episode.get("ended_at", raw.get("episode_ended_at")),
        }

    def update_monitor_observation(self, response: Mapping[str, Any]) -> None:
        """Accept sanitized read-only feedback without creating a control session."""
        payload: Any = response.get("observation") or response.get("payload") or response
        if not isinstance(payload, Mapping):
            raise ValueError("Robot observation response must contain an object")
        observation = self._public_observation(payload)
        parse_observation(observation)
        with self._lock:
            self._monitor_observation = observation

    def update_queue_snapshot(self, response: Mapping[str, Any]) -> None:
        if response.get("schema_version") != SCHEMA_VERSION or response.get("type") != "queue_snapshot":
            raise ValueError("Unsupported queue snapshot")
        entries: list[dict[str, Any]] = []
        for raw in response.get("entries", []):
            if isinstance(raw, Mapping):
                entries.append({
                    "position": int(raw["position"]), "session_id": str(raw["session_id"]),
                    "status": str(raw["status"]), "created_at": float(raw["created_at"]),
                })
        stations: list[dict[str, Any]] = []
        for raw in response.get("stations", []):
            if isinstance(raw, Mapping):
                stations.append({
                    "jetson_id": str(raw["jetson_id"]), "connected": bool(raw["connected"]),
                    "available": bool(raw["available"]), "source": raw.get("source"),
                    "mode": raw.get("mode"), "observed_at": raw.get("observed_at"),
                })
        entries.sort(key=lambda entry: entry["position"])
        with self._lock:
            self._queue_snapshot = {
                "schema_version": SCHEMA_VERSION, "type": "queue_snapshot",
                "generated_at": float(response.get("generated_at", 0)),
                "entries": entries, "stations": stations,
            }

    def monitor_camera_url(self, name: str) -> str | None:
        if name not in {"left", "top", "right"}:
            return None
        with self._lock:
            observation = self._monitor_observation or {}
            images = observation.get("images")
            image = images.get(name) if isinstance(images, Mapping) else None
            url = image.get("url") if isinstance(image, Mapping) else None
            return str(url) if isinstance(url, str) else None

    def _queue_for_status(self) -> dict[str, Any] | None:
        if self._queue_snapshot is None:
            return None
        snapshot = deepcopy_dict(self._queue_snapshot)
        for entry in snapshot["entries"]:
            entry["is_mine"] = entry["session_id"] == self._session_id
        return snapshot

    def _display_observation(self) -> dict[str, Any] | None:
        choices = [item for item in (self._last_observation, self._monitor_observation) if item]
        if not choices:
            return None
        return max(choices, key=lambda item: float(item.get("observed_at") or 0.0))

    def _handle_event(self, event: Mapping[str, Any]) -> None:
        if event.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("Unsupported Session API schema version")
        with self._lock:
            if event.get("session_id") != self._session_id:
                raise RuntimeError("Session event belongs to another session")
            event_type = str(event.get("type", ""))
            self._last_event_type = event_type
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                raise RuntimeError("Session event payload must be an object")
            summary: dict[str, Any] = {
                "type": event_type,
                "timestamp": event.get("timestamp"),
            }
            event_step = event.get("step_id", payload.get("step_id"))
            if isinstance(event_step, int):
                summary["step_id"] = event_step
            if isinstance(payload.get("replayed"), bool):
                summary["replayed"] = payload["replayed"]
            self._run_events.append(summary)
            self._run_events = self._run_events[-512:]
        if event_type == "snapshot":
            active_trajectory = payload.get("active_trajectory")
            with self._lock:
                self._status = str(payload.get("status", self._status))
                self._queue_position = payload.get("position", self._queue_position)
                episode_id = payload.get("episode_id") or event.get("episode_id")
                lease_id = payload.get("lease_id")
                if episode_id:
                    self._episode_id = str(episode_id)
                if lease_id:
                    self._lease_id = str(lease_id)
            if active_trajectory is not None:
                if not isinstance(active_trajectory, Mapping):
                    raise RuntimeError("Session snapshot active_trajectory must be an object")
                self._recover_trajectory(active_trajectory)
            elif self._trajectory is not None and self._trajectory.get("state") != "completed" and not self._is_terminal(self._status):
                self._recover_finished_trajectory()
        elif event_type == "queue_update":
            with self._lock:
                self._status = "queued"
                self._queue_position = payload.get("position", self._queue_position)
        elif event_type == "lifecycle":
            self._lifecycle(event, payload)
        elif event_type == "observation":
            self._observation(event, payload)
        elif event_type == "heartbeat":
            with self._lock:
                self._heartbeat_seen = True
        elif event_type == "episode":
            with self._lock:
                self._episode = deepcopy_dict(payload)
        elif event_type == "error":
            self._handle_error(payload)
        elif event_type == "trajectory_progress":
            self._trajectory_progress(payload)
        elif event_type == "trajectory_result":
            self._trajectory_result(payload)
        elif event_type == "action_result":
            with self._lock:
                if self._trajectory is not None and self._trajectory.get("state") != "completed":
                    raise RuntimeError("Legacy action result received while a trajectory is active")
        else:
            raise RuntimeError(f"Unsupported Session API event type: {event_type}")

    def _lifecycle(self, event: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        state = str(payload.get("state", ""))
        self._interactions.add('lifecycle', f'Session {state}', reason=payload.get('reason'),
                               episode_id=event.get('episode_id') or self._episode_id)
        with self._lock:
            self._status = state
            episode_id = event.get("episode_id")
            if episode_id:
                self._episode_id = str(episode_id)
            lease_id = payload.get("lease_id")
            if lease_id:
                self._lease_id = str(lease_id)
            if self._is_terminal(state):
                self._stop_event.set()
                self._provider = None
                self._prompt = ""
                if state == "timed_out":
                    self._error = "Policy failed: 3-minute runtime limit exceeded; station parking at zero"
                if state == "safety_aborted" and self._safety_error is None:
                    reason = str(payload.get("reason") or "safety_abort")
                    self._safety_error = {
                        "category": "hardware_safety",
                        "code": reason,
                        "message": reason,
                        "terminal": True,
                        "step_id": None,
                        "observed_at": None,
                        "details": {},
                    }
                    self._error = f"{reason}: {reason}"

    def _handle_error(self, payload: Mapping[str, Any]) -> None:
        legacy = not payload.get("code") and payload.get("reason") is not None
        category = str(payload.get("category") or ("hardware_safety" if legacy else "session"))
        code = str(payload.get("code") or ("safety_abort" if legacy else "session_error"))
        message = str(payload.get("message") or payload.get("reason") or "Session API error")[:500]
        terminal = payload.get("terminal") is True or legacy
        context = {
            "category": category,
            "code": code,
            "message": message,
            "terminal": terminal,
            "step_id": payload.get("step_id") if isinstance(payload.get("step_id"), int) else None,
            "observed_at": payload.get("observed_at") if isinstance(payload.get("observed_at"), (int, float)) else None,
            "details": safe_context(payload.get("details", {})),
        }
        self._interactions.add('error', f'{code}: {message}', context=context)
        with self._lock:
            self._error = f"{code}: {message}"
            if category == "hardware_safety" or terminal:
                self._safety_error = context
            if terminal:
                self._status = "safety_aborted"
                self._stop_event.set()
                self._provider = None
                self._prompt = ""
        if terminal:
            self._close_events()

    def _observation(self, event: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        with self._lock:
            provider = self._provider
            session_id = self._session_id
            episode_id = str(payload.get("episode_id") or event.get("episode_id") or self._episode_id or "")
            lease_id = str(payload.get("lease_id") or self._lease_id or "")
            step_id = self._command_step_id
            observation_step = payload.get("step_id")
            prompt = self._prompt
            if self._status != "running":
                raise RuntimeError("Observation received outside a running session")
            self._last_observation = self._public_observation(payload)
        if not isinstance(observation_step, int):
            raise RuntimeError("Observation is missing a numeric step_id")
        if observation_step < step_id:
            return
        if observation_step > step_id:
            raise RuntimeError(
                f"Observation step gap: expected {step_id}, received {observation_step}"
            )
        with self._lock:
            source = (self._monitor_observation or {}).get("source") or payload.get("source")
            if not self._hardware_control_enabled and source != "simulation":
                self._execution_blocked_reason = "Waiting for hardware execution enablement; your session remains joined"
                return
            self._execution_blocked_reason = None
        with self._lock:
            submitted_at = self._last_action_submitted_monotonic
            self._last_action_submitted_monotonic = None
        if submitted_at is not None:
            self._note_latency("command_settle", (time.monotonic() - submitted_at) * 1000.0)
        if provider is None or session_id is None or not episode_id or not lease_id:
            raise RuntimeError("Observation is missing runner lease context")
        if step_id >= MAX_COMMANDS:
            raise RuntimeError(f"Session command limit of {MAX_COMMANDS} reached")
        provider_observation = dict(payload)
        provider_observation["episode_id"] = episode_id
        observation = parse_observation(provider_observation)
        provider_observation["left_joints_deg"] = list(observation.left_joints_deg)
        provider_observation["right_joints_deg"] = list(observation.right_joints_deg)
        with self._lock:
            station = deepcopy_dict(self._monitor_observation or {})
        observed_at = float(station.get("observed_at") or 0.0)
        if not station or time.time() - observed_at > 10.0:
            try:
                self.update_monitor_observation(
                    self._session_api.get_robot_observation(self._robot_id)
                )
            except Exception as exc:
                raise RuntimeError(
                    "Fresh station safety context is required before inference"
                ) from exc
            with self._lock:
                station = deepcopy_dict(self._monitor_observation or {})
            observed_at = float(station.get("observed_at") or 0.0)
        if time.time() - observed_at > 10.0:
            raise RuntimeError("Fresh station safety context is required before inference")
        if station.get("source") == "hardware":
            station = self._reconcile_hardware_feedback(payload, station, require_home=step_id == 0)
        provider_observation["source"] = station.get("source")
        provider_observation["mode"] = station.get("mode")
        provider_observation["safety"] = station.get("safety")
        provider_observation["images"] = deepcopy_dict(station.get("images") or {})
        if station.get("source") == "hardware":
            leased_observed_at = payload.get("observed_at")
            if not isinstance(leased_observed_at, (int, float)):
                raise RuntimeError("Fresh hardware observation observed_at is required")
            leased_age = time.time() - float(leased_observed_at)
            if leased_age < -2.0 or leased_age > 10.0:
                raise RuntimeError("Fresh hardware observation observed_at is required")
            provider_observation["homed"] = (
                payload.get("homed") is True and station.get("homed") is True
            )
            provider_observation["settled"] = (
                payload.get("settled") is True and station.get("settled") is True
            )
        with self._lock:
            trajectory = self._trajectory
        if trajectory is not None:
            self._trajectory_final_observation(provider, provider_observation, observation_step)
            with self._lock:
                self._trajectory["final_payload"] = dict(payload)
            return
        self._interactions.add('observation', 'Measured arm feedback received',
                               step_id=observation_step,
                               left_joints_deg=provider_observation['left_joints_deg'],
                               right_joints_deg=provider_observation['right_joints_deg'],
                               left_gripper=provider_observation.get('left_gripper'),
                               right_gripper=provider_observation.get('right_gripper'))
        trajectory_builder = getattr(provider, "build_trajectory", None)
        if getattr(self._session_api, "supports_trajectories", False) is True:
            if not callable(trajectory_builder):
                def trajectory_builder(prompt, observation, first_step_id):
                    from .model_trajectory import build_model_trajectory
                    return build_model_trajectory(
                        provider.infer(prompt, observation), observation, first_step_id, self._ik_solver,
                    )
            self._dispatch_trajectory(
                trajectory_builder,
                provider,
                prompt,
                provider_observation,
                session_id,
                episode_id,
                lease_id,
                step_id,
            )
            return
        try:
            model_started = time.monotonic()
            try:
                requested = provider.infer(prompt, provider_observation)
            finally:
                self._note_latency("model", (time.monotonic() - model_started) * 1000.0)
        except PolicyComplete:
            self.stop("policy_complete")
            return
        resolved = self._ik_solver.resolve(requested)
        idempotency_key = f"{episode_id}:step:{step_id}"
        receipt: dict[str, Any] | None = None
        for attempt in range(self._submit_attempts):
            server_started = time.monotonic()
            try:
                receipt = self._session_api.submit_action(
                    session_id,
                    episode_id,
                    lease_id,
                    step_id,
                    idempotency_key,
                    resolved.left_joints_deg,
                    resolved.right_joints_deg,
                    resolved.left_gripper,
                    resolved.right_gripper,
                )
                break
            except SessionAPIError:
                raise  # HTTP transport already exhausted its safe retry budget.
            except Exception:
                if attempt + 1 >= self._submit_attempts:
                    raise
            finally:
                self._note_latency("server_action_rtt", (time.monotonic() - server_started) * 1000.0)
        if receipt is None or int(receipt.get("step_id", step_id)) != step_id:
            raise RuntimeError("Session API acknowledged the wrong command step")
        with self._lock:
            self._episode_id = episode_id
            self._lease_id = lease_id
            self._last_model_command = {
                "step_id": step_id,
                "left": {
                    "mode": requested.left.mode,
                    "values": list(requested.left.values),
                    "joints_deg": list(resolved.left_joints_deg),
                },
                "right": {
                    "mode": requested.right.mode,
                    "values": list(requested.right.values),
                    "joints_deg": list(resolved.right_joints_deg),
                },
                "left_gripper": resolved.left_gripper,
                "right_gripper": resolved.right_gripper,
            }
            self._command_step_id += 1
            self._submitted_step_ids.append(step_id)
            self._last_action_submitted_monotonic = time.monotonic()

    def _reconcile_hardware_feedback(self, payload, station, *, require_home):
        # A monitor sample less than ten seconds old can still predate completion.
        # Refresh that sample instead of treating its moving flag as a new failure.
        for attempt in range(4):
            if self._stop_event.is_set():
                raise RuntimeError("Feedback reconciliation interrupted by Stop")
            decision, reason = feedback_decision(
                payload, station, now=time.time(), require_home=require_home,
            )
            with self._lock:
                self._feedback_checks.append({
                    "attempt": attempt, "decision": decision, "reason": reason,
                    "completion": feedback_fields(payload), "station": feedback_fields(station),
                })
                self._feedback_checks = self._feedback_checks[-16:]
            if decision == "ready":
                return station
            if decision == "reject" or attempt == 3:
                raise RuntimeError(f"Hardware feedback blocked: {reason}")
            # Gateway station status is published every 0.5 seconds. Allow three
            # publication opportunities rather than exhausting retries in 0.3s.
            if self._stop_event.wait(0.5):
                raise RuntimeError("Feedback reconciliation interrupted by Stop")
            self.update_monitor_observation(self._session_api.get_robot_observation(self._robot_id))
            with self._lock:
                station = deepcopy_dict(self._monitor_observation or {})
        raise AssertionError("Unreachable feedback state")

    def _dispatch_trajectory(
        self,
        builder: Any,
        provider: ProviderAdapter,
        prompt: str,
        observation: Mapping[str, Any],
        session_id: str,
        episode_id: str,
        lease_id: str,
        first_step_id: int,
    ) -> None:
        def still_running():
            with self._lock:
                return (not self._stop_event.is_set() and self._status == "running"
                        and self._provider is provider and self._session_id == session_id
                        and self._episode_id == episode_id and self._lease_id == lease_id)

        if hasattr(provider, "cancelled"):
            provider.cancelled = lambda: not still_running()
        model_started = time.monotonic()
        try:
            waypoints = builder(prompt, observation, first_step_id)
        except PolicyComplete:
            if still_running():
                self.stop("policy_complete")
            return
        finally:
            self._note_latency("model", (time.monotonic() - model_started) * 1000.0)
        # Inference can block while the operator stops or replaces this session.
        # Its late response must never dispatch into the old or a new lease.
        if not still_running():
            return
        if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= MAX_WAYPOINTS_PER_PACKET:
            raise RuntimeError(f"Trajectory provider must return 1..{MAX_WAYPOINTS_PER_PACKET} waypoints")
        expected_steps = list(range(first_step_id, first_step_id + len(waypoints)))
        if expected_steps[-1] >= MAX_COMMANDS:
            raise RuntimeError("Trajectory exceeds the session command limit")
        allowed = {"step_id", "left_joints_deg", "right_joints_deg", "left_gripper", "right_gripper"}
        required = {"step_id", "left_joints_deg", "right_joints_deg"}
        for expected_step, waypoint in zip(expected_steps, waypoints):
            if not isinstance(waypoint, Mapping):
                raise RuntimeError("Trajectory waypoint must be an object")
            if set(waypoint) - allowed or not required.issubset(waypoint):
                raise RuntimeError("Trajectory waypoint does not match the v1 schema")
            if waypoint.get("step_id") != expected_step:
                raise RuntimeError("Trajectory waypoint steps must be contiguous")
        trajectory_id = f"traj_{episode_id}_{first_step_id}"
        state = {
            "trajectory_id": trajectory_id,
            "state": "dispatching",
            "cadence_hz": 10.0,
            "first_step_id": expected_steps[0],
            "last_step_id": expected_steps[-1],
            "waypoint_count": len(expected_steps),
            "final_target": dict(waypoints[-1]),
            "progress_count": 0,
            "last_progress_step_id": None,
            "final_observation_received": False,
            "dispatched_at": None,
            "dispatch_started_monotonic": time.monotonic(),
        }
        with self._lock:
            if not still_running():
                return
            if self._trajectory is not None:
                raise RuntimeError("A trajectory is already active")
            self._trajectory = state
        self._interactions.add('packet_submit', f'Sending {len(waypoints)} waypoints to AWS',
                               trajectory_id=trajectory_id, cadence_hz=10, waypoint_count=len(waypoints),
                               waypoints=waypoints)
        receipt: dict[str, Any] | None = None
        for attempt in range(self._submit_attempts):
            if not still_running():
                return
            server_started = time.monotonic()
            try:
                receipt = self._session_api.submit_trajectory(
                    session_id,
                    episode_id,
                    lease_id,
                    trajectory_id,
                    10.0,
                    waypoints,
                )
                break
            except SessionAPIError:
                raise  # HTTP transport already exhausted its safe retry budget.
            except Exception:
                if attempt + 1 >= self._submit_attempts:
                    raise
            finally:
                self._note_latency(
                    "server_trajectory_dispatch_rtt",
                    (time.monotonic() - server_started) * 1000.0,
                )
        expected_ack = {
            "schema_version", "accepted", "duplicate", "session_id", "episode_id", "lease_id",
            "trajectory_id", "first_step_id", "last_step_id", "waypoint_count", "dispatched_at",
        }
        if receipt is None or set(receipt) != expected_ack:
            raise RuntimeError("Session API returned a malformed trajectory dispatch acknowledgement")
        if (
            receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("accepted") is not True
            or not isinstance(receipt.get("duplicate"), bool)
            or receipt.get("session_id") != session_id
            or receipt.get("episode_id") != episode_id
            or receipt.get("lease_id") != lease_id
            or receipt.get("trajectory_id") != trajectory_id
            or receipt.get("first_step_id") != expected_steps[0]
            or receipt.get("last_step_id") != expected_steps[-1]
            or receipt.get("waypoint_count") != len(expected_steps)
            or not isinstance(receipt.get("dispatched_at"), (int, float))
        ):
            raise RuntimeError("Session API acknowledged the wrong trajectory")
        with self._lock:
            if not still_running():
                return
            assert self._trajectory is not None
            self._trajectory["state"] = "dispatched"
            self._trajectory["dispatched_at"] = float(receipt["dispatched_at"])
            self._episode_id = episode_id
            self._lease_id = lease_id
            self._command_step_id = expected_steps[-1] + 1
            self._submitted_step_ids.extend(expected_steps)
            self._packets_submitted += 1
            self._last_model_command = {
                "type": "joint_trajectory",
                "trajectory_id": trajectory_id,
                "cadence_hz": 10.0,
                "first_step_id": expected_steps[0],
                "last_step_id": expected_steps[-1],
                "waypoint_count": len(expected_steps),
                "provider": provider.public_config(),
            }
            self._last_action_submitted_monotonic = time.monotonic()
            self._interactions.add('packet_dispatched', 'AWS accepted the packet for dispatch; waiting for gateway acceptance',
                                   trajectory_id=trajectory_id, waypoint_count=len(waypoints))

    def _trajectory_progress(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            trajectory = self._trajectory
            if trajectory is None:
                raise RuntimeError("Trajectory progress received without a local trajectory")
            if payload.get("trajectory_id") != trajectory["trajectory_id"]:
                raise RuntimeError("Trajectory progress belongs to another trajectory")
            if trajectory["state"] != "accepted":
                raise RuntimeError("Trajectory progress arrived before Jetson acceptance")
            step_id = payload.get("step_id")
            expected = trajectory["first_step_id"] + trajectory["progress_count"]
            if isinstance(step_id, bool) or not isinstance(step_id, int) or step_id != expected:
                raise RuntimeError(
                    f"Trajectory progress gap: expected {expected}, received {step_id}"
                )
            if not isinstance(payload.get("executed_at"), (int, float)):
                raise RuntimeError("Trajectory progress is missing executed_at")
            trajectory["progress_count"] += 1
            trajectory["last_progress_step_id"] = step_id
            self._interactions.add('packet_progress',
                                   f'Executing waypoint {trajectory["progress_count"]}/{trajectory["waypoint_count"]}',
                                   trajectory_id=trajectory['trajectory_id'], step_id=step_id,
                                   progress_count=trajectory['progress_count'], waypoint_count=trajectory['waypoint_count'])

    def _trajectory_result(self, payload: Mapping[str, Any]) -> None:
        status = payload.get("status")
        with self._lock:
            trajectory = self._trajectory
            if trajectory is None:
                raise RuntimeError("Trajectory result received without a local trajectory")
            if payload.get("trajectory_id") != trajectory["trajectory_id"]:
                raise RuntimeError("Trajectory result belongs to another trajectory")
            if not isinstance(payload.get("reported_at"), (int, float)):
                raise RuntimeError("Trajectory result is missing reported_at")
            if status == "accepted":
                if trajectory["state"] not in {"dispatched", "accepted"}:
                    raise RuntimeError("Trajectory acceptance arrived out of order")
                trajectory["state"] = "accepted"
                self._interactions.add('packet_accepted', 'Gateway safety checks passed; executing packet',
                                       trajectory_id=trajectory['trajectory_id'])
                return
            if status in {"rejected", "aborted"}:
                code = str(payload.get("code") or f"trajectory_{status}")
                message = str(payload.get("message") or status)
                self._interactions.add('packet_error', f'Gateway {status}: {code} — {message}',
                                       stage='gateway', code=code, status=status,
                                       reported_progress=trajectory['progress_count'],
                                       trajectory_id=trajectory['trajectory_id'])
                raise RuntimeError(f"{code}: {message}")
            if status != "completed":
                raise RuntimeError(f"Unsupported trajectory result status: {status}")
            if trajectory["state"] != "accepted":
                raise RuntimeError("Trajectory completion arrived before acceptance")
            if trajectory["progress_count"] != trajectory["waypoint_count"]:
                raise RuntimeError("Trajectory completed before all waypoint progress")
            if trajectory["final_observation_received"] is not True:
                raise RuntimeError("Trajectory completed before its final settled observation")
            result_step = payload.get("step_id")
            if result_step is not None and result_step != trajectory["last_step_id"]:
                raise RuntimeError("Trajectory completion reported the wrong final step")
            trajectory["state"] = "completed"
            started = trajectory.get("dispatch_started_monotonic")
        if isinstance(started, (int, float)):
            self._note_latency("trajectory_total", (time.monotonic() - started) * 1000.0)
        with self._lock:
            final_payload = trajectory.get("final_payload")
            provider = self._provider
            self._trajectory = None
            self._interactions.add('packet_completed', f'Completed {trajectory["waypoint_count"]} waypoints; arms settled',
                                   trajectory_id=trajectory['trajectory_id'], waypoint_count=trajectory['waypoint_count'],
                                   feedback={k:final_payload.get(k) for k in ('left_joints_deg','right_joints_deg','left_gripper','right_gripper')} if final_payload else None)
        # A packet completing does not release the policy lease. Ask the policy
        # for its next packet using measured, settled feedback.
        if final_payload is not None:
            completed = getattr(provider, "trajectory_completed", None)
            if callable(completed):
                completed(final_payload, trajectory["waypoint_count"])
            self._observation({}, final_payload)
            with self._lock:
                if self._trajectory is None and self._status == "stopped":
                    self._trajectory = trajectory
        else:
            raise RuntimeError("Completed packet has no measured final observation")

    def _trajectory_final_observation(
        self,
        provider: ProviderAdapter,
        observation: Mapping[str, Any],
        observation_step: int,
    ) -> None:
        with self._lock:
            trajectory = self._trajectory
            if trajectory is None:
                raise RuntimeError("Final trajectory observation has no trajectory context")
            expected = trajectory["last_step_id"] + 1
            if observation_step != expected:
                raise RuntimeError(
                    f"Final trajectory observation gap: expected {expected}, received {observation_step}"
                )
            if trajectory["state"] != "accepted":
                raise RuntimeError("Final trajectory observation arrived before acceptance")
            if trajectory["progress_count"] != trajectory["waypoint_count"]:
                raise RuntimeError("Final trajectory observation arrived before all progress")
        if observation.get("settled") is not True:
            raise RuntimeError("Final trajectory observation must be settled")
        validator = getattr(provider, "validate_trajectory_final", None)
        if callable(validator):
            validator(observation)
        else:
            for field in ("left_joints_deg", "right_joints_deg"):
                measured = observation.get(field)
                target = trajectory["final_target"][field]
                if not isinstance(measured, (list, tuple)) or len(measured) != 6:
                    raise RuntimeError(f"Final trajectory observation missing {field}")
                if any(not math.isfinite(float(a)) or abs(float(a) - float(b)) > math.degrees(.05)
                       for a, b in zip(measured, target)):
                    raise RuntimeError(f"Final trajectory observation has not reached {field}")
        with self._lock:
            assert self._trajectory is not None
            self._trajectory["final_observation_received"] = True

    def _recover_finished_trajectory(self) -> None:
        """Recover missed progress when a packet completed during disconnection.

        The server replays the final observation and result, but its snapshot no
        longer contains an active trajectory. Require the authenticated trace's
        acceptance and complete ordered progress before consuming that replay.
        """
        with self._lock:
            trajectory = self._trajectory
            session_id, episode_id, lease_id = self._session_id, self._episode_id, self._lease_id
        if trajectory is None or session_id is None:
            raise RuntimeError("Cannot recover an unknown completed trajectory")
        trace = self._session_api.get_episode_trace(session_id)
        trajectory_id = trajectory["trajectory_id"]

        def matching(field):
            values = trace.get(field, [])
            if not isinstance(values, list):
                raise RuntimeError("Trajectory recovery trace is malformed")
            matches = [v for v in values if isinstance(v, Mapping) and v.get("trajectory_id") == trajectory_id]
            for value in matches:
                if value.get("episode_id") != episode_id or value.get("lease_id") != lease_id:
                    raise RuntimeError("Trajectory recovery belongs to another episode or lease")
                if value.get("session_id", session_id) != session_id:
                    raise RuntimeError("Trajectory recovery belongs to another session")
            return matches

        results = matching("trajectory_results")
        failure = next((r for r in results if r.get("status") in {"rejected", "aborted", "stopped"}), None)
        if failure is not None:
            raise RuntimeError(f"Trajectory failed while disconnected: {failure.get('code') or failure['status']}")
        if [r.get("status") for r in results] != ["accepted", "completed"]:
            raise RuntimeError("Trajectory recovery requires recorded acceptance and completion")
        progress = matching("trajectory_progress")
        expected = list(range(trajectory["first_step_id"], trajectory["last_step_id"] + 1))
        if [p.get("step_id") for p in progress] != expected:
            raise RuntimeError("Trajectory recovery has incomplete or unordered progress")
        if any(not isinstance(p.get("executed_at"), (int, float)) for p in progress):
            raise RuntimeError("Trajectory recovery is missing execution timestamps")
        with self._lock:
            trajectory["state"] = "accepted"
            trajectory["progress_count"] = len(progress)
            trajectory["last_progress_step_id"] = expected[-1]
            # Validate the replayed settled observation through the normal path.
            trajectory["final_observation_received"] = False

    def _recover_trajectory(self, active: Mapping[str, Any]) -> None:
        with self._lock:
            trajectory = self._trajectory
            if trajectory is None:
                raise RuntimeError("Cannot recover an unknown active trajectory")
            for key in ("trajectory_id", "first_step_id", "last_step_id", "waypoint_count"):
                if active.get(key) != trajectory.get(key):
                    raise RuntimeError(f"Active trajectory snapshot changed {key}")
            state = active.get("state")
            if state not in {"dispatched", "accepted"}:
                raise RuntimeError("Active trajectory snapshot has an invalid state")
            progress_count = active.get("progress_count")
            if (
                isinstance(progress_count, bool)
                or not isinstance(progress_count, int)
                or not 0 <= progress_count <= trajectory["waypoint_count"]
            ):
                raise RuntimeError("Active trajectory snapshot has invalid progress")
            expected_last = None if progress_count == 0 else trajectory["first_step_id"] + progress_count - 1
            if active.get("last_progress_step_id") != expected_last:
                raise RuntimeError("Active trajectory snapshot progress cursor is inconsistent")
            if not isinstance(active.get("final_observation_received"), bool):
                raise RuntimeError("Active trajectory snapshot lacks final observation state")
            trajectory["state"] = state
            trajectory["progress_count"] = progress_count
            trajectory["last_progress_step_id"] = expected_last
            trajectory["final_observation_received"] = active["final_observation_received"]
            if isinstance(active.get("dispatched_at"), (int, float)):
                trajectory["dispatched_at"] = float(active["dispatched_at"])

    def _trajectory_status(self) -> dict[str, Any] | None:
        if self._trajectory is None:
            return None
        return {
            key: self._trajectory.get(key)
            for key in (
                "trajectory_id", "state", "cadence_hz", "first_step_id", "last_step_id",
                "waypoint_count", "progress_count", "last_progress_step_id",
                "final_observation_received", "dispatched_at",
            )
        }

    def _note_latency(self, stage: str, elapsed_ms: float) -> None:
        report: str | None = None
        with self._lock:
            stats = self._latency.setdefault(
                stage, {"count": 0, "total_ms": 0.0, "last_ms": 0.0, "max_ms": 0.0}
            )
            stats["count"] = int(stats["count"]) + 1
            stats["total_ms"] = float(stats["total_ms"]) + elapsed_ms
            stats["last_ms"] = elapsed_ms
            stats["max_ms"] = max(float(stats["max_ms"]), elapsed_ms)
            now = time.monotonic()
            if now - self._last_latency_log_monotonic >= 2.0:
                fields = []
                for name, values in sorted(self._latency.items()):
                    count = int(values["count"])
                    average = float(values["total_ms"]) / count
                    fields.append(
                        f"{name}={average:.1f}/{float(values['max_ms']):.1f}ms n={count}"
                    )
                report = "[lat] " + " ".join(fields)
                self._last_latency_log_monotonic = now
        if report:
            print(report, flush=True)

    def _latency_status(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for name, values in self._latency.items():
            count = int(values["count"])
            result[name] = {
                "count": count,
                "last_ms": round(float(values["last_ms"]), 2),
                "avg_ms": round(float(values["total_ms"]) / count, 2),
                "max_ms": round(float(values["max_ms"]), 2),
            }
        return result

    @staticmethod
    def _public_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
        def numbers(value: Any) -> list[float]:
            if not isinstance(value, (list, tuple)):
                return []
            return [float(item) for item in value if isinstance(item, (int, float))]

        images: dict[str, dict[str, str]] = {}
        raw_images = payload.get("images", {})
        if isinstance(raw_images, Mapping):
            for raw_name, raw_image in raw_images.items():
                url: Any = raw_image
                if isinstance(raw_image, Mapping):
                    url = raw_image.get("url") or raw_image.get("frame_url") or raw_image.get("image_url")
                if isinstance(url, str) and url.startswith(("/", "https://", "http://", "data:image/")):
                    images[str(raw_name)] = {"url": url}
        fallback_url = payload.get("frame_url") or payload.get("image_url") or payload.get("camera_url")
        if not images and isinstance(fallback_url, str) and fallback_url.startswith(("/", "https://", "http://", "data:image/")):
            images["camera"] = {"url": fallback_url}
        return {
            "episode_id": payload.get("episode_id"),
            "step_id": payload.get("step_id"),
            "observed_at": payload.get("observed_at"),
            "source": payload.get("source") if payload.get("source") in {"simulation", "hardware"} else None,
            "mode": payload.get("mode"),
            "homed": payload.get("homed") if isinstance(payload.get("homed"), bool) else None,
            "settled": payload.get("settled") if isinstance(payload.get("settled"), bool) else None,
            "left_joints_deg": numbers(payload.get("left_joints_deg")),
            "right_joints_deg": numbers(payload.get("right_joints_deg")),
            "left_gripper": payload.get("left_gripper"),
            "right_gripper": payload.get("right_gripper"),
            "safety": {
                "ok": payload.get("safety", {}).get("ok") is True,
                "estop_engaged": payload.get("safety", {}).get("estop_engaged"),
                "contact_count": payload.get("safety", {}).get("contact_count"),
                "active_contacts": payload.get("safety", {}).get("active_contacts"),
                "reason": payload.get("safety", {}).get("reason"),
            } if isinstance(payload.get("safety"), Mapping) else None,
            "images": images,
        }

    def _run_loop(self, max_steps: int | None) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self.process_next_event(0.5)
                except TimeoutError:
                    continue
                except ConnectionError as exc:
                    self._record_event_disconnect(exc)
                    if not self._reconnect_events():
                        return
                    continue
                with self._lock:
                    if max_steps is not None and self._command_step_id >= max_steps:
                        self.stop("max_steps")
                        return
        except Exception as exc:
            if self._stop_event.is_set():
                return
            with self._lock:
                session_id = self._session_id
                self._error = f"{type(exc).__name__}: {exc}"
                self._status = "disconnected" if isinstance(exc, ConnectionError) else "stopped"
                self._stop_event.set()
                self._interactions.add('error', self._error)
            if session_id is not None:
                try:
                    self._session_api.stop_session(session_id)
                except Exception:
                    pass
            self._close_events()

    def _record_event_disconnect(self, exc: ConnectionError) -> None:
        self._interactions.add('reconnecting', 'Server contact issue — reconnecting to the same session')
        # Exception messages may contain URLs/headers. Preserve diagnostic types
        # and numeric errno only, never credentials or arbitrary exception text.
        cause = exc.__cause__ or exc
        errno = getattr(cause, "errno", None)
        with self._lock:
            event = {
                "type": "event_stream_disconnect",
                "timestamp": time.time(),
                "cause_type": type(cause).__name__,
                "errno": errno if isinstance(errno, int) else None,
                "previous_event_type": self._last_event_type,
            }
            self._run_events.append(event)
            self._run_events = self._run_events[-512:]
        print(
            f"[connect] event_stream_disconnect cause={event['cause_type']} "
            f"errno={event['errno']} previous_event={event['previous_event_type']}",
            flush=True,
        )

    def _reconnect_events(self) -> bool:
        """Pause command processing and reconnect only to the same capability-scoped session."""
        last_error: Exception | None = None
        for attempt in range(3):
            if self._stop_event.is_set():
                return False
            with self._lock:
                session_id = self._session_id
            if session_id is None:
                return False
            try:
                state = self._session_api.get_session(session_id)
                status = str(state.get("status", ""))
                if self._is_terminal(status):
                    with self._lock:
                        self._status = status
                        self._stop_event.set()
                    self._close_events()
                    return False
                replacement = self._session_api.open_events(session_id)
                with self._lock:
                    previous, self._events = self._events, replacement
                if previous is not None:
                    previous.close()
                return True
            except Exception as exc:
                last_error = exc
                time.sleep(0.25 * (attempt + 1))
        raise ConnectionError(
            f"Server contact issue — event stream reconnect failed: {type(last_error).__name__}"
        ) from last_error

    @staticmethod
    def _is_terminal(status: str) -> bool:
        return status == "safety_aborted" or status in TERMINAL_STATES

    def _close_events(self) -> None:
        with self._lock:
            events, self._events = self._events, None
        if events is not None:
            events.close()


def deepcopy_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy_dict(item) if isinstance(item, dict) else item for key, item in value.items()}


def safe_context(value: Any, depth: int = 0) -> Any:
    """Bound untrusted safety context while preserving useful local diagnostics."""
    if depth >= 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: safe_context(item, depth + 1)
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple)):
        return [safe_context(item, depth + 1) for item in value[:32]]
    return str(value)[:500]

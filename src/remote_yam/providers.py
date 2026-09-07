"""Credential-isolated model provider adapters."""

from __future__ import annotations

import base64
import json
import math
import re
import ssl
import time
import http.client
from typing import Any, Mapping, Protocol, Sequence
from urllib import request, error

from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET
from .ik import ArmIKCommand, IKCommand
from .cameras import CameraFrameSource
from .mujoco_ik import BimanualRelativeIK, RelativeTrajectory


class ProviderAdapter(Protocol):
    provider_name: str
    model: str

    def public_config(self) -> dict[str, Any]: ...
    def infer(self, prompt: str, observation: Mapping[str, Any]) -> IKCommand: ...


class ResponsesAdapter:
    """Small Responses-compatible adapter with no SDK dependency."""

    provider_name = "responses"

    def __init__(self, api_key: str, model: str, endpoint: str, *, camera_source: CameraFrameSource | None = None) -> None:
        if not api_key.strip():
            raise ValueError("A provider API key is required")
        if not model.strip():
            raise ValueError("A model is required")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("Provider endpoint must be an HTTP(S) URL")
        self._api_key = api_key
        self.model = model
        self._endpoint = endpoint
        self._camera_source = camera_source
        self._vision_state = "awaiting_observation" if camera_source else "disabled"
        self._vision_frames: list[dict[str, Any]] = []
        self._contact_issue = None
        self.cancelled = lambda: False

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r}, api_key_configured=True)"

    def public_config(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name, "model": self.model, "api_key_configured": True,
            "vision": {"state": self._vision_state, "frames": list(self._vision_frames)},
            "server_contact_issue": self._contact_issue,
        }

    def _observation_input(self, observation: Mapping[str, Any]) -> Any:
        if self._camera_source is None:
            if observation.get("source") == "hardware":
                raise RuntimeError("Hardware model inference requires a camera frame source")
            return json.dumps({"observation": observation}, separators=(",", ":"))
        self._vision_state = "capturing"
        self._vision_frames = []
        try:
            frames = self._camera_source.capture(observation)
        except Exception:
            self._vision_state = "camera_error"
            raise
        self._vision_frames = [frame.summary() for frame in frames]
        state = {key: value for key, value in observation.items() if key != "images"}
        content = [{"type": "input_text", "text": json.dumps({"observation": state}, separators=(",", ":"))}]
        # OpenAI Responses vision format; see docs/refs/openai/vision-inputs.md.
        for frame in frames:
            content.append({"type": "input_text", "text": f"Camera view: {frame.name}. Received at {frame.received_at:.3f} UTC epoch seconds."})
            content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode("ascii"), "detail": "high"})
        self._vision_state = "requesting"
        return [{"role": "user", "content": content}]

    def infer(self, prompt: str, observation: Mapping[str, Any]) -> IKCommand:
        instructions = (
            f"{prompt.strip()}\n\nReturn only one JSON object with both arms explicitly: "
            '{"left":{"mode":"joints"|"pose","values":[v1,v2,v3,v4,v5,v6]},'
            '"right":{"mode":"joints"|"pose","values":[v1,v2,v3,v4,v5,v6]},'
            '"left_gripper":0.0,"right_gripper":0.0}. '
            "Use joint values in degrees. Use absolute world-frame pose values [x,y,z,roll,pitch,yaw], positions in meters and angles in radians. Grippers are optional values from 0 to 1. "
            "Never omit, copy, mirror, or pad either arm."
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": self._observation_input(observation),
            "store": False,
        }
        try:
            raw = self._post_json(payload)
        except Exception:
            if self._camera_source is not None:
                self._vision_state = "request_error"
            raise
        if self._camera_source is not None:
            self._vision_state = "response_received"
        return _parse_action(_response_text(raw))

    def _post_json(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        req = request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        self._contact_issue = None
        for attempt in range(1, 4):
            if self.cancelled():
                raise RuntimeError("Model request cancelled")
            try:
                result = self._send_json(req)
            except (error.HTTPError, error.URLError, TimeoutError, ConnectionError,
                    ssl.SSLError, http.client.IncompleteRead) as exc:
                cause = exc.reason if isinstance(exc, error.URLError) and not isinstance(exc, error.HTTPError) else exc
                retrying = attempt < 3 and not isinstance(cause, ssl.SSLCertVerificationError)
                detail = {
                    "message": "Server contact issue — Astra",
                    "state": "retrying" if retrying else "failed",
                    "attempt": attempt, "max_attempts": 3,
                    "cause_type": type(cause).__name__,
                    "http_status": exc.code if isinstance(exc, error.HTTPError) else None,
                }
                # SSL reason constants identify the failure without logging
                # arbitrary exception text, keys, or request content.
                reason = getattr(cause, "reason", None)
                if isinstance(reason, str) and re.fullmatch(r"[A-Z0-9_]{1,100}", reason):
                    detail["ssl_reason"] = reason
                self._contact_issue = detail
                self._contact_event("server_contact_issue", detail)
                if not retrying:
                    failure_label = f"HTTP {exc.code}" if isinstance(exc, error.HTTPError) else type(cause).__name__
                    raise RuntimeError(
                        f"Server contact issue — Astra request failed after {attempt} attempts ({failure_label})"
                    ) from None
                self._wait_before_retry(float(attempt))
                continue
            if self.cancelled():
                raise RuntimeError("Model request cancelled")
            if self._contact_issue:
                self._contact_event("server_contact_recovered", {"message": "Astra server contact restored"})
            self._contact_issue = None
            return result
        raise AssertionError("unreachable")

    def _contact_event(self, kind, detail):
        interaction = getattr(self, "_interaction", None)
        if callable(interaction):
            interaction(kind, detail["message"], **{k: v for k, v in detail.items() if k != "message"})

    def _wait_before_retry(self, delay):
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if self.cancelled():
                raise RuntimeError("Model request cancelled")
            time.sleep(min(.1, max(0., deadline - time.monotonic())))

    def _send_json(self, req):
        try:
            with request.urlopen(req, timeout=45.0) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code in {408, 500, 502, 503, 504}:
                exc.close()
                raise
            self._contact_issue = None
            detail = ""
            try:
                body = json.loads(exc.read(16384).decode("utf-8"))
                api_error = body.get("error", {})
                if isinstance(api_error, dict):
                    detail = " | ".join(str(api_error[k]) for k in ("code", "message") if api_error.get(k))
            except (ValueError, UnicodeError, AttributeError):
                pass
            finally:
                exc.close()
            detail = detail.replace(self._api_key, "[REDACTED]")
            detail = re.sub(r"sk-[A-Za-z0-9_*.-]+", "[REDACTED]", detail)
            detail = " ".join(detail.split())[:1000]
            raise RuntimeError(
                f"{self.provider_name} inference request failed: HTTP {exc.code}"
                + (f" — {detail}" if detail else "")
            ) from None
        except (error.URLError, TimeoutError, ConnectionError, ssl.SSLError,
                http.client.IncompleteRead):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"{self.provider_name} inference request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(decoded, dict):
            raise RuntimeError(f"{self.provider_name} returned a non-object response")
        return decoded


class OpenAIAdapter(ResponsesAdapter):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str, endpoint: str = "https://api.openai.com/v1/responses", *, camera_source: CameraFrameSource | None = None) -> None:
        super().__init__(api_key, model, endpoint, camera_source=camera_source)


class AstraAdapter(ResponsesAdapter):
    """Boundary for an Astra deployment exposing a Responses-style endpoint."""

    provider_name = "astra"

    def __init__(self, api_key: str, model: str, endpoint: str, *, camera_source: CameraFrameSource | None = None) -> None:
        if not endpoint.strip():
            raise ValueError("Astra endpoint is required")
        super().__init__(api_key, model, endpoint, camera_source=camera_source)


class ScriptedAdapter:
    """Deterministic adapter for recorded-observation tests."""

    provider_name = "scripted"

    def __init__(self, actions: Sequence[IKCommand], model: str = "recorded") -> None:
        self.model = model
        self._actions = list(actions)
        self._index = 0

    def public_config(self) -> dict[str, Any]:
        return {"provider": self.provider_name, "model": self.model, "api_key_configured": False}

    def infer(self, prompt: str, observation: Mapping[str, Any]) -> IKCommand:
        del prompt, observation
        if not self._actions:
            raise RuntimeError("Scripted adapter has no actions")
        action = self._actions[min(self._index, len(self._actions) - 1)]
        self._index += 1
        return action


class PolicyComplete(RuntimeError):
    """Signals that a deterministic policy reached its verified final state."""


class RaiseLowerSimulationAdapter:
    """Reviewed provider-free IK relative to the first settled observation."""

    provider_name = "local_raise_lower"

    def __init__(self, scene_path: str | None = None) -> None:
        self._ik = BimanualRelativeIK(scene_path)
        self._trajectory: RelativeTrajectory | None = None
        self._index = 0
        self._settle_tolerance_deg = math.degrees(0.02)
        self._apex_lifts = [0.0, 0.0]
        self._left_gripper: float | None = None
        self._right_gripper: float | None = None
        self._source: str | None = None
        self._batched_first_step: int | None = None
        self._batched_waypoints: list[dict[str, Any]] | None = None
        self.model = "observation_relative_raise_lower_both_arms_v6_atomic_10hz"

    def public_config(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider": self.provider_name,
            "model": self.model,
            "api_key_configured": False,
        }
        if self._trajectory is not None:
            result["trajectory"] = self._trajectory.diagnostics()
        return result

    def infer(self, prompt: str, observation: Mapping[str, Any]) -> IKCommand:
        del prompt
        source = self._require_context(observation)
        self._ensure_trajectory(observation, source)
        assert self._trajectory is not None
        if self._index > 0:
            self._require_reached(observation, self._index - 1)
            metrics = self._ik.pose_errors(
                self._vector(observation, "left_joints_deg"),
                self._vector(observation, "right_joints_deg"),
                self._trajectory,
            )
            for arm, (lift, _orientation_error) in enumerate(metrics):
                self._apex_lifts[arm] = max(self._apex_lifts[arm], lift)
        if self._index >= self._trajectory.length:
            self._require_final(observation)
            raise PolicyComplete("observation-relative raise/lower trajectory returned to its clipped start")
        left_target = self._trajectory.left_waypoints_deg[self._index]
        right_target = self._trajectory.right_waypoints_deg[self._index]
        self._index += 1
        return IKCommand(
            left=ArmIKCommand("joints", left_target),
            right=ArmIKCommand("joints", right_target),
            left_gripper=self._left_gripper,
            right_gripper=self._right_gripper,
        )

    def build_trajectory(
        self,
        prompt: str,
        observation: Mapping[str, Any],
        first_step_id: int,
    ) -> list[dict[str, Any]]:
        """Build one atomic, API-ready trajectory from the first leased observation."""
        del prompt
        if getattr(self, "_batch_complete", False):
            raise PolicyComplete("raise/lower policy completed")
        if isinstance(first_step_id, bool) or not isinstance(first_step_id, int) or first_step_id < 0:
            raise ValueError("first_step_id must be a non-negative integer")
        if self._batched_waypoints is not None:
            if first_step_id != self._batched_first_step:
                raise RuntimeError("Trajectory retry changed its first step")
            return [dict(item) for item in self._batched_waypoints]
        if self._index != 0:
            raise RuntimeError("Cannot batch a trajectory after waypoint execution began")
        source = self._require_context(observation)
        self._ensure_trajectory(observation, source)
        assert self._trajectory is not None
        if self._trajectory.length > MAX_WAYPOINTS_PER_PACKET or first_step_id + self._trajectory.length > MAX_COMMANDS:
            raise RuntimeError("Observation-relative trajectory exceeds session max_steps")
        waypoints: list[dict[str, Any]] = []
        for offset, (left, right) in enumerate(zip(
            self._trajectory.left_waypoints_deg,
            self._trajectory.right_waypoints_deg,
        )):
            values = tuple(left) + tuple(right)
            if len(left) != 6 or len(right) != 6 or not all(math.isfinite(value) for value in values):
                raise RuntimeError("Observation-relative trajectory contains malformed joints")
            waypoint: dict[str, Any] = {
                "step_id": first_step_id + offset,
                "left_joints_deg": list(left),
                "right_joints_deg": list(right),
            }
            if self._left_gripper is not None:
                waypoint["left_gripper"] = self._left_gripper
            if self._right_gripper is not None:
                waypoint["right_gripper"] = self._right_gripper
            waypoints.append(waypoint)
        self._batched_first_step = first_step_id
        self._batched_waypoints = waypoints
        return [dict(item) for item in waypoints]

    def validate_trajectory_final(self, observation: Mapping[str, Any]) -> None:
        """Validate the sole post-trajectory observation against the captured baseline."""
        source = self._require_context(observation)
        if self._trajectory is None or self._batched_waypoints is None:
            raise RuntimeError("No atomic trajectory is active")
        if source == "hardware" and observation.get("homed") is not True:
            raise RuntimeError("raise/lower hardware policy did not return to calibrated EEF Home")
        self._require_reached(observation, self._trajectory.length - 1)
        metrics = self._ik.pose_errors(
            self._vector(observation, "left_joints_deg"),
            self._vector(observation, "right_joints_deg"),
            self._trajectory,
        )
        if any(abs(lift) > 0.005 or orientation > 0.02 for lift, orientation in metrics):
            raise RuntimeError("raise/lower trajectory did not return to its observed start pose")

        self._batch_complete = True

    def _require_context(self, observation: Mapping[str, Any]) -> str:
        source = observation.get("source")
        if source not in {"simulation", "hardware"}:
            raise RuntimeError("raise/lower policy requires an explicit simulation or hardware source")
        if self._source is not None and source != self._source:
            raise RuntimeError("raise/lower policy source changed during execution")
        safety = observation.get("safety")
        if not isinstance(safety, Mapping) or safety.get("ok") is not True:
            raise RuntimeError("raise/lower policy requires an explicit safe station state")
        estop_values = [safety.get(name) for name in ("estop", "estop_active", "estop_engaged") if name in safety]
        if not estop_values or any(value is not False for value in estop_values):
            raise RuntimeError("raise/lower policy requires estop=false")
        active_contacts = safety.get("active_contacts")
        contact_count = safety.get("contact_count")
        if active_contacts not in (None, 0, []) or contact_count not in (None, 0):
            raise RuntimeError("raise/lower policy requires zero active contacts")
        if observation.get("settled") is not True:
            raise RuntimeError("raise/lower policy requires settled=true")
        return str(source)

    def _ensure_trajectory(self, observation: Mapping[str, Any], source: str) -> None:
        if self._trajectory is None:
            if source == "hardware" and observation.get("homed") is not True:
                raise RuntimeError("raise/lower hardware policy requires calibrated EEF Home")
            left = self._vector(observation, "left_joints_deg")
            right = self._vector(observation, "right_joints_deg")
            self._trajectory = self._ik.build(left, right)
            self._source = str(source)
            self._left_gripper = self._gripper(observation.get("left_gripper"), "left_gripper")
            self._right_gripper = self._gripper(observation.get("right_gripper"), "right_gripper")

    def _require_reached(self, observation: Mapping[str, Any], target_index: int) -> None:
        assert self._trajectory is not None
        targets = (
            self._trajectory.left_waypoints_deg[target_index],
            self._trajectory.right_waypoints_deg[target_index],
        )
        for name, target in zip(("left_joints_deg", "right_joints_deg"), targets):
            observed = self._vector(observation, name)
            if max(abs(actual - goal) for actual, goal in zip(observed, target)) > self._settle_tolerance_deg:
                raise RuntimeError(f"raise/lower policy requires settled {name}")

    def _require_final(self, observation: Mapping[str, Any]) -> None:
        assert self._trajectory is not None
        if any(abs(lift - self._trajectory.requested_apex_m) > 0.01 for lift in self._apex_lifts):
            raise RuntimeError("raise/lower policy did not observe the requested apex for both arms")
        metrics = self._ik.pose_errors(
            self._vector(observation, "left_joints_deg"),
            self._vector(observation, "right_joints_deg"),
            self._trajectory,
        )
        if any(abs(lift) > 0.005 or orientation > 0.02 for lift, orientation in metrics):
            raise RuntimeError("raise/lower policy did not return to its observed start pose")

    @staticmethod
    def _vector(payload: Mapping[str, Any], name: str) -> list[float]:
        raw = payload.get(name)
        if not isinstance(raw, list) or len(raw) != 6:
            raise ValueError(f"{name} must contain six explicit values")
        return [float(value) for value in raw]

    @staticmethod
    def _gripper(raw: Any, name: str) -> float | None:
        if raw is None:
            return None
        value = float(raw)
        if not 0.0 <= value <= 1.0:
            raise RuntimeError(f"Observed {name} must be between 0 and 1")
        return value


def _response_text(payload: Mapping[str, Any]) -> str:
    shortcut = payload.get("output_text")
    if isinstance(shortcut, str) and shortcut.strip():
        return shortcut
    texts: list[str] = []
    output = payload.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                continue
            for part in item["content"]:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    if not texts:
        raise RuntimeError("Provider response did not contain output text")
    return "\n".join(texts)


def _parse_action(text: str) -> IKCommand:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        action = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Provider output was not valid action JSON") from exc
    if not isinstance(action, dict):
        raise RuntimeError("Provider action must be a JSON object")
    left = _parse_arm(action.get("left"), "left")
    right = _parse_arm(action.get("right"), "right")
    return IKCommand(
        left=left,
        right=right,
        left_gripper=_parse_gripper(action.get("left_gripper"), "left_gripper"),
        right_gripper=_parse_gripper(action.get("right_gripper"), "right_gripper"),
    )


def _parse_arm(raw: Any, name: str) -> ArmIKCommand:
    if not isinstance(raw, dict):
        raise RuntimeError(f"Provider action requires explicit {name} arm output")
    mode, values = raw.get("mode"), raw.get("values")
    if mode not in {"joints", "pose"} or not isinstance(values, list) or len(values) != 6:
        raise RuntimeError(f"Provider {name} arm requires mode and 6 values")
    return ArmIKCommand(mode=mode, values=tuple(float(value) for value in values))


def _parse_gripper(raw: Any, name: str) -> float | None:
    if raw is None:
        return None
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise RuntimeError(f"Provider {name} must be between 0 and 1")
    return value


class RepeatingRaiseLowerAdapter(RaiseLowerSimulationAdapter):
    """Repeat a captured Home-relative path without accumulating pose drift."""

    def __init__(self, *, cycles: int = 3, scene_path: str | None = None):
        super().__init__(scene_path)
        if isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= 3:
            raise ValueError("cycles must be an integer from 1 to 3")
        self.cycles = cycles
        self.completed_cycles = 0
        self._phase = "up"
        self._packet_end = 0
        self.model = "repeating_raise_lower_20cm_10hz"

    def public_config(self):
        return {**super().public_config(), "repeat": True,
                "completed_cycles": self.completed_cycles, "cycles": self.cycles, "phase": self._phase}

    def build_trajectory(self, prompt, observation, first_step_id):
        if self.completed_cycles >= self.cycles:
            raise PolicyComplete("Requested up/down cycles completed")
        if self._batched_waypoints is None:
            super().build_trajectory(prompt, observation, first_step_id)
            if len(self._batched_waypoints) * self.cycles + first_step_id > MAX_COMMANDS:
                raise RuntimeError(f"Three-cycle path exceeds the {MAX_COMMANDS}-waypoint session budget")
        else:
            self._require_context(observation)
        midpoint = (len(self._batched_waypoints) + 1) // 2
        start = 0 if self._phase == "up" else midpoint
        self._packet_end = midpoint if self._phase == "up" else len(self._batched_waypoints)
        return [{**point, "step_id": first_step_id + i}
                for i, point in enumerate(self._batched_waypoints[start:self._packet_end])]

    def validate_trajectory_final(self, observation):
        self._require_context(observation)
        if self._phase == "up":
            self._validate_apex(observation)
        else:
            self._require_reached(observation, self._packet_end - 1)
        if self._phase == "down":
            super().validate_trajectory_final(observation)
            self.completed_cycles += 1
        self._phase = "down" if self._phase == "up" else "up"

    def infer(self, prompt, observation):
        raise RuntimeError("Repeated motion requires waypoint-packet transport")

    def _validate_apex(self, observation):
        index = self._packet_end - 1
        targets = (self._trajectory.left_waypoints_deg[index], self._trajectory.right_waypoints_deg[index])
        measured = tuple(self._vector(observation, key) for key in ("left_joints_deg", "right_joints_deg"))
        # Match the gateway's existing settled tracking envelope, then require
        # the task's 1 cm apex accuracy in full Cartesian space (not just Z).
        for arm, actual, target in zip(("left", "right"), measured, targets):
            residual = max(abs(a-b) for a,b in zip(actual, target))
            if residual > math.degrees(0.05):
                raise RuntimeError(f"{arm} apex tracking error {residual:.3f} deg exceeds gateway settled envelope")
        errors = self._ik.endpoint_errors(*measured, *targets)
        self._apex_endpoint_errors = errors
        for arm, (distance, rotation) in zip(("left", "right"), errors):
            if distance > 0.01 or rotation > 0.05:
                raise RuntimeError(f"{arm} apex outside task tolerance: position={distance:.4f} m orientation={rotation:.4f} rad")

from __future__ import annotations

import binascii
import struct
import threading
import zlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import mujoco
import numpy as np


def _scene_path() -> Path:
    return Path(__file__).resolve().parents[2] / "models" / "yam_bimanual" / "scene.xml"


def _joint_vector(observation: Mapping[str, Any]) -> np.ndarray:
    left = observation.get("left_joints_deg")
    right = observation.get("right_joints_deg")
    arms = (left, right)
    if any(
        not isinstance(arm, Sequence)
        or isinstance(arm, (str, bytes))
        or len(arm) != 6
        for arm in arms
    ):
        raise ValueError("twin requires explicit left and right six-joint observations")
    values = np.asarray([*left, *right], dtype=np.float64)
    if values.shape != (12,) or not np.all(np.isfinite(values)):
        raise ValueError("twin joint observation must contain 12 finite values")
    return np.deg2rad(values)


def _png(rgb: np.ndarray) -> bytes:
    image = np.ascontiguousarray(rgb, dtype=np.uint8)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("renderer must return RGB pixels")
    height, width, _ = image.shape

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        checksum = binascii.crc32(body) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + body + struct.pack(">I", checksum)

    rows = b"".join(b"\x00" + row.tobytes() for row in image)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 3))
        + chunk(b"IEND", b"")
    )


class YamBimanualViewer:
    """Read-only canonical MuJoCo twin driven by public station observations."""

    def __init__(
        self,
        observation: Callable[[], Mapping[str, Any] | None],
        scene_path: Path | None = None,
        frame_period_s: float = 0.08,
    ) -> None:
        self._observation = observation
        self._scene_path = scene_path or _scene_path()
        self._frame_period_s = frame_period_s
        self._frame: bytes | None = None
        self._error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            name="yam-read-only-twin",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def error(self) -> str | None:
        with self._lock:
            return self._error

    def _loop(self) -> None:
        renderer = None
        try:
            model = mujoco.MjModel.from_xml_path(str(self._scene_path))
            data = mujoco.MjData(model)
            renderer = mujoco.Renderer(model, height=480, width=720)
            camera = mujoco.MjvCamera()
            camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            camera.azimuth = 135
            camera.elevation = -22
            camera.distance = 2.0
            camera.lookat[:] = [0.25, 0.0, 0.30]
            while not self._stop.is_set():
                observation = self._observation()
                try:
                    if not isinstance(observation, Mapping):
                        raise ValueError("waiting for public bimanual observation")
                    data.qpos[:12] = _joint_vector(observation)
                    mujoco.mj_forward(model, data)
                    renderer.update_scene(data, camera=camera)
                    frame = _png(renderer.render())
                    with self._lock:
                        self._frame = frame
                        self._error = None
                except (TypeError, ValueError) as exc:
                    with self._lock:
                        self._frame = None
                        self._error = str(exc)
                self._stop.wait(self._frame_period_s)
        except Exception as exc:
            with self._lock:
                self._frame = None
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            if renderer is not None:
                renderer.close()

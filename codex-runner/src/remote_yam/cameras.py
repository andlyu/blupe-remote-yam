"""Bounded, fresh JPEG reads from the configured station camera relay."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.parser import BytesHeaderParser
import hashlib
import ssl
import time
from typing import Any, Mapping
from urllib import error, request
from urllib.parse import urlsplit


CAMERA_NAMES = ("left", "top", "right")
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024


class CameraUnavailable(RuntimeError):
    def __init__(self, name, cause, http_status=None, retryable=True):
        self.camera = name
        self.http_status = http_status
        self.retryable = retryable
        self.detail = f"HTTP {http_status}" if http_status is not None else cause
        super().__init__(f"{name} camera frame unavailable ({self.detail})")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Camera URL must use HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("Camera URL must not contain credentials")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def trusted_origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Camera origin must contain only scheme, host, and port")
    return origin(value)


@dataclass(frozen=True)
class CameraFrame:
    name: str
    jpeg: bytes
    received_at: float

    def summary(self) -> dict[str, Any]:
        return {
            "camera": self.name, "received_at": self.received_at,
            "bytes": len(self.jpeg), "sha256": hashlib.sha256(self.jpeg).hexdigest(),
        }


class CameraFrameSource:
    def __init__(self, camera_origin: str, timeout_s: float = 2.0, camera_names=CAMERA_NAMES):
        self.camera_names = tuple(camera_names)
        self.allowed_origin = trusted_origin(camera_origin)
        self.timeout_s = timeout_s

    def capture_for_policy(self, observation, *, cancelled=lambda: False, on_event=lambda *args: None):
        """Retry fresh sets, never combine an old successful view with a later one."""
        for attempt in range(1, 4):
            if cancelled():
                raise RuntimeError("Camera capture cancelled")
            try:
                frames = self.capture(observation)
            except CameraUnavailable as exc:
                if cancelled():
                    raise RuntimeError("Camera capture cancelled") from None
                retrying = exc.retryable and attempt < 3
                detail = {"camera": exc.camera, "attempt": attempt, "max_attempts": 3,
                          "http_status": exc.http_status, "cause": exc.detail,
                          "state": "retrying" if retrying else "failed"}
                message = (f"Waiting for next {exc.camera} camera frame ({attempt}/3; {exc.detail})"
                           if retrying else f"Camera failure: {exc.camera} camera frame unavailable after {attempt} attempt(s) ({exc.detail})")
                on_event("camera_retry" if retrying else "camera_failure", message, detail)
                if not retrying:
                    raise RuntimeError(message) from None
                deadline = time.monotonic() + .25
                while time.monotonic() < deadline:
                    if cancelled():
                        raise RuntimeError("Camera capture cancelled")
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
                continue
            if cancelled():
                raise RuntimeError("Camera capture cancelled")
            if attempt > 1:
                on_event("camera_recovered", "Camera feeds recovered; continuing with fresh frames",
                         {"state": "recovered", "attempt": attempt, "max_attempts": 3})
            return frames
        raise AssertionError("unreachable")

    def fetch(self, name: str, url: str) -> CameraFrame:
        """Fetch one named frame with the same origin checks as model capture."""
        parsed = urlsplit(url)
        if (name not in self.camera_names or origin(url) != self.allowed_origin
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.startswith("/")):
            raise ValueError("Camera URL is outside the configured relay")
        return self._capture_one(name, url)

    def capture(self, observation: Mapping[str, Any]) -> list[CameraFrame]:
        images = observation.get("images")
        if not isinstance(images, Mapping):
            raise RuntimeError("Station camera metadata is missing")
        urls = []
        # Validate every expected view before opening any connection.
        for name in self.camera_names:
            item = images.get(name)
            url = item.get("url") if isinstance(item, Mapping) else None
            if not isinstance(url, str):
                raise RuntimeError(f"{name} camera URL is missing")
            parsed = urlsplit(url)
            if origin(url) != self.allowed_origin or parsed.query or parsed.fragment or not parsed.path.startswith("/"):
                raise RuntimeError(f"{name} camera URL is outside the configured relay")
            urls.append((name, url))
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self._capture_one, name, url) for name, url in urls]
            return [future.result() for future in futures]

    def _capture_one(self, name: str, url: str) -> CameraFrame:
        try:
            opener = request.build_opener(NoRedirect)
            req = request.Request(url, headers={"Accept": "multipart/x-mixed-replace, image/jpeg", "Cache-Control": "no-cache"})
            with opener.open(req, timeout=self.timeout_s) as response:
                deadline = time.monotonic() + self.timeout_s
                content_type = response.headers.get_content_type()
                if content_type == "multipart/x-mixed-replace":
                    boundary = response.headers.get_param("boundary")
                    if not isinstance(boundary, str) or not 1 <= len(boundary) <= 200:
                        raise ValueError("MJPEG boundary is missing")
                    marker = b"--" + boundary.encode("ascii")
                    if self._line(response, deadline).strip() != marker:
                        raise ValueError("MJPEG frame boundary is invalid")
                    header = bytearray()
                    while True:
                        line = self._line(response, deadline)
                        if line in (b"\r\n", b"\n"):
                            break
                        header.extend(line)
                        if len(header) > MAX_HEADER_BYTES:
                            raise ValueError("MJPEG frame headers are too large")
                    headers = BytesHeaderParser().parsebytes(bytes(header))
                    if headers.get_content_type() != "image/jpeg":
                        raise ValueError("Camera frame must be JPEG")
                elif content_type == "image/jpeg":
                    headers = response.headers
                else:
                    raise ValueError("Camera response must be JPEG or MJPEG")
                lengths = headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not str(lengths[0]).isdigit():
                    raise ValueError("Camera frame length is missing or invalid")
                length = int(lengths[0])
                if not 4 <= length <= MAX_FRAME_BYTES:
                    raise ValueError("Camera frame exceeds the size limit")
                frame = bytearray()
                while len(frame) < length:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Camera frame deadline exceeded")
                    chunk = response.read1(min(65536, length - len(frame)))
                    if not chunk:
                        raise ValueError("Camera frame is truncated")
                    frame.extend(chunk)
                if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
                    raise ValueError("Camera frame is not a complete JPEG")
                return CameraFrame(name, bytes(frame), time.time())
        except Exception as exc:
            http_status = exc.code if isinstance(exc, error.HTTPError) else None
            if isinstance(exc, error.HTTPError):
                exc.close()
            # Do not expose response bodies, arbitrary URLs or network headers.
            cause = exc.reason if isinstance(exc, error.URLError) else exc
            retryable = (http_status is None or http_status in {408, 429, 500, 502, 503, 504})
            retryable = retryable and not isinstance(cause, ssl.SSLCertVerificationError)
            raise CameraUnavailable(name, type(exc).__name__, http_status, retryable) from None

    @staticmethod
    def _line(response, deadline: float) -> bytes:
        if time.monotonic() > deadline:
            raise TimeoutError("Camera header deadline exceeded")
        line = response.readline(MAX_HEADER_BYTES + 1)
        if not line or len(line) > MAX_HEADER_BYTES or not line.endswith(b"\n"):
            raise ValueError("Camera header is missing or too large")
        return line

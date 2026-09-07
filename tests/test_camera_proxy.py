from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import threading
import unittest
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("remote_yam_runner_app", ROOT / "run.py")
assert SPEC and SPEC.loader
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class CameraSource(BaseHTTPRequestHandler):
    content_type = "multipart/x-mixed-replace; boundary=frame"
    payload = b"--frame\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n--frame--\r\n"

    def do_GET(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class FakeController:
    def __init__(self, camera_url: str) -> None:
        self.camera_url = camera_url
        self.observation = {"images": {"left": {"url": camera_url}}}

    def status(self) -> dict[str, object]:
        return {"last_observation": self.observation}

    def monitor_camera_url(self, name: str) -> str | None:
        image = self.observation["images"].get(name)
        return image.get("url") if image else None


class FakeCredentials:
    def public_status(self) -> dict[str, bool]:
        return {}


class CameraProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ThreadingHTTPServer(("127.0.0.1", 0), CameraSource)
        self.source_thread = threading.Thread(target=self.source.serve_forever, daemon=True)
        self.source_thread.start()
        source_origin = f"http://127.0.0.1:{self.source.server_port}"
        handler = APP.build_handler(
            FakeController(source_origin + "/4"), FakeCredentials(), "csrf", "local_raise_lower", False, source_origin
        )
        self.runner = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.runner_thread = threading.Thread(target=self.runner.serve_forever, daemon=True)
        self.runner_thread.start()
        self.base = f"http://127.0.0.1:{self.runner.server_port}"

    def tearDown(self) -> None:
        self.runner.shutdown()
        self.runner.server_close()
        self.source.shutdown()
        self.source.server_close()

    def test_streams_only_named_latest_station_camera(self) -> None:
        with request.urlopen(self.base + "/api/status", timeout=2) as status_response:
            self.assertIn(b'"/api/monitor/cameras/left"', status_response.read())
        with request.urlopen(self.base + "/api/monitor/cameras/left", timeout=2) as response:
            self.assertEqual(response.status, HTTPStatus.OK)
            self.assertTrue(response.headers["Content-Type"].startswith("multipart/x-mixed-replace"))
            self.assertEqual(response.read(), CameraSource.payload)

    def test_proxies_api_jpeg_and_rejects_invalid_jpeg(self) -> None:
        original_type, original_payload = CameraSource.content_type, CameraSource.payload
        try:
            CameraSource.content_type = "image/jpeg"
            CameraSource.payload = b"\xff\xd8frame\xff\xd9"
            with request.urlopen(self.base + "/api/monitor/cameras/left", timeout=2) as response:
                self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                self.assertEqual(response.read(), CameraSource.payload)
            CameraSource.payload = b"invalid"
            with self.assertRaises(error.HTTPError) as raised:
                request.urlopen(self.base + "/api/monitor/cameras/left", timeout=2)
            self.assertEqual(raised.exception.code, HTTPStatus.BAD_GATEWAY)
            raised.exception.close()
        finally:
            CameraSource.content_type, CameraSource.payload = original_type, original_payload

    def test_rejects_caller_parameters_unknown_names_and_wrong_origin(self) -> None:
        for path in (
            "/api/monitor/cameras/left?url=http://example.com",
            "/api/monitor/cameras/front",
        ):
            with self.assertRaises(error.HTTPError) as raised:
                request.urlopen(self.base + path, timeout=2)
            self.assertEqual(raised.exception.code, HTTPStatus.NOT_FOUND)
            raised.exception.close()

        handler = APP.build_handler(
            FakeController("http://127.0.0.1:1/4"), FakeCredentials(), "csrf", "local_raise_lower", False,
            f"http://127.0.0.1:{self.source.server_port}",
        )
        blocked = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=blocked.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(error.HTTPError) as raised:
                request.urlopen(f"http://127.0.0.1:{blocked.server_port}/api/monitor/cameras/left", timeout=2)
            self.assertEqual(raised.exception.code, HTTPStatus.BAD_GATEWAY)
            self.assertEqual(raised.exception.read(), b"NO SIGNAL\n")
            raised.exception.close()
        finally:
            blocked.shutdown()
            blocked.server_close()


if __name__ == "__main__":
    unittest.main()

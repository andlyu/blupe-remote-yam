import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import run


class _Controller:
    pass


class _Credentials:
    def public_status(self):
        return {"openai": False, "astra": False}


class LocalControlTokenTests(unittest.TestCase):
    def test_stale_page_can_refresh_local_control_token(self):
        token = "current-local-token"
        handler = run.build_handler(
            _Controller(), _Credentials(), token, "local_raise_lower", False,
            "http://127.0.0.1:8089",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/api/config", timeout=2
            ) as response:
                config = json.load(response)
            self.assertEqual(token, config["local_control_token"])
            self.assertIn("response.status===403&&!retried", run.PAGE)
            self.assertIn("CSRF=c.local_control_token||CSRF", run.PAGE)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

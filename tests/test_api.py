import json
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


if "serial" not in sys.modules:
    serial_stub = types.ModuleType("serial")
    serial_stub.SerialException = OSError
    serial_stub.Serial = object
    sys.modules["serial"] = serial_stub

from gateway_manager.config import ConfigStore
from gateway_manager.manager import GatewayManager
from gateway_manager.server import ApiHandler


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = GatewayManager(ConfigStore(Path(self.temp.name) / "config.json"))
        handler = type("TestApiHandler", (ApiHandler,), {"manager": self.manager})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.manager.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_health_and_disabled_port_lifecycle(self):
        status, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["version"], "0.2.0")

        payload = {
            "name": "arduino-test",
            "mode": "raw",
            "device": "/dev/serial/by-path/test99",
            "baud": 115200,
            "tcp_port": 8899,
            "enabled": False,
        }
        status, created = self.request("/api/ports", "POST", payload)
        self.assertEqual(status, 201)
        port_id = created["port"]["id"]

        _, listed = self.request("/api/ports")
        self.assertEqual(listed["ports"][0]["runtime"]["status"], "disabled")

        status, deleted = self.request(f"/api/ports/{port_id}", "DELETE")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])

    def test_rejects_port_with_surrounding_whitespace(self):
        payload = {
            "name": "bad-port",
            "mode": "raw",
            "device": "/dev/serial/by-path/test99",
            "baud": 9600,
            "tcp_port": "8888 ",
            "enabled": False,
        }
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/ports", "POST", payload)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()

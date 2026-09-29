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
        self.assertEqual(health["version"], "0.8.0")

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
        self.assertTrue(listed["ports"][0]["runtime"]["metrics_available"])
        self.assertEqual(listed["ports"][0]["runtime"]["tx_bytes"], 0)

        status, updated = self.request(
            f"/api/ports/{port_id}", "PUT", {"enabled": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(updated["port"]["enabled"])
        self.assertEqual(updated["port"]["baud"], 115200)
        self.assertEqual(updated["port"]["tcp_port"], 8899)

        self.request(f"/api/ports/{port_id}", "PUT", {"enabled": False})

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

    def test_logs_and_diagnostics(self):
        self.manager.events.emit('error', 'lost topology', 'example', 'USB test')
        _, result = self.request('/api/logs?gateway_id=example&level=error&limit=20')
        self.assertEqual(result['logs'][0]['message'], 'lost topology')
        _, result = self.request('/api/logs?level=warning')
        self.assertEqual(result['logs'], [])
        _, result = self.request('/api/diagnostics')
        self.assertIn('/dev/serial/by-path', result['directories'])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/logs?limit=invalid')
        self.assertEqual(caught.exception.code, 400)

    def test_worker_logs_survive_disable(self):
        _, created = self.request('/api/ports', 'POST', {
            'name': 'test', 'mode': 'raw', 'device': '/dev/serial/by-path/test',
            'baud': 9600, 'tcp_port': 18992, 'enabled': True})
        port_id = created['port']['id']
        self.manager.workers[port_id].log('error', 'before disable')
        self.request(f'/api/ports/{port_id}', 'PUT', {'enabled': False})
        _, result = self.request(f'/api/ports/{port_id}/logs')
        self.assertTrue(any(e['message'] == 'before disable' for e in result['logs']))

    def test_reorders_ports_and_persists_order(self):
        payload = {
            "mode": "raw",
            "device": "/dev/serial/by-path/test99",
            "baud": 9600,
            "enabled": False,
        }
        _, first = self.request(
            "/api/ports", "POST", {**payload, "name": "first", "tcp_port": 8891}
        )
        _, second = self.request(
            "/api/ports", "POST", {**payload, "name": "second", "tcp_port": 8892}
        )
        ordered_ids = [second["port"]["id"], first["port"]["id"]]

        status, result = self.request(
            "/api/ports/reorder", "POST", {"ordered_ids": ordered_ids}
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])

        _, listed = self.request("/api/ports")
        self.assertEqual([port["id"] for port in listed["ports"]], ordered_ids)
        self.assertEqual(
            [port["id"] for port in self.manager.store.load()], ordered_ids
        )


if __name__ == "__main__":
    unittest.main()

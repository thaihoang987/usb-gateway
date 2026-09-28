import json
import tempfile
import unittest
from pathlib import Path

from gateway_manager.config import ConfigError, ConfigStore, normalize_port, validate_ports


def port_payload(**overrides):
    payload = {
        "name": "arduino-one",
        "mode": "raw",
        "device": "/dev/serial/by-path/usb-arduino-one",
        "baud": 115200,
        "tcp_port": 8890,
    }
    payload.update(overrides)
    return payload


class NormalizePortTests(unittest.TestCase):
    def test_applies_safe_uart_defaults(self):
        port = normalize_port(port_payload())
        self.assertEqual(port["data_bits"], 8)
        self.assertEqual(port["parity"], "N")
        self.assertEqual(port["stop_bits"], 1)
        self.assertFalse(port["dtr"])
        self.assertFalse(port["rts"])

    def test_rejects_invalid_tcp_port(self):
        with self.assertRaisesRegex(ConfigError, "tcp_port"):
            normalize_port(port_payload(tcp_port="8888 "))

    def test_rejects_non_device_path(self):
        with self.assertRaisesRegex(ConfigError, "/dev"):
            normalize_port(port_payload(device="ttyUSB0"))

    def test_rejects_duplicate_enabled_tcp_port(self):
        first = normalize_port(port_payload(), "one")
        second = normalize_port(
            port_payload(name="relay", device="/dev/serial/by-path/test9"), "two"
        )
        with self.assertRaisesRegex(ConfigError, "TCP port 8890"):
            validate_ports([first, second])

    def test_allows_duplicate_tcp_port_when_second_is_disabled(self):
        first = normalize_port(port_payload(), "one")
        second = normalize_port(
            port_payload(
                name="relay", device="/dev/serial/by-path/test9", enabled=False
            ),
            "two",
        )
        validate_ports([first, second])


class ConfigStoreTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            expected = [normalize_port(port_payload(), "raw-one")]
            store.save(expected)
            self.assertEqual(store.load(), expected)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], 1)

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(ConfigStore(Path(directory) / "missing.json").load(), [])


if __name__ == "__main__":
    unittest.main()

import sys
import types
import unittest


if "serial" not in sys.modules:
    serial_stub = types.ModuleType("serial")
    serial_stub.SerialException = OSError
    serial_stub.Serial = object
    sys.modules["serial"] = serial_stub

from gateway_manager.config import normalize_port
from gateway_manager import workers


class WorkerTests(unittest.TestCase):
    def test_mbusd_command_contains_gateway_settings(self):
        config = normalize_port(
            {
                "name": "relay",
                "mode": "modbus",
                "device": "/dev/serial/by-id/relay",
                "baud": 9600,
                "tcp_port": 8891,
                "parity": "E",
                "stop_bits": 1,
            },
            "relay",
        )
        command = workers.MbusdWorker(config)._command()
        self.assertEqual(command[0], "/usr/local/bin/mbusd")
        self.assertIn("8E1", command)
        self.assertEqual(command[command.index("-P") + 1], "8891")
        self.assertEqual(command[command.index("-p") + 1], config["device"])

    def test_raw_worker_sets_control_lines_before_open(self):
        events = []

        class FakeSerial:
            def __setattr__(self, name, value):
                if name in {"dtr", "rts"}:
                    events.append((name, value))
                object.__setattr__(self, name, value)

            def open(self):
                events.append(("open", True))

        original = workers.serial.Serial
        workers.serial.Serial = FakeSerial
        try:
            config = normalize_port(
                {
                    "name": "arduino",
                    "mode": "raw",
                    "device": "/dev/serial/by-id/arduino",
                    "baud": 115200,
                    "tcp_port": 8890,
                },
                "arduino",
            )
            workers.RawSerialWorker(config)._open_serial()
        finally:
            workers.serial.Serial = original

        self.assertLess(events.index(("dtr", False)), events.index(("open", True)))
        self.assertLess(events.index(("rts", False)), events.index(("open", True)))


if __name__ == "__main__":
    unittest.main()

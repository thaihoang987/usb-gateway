import socket
import sys
import threading
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
                "device": "/dev/serial/by-path/relay",
                "baud": 9600,
                "tcp_port": 8891,
                "parity": "E",
                "stop_bits": 1,
            },
            "relay",
        )
        worker = workers.MbusdWorker(config)
        command = worker._command()
        self.assertEqual(command[0], "/usr/local/bin/mbusd")
        self.assertIn("8E1", command)
        self.assertEqual(command[command.index("-P") + 1], "8891")
        self.assertEqual(command[command.index("-p") + 1], config["device"])
        internal = worker._command("127.0.0.1", 19001)
        self.assertEqual(internal[internal.index("-A") + 1], "127.0.0.1")
        self.assertEqual(internal[internal.index("-P") + 1], "19001")

    def test_reanchors_to_new_tty_after_replug(self):
        config = normalize_port(
            {"name": "relay", "mode": "modbus", "device": "/dev/serial/by-path/relay", "tcp_port": 8891},
            "relay",
        )
        worker = workers.MbusdWorker(config)
        live = {"tty": "/dev/ttyUSB0"}
        worker._resolve_device = lambda: live["tty"]
        self.assertEqual(worker._device_ready(), "/dev/ttyUSB0")
        command = worker._command()
        self.assertEqual(command[command.index("-p") + 1], "/dev/ttyUSB0")
        self.assertFalse(worker._device_changed())
        live["tty"] = "/dev/ttyUSB3"
        self.assertTrue(worker._device_changed())
        self.assertEqual(worker._device_ready(), "/dev/ttyUSB3")
        live["tty"] = None
        self.assertTrue(worker._device_changed())
        self.assertIsNone(worker._device_ready())
        self.assertEqual(worker.status, "waiting")
        self.assertTrue(worker.snapshot()["metrics_available"])

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
                    "device": "/dev/serial/by-path/arduino",
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

    def test_raw_worker_tracks_transfer_totals(self):
        config = normalize_port(
            {
                "name": "meter",
                "mode": "raw",
                "device": "/dev/serial/by-path/meter",
                "baud": 9600,
                "tcp_port": 8893,
            },
            "meter",
        )
        worker = workers.RawSerialWorker(
            config, {"tx_bytes": 10, "tx_count": 1, "rx_bytes": 20, "rx_count": 2}
        )
        worker.record_tx(5)
        worker.record_rx(7)

        snapshot = worker.snapshot()
        self.assertTrue(snapshot["metrics_available"])
        self.assertEqual(snapshot["tx_bytes"], 15)
        self.assertEqual(snapshot["tx_count"], 2)
        self.assertEqual(snapshot["rx_bytes"], 27)
        self.assertEqual(snapshot["rx_count"], 3)

    def test_mbusd_proxy_forwards_and_tracks_clients(self):
        config = normalize_port(
            {
                "name": "rs485",
                "mode": "modbus",
                "device": "/dev/serial/by-path/rs485",
                "baud": 9600,
                "tcp_port": 8894,
            },
            "rs485",
        )
        worker = workers.MbusdWorker(config)
        backend_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_server.bind(("127.0.0.1", 0))
        backend_server.listen(1)
        worker.backend_port = backend_server.getsockname()[1]
        release_backend = threading.Event()
        backend_received = []

        def serve_backend():
            connection, _ = backend_server.accept()
            with connection:
                backend_received.append(connection.recv(4096))
                connection.sendall(b"response")
                release_backend.wait(2)

        backend_thread = threading.Thread(target=serve_backend)
        backend_thread.start()

        front_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        front_server.bind(("127.0.0.1", 0))
        front_server.listen(1)
        external = socket.create_connection(front_server.getsockname())
        proxy_client, _ = front_server.accept()
        proxy_thread = threading.Thread(
            target=worker._proxy_connection, args=(proxy_client, "test-client")
        )
        proxy_thread.start()

        try:
            external.sendall(b"request")
            self.assertEqual(external.recv(4096), b"response")
            snapshot = worker.snapshot()
            self.assertEqual(snapshot["client_count"], 1)
            self.assertEqual(snapshot["tx_bytes"], 7)
            self.assertEqual(snapshot["tx_count"], 1)
            self.assertEqual(snapshot["rx_bytes"], 8)
            self.assertEqual(snapshot["rx_count"], 1)
        finally:
            release_backend.set()
            external.close()
            front_server.close()
            backend_server.close()
            proxy_thread.join(2)
            backend_thread.join(2)

        self.assertEqual(backend_received, [b"request"])
        self.assertEqual(worker.snapshot()["client_count"], 0)


if __name__ == "__main__":
    unittest.main()

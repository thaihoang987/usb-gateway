from __future__ import annotations

import collections
import os
import select
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

import serial

from .hotplug import ensure_serial_node


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class GatewayWorker:
    def __init__(self, config: dict[str, Any], metrics: dict[str, int] | None = None):
        self.config = dict(config)
        metrics = metrics or {}
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "stopped"
        self.message = ""
        self.started_at: str | None = None
        self.restart_count = 0
        self.client_count = 0
        self.tx_bytes = metrics.get("tx_bytes", 0)
        self.tx_count = metrics.get("tx_count", 0)
        self.rx_bytes = metrics.get("rx_bytes", 0)
        self.rx_count = metrics.get("rx_count", 0)
        self.logs: collections.deque[dict[str, str]] = collections.deque(maxlen=500)
        self._state_lock = threading.Lock()

    def log(self, level: str, message: str) -> None:
        clean = message.rstrip()
        if not clean:
            return
        with self._state_lock:
            self.logs.append({"time": _now(), "level": level, "message": clean})

    def set_status(self, status: str, message: str = "") -> None:
        with self._state_lock:
            self.status = status
            self.message = message
            if status == "running" and self.started_at is None:
                self.started_at = _now()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run_guarded,
            name=f"gateway-{self.config['id']}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._interrupt()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.set_status("stopped", "Disabled")

    def restart(self) -> None:
        self.stop()
        self.started_at = None
        self.restart_count += 1
        self.start()

    def _interrupt(self) -> None:
        pass

    def _run_guarded(self) -> None:
        try:
            self.run()
        except Exception as exc:  # pragma: no cover - last-resort containment
            self.log("error", f"Unhandled worker error: {exc}")
            self.set_status("error", str(exc))

    def _device_ready(self) -> bool:
        device = self.config["device"]
        try:
            was_present = os.path.exists(device)
            ready = ensure_serial_node(device)
            if ready:
                if not was_present:
                    self.log("info", f"Restored serial device for topology {device}")
                return True
            message = f"Waiting for USB topology {device}"
        except (OSError, ValueError) as exc:
            message = f"USB topology unavailable: {device}: {exc}"
        if self.message != message:
            self.log("warning", message)
        self.set_status("waiting", message)
        return False

    def run(self) -> None:
        raise NotImplementedError

    def record_tx(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._state_lock:
            self.tx_bytes += byte_count
            self.tx_count += 1

    def record_rx(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._state_lock:
            self.rx_bytes += byte_count
            self.rx_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status": self.status,
                "message": self.message,
                "started_at": self.started_at,
                "restart_count": self.restart_count,
                "client_count": self.client_count,
                "metrics_available": True,
                "tx_bytes": self.tx_bytes,
                "tx_count": self.tx_count,
                "rx_bytes": self.rx_bytes,
                "rx_count": self.rx_count,
            }

    def get_logs(self, limit: int = 200) -> list[dict[str, str]]:
        with self._state_lock:
            return list(self.logs)[-limit:]


class MbusdWorker(GatewayWorker):
    def __init__(self, config: dict[str, Any], binary: str = "/usr/local/bin/mbusd", metrics: dict[str, int] | None = None):
        super().__init__(config, metrics)
        self.binary = binary
        self.process: subprocess.Popen[str] | None = None
        self.server: socket.socket | None = None
        self.backend_port: int | None = None
        self.proxy_connections: set[tuple[socket.socket, socket.socket]] = set()
        self.proxy_lock = threading.Lock()

    def _command(self, listen_address: str | None = None, tcp_port: int | None = None) -> list[str]:
        cfg = self.config
        serial_mode = f"{cfg['data_bits']}{cfg['parity']}{cfg['stop_bits']}"
        return [
            self.binary,
            "-d",
            "-L",
            "-",
            "-v",
            str(cfg["log_level"]),
            "-p",
            cfg["device"],
            "-s",
            str(cfg["baud"]),
            "-m",
            serial_mode,
            "-A",
            listen_address or "0.0.0.0",
            "-P",
            str(tcp_port or cfg["tcp_port"]),
            "-C",
            str(cfg["max_connections"]),
            "-N",
            str(cfg["retries"]),
            "-R",
            str(cfg["pause_ms"]),
            "-W",
            str(cfg["wait_ms"]),
            "-T",
            str(cfg["timeout_s"]),
        ]

    def _interrupt(self) -> None:
        server = self.server
        if server:
            try:
                server.close()
            except OSError:
                pass
        self._close_proxy_connections()
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    @staticmethod
    def _allocate_backend_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _wait_for_backend(self, process: subprocess.Popen[str], timeout: float = 3) -> None:
        assert self.backend_port is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if process.poll() is not None:
                raise OSError(f"mbusd exited with code {process.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", self.backend_port), timeout=0.2):
                    return
            except OSError:
                self.stop_event.wait(0.05)
        raise OSError("mbusd internal listener did not become ready")

    def _close_proxy_connections(self) -> None:
        with self.proxy_lock:
            connections = list(self.proxy_connections)
            self.proxy_connections.clear()
            self.client_count = 0
        for pair in connections:
            for connection in pair:
                try:
                    connection.close()
                except OSError:
                    pass

    def _proxy_connection(self, client: socket.socket, peer: str) -> None:
        backend: socket.socket | None = None
        registered = False
        try:
            assert self.backend_port is not None
            backend = socket.create_connection(("127.0.0.1", self.backend_port), timeout=2)
            client.settimeout(None)
            backend.settimeout(None)
            with self.proxy_lock:
                self.proxy_connections.add((client, backend))
                self.client_count = len(self.proxy_connections)
                registered = True
            self.log("info", f"Connected {peer}")

            closed = False
            while not self.stop_event.is_set() and not closed:
                readable, _, _ = select.select([client, backend], [], [], 0.3)
                for source in readable:
                    data = source.recv(4096)
                    if not data:
                        closed = True
                        break
                    destination = backend if source is client else client
                    if source is client:
                        self.record_tx(len(data))
                    else:
                        self.record_rx(len(data))
                    destination.sendall(data)
        except OSError as exc:
            if not self.stop_event.is_set():
                self.log("warning", f"Proxy connection {peer} closed: {exc}")
        finally:
            if registered and backend is not None:
                with self.proxy_lock:
                    self.proxy_connections.discard((client, backend))
                    self.client_count = len(self.proxy_connections)
            for connection in (client, backend):
                if connection:
                    try:
                        connection.close()
                    except OSError:
                        pass
            self.log("info", f"Disconnected {peer}")

    def _proxy_loop(self) -> None:
        assert self.server is not None
        while not self.stop_event.is_set():
            try:
                client, address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer = f"{address[0]}:{address[1]}"
            threading.Thread(
                target=self._proxy_connection, args=(client, peer), daemon=True
            ).start()

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.log("info", line)

    def run(self) -> None:
        while not self.stop_event.is_set():
            device = self.config["device"]
            if not self._device_ready():
                self.stop_event.wait(2)
                continue

            self.backend_port = self._allocate_backend_port()
            command = self._command("127.0.0.1", self.backend_port)
            self.log("info", "Starting: " + " ".join(command))
            try:
                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                self.set_status("error", str(exc))
                self.log("error", f"Cannot start mbusd: {exc}")
                self.stop_event.wait(3)
                continue

            output_thread = threading.Thread(
                target=self._read_output, args=(self.process,), daemon=True
            )
            output_thread.start()
            proxy_thread: threading.Thread | None = None
            try:
                self._wait_for_backend(self.process)
                self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server.bind(("0.0.0.0", self.config["tcp_port"]))
                self.server.listen(self.config["max_connections"])
                self.server.settimeout(0.5)
                proxy_thread = threading.Thread(target=self._proxy_loop, daemon=True)
                proxy_thread.start()
            except OSError as exc:
                self.log("error", f"Cannot start Modbus traffic proxy: {exc}")
                self.set_status("error", str(exc))
                self._interrupt()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
                output_thread.join(timeout=1)
                self.process = None
                self.server = None
                self.backend_port = None
                self.restart_count += 1
                self.stop_event.wait(3)
                continue
            self.set_status("running", f"TCP :{self.config['tcp_port']}")

            while not self.stop_event.wait(0.5):
                if self.process.poll() is not None:
                    break

            if self.stop_event.is_set() and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)

            return_code = self.process.poll()
            if self.server:
                try:
                    self.server.close()
                except OSError:
                    pass
                self.server = None
            self._close_proxy_connections()
            if proxy_thread:
                proxy_thread.join(timeout=1)
            output_thread.join(timeout=1)
            self.process = None
            self.backend_port = None
            if self.stop_event.is_set():
                break
            self.restart_count += 1
            self.set_status("error", f"mbusd exited with code {return_code}")
            self.log("error", f"mbusd exited with code {return_code}; retrying")
            self.stop_event.wait(3)


class RawSerialWorker(GatewayWorker):
    def __init__(self, config: dict[str, Any], metrics: dict[str, int] | None = None):
        super().__init__(config, metrics)
        self.server: socket.socket | None = None
        self.serial_port: serial.Serial | None = None
        self.clients: dict[socket.socket, str] = {}
        self.clients_lock = threading.Lock()
        self.serial_write_lock = threading.Lock()

    def _interrupt(self) -> None:
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
        if self.serial_port:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass

    def _open_serial(self) -> serial.Serial:
        cfg = self.config
        port = serial.Serial()
        port.port = cfg["device"]
        port.baudrate = cfg["baud"]
        port.bytesize = cfg["data_bits"]
        port.parity = cfg["parity"]
        port.stopbits = cfg["stop_bits"]
        port.timeout = 0.2
        port.write_timeout = 2
        port.exclusive = True
        port.dtr = cfg["dtr"]
        port.rts = cfg["rts"]
        port.open()
        return port

    def _close_clients(self) -> None:
        with self.clients_lock:
            clients = list(self.clients)
            self.clients.clear()
            self.client_count = 0
        for client in clients:
            try:
                client.close()
            except OSError:
                pass

    def _serial_reader(self, failed: threading.Event, error: list[str]) -> None:
        assert self.serial_port is not None
        while not self.stop_event.is_set() and not failed.is_set():
            try:
                chunk = self.serial_port.read(4096)
            except (OSError, serial.SerialException) as exc:
                error.append(str(exc))
                failed.set()
                return
            if not chunk:
                continue
            self.record_rx(len(chunk))
            stale: list[socket.socket] = []
            with self.clients_lock:
                for client in self.clients:
                    try:
                        client.sendall(chunk)
                    except OSError:
                        stale.append(client)
                for client in stale:
                    peer = self.clients.pop(client, "client")
                    self.log("info", f"Disconnected {peer}")
                    try:
                        client.close()
                    except OSError:
                        pass
                self.client_count = len(self.clients)

    def _serve(self) -> None:
        self.serial_port = self._open_serial()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("0.0.0.0", self.config["tcp_port"]))
        self.server.listen(self.config["max_connections"])
        self.server.setblocking(False)

        failed = threading.Event()
        serial_error: list[str] = []
        reader = threading.Thread(
            target=self._serial_reader, args=(failed, serial_error), daemon=True
        )
        reader.start()
        self.set_status("running", f"TCP :{self.config['tcp_port']}")
        self.log(
            "info",
            f"Raw bridge listening on :{self.config['tcp_port']} -> {self.config['device']}",
        )

        try:
            while not self.stop_event.is_set() and not failed.is_set():
                with self.clients_lock:
                    sockets = [self.server, *self.clients.keys()]
                try:
                    readable, _, _ = select.select(sockets, [], [], 0.3)
                except (OSError, ValueError):
                    if self.stop_event.is_set():
                        break
                    raise

                for ready in readable:
                    if ready is self.server:
                        client, address = self.server.accept()
                        client.setblocking(False)
                        peer = f"{address[0]}:{address[1]}"
                        with self.clients_lock:
                            self.clients[client] = peer
                            self.client_count = len(self.clients)
                        self.log("info", f"Connected {peer}")
                        continue

                    try:
                        data = ready.recv(4096)
                    except OSError:
                        data = b""
                    if data:
                        assert self.serial_port is not None
                        with self.serial_write_lock:
                            written = self.serial_port.write(data)
                            self.serial_port.flush()
                        self.record_tx(written)
                    else:
                        with self.clients_lock:
                            peer = self.clients.pop(ready, "client")
                            self.client_count = len(self.clients)
                        self.log("info", f"Disconnected {peer}")
                        ready.close()
        finally:
            failed.set()
            reader.join(timeout=1)
            self._close_clients()
            if self.server:
                self.server.close()
                self.server = None
            if self.serial_port:
                self.serial_port.close()
                self.serial_port = None
        if serial_error:
            raise serial.SerialException(serial_error[-1])

    def run(self) -> None:
        while not self.stop_event.is_set():
            device = self.config["device"]
            if not self._device_ready():
                self.stop_event.wait(2)
                continue
            try:
                self._serve()
            except (OSError, serial.SerialException) as exc:
                if self.stop_event.is_set():
                    break
                self.restart_count += 1
                self.set_status("error", str(exc))
                self.log("error", f"Raw bridge stopped: {exc}; retrying")
                self.stop_event.wait(3)


def create_worker(config: dict[str, Any], metrics: dict[str, int] | None = None) -> GatewayWorker:
    if config["mode"] == "modbus":
        return MbusdWorker(config, metrics=metrics)
    return RawSerialWorker(config, metrics)

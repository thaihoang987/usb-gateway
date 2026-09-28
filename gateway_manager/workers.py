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


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class GatewayWorker:
    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "stopped"
        self.message = ""
        self.started_at: str | None = None
        self.restart_count = 0
        self.client_count = 0
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

    def run(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status": self.status,
                "message": self.message,
                "started_at": self.started_at,
                "restart_count": self.restart_count,
                "client_count": self.client_count,
            }

    def get_logs(self, limit: int = 200) -> list[dict[str, str]]:
        with self._state_lock:
            return list(self.logs)[-limit:]


class MbusdWorker(GatewayWorker):
    def __init__(self, config: dict[str, Any], binary: str = "/usr/local/bin/mbusd"):
        super().__init__(config)
        self.binary = binary
        self.process: subprocess.Popen[str] | None = None

    def _command(self) -> list[str]:
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
            "0.0.0.0",
            "-P",
            str(cfg["tcp_port"]),
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
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.log("info", line)

    def run(self) -> None:
        while not self.stop_event.is_set():
            device = self.config["device"]
            if not os.path.exists(device):
                self.set_status("waiting", f"Waiting for {device}")
                self.stop_event.wait(2)
                continue

            command = self._command()
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
            output_thread.join(timeout=1)
            self.process = None
            if self.stop_event.is_set():
                break
            self.restart_count += 1
            self.set_status("error", f"mbusd exited with code {return_code}")
            self.log("error", f"mbusd exited with code {return_code}; retrying")
            self.stop_event.wait(3)


class RawSerialWorker(GatewayWorker):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
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
                            self.serial_port.write(data)
                            self.serial_port.flush()
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
            if not os.path.exists(device):
                self.set_status("waiting", f"Waiting for {device}")
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


def create_worker(config: dict[str, Any]) -> GatewayWorker:
    if config["mode"] == "modbus":
        return MbusdWorker(config)
    return RawSerialWorker(config)

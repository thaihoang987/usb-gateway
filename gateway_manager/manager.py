from __future__ import annotations

import copy
import socket
import threading
import time
from typing import Any

from .config import ConfigError, ConfigStore, normalize_port, validate_ports
from .devices import scan_devices
from .workers import GatewayWorker, create_worker


class GatewayManager:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.lock = threading.RLock()
        self.ports = store.load()
        self.workers: dict[str, GatewayWorker] = {}

    def start(self) -> None:
        with self.lock:
            self._reconcile()

    def stop(self) -> None:
        with self.lock:
            workers = list(self.workers.values())
            self.workers.clear()
        for worker in workers:
            worker.stop()

    def _reconcile(self) -> None:
        desired = {port["id"]: port for port in self.ports if port["enabled"]}
        for port_id, worker in list(self.workers.items()):
            config = desired.get(port_id)
            if config is None or config != worker.config:
                worker.stop()
                del self.workers[port_id]
        for port_id, config in desired.items():
            if port_id not in self.workers:
                worker = create_worker(config)
                self.workers[port_id] = worker
                worker.start()

    def _save(self, ports: list[dict[str, Any]]) -> None:
        validate_ports(ports)
        self.store.save(ports)
        self.ports = ports
        self._reconcile()

    def list_ports(self) -> list[dict[str, Any]]:
        with self.lock:
            result: list[dict[str, Any]] = []
            for port in self.ports:
                item = copy.deepcopy(port)
                worker = self.workers.get(port["id"])
                item["runtime"] = worker.snapshot() if worker else {
                    "status": "disabled",
                    "message": "Disabled",
                    "started_at": None,
                    "restart_count": 0,
                    "client_count": 0,
                }
                result.append(item)
            return result

    def get_port(self, port_id: str) -> dict[str, Any]:
        for port in self.ports:
            if port["id"] == port_id:
                return port
        raise ConfigError("port not found")

    def add_port(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            port = normalize_port(payload)
            self._save([*self.ports, port])
            return copy.deepcopy(port)

    def update_port(self, port_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            current = self.get_port(port_id)
            merged = {**current, **payload, "id": port_id}
            updated = normalize_port(merged, port_id)
            ports = [updated if port["id"] == port_id else port for port in self.ports]
            self._save(ports)
            return copy.deepcopy(updated)

    def delete_port(self, port_id: str) -> None:
        with self.lock:
            self.get_port(port_id)
            self._save([port for port in self.ports if port["id"] != port_id])

    def restart_port(self, port_id: str) -> None:
        with self.lock:
            port = self.get_port(port_id)
            if not port["enabled"]:
                raise ConfigError("enable the port before restarting it")
            worker = self.workers.get(port_id)
            if not worker:
                self._reconcile()
                worker = self.workers.get(port_id)
            if not worker:
                raise ConfigError("worker is not available")
            worker.restart()

    def test_port(self, port_id: str) -> dict[str, Any]:
        with self.lock:
            port = copy.deepcopy(self.get_port(port_id))
        started = time.perf_counter()
        try:
            with socket.create_connection(("127.0.0.1", port["tcp_port"]), timeout=2):
                pass
        except OSError as exc:
            return {"ok": False, "error": str(exc), "tcp_port": port["tcp_port"]}
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tcp_port": port["tcp_port"],
        }

    def logs(self, port_id: str, limit: int = 200) -> list[dict[str, str]]:
        with self.lock:
            self.get_port(port_id)
            worker = self.workers.get(port_id)
            return worker.get_logs(limit) if worker else []

    def devices(self) -> list[dict[str, Any]]:
        with self.lock:
            claimed = {
                port["device"]: port["name"] for port in self.ports if port["enabled"]
            }
        devices = scan_devices()
        for device in devices:
            aliases = [device["path"], device["real_path"], *device["by_id"], *device["by_path"]]
            device["claimed_by"] = next(
                (name for path, name in claimed.items() if path in aliases), None
            )
        return devices

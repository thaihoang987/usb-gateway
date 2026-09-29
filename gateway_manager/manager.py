from __future__ import annotations

import copy
import json
import os
import socket
import threading
import time
from typing import Any

from .config import ConfigError, ConfigStore, normalize_port, validate_ports
from .devices import find_topology_tty, scan_devices
from .communication import exchange
from .presets import PresetStore
from .workers import GatewayWorker, create_worker
from .eventlog import EventLog
from .diagnostics import usb_diagnostics


class GatewayManager:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.presets = PresetStore(store.path.with_name("communication-presets.json"))
        self.lock = threading.RLock()
        self.ports = store.load()
        self.workers: dict[str, GatewayWorker] = {}
        self.metrics: dict[str, dict[str, int]] = {}
        self.events = EventLog(store.path.with_name('events.jsonl'))
        self._monitor_stop = threading.Event()
        self._monitor_thread = None

    def start(self) -> None:
        with self.lock:
            self._reconcile()
            if self._monitor_thread is None or not self._monitor_thread.is_alive():
                self._monitor_stop.clear()
                self.events.emit('info', 'Gateway manager started')
                self._monitor_thread = threading.Thread(target=self._monitor_usb, daemon=True)
                self._monitor_thread.start()

    def diagnostics(self):
        with self.lock:
            ports = copy.deepcopy(self.ports)
        result = usb_diagnostics(ports)
        result['log_persistence_error'] = self.events.persistence_error
        return result

    def _monitor_usb(self):
        previous = None
        while not self._monitor_stop.is_set():
            try:
                snapshot = self.diagnostics()
                summary = {
                    'bindings': {b['name']: 'ok' if b['present'] else b['reason'] for b in snapshot['bindings']},
                    'devices': sorted(f"{d.get('real_path')}@{d.get('usb_port', '?')}"
                                      f"{'' if d.get('by_path') else ' (no by-path)'}" for d in snapshot['devices']),
                    'stale_serial_mount': snapshot['stale_serial_mount'],
                }
                signature = json.dumps(summary, sort_keys=True, ensure_ascii=False)
                if signature != previous:
                    bad = snapshot['stale_serial_mount'] or any(not b['present'] for b in snapshot['bindings'])
                    self.events.emit('warning' if bad else 'info', 'USB inventory changed: ' + signature)
                    if snapshot['stale_serial_mount']:
                        self.events.emit('error', snapshot['hints'][0])
                    previous = signature
            except Exception as exc:
                message = str(exc)
                if previous != message:
                    self.events.emit('error', 'USB scan failed: ' + message)
                    previous = message
            self._monitor_stop.wait(4)

    def stop(self) -> None:
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        with self.lock:
            workers = list(self.workers.values())
            self.workers.clear()
        for worker in workers:
            worker.stop()

    def _reconcile(self) -> None:
        desired = {port["id"]: port for port in self.ports if port["enabled"]}
        for port_id, worker in list(self.workers.items()):
            config = desired.get(port_id)
            if config is None or {k: v for k, v in config.items() if k != "notes"} != {k: v for k, v in worker.config.items() if k != "notes"}:
                worker.stop()
                snapshot = worker.snapshot()
                self.metrics[port_id] = {
                    key: snapshot[key]
                    for key in ("tx_bytes", "tx_count", "rx_bytes", "rx_count")
                }
                del self.workers[port_id]
        for port_id, config in desired.items():
            if port_id not in self.workers:
                metrics = self.metrics.get(port_id)
                worker = create_worker(config, metrics) if metrics else create_worker(config)
                worker.event_sink = self.events.emit
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
                item["device_present"] = os.path.exists(port["device"]) or find_topology_tty(port["device"]) is not None
                worker = self.workers.get(port["id"])
                item["runtime"] = worker.snapshot() if worker else {
                    "status": "disabled",
                    "message": "Select a USB topology path to re-enable this gateway" if not port["device"].startswith("/dev/serial/by-path/") else "Disabled",
                    "started_at": None,
                    "restart_count": 0,
                    "client_count": 0,
                    "metrics_available": True,
                    "tx_bytes": self.metrics.get(port["id"], {}).get("tx_bytes", 0),
                    "tx_count": self.metrics.get(port["id"], {}).get("tx_count", 0),
                    "rx_bytes": self.metrics.get(port["id"], {}).get("rx_bytes", 0),
                    "rx_count": self.metrics.get(port["id"], {}).get("rx_count", 0),
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

    def reorder_ports(self, ordered_ids: list[str]) -> None:
        with self.lock:
            current_ids = [port["id"] for port in self.ports]
            if len(ordered_ids) != len(current_ids) or set(ordered_ids) != set(current_ids):
                raise ConfigError("ordered_ids must contain every gateway id exactly once")
            by_id = {port["id"]: port for port in self.ports}
            self._save([by_id[port_id] for port_id in ordered_ids])

    def delete_port(self, port_id: str) -> None:
        with self.lock:
            self.get_port(port_id)
            self._save([port for port in self.ports if port["id"] != port_id])
            self.metrics.pop(port_id, None)

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

    def communicate(self, port_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            port = copy.deepcopy(self.get_port(port_id))
            worker = self.workers.get(port_id)
            if not port["enabled"] or not worker or worker.snapshot()["status"] != "running":
                raise ConfigError("Gateway must be enabled and running")
        return exchange(port, payload)

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
            return self.events.read(limit, gateway_id=port_id)

    def devices(self) -> list[dict[str, Any]]:
        with self.lock:
            claimed = {
                port["device"]: port["name"] for port in self.ports if port["enabled"]
            }
            saved_ports = copy.deepcopy(self.ports)
        devices = scan_devices()
        for device in devices:
            aliases = [device["path"], device["real_path"], *device["by_id"], *device["by_path"]]
            device["claimed_by"] = next(
                (name for path, name in claimed.items() if path in aliases), None
            )
        known_paths = {alias for d in devices for alias in [d["path"], d["real_path"], *d["by_id"], *d["by_path"]]}
        for port in saved_ports:
            if port["device"] not in known_paths:
                devices.append({
                    "path": port["device"], "real_path": os.path.realpath(port["device"]),
                    "by_id": [], "by_path": [], "present": os.path.exists(port["device"]),
                    "claimed_by": port["name"] if port["enabled"] else None,
                })
                known_paths.add(port["device"])
        return devices

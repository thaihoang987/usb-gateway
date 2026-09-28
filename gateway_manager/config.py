from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any


VALID_MODES = {"modbus", "raw"}
VALID_PARITY = {"N", "E", "O"}
VALID_DATA_BITS = {5, 6, 7, 8}
VALID_STOP_BITS = {1, 2}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ConfigError(ValueError):
    pass


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, str) and value != value.strip():
        raise ConfigError(f"{field} must not contain surrounding whitespace")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ConfigError(f"{field} must be between {minimum} and {maximum}")
    return result


def normalize_port(payload: dict[str, Any], port_id: str | None = None, *, allow_legacy: bool = False) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not NAME_RE.fullmatch(name):
        raise ConfigError("name must use 1-64 letters, numbers, dot, dash or underscore")

    mode = str(payload.get("mode", "raw")).strip().lower()
    if mode not in VALID_MODES:
        raise ConfigError("mode must be modbus or raw")

    device = str(payload.get("device", "")).strip()
    if not device.startswith("/dev/") or "\x00" in device:
        raise ConfigError("device must be an absolute /dev path")

    topology_bound = (device.startswith("/dev/serial/by-path/")
                      and bool(device.removeprefix("/dev/serial/by-path/"))
                      and "/" not in device.removeprefix("/dev/serial/by-path/")
                      and device.rsplit("/", 1)[-1] not in {".", ".."})
    if not topology_bound and not allow_legacy:
        raise ConfigError("Select a USB topology path under /dev/serial/by-path/; by-id and tty paths are not allowed")

    parity = str(payload.get("parity", "N")).strip().upper()
    if parity not in VALID_PARITY:
        raise ConfigError("parity must be N, E or O")

    notes = payload.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 1000:
        raise ConfigError("notes must be text up to 1000 characters")

    return {
        "notes": notes,
        "id": port_id or str(payload.get("id") or uuid.uuid4().hex[:12]),
        "name": name,
        "enabled": _as_bool(payload.get("enabled"), True) if topology_bound else False,
        "mode": mode,
        "device": device,
        "baud": _as_int(payload.get("baud", 9600), "baud", 300, 4_000_000),
        "data_bits": _as_int(payload.get("data_bits", 8), "data_bits", 5, 8),
        "parity": parity,
        "stop_bits": _as_int(payload.get("stop_bits", 1), "stop_bits", 1, 2),
        "tcp_port": _as_int(payload.get("tcp_port"), "tcp_port", 1, 65535),
        "dtr": _as_bool(payload.get("dtr"), False),
        "rts": _as_bool(payload.get("rts"), False),
        "log_level": _as_int(payload.get("log_level", 2), "log_level", 0, 9),
        "max_connections": _as_int(
            payload.get("max_connections", 32), "max_connections", 1, 256
        ),
        "retries": _as_int(payload.get("retries", 3), "retries", 0, 20),
        "pause_ms": _as_int(payload.get("pause_ms", 100), "pause_ms", 1, 60_000),
        "wait_ms": _as_int(payload.get("wait_ms", 500), "wait_ms", 1, 60_000),
        "timeout_s": _as_int(payload.get("timeout_s", 60), "timeout_s", 0, 86_400),
    }


def validate_ports(ports: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    names: set[str] = set()
    tcp_ports: dict[int, str] = {}
    devices: dict[str, str] = {}

    for port in ports:
        if port["id"] in ids:
            raise ConfigError(f"duplicate id: {port['id']}")
        if port["name"] in names:
            raise ConfigError(f"duplicate name: {port['name']}")
        ids.add(port["id"])
        names.add(port["name"])

        if not port["enabled"]:
            continue
        tcp_port = port["tcp_port"]
        if tcp_port in tcp_ports:
            raise ConfigError(
                f"TCP port {tcp_port} is already used by {tcp_ports[tcp_port]}"
            )
        tcp_ports[tcp_port] = port["name"]

        resolved = os.path.realpath(port["device"])
        if resolved in devices:
            raise ConfigError(
                f"device {port['device']} is already used by {devices[resolved]}"
            )
        devices[resolved] = port["name"]


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read {self.path}: {exc}") from exc
        entries = raw.get("ports", []) if isinstance(raw, dict) else []
        if not isinstance(entries, list):
            raise ConfigError("ports must be a list")
        ports = [normalize_port(entry, str(entry.get("id") or "") or None, allow_legacy=True) for entry in entries]
        validate_ports(ports)
        return ports

    def save(self, ports: list[dict[str, Any]]) -> None:
        validate_ports(ports)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "ports": ports}, indent=2, ensure_ascii=False)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

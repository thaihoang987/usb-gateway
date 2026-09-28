from __future__ import annotations

import glob
import os
from typing import Any
from pathlib import Path


def _aliases(directory: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(directory, "*"))):
        if not os.path.islink(path) and not os.path.exists(path):
            continue
        result.setdefault(os.path.realpath(path), []).append(path)
    return result


def device_details(real_path: str) -> dict[str, str]:
    details: dict[str, str] = {"tty": os.path.basename(real_path)}
    node = Path("/sys/class/tty") / details["tty"] / "device"
    try:
        resolved = node.resolve(strict=True)
        for parent in [resolved, *resolved.parents]:
            if (parent / "idVendor").exists():
                details["usb_port"] = parent.name
                for key, filename in {
                    "manufacturer": "manufacturer", "product": "product",
                    "serial": "serial", "vid": "idVendor", "pid": "idProduct",
                }.items():
                    try:
                        details[key] = (parent / filename).read_text().strip()
                    except OSError:
                        pass
                break
    except (OSError, RuntimeError):
        pass
    return details


def scan_devices() -> list[dict[str, Any]]:
    by_id = _aliases("/dev/serial/by-id")
    by_path = _aliases("/dev/serial/by-path")
    real_paths = set(by_id) | set(by_path)
    real_paths.update(glob.glob("/dev/ttyUSB*"))
    real_paths.update(glob.glob("/dev/ttyACM*"))

    devices: list[dict[str, Any]] = []
    for real_path in sorted(os.path.realpath(path) for path in real_paths):
        id_aliases = by_id.get(real_path, [])
        path_aliases = by_path.get(real_path, [])
        preferred = (path_aliases or [real_path])[0]
        devices.append(
            {
                **device_details(real_path),
                "path": preferred,
                "topology_available": bool(path_aliases),
                "real_path": real_path,
                "by_id": id_aliases,
                "by_path": path_aliases,
                "present": os.path.exists(real_path),
            }
        )
    return devices

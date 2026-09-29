from __future__ import annotations

import glob
import os
import re
from typing import Any
from pathlib import Path

SYS_TTY = "/sys/class/tty"
_TTY = re.compile(r"tty(?:USB|ACM)[0-9]+")
_PCI = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
_IFACE = re.compile(r"[0-9]+-(?P<port>[0-9.]+):(?P<cfg>[0-9]+\.[0-9]+)")


def topology_name_from_sysfs(device_path: str, port_number: str | None) -> str | None:
    """Build the udev by-path name (path_id) from a resolved sysfs device path."""
    parts = device_path.rstrip("/").split("/")
    if port_number is not None:
        parts = parts[:-1]  # usb-serial port node -> USB interface
    iface = _IFACE.fullmatch(parts[-1]) if parts else None
    root = next((i for i, part in enumerate(parts) if re.fullmatch(r"usb[0-9]+", part)), None)
    if not iface or not root or not _PCI.fullmatch(parts[root - 1]):
        return None
    name = f"pci-{parts[root - 1]}-usb-0:{iface['port']}:{iface['cfg']}"
    return name + (f"-port{port_number}" if port_number is not None else "")


def topology_name(tty: str) -> str | None:
    """by-path name of a live tty, read from sysfs (independent of /dev/serial)."""
    try:
        device = Path(SYS_TTY, tty, "device").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    port_number = None
    if device.name.startswith("ttyUSB"):
        try:
            port_number = str(int((device / "port_number").read_text().strip()))
        except (OSError, ValueError):
            port_number = "0"
    return topology_name_from_sysfs(device.as_posix(), port_number)


def live_ttys() -> list[str]:
    try:
        return sorted(name for name in os.listdir(SYS_TTY) if _TTY.fullmatch(name))
    except OSError:
        return []


def find_topology_tty(by_path: str) -> str | None:
    """Current tty behind a saved by-path topology, or None when unplugged."""
    wanted = os.path.basename(by_path)
    return next((tty for tty in live_ttys() if topology_name(tty) == wanted), None)


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
    # Container /dev may be a stale snapshot; sysfs always lists live ttys.
    real_paths.update("/dev/" + tty for tty in live_ttys())

    devices: list[dict[str, Any]] = []
    for real_path in sorted(os.path.realpath(path) for path in real_paths):
        id_aliases = by_id.get(real_path, [])
        path_aliases = by_path.get(real_path, [])
        tty = os.path.basename(real_path)
        live = bool(_TTY.fullmatch(tty)) and os.path.exists(os.path.join(SYS_TTY, tty))
        if not path_aliases and live:
            name = topology_name(tty)
            if name:
                path_aliases = ["/dev/serial/by-path/" + name]
        preferred = (path_aliases or [real_path])[0]
        devices.append(
            {
                **device_details(real_path),
                "path": preferred,
                "topology_available": bool(path_aliases),
                "real_path": real_path,
                "by_id": id_aliases,
                "by_path": path_aliases,
                "present": os.path.exists(real_path) or live,
            }
        )
    return devices

"""Re-anchor a saved USB topology to its live tty after a USB re-plug.

The saved binding is the udev by-path name. It is resolved through sysfs
(always live in a privileged container), so a re-plug that renumbers the tty
or a stale /dev/serial bind mount does not break the gateway.
"""
import os
import re
import stat
from pathlib import Path

from .devices import SYS_TTY, find_topology_tty

_TTY = re.compile(r'tty(?:USB|ACM)[0-9]+')


def ensure_tty_node(name: str) -> bool:
    """Create /dev/<name> from sysfs major:minor if missing; never replace files."""
    if not _TTY.fullmatch(name):
        return False
    info = Path(SYS_TTY) / name
    hardware = (info / 'device').resolve()
    if not any((parent / 'idVendor').is_file() for parent in [hardware, *hardware.parents]):
        return False
    major, minor = map(int, (info / 'dev').read_text().strip().split(':'))
    expected_major = 188 if name.startswith('ttyUSB') else 166
    if major != expected_major or minor < 0:
        return False
    target = '/dev/' + name
    try:
        current = os.lstat(target)
    except FileNotFoundError:
        current = None
    if current is not None:
        return stat.S_ISCHR(current.st_mode) and current.st_rdev == os.makedev(major, minor)
    try:
        os.mknod(target, stat.S_IFCHR | 0o660, os.makedev(major, minor))
    except FileExistsError:
        current = os.lstat(target)
        return stat.S_ISCHR(current.st_mode) and current.st_rdev == os.makedev(major, minor)
    return True


def resolve_serial_device(device: str) -> str | None:
    """Live /dev/ttyX for a saved by-path topology, or None while unplugged."""
    if not device.startswith('/dev/serial/by-path/'):
        return None
    name = find_topology_tty(device)
    if name is None and os.path.exists(device):
        # sysfs layout not recognised: fall back to the host udev symlink.
        name = os.path.basename(os.path.realpath(device))
    if name and ensure_tty_node(name):
        return '/dev/' + name
    return None

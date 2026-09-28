"""Restore only configured USB serial nodes in Docker's private /dev."""
import os
import re
import stat
from pathlib import Path


def ensure_serial_node(device: str) -> bool:
    if not device.startswith('/dev/serial/by-path/'):
        return False
    # The host-owned topology symlink remains the source of the binding.
    target = os.path.realpath(device)
    if not re.fullmatch(r'/dev/tty(?:USB|ACM)[0-9]+', target):
        return False
    name = os.path.basename(target)
    info = Path('/sys/class/tty') / name
    hardware = (info / 'device').resolve()
    if not any((parent / 'idVendor').is_file() for parent in [hardware, *hardware.parents]):
        return False
    major, minor = map(int, (info / 'dev').read_text().strip().split(':'))
    expected_major = 188 if name.startswith('ttyUSB') else 166
    if major != expected_major or minor < 0:
        return False
    try:
        current = os.lstat(target)
    except FileNotFoundError:
        current = None
    if current is not None:
        # Never replace files, symlinks, or existing device nodes.
        return stat.S_ISCHR(current.st_mode) and current.st_rdev == os.makedev(major, minor)
    try:
        os.mknod(target, stat.S_IFCHR | 0o660, os.makedev(major, minor))
    except FileExistsError:
        current = os.lstat(target)
        return stat.S_ISCHR(current.st_mode) and current.st_rdev == os.makedev(major, minor)
    return True

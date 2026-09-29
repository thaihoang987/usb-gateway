"""Read-only USB evidence; never guess a replacement device binding."""
import os
from pathlib import Path
from .devices import find_topology_tty, scan_devices


def usb_diagnostics(ports):
    directories = {}
    for name in ('/dev/serial', '/dev/serial/by-path', '/dev/serial/by-id'):
        try:
            directories[name] = {'entries': sorted(os.listdir(name)), 'error': None}
        except OSError as exc:
            directories[name] = {'entries': [], 'error': str(exc)}
    bindings = []
    for port in ports:
        path = port['device']
        link = os.path.islink(path)
        target = os.path.realpath(path)
        live_tty = find_topology_tty(path)
        present = os.path.exists(path) or live_tty is not None
        reason = ('path_resolves' if os.path.exists(path) else 'resolved_via_sysfs' if live_tty
                  else 'symlink_target_missing' if link else 'topology_alias_missing')
        bindings.append(dict(gateway_id=port['id'], name=port['name'], path=path,
                             target=target, symlink=link, present=present, reason=reason,
                             live_tty='/dev/' + live_tty if live_tty else None))
    try:
        mounts = [line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                  if ' /dev/serial' in line]
    except OSError:
        mounts = []
    devices = scan_devices()
    hints = []
    # udev deletes /dev/serial when the last serial device disappears and
    # creates a new directory on re-plug. A bind mount of the old directory
    # keeps pointing at the deleted inode (link count 0) forever.
    try:
        stale_mount = os.stat('/dev/serial').st_nlink == 0
    except OSError:
        stale_mount = False
    if stale_mount:
        hints.append('/dev/serial in this container is a deleted directory (stale bind mount after a USB re-plug). '
                     'Gateways re-anchor through sysfs, but mapping host /dev to /dev instead of /dev/serial keeps aliases visible.')
    if any(b['reason'] == 'topology_alias_missing' for b in bindings):
        hints.append('Saved by-path aliases are missing. Compare host and container /dev/serial/by-path; check udev and the Docker serial bind mount before changing bindings.')
    if any(b['reason'] == 'symlink_target_missing' for b in bindings):
        hints.append('A topology symlink exists but its target is missing. Check live sysfs and container tty nodes; hotplug node recovery may have failed.')
    if any(d.get('usb_port') and not d.get('by_path') for d in devices):
        hints.append('USB metadata is visible in sysfs without by-path aliases. This alone does not prove a physical USB disconnect.')
    return dict(directories=directories, bindings=bindings, devices=devices,
                serial_mounts=mounts, stale_serial_mount=stale_mount, hints=hints)

import stat
import unittest
from unittest.mock import Mock, patch
from gateway_manager.devices import topology_name_from_sysfs
from gateway_manager.hotplug import ensure_tty_node, resolve_serial_device

PCI = '/sys/devices/pci0000:00/0000:00:14.0/usb1'


class TopologyNameTests(unittest.TestCase):
    def test_cdc_acm_interface(self):
        path = f'{PCI}/1-6/1-6.3/1-6.3.4/1-6.3.4:1.0'
        self.assertEqual(topology_name_from_sysfs(path, None), 'pci-0000:00:14.0-usb-0:6.3.4:1.0')

    def test_usb_serial_port(self):
        path = f'{PCI}/1-9/1-9.4/1-9.4.4/1-9.4.4.1/1-9.4.4.1:1.0/ttyUSB1'
        self.assertEqual(topology_name_from_sysfs(path, '0'), 'pci-0000:00:14.0-usb-0:9.4.4.1:1.0-port0')

    def test_rejects_non_usb(self):
        self.assertIsNone(topology_name_from_sysfs('/sys/devices/platform/serial8250/tty/ttyS0', None))


class ResolveTests(unittest.TestCase):
    def test_rejects_non_topology(self):
        self.assertIsNone(resolve_serial_device('/dev/ttyUSB1'))

    def test_reanchors_via_sysfs_when_alias_missing(self):
        with patch('gateway_manager.hotplug.find_topology_tty', return_value='ttyUSB3'), \
                patch('gateway_manager.hotplug.ensure_tty_node', return_value=True) as node:
            self.assertEqual(resolve_serial_device('/dev/serial/by-path/x'), '/dev/ttyUSB3')
            node.assert_called_once_with('ttyUSB3')

    def test_waits_while_unplugged(self):
        with patch('gateway_manager.hotplug.find_topology_tty', return_value=None), \
                patch('gateway_manager.hotplug.os.path.exists', return_value=False):
            self.assertIsNone(resolve_serial_device('/dev/serial/by-path/x'))


class NodeTests(unittest.TestCase):
    def check_node(self, major, existing, name='ttyUSB1'):
        info = Mock()
        hardware = Mock()
        hardware.parents = []
        hardware.__truediv__ = Mock(return_value=Mock(is_file=Mock(return_value=True)))
        info.__truediv__ = Mock(side_effect=lambda key: Mock(resolve=Mock(return_value=hardware)) if key == 'device' else Mock(read_text=Mock(return_value=f'{major}:1')))
        root = Mock()
        root.__truediv__ = Mock(return_value=info)
        with patch('gateway_manager.hotplug.Path', return_value=root), patch('gateway_manager.hotplug.os.lstat', side_effect=FileNotFoundError if existing is None else None, return_value=existing), patch('gateway_manager.hotplug.os.makedev', create=True, return_value=48129), patch('gateway_manager.hotplug.os.mknod', create=True) as create:
            return ensure_tty_node(name), create.call_count

    def test_creates_missing_serial_node(self):
        self.assertEqual(self.check_node(188, None), (True, 1))

    def test_rejects_non_serial_name(self):
        self.assertEqual(self.check_node(188, None, name='sda'), (False, 0))

    def test_rejects_unexpected_major(self):
        self.assertEqual(self.check_node(8, None), (False, 0))

    def test_never_overwrites_regular_file(self):
        self.assertEqual(self.check_node(188, Mock(st_mode=stat.S_IFREG)), (False, 0))

    def test_existing_correct_node_needs_no_change(self):
        self.assertEqual(self.check_node(188, Mock(st_mode=stat.S_IFCHR, st_rdev=48129)), (True, 0))

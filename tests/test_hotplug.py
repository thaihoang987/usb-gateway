import stat
import unittest
from unittest.mock import Mock, patch
from gateway_manager.hotplug import ensure_serial_node


class HotplugTests(unittest.TestCase):
    def test_rejects_non_topology(self):
        self.assertFalse(ensure_serial_node('/dev/ttyUSB1'))

    def test_rejects_non_serial_target(self):
        with patch('gateway_manager.hotplug.os.path.realpath', return_value='/dev/sda'):
            self.assertFalse(ensure_serial_node('/dev/serial/by-path/test'))

    def check_node(self, major, existing):
        info = Mock()
        hardware = Mock()
        hardware.parents = []
        hardware.__truediv__ = Mock(return_value=Mock(is_file=Mock(return_value=True)))
        info.__truediv__ = Mock(side_effect=lambda key: Mock(resolve=Mock(return_value=hardware)) if key == 'device' else Mock(read_text=Mock(return_value=f'{major}:1')))
        root = Mock()
        root.__truediv__ = Mock(return_value=info)
        with patch('gateway_manager.hotplug.Path', return_value=root), patch('gateway_manager.hotplug.os.path.realpath', return_value='/dev/ttyUSB1'), patch('gateway_manager.hotplug.os.lstat', side_effect=FileNotFoundError if existing is None else None, return_value=existing), patch('gateway_manager.hotplug.os.makedev', create=True, return_value=48129), patch('gateway_manager.hotplug.os.mknod', create=True) as create:
            result = ensure_serial_node('/dev/serial/by-path/test')
            return result, create.call_count

    def test_creates_missing_serial_node(self):
        self.assertEqual(self.check_node(188, None), (True, 1))

    def test_rejects_unexpected_major(self):
        self.assertEqual(self.check_node(8, None), (False, 0))

    def test_never_overwrites_regular_file(self):
        self.assertEqual(self.check_node(188, Mock(st_mode=stat.S_IFREG)), (False, 0))

    def test_existing_correct_node_needs_no_change(self):
        self.assertEqual(self.check_node(188, Mock(st_mode=stat.S_IFCHR, st_rdev=48129)), (True, 0))

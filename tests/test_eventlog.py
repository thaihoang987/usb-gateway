import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gateway_manager.eventlog import EventLog
from gateway_manager.diagnostics import usb_diagnostics


class EventLogTests(unittest.TestCase):
    def test_restart_filter_rotation_and_corrupt_tail(self):
        with tempfile.TemporaryDirectory() as directory, patch('builtins.print'):
            path = Path(directory) / 'events.jsonl'
            log = EventLog(path)
            log.emit('error', 'USB read failed', 'a', 'Arduino')
            with path.open('a') as stream:
                stream.write('broken\n' + ' ' * (2 * 1024 * 1024) + '\n')
            log.emit('info', 'Recovered', 'b', 'Relay')
            self.assertTrue(path.with_suffix('.jsonl.1').exists())
            restored = EventLog(path)
            self.assertEqual(len(restored.read()), 2)
            self.assertEqual(restored.read(gateway_id='a', level='error')[0]['message'], 'USB read failed')
            self.assertEqual(len(restored.read(0)), 1)

    def test_disk_error_keeps_memory_history(self):
        with tempfile.TemporaryDirectory() as directory, patch('builtins.print'):
            log = EventLog(Path(directory) / 'events.jsonl')
            with patch.object(Path, 'open', side_effect=PermissionError('read only')):
                log.emit('warning', 'USB missing')
            self.assertIn('read only', log.persistence_error)
            self.assertEqual(log.read()[0]['message'], 'USB missing')

    def test_missing_alias_distinct_from_missing_target(self):
        ports = [dict(id='a', name='Arduino', device='/dev/serial/by-path/a')]
        with patch('gateway_manager.diagnostics.scan_devices', return_value=[dict(usb_port='1-6.3.1', by_path=[])]), patch('gateway_manager.diagnostics.os.path.exists', return_value=False), patch('gateway_manager.diagnostics.os.path.islink', return_value=False):
            result = usb_diagnostics(ports)
            self.assertEqual(result['bindings'][0]['reason'], 'topology_alias_missing')
            self.assertTrue(any('sysfs' in hint for hint in result['hints']))
            with patch('gateway_manager.diagnostics.os.path.islink', return_value=True):
                self.assertEqual(usb_diagnostics(ports)['bindings'][0]['reason'], 'symlink_target_missing')

    def test_stale_serial_mount_detected(self):
        fake = type('S', (), {'st_nlink': 0})()
        with patch('gateway_manager.diagnostics.scan_devices', return_value=[]), patch('gateway_manager.diagnostics.os.stat', return_value=fake):
            result = usb_diagnostics([])
        self.assertTrue(result['stale_serial_mount'])
        self.assertIn('stale bind mount', result['hints'][0])

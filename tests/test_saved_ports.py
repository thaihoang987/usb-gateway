import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import test_api
from gateway_manager.config import ConfigStore, normalize_port, ConfigError
from gateway_manager.manager import GatewayManager

class Changes(unittest.TestCase):
    def test_notes_and_disconnection_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / 'config.json')
            manager = GatewayManager(store)
            p = manager.add_port(dict(name='relay', device='/dev/serial/by-path/test', tcp_port=8891, enabled=False, notes='Bơm tầng 1'))
            manager = GatewayManager(store)
            with patch('gateway_manager.manager.os.path.exists', return_value=False), patch('gateway_manager.manager.scan_devices', return_value=[]):
                row = manager.list_ports()[0]
                self.assertEqual(row['notes'], 'Bơm tầng 1')
                self.assertFalse(row['device_present'])
                self.assertEqual(manager.devices()[0]['path'], p['device'])
            with patch('gateway_manager.manager.os.path.exists', return_value=True):
                self.assertTrue(manager.list_ports()[0]['device_present'])

    def test_notes_only_edit_does_not_restart_worker(self):
        with tempfile.TemporaryDirectory() as tmp, patch('gateway_manager.manager.create_worker') as create:
            manager = GatewayManager(ConfigStore(Path(tmp) / 'config.json'))
            worker = Mock()
            create.side_effect = lambda config: (setattr(worker, 'config', dict(config)) or worker)
            p = manager.add_port(dict(name='relay', device='/dev/serial/by-path/test99', tcp_port=8891))
            manager.update_port(p['id'], {'notes': 'New purpose'})
            worker.stop.assert_not_called()
            self.assertEqual(create.call_count, 1)

    def test_notes_validation_and_legacy_config(self):
        payload = dict(name='relay', device='/dev/serial/by-path/test99', tcp_port=8891)
        self.assertEqual(normalize_port(payload)['notes'], '')
        with self.assertRaises(ConfigError):
            normalize_port({**payload, 'notes': 'x' * 1001})

if __name__ == '__main__':
    unittest.main()


class TopologyTests(unittest.TestCase):
    def test_rejects_identity_and_tty_bindings(self):
        for device in ['/dev/ttyUSB0', '/dev/serial/by-id/duplicate', '/dev/serial/by-path/../ttyUSB0']:
            with self.subTest(device=device), self.assertRaises(ConfigError):
                normalize_port(dict(name='relay', device=device, tcp_port=8891))

    def test_legacy_binding_is_kept_but_disabled(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'ports': [dict(name='relay', device='/dev/ttyUSB0', tcp_port=8891, enabled=True, notes='Pump')]}))
            row = ConfigStore(path).load()[0]
            self.assertFalse(row['enabled'])
            self.assertEqual(row['device'], '/dev/ttyUSB0')
            self.assertEqual(row['notes'], 'Pump')

    def test_by_path_is_preserved_even_if_absent(self):
        path = '/dev/serial/by-path/pci-0000:00:14.0-usb-0:9.4.3:1.0-port0'
        row = normalize_port(dict(name='relay', device=path, tcp_port=8891))
        self.assertEqual(row['device'], path)
        self.assertTrue(row['enabled'])


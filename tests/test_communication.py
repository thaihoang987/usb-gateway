import socket
import threading
import unittest
from gateway_manager.communication import build_request, exchange
from gateway_manager.config import ConfigError


class CommunicationTests(unittest.TestCase):
    def test_rtu_crc(self):
        frame = build_request({'mode': 'raw'}, {'action': 'modbus'})
        self.assertEqual(frame.hex(), '010300000001840a')

    def test_tcp_mbap(self):
        frame = build_request({'mode': 'modbus'}, {'action': 'modbus'})
        self.assertEqual(frame.hex(), '000100000006010300000001')

    def test_invalid_requests(self):
        for payload in [{'action': 'modbus', 'function': 6}, {'action': 'modbus', 'address': 65535, 'quantity': 2}, {'action': 'hex', 'data': 'xx'}]:
            with self.assertRaises(ConfigError):
                build_request({'mode': 'raw'}, payload)
        with self.assertRaises(ConfigError):
            build_request({'mode': 'modbus'}, {'action': 'listen'})

    def test_exchange_receives_bytes(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen()
            received = []
            def serve():
                with server.accept()[0] as client:
                    received.append(client.recv(4096))
                    client.sendall(b'answer')
            thread = threading.Thread(target=serve)
            thread.start()
            result = exchange({'mode': 'raw', 'tcp_port': server.getsockname()[1]}, {'action': 'hex', 'data': '01 02', 'duration': 1})
            thread.join(2)
            self.assertEqual(received, [b'\x01\x02'])
            self.assertEqual(result['received_bytes'], 6)
            self.assertEqual(result['rx'][0]['text'], 'answer')

    def test_listen_does_not_send(self):
        self.assertEqual(build_request({'mode': 'raw'}, {'action': 'listen'}), b'')

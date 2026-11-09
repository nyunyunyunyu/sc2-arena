"""Exercise real relay sockets without starting SC2."""
import contextlib
import socket
import time
import unittest

from sc2.lan_transport import GameRelay


def free_ports():
    for _ in range(100):
        try:
            with contextlib.ExitStack() as stack:
                probe = stack.enter_context(socket.socket())
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
                if port > 65531:
                    continue
                for offset in range(1, 5):
                    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                        sock = stack.enter_context(socket.socket(socket.AF_INET, kind))
                        sock.bind(('0.0.0.0', port + offset))
                return port
        except OSError:
            pass
    raise RuntimeError('No available game ports')


def read_all(sock):
    chunks = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)


class GameRelayTests(unittest.TestCase):
    def bound(self, ip, port, kind=socket.SOCK_STREAM):
        sock = socket.socket(socket.AF_INET, kind)
        self.addCleanup(sock.close)
        sock.settimeout(3)
        sock.bind((ip, port))
        if kind == socket.SOCK_STREAM:
            sock.listen(1)
        return sock

    def relay(self, host=True):
        relay = GameRelay('127.0.0.2', '127.0.0.3', free_ports(), host)
        self.addCleanup(relay.close)
        return relay

    def test_udp_both_roles_routes_only_expected_peer_and_releases_ports(self):
        for host in (True, False):
            with self.subTest(host=host):
                relay = self.relay(host)
                native = self.bound('127.0.0.1', relay.own_game, socket.SOCK_DGRAM)
                remote = self.bound('127.0.0.3', relay.own_game, socket.SOCK_DGRAM)
                stranger = self.bound('127.0.0.4', relay.own_game, socket.SOCK_DGRAM)
                stranger.sendto(b'rejected', ('127.0.0.2', relay.peer_game))
                remote.sendto(b'from peer', ('127.0.0.2', relay.peer_game))
                self.assertEqual(native.recvfrom(100), (b'from peer', ('127.0.0.1', relay.peer_game)))
                native.sendto(b'from SC2', ('127.0.0.1', relay.peer_game))
                self.assertEqual(remote.recvfrom(100), (b'from SC2', ('127.0.0.2', relay.peer_game)))
                self.assertIsNone(relay.error)
                relay.close()
                self.assertTrue(all(not thread.is_alive() for thread in relay.threads))
                self.assertFalse(relay.sockets)
                self.bound('127.0.0.1', relay.peer_game, socket.SOCK_DGRAM)
                self.bound('127.0.0.2', relay.peer_game, socket.SOCK_DGRAM)
                self.bound('127.0.0.1', relay.peer_game + 1)
                self.bound('127.0.0.2', relay.own_game + 1)

    def test_tcp_both_directions_preserve_reply_after_half_close(self):
        relay = self.relay()
        for incoming in (False, True):
            with self.subTest(incoming=incoming):
                port = relay.own_game + 1 if incoming else relay.peer_game + 1
                target_ip = '127.0.0.1' if incoming else '127.0.0.3'
                client_ip = '127.0.0.3' if incoming else '127.0.0.1'
                relay_ip = '127.0.0.2' if incoming else '127.0.0.1'
                # Connect before the destination listens, as SC2 does while joining.
                client = socket.create_connection((relay_ip, port), timeout=3, source_address=(client_ip, 0))
                self.addCleanup(client.close)
                target = self.bound(target_ip, port)
                server, address = target.accept()
                self.addCleanup(server.close)
                server.settimeout(3)
                self.assertEqual(address[0], '127.0.0.1' if incoming else '127.0.0.2')
                payload = bytes(range(256)) * 128
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(read_all(server), payload)
                server.sendall(payload[::-1])
                server.shutdown(socket.SHUT_WR)
                self.assertEqual(read_all(client), payload[::-1])
        self.assertIsNone(relay.error)

    def test_udp_recovers_when_sc2_port_opens_after_first_packet(self):
        relay = self.relay()
        remote = self.bound('127.0.0.3', relay.own_game, socket.SOCK_DGRAM)
        remote.sendto(b'early', ('127.0.0.2', relay.peer_game))
        # Allow Windows to deliver ICMP port-unreachable before SC2 starts listening.
        time.sleep(.3)
        native = self.bound('127.0.0.1', relay.own_game, socket.SOCK_DGRAM)
        remote.sendto(b'retry', ('127.0.0.2', relay.peer_game))
        self.assertEqual(native.recvfrom(100)[0], b'retry')
        self.assertIsNone(relay.error)

    def test_tcp_rejects_other_peer(self):
        relay = self.relay()
        with socket.create_connection(('127.0.0.2', relay.own_game + 1), timeout=3,
                                      source_address=('127.0.0.4', 0)) as stranger:
            self.assertEqual(stranger.recv(1), b'')
        self.assertIsNone(relay.error)

    def test_failed_start_releases_already_bound_sockets(self):
        port = free_ports()
        self.bound('127.0.0.1', port + 4)
        with self.assertRaises(OSError):
            GameRelay('127.0.0.2', '127.0.0.3', port, True)
        self.bound('127.0.0.1', port + 3, socket.SOCK_DGRAM)
        self.bound('127.0.0.2', port + 3, socket.SOCK_DGRAM)


if __name__ == '__main__':
    unittest.main()

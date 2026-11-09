"""Forward SC2's native loopback game ports between two room participants."""
import select
import socket
import threading
import time


class GameRelay:
    def __init__(self, local_ip, peer_ip, port, host):
        self.local_ip, self.peer_ip = local_ip, peer_ip
        self.own_game = port + (1 if host else 3)
        self.peer_game = port + (3 if host else 1)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.sockets, self.threads = set(), []
        self.connections = threading.BoundedSemaphore(8)
        self.error = None
        try:
            local_udp = self._bind(('127.0.0.1', self.peer_game), socket.SOCK_DGRAM)
            network_udp = self._bind((local_ip, self.peer_game), socket.SOCK_DGRAM)
            self._start(self._udp, local_udp, network_udp)
            for address, target, allowed in (
                (('127.0.0.1', self.peer_game + 1), (peer_ip, self.peer_game + 1), '127.0.0.1'),
                ((local_ip, self.own_game + 1), ('127.0.0.1', self.own_game + 1), peer_ip),
            ):
                listener = self._bind(address, socket.SOCK_STREAM)
                listener.listen(4)
                self._start(self._accept, listener, target, allowed)
        except BaseException:
            self.close()
            raise

    def _track(self, sock):
        with self.lock:
            if self.stopped.is_set():
                sock.close()
                raise OSError('Game relay is closed')
            self.sockets.add(sock)
        return sock

    def _bind(self, address, kind):
        sock = self._track(socket.socket(socket.AF_INET, kind))
        sock.settimeout(.2)
        sock.bind(address)
        return sock

    def _start(self, target, *args):
        with self.lock:
            if self.stopped.is_set():
                return
            self.threads = [thread for thread in self.threads if thread.is_alive()]
            thread = threading.Thread(target=target, args=args, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _failed(self, exc):
        if not self.stopped.is_set() and self.error is None:
            self.error = str(exc)

    def _udp(self, local, network):
        try:
            while not self.stopped.is_set():
                for source in select.select([local, network], [], [], .2)[0]:
                    try:
                        data, address = source.recvfrom(65535)
                        if source is local and address == ('127.0.0.1', self.own_game):
                            network.sendto(data, (self.peer_ip, self.own_game))
                        elif source is network and address == (self.peer_ip, self.own_game):
                            local.sendto(data, ('127.0.0.1', self.own_game))
                    except ConnectionResetError:
                        # Windows reports ICMP when SC2 has not opened its UDP port yet.
                        continue
        except (OSError, ValueError) as exc:
            self._failed(exc)

    def _accept(self, listener, target, allowed):
        while not self.stopped.is_set():
            try:
                conn, address = listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                self._failed(exc)
                return
            if address[0] != allowed or not self.connections.acquire(blocking=False):
                conn.close()
                continue
            try:
                self._track(conn)
            except OSError:
                self.connections.release()
                return
            self._start(self._tcp, conn, target)

    def _tcp(self, conn, target):
        upstream = None
        try:
            deadline = time.monotonic() + 30
            source_ip = self.local_ip if target[0] == self.peer_ip else '127.0.0.1'
            while not self.stopped.is_set():
                try:
                    upstream = self._track(socket.create_connection(
                        target, timeout=1, source_address=(source_ip, 0)))
                    break
                except OSError:
                    # The two SC2 processes begin listening asynchronously during JoinGame.
                    if time.monotonic() >= deadline:
                        raise
                    self.stopped.wait(.05)
            if upstream is None:
                return
            conn.settimeout(5)
            upstream.settimeout(5)
            readers = [conn, upstream]
            while readers and not self.stopped.is_set():
                for source in select.select(readers, [], [], .2)[0]:
                    destination = upstream if source is conn else conn
                    data = source.recv(65536)
                    if data:
                        destination.sendall(data)
                    else:
                        readers.remove(source)
                        destination.shutdown(socket.SHUT_WR)
        except (OSError, ValueError) as exc:
            self._failed(exc)
        finally:
            with self.lock:
                for sock in (conn, upstream):
                    if sock is not None:
                        self.sockets.discard(sock)
                        sock.close()
            self.connections.release()

    def close(self):
        self.stopped.set()
        with self.lock:
            sockets, threads = list(self.sockets), list(self.threads)
            self.sockets.clear()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        deadline = time.monotonic() + 2
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))

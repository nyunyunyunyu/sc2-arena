"""Real TCP lobby handshakes and Tk state; no SC2 process required."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sc2 import lan, lan_ui
from .test_lan import rooms


def selection(control="human", ai="default", ready=False, speed=None, no_window=False, rounds=None, sound=False):
    return dict(kind="lobby", protocol=3, control=control, ai=ai, ready=ready, speed=speed, no_window=no_window,
                rounds=(1 if speed is not None else None) if rounds is None else rounds, sound=sound)


def take(events, kind):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        event, value = events.get(timeout=max(0.01, deadline - time.monotonic()))
        if event == kind:
            return value
        if event == "error":
            raise AssertionError(value)
    raise AssertionError(f"Missing event: {kind}")


class LobbyTests(unittest.TestCase):
    def test_choices_validate_at_network_boundary(self):
        for control, ai in lan_ui.CHOICES.values():
            lan_ui.check_selection(selection(control, ai), False)
        for field, value in (("kind", "hello"), ("protocol", 1), ("control", "observer"),
                             ("ai", "../malicious"), ("ai", []), ("ready", 1), ("sound", 1), ("sound", None)):
            invalid = selection()
            invalid[field] = value
            with self.assertRaises(ValueError):
                lan_ui.check_selection(invalid, False)
        with self.assertRaises(ValueError):
            lan_ui.check_selection(selection("human", "a"), False)
        with self.assertRaises(ValueError):
            lan_ui.check_selection(selection(no_window=True), False)
        with self.assertRaises(ValueError):
            lan_ui.check_selection(selection(speed=2), False)
        for rounds in (0, 1001, True, "2", 1.5):
            with self.assertRaises(ValueError):
                lan_ui.check_selection(selection(speed=1, rounds=rounds), True)
        with self.assertRaises(ValueError):
            lan_ui.check_selection(selection(rounds=2), False)
        for rounds in (1, 1000):
            lan_ui.check_selection(selection(speed=1, rounds=rounds), True)
        for speed in (None, True, "1", 3, float("nan")):
            with self.assertRaises(ValueError):
                lan_ui.check_selection(selection(speed=speed), True)

    def test_only_both_ready_start_and_socket_handoff_has_no_lobby_messages(self):
        for host_choice in (("human", "default"), ("ai", "a4")):
            for join_choice in (("human", "default"), ("ai", "b")):
                with self.subTest(host=host_choice, join=join_choice), rooms() as (host, join), \
                        ThreadPoolExecutor(max_workers=2) as pool:
                    queues = [queue.Queue(), queue.Queue()]
                    events = [queue.Queue(), queue.Queue()]
                    cancelled = threading.Event()
                    futures = [pool.submit(lan_ui.wait_ready, room,
                        SimpleNamespace(mode=role, control="human", ai="default", speed=1 if role == "host" else None, no_window=False, rounds=1 if role == "host" else None, sound=False), commands, updates, cancelled)
                        for room, role, commands, updates in zip((host, join), ("host", "join"), queues, events)]
                    try:
                        for updates in events:
                            self.assertFalse(take(updates, "peer")["ready"])
                        queues[0].put(selection(*host_choice, speed=0.5))
                        self.assertEqual(take(events[1], "peer")["ai"], host_choice[1])
                        queues[0].put(selection(*host_choice, ready=True, speed=0.5))
                        self.assertTrue(take(events[1], "peer")["ready"])
                        self.assertFalse(any(future.done() for future in futures))
                        queues[1].put(selection(*join_choice, ready=True))
                        for future, expected in zip(futures, (host_choice, join_choice)):
                            chosen = future.result(timeout=3)
                            self.assertEqual((chosen["control"], chosen["ai"]), expected)
                        self.assertEqual(futures[0].result()["speed"], 0.5)
                        self.assertIsNone(futures[1].result()["speed"])
                        host.send(dict(kind="hello"))
                        self.assertEqual(join.receive()["kind"], "hello")
                        join.send(dict(kind="hello"))
                        self.assertEqual(host.receive()["kind"], "hello")
                    finally:
                        cancelled.set()

    def test_rejects_early_start_and_changes_after_ready(self):
        for messages in ([dict(kind="lobby_start")],
                         [selection(ready=True, speed=1), selection("ai", "a", ready=True, speed=1)]):
            with rooms() as (host, join), ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lan_ui.wait_ready, join,
                    SimpleNamespace(mode="join", control="human", ai="default", speed=None, no_window=False, rounds=None, sound=False),
                    queue.Queue(), queue.Queue(), threading.Event())
                host.receive("lobby")
                for message in messages:
                    host.send(message)
                with self.assertRaises(ValueError):
                    future.result(timeout=3)

    def test_disconnect_exits_wait(self):
        with rooms() as (host, join), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lan_ui.wait_ready, join,
                SimpleNamespace(mode="join", control="human", ai="default", speed=None, no_window=False, rounds=None, sound=False),
                queue.Queue(), queue.Queue(), threading.Event())
            host.receive("lobby")
            host.socket.close()
            with self.assertRaises(lan.PeerDisconnected):
                future.result(timeout=3)

    def test_session_connect_cancel_and_reopen_port(self):
        options = SimpleNamespace(mode="host", address="127.0.0.1", port=0, timeout=2, key="")
        for _ in range(2):
            session = lan_ui.Session(options, queue.Queue())
            session.start()
            take(session.events, "status")
            options.port = session.transport.getsockname()[1]
            self.assertTrue(session.is_alive())
            session.stop()
            session.join(timeout=3)
            self.assertFalse(session.is_alive())
            take(session.events, "done")

    def test_sessions_launch_selected_visible_players_after_ready(self):
        launched = []

        def play(args, room=None):
            self.assertIsNotNone(room)
            self.assertFalse(args.hidden)
            self.assertEqual(args.no_window, args.control == "ai")
            launched.append((args.control, args.ai))
            room.send(dict(kind="hello", speed=args.speed, rounds=args.rounds))
            peer = room.receive("hello")
            self.assertEqual(args.speed if args.mode == "host" else peer["speed"], 2)
            self.assertEqual(args.rounds if args.mode == "host" else peer["rounds"], 3)
            self.assertEqual(args.sound, args.mode == "host")
            args.output.mkdir(parents=True)
            (args.output/"series.json").write_text(json.dumps(dict(status="complete", games=[])), encoding="utf-8")
            return 0

        with tempfile.TemporaryDirectory() as temporary, patch.object(lan_ui, "__file__", str(Path(temporary)/"lan_ui.py")), \
                patch.object(lan, "play", side_effect=play), patch.object(lan.sys, "platform", "win32"):
            host_args = lan.arguments(["host", "--yes"])
            host_args.port = 0
            host = lan_ui.Session(host_args, queue.Queue())
            join = None
            try:
                host.start()
                take(host.events, "status")
                port = host.transport.getsockname()[1]
                join_args = lan.arguments(["join", "--address", "127.0.0.1", "--port", str(port), "--yes"])
                join = lan_ui.Session(join_args, queue.Queue())
                join.start()
                for session in (host, join):
                    take(session.events, "connected")
                    take(session.events, "peer")
                host.commands.put(selection("ai", "a4", ready=True, speed=2, no_window=True, rounds=3, sound=True))
                self.assertTrue(take(join.events, "peer")["ready"])
                self.assertEqual(launched, [])
                join.commands.put(selection("human", ready=True))
                for session in (host, join):
                    take(session.events, "done")
                    session.join(timeout=3)
                self.assertCountEqual(launched, [("ai", "a4"), ("human", "default")])
            finally:
                for session in (host, join):
                    if session:
                        session.stop()
                        session.join(timeout=3)


@unittest.skipUnless(sys.platform == "win32", "Windows lobby UI required")
class LobbyWindowTests(unittest.TestCase):
    def test_selection_ready_and_disconnect_restore_controls(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            ui = lan_ui.Lobby(root, SimpleNamespace(port=14380, version="latest", windowed=False))
            session = SimpleNamespace(commands=queue.Queue(), cancelled=threading.Event())
            ui.session = session
            ui.mode = "host"
            ui.events.put(("connected", "127.0.0.1"))
            ui.poll()
            ui.choice.set("AI · A4")
            ui.speed.set("≈0.43×（流畅）")
            ui.rounds.set("3")
            ui.sound.set(False)
            ui.no_window.set(True)
            ui.update_selection()
            self.assertEqual(session.commands.get_nowait()["ai"], "a4")
            ui.prepare()
            chosen = session.commands.get_nowait()
            self.assertTrue(chosen["ready"])
            self.assertEqual(chosen["ai"], "a4")
            self.assertEqual(chosen["speed"], 0.43)
            self.assertTrue(chosen["no_window"])
            self.assertEqual(chosen["rounds"], 3)
            self.assertFalse(chosen["sound"])
            self.assertEqual(str(ui.rounds_selector["state"]), "disabled")
            self.assertEqual(str(ui.sound_check["state"]), "disabled")
            ui.update_selection()
            self.assertTrue(session.commands.empty())
            self.assertEqual(str(ui.selector["state"]), "disabled")
            self.assertEqual(str(ui.speed_selector["state"]), "disabled")
            self.assertEqual(str(ui.hide_check["state"]), "disabled")
            ui.events.put(("done", None))
            ui.poll()
            self.assertEqual(str(ui.selector["state"]), "readonly")
            self.assertEqual(str(ui.host_button["state"]), "normal")
            self.assertIsNone(ui.session)
            self.assertEqual(str(ui.rounds_selector["state"]), "normal")
            self.assertEqual(str(ui.sound_check["state"]), "normal")
            ui.choice.set("真人操作")
            ui.update_selection()
            self.assertFalse(ui.no_window.get())
            self.assertEqual(str(ui.hide_check["state"]), "disabled")
        finally:
            root.destroy()

    def test_invalid_rounds_block_creation_and_ready_without_freezing(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            ui = lan_ui.Lobby(root, SimpleNamespace(port=14380, version="latest", windowed=False))
            for value in ("", "bad", "0", "1001", "1.5"):
                ui.rounds.set(value)
                ui.connect("host")
                self.assertIsNone(ui.session)
            ui.session = SimpleNamespace(commands=queue.Queue())
            ui.mode, ui.connected = "host", True
            ui.prepare()
            self.assertFalse(ui.ready)
            self.assertTrue(ui.session.commands.empty())
            ui.rounds.set("1000")
            ui.prepare()
            self.assertTrue(ui.ready)
            self.assertEqual(ui.session.commands.get_nowait()["rounds"], 1000)
        finally:
            root.destroy()

    def test_bad_ip_and_port_do_not_start_session(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            ui = lan_ui.Lobby(root, SimpleNamespace(port=14380, version="latest", windowed=False))
            for address, port in (("bad-ip", "14380"), ("0.0.0.0", "14380"), ("127.0.0.1", "65532")):
                ui.address.set(address)
                ui.port.set(port)
                ui.connect("join")
                self.assertIsNone(ui.session)
                self.assertIn("无效", ui.status.get())
        finally:
            root.destroy()

    def test_joiner_displays_host_speed_without_editing_it(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            ui = lan_ui.Lobby(root, SimpleNamespace(port=14380, version="latest", windowed=False))
            ui.address.set("127.0.0.1")
            with patch.object(lan_ui, "Session") as session:
                ui.connect("join")
                self.assertIsNone(session.call_args.args[0].speed)
            self.assertEqual(str(ui.speed_selector["state"]), "disabled")
            ui.events.put(("peer", selection(speed=0.57, rounds=5)))
            ui.poll()
            self.assertEqual(ui.speed.get(), "≈0.57×（流畅）")
            self.assertEqual(ui.rounds.get(), "5")
            self.assertEqual(str(ui.rounds_selector["state"]), "disabled")
            self.assertTrue(ui.sound.get())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

"""Room framing/authentication and independent control routing; no game needed."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from s2clientprotocol import common_pb2, data_pb2, sc2api_pb2 as sc_pb

from . import lan
from .record import proto_dict, sha256


@contextlib.contextmanager
def rooms():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        a = socket.create_connection(listener.getsockname())
        b, _ = listener.accept()
    try:
        yield lan.Room(a, 1), lan.Room(b, 1)
    finally:
        a.close()
        b.close()


def hello(control="human"):
    return dict(kind="hello", protocol=1, control=control,
        engine=dict(game_version="5.0.test", base_build=123, data_version="abc", data_build=123),
        map_sha256="a"*64, rules_sha256="b"*64, policy_sha256="c"*64)


def observation(loop=0, ended=False):
    obs = sc_pb.ResponseObservation()
    obs.observation.game_loop = loop
    obs.observation.player_common.player_id = 1
    own = obs.observation.raw_data.units.add(tag=123, unit_type=48, alliance=1,
        display_type=1, health=60, health_max=60)
    own.pos.x, own.pos.y = 16, 16
    if ended:
        obs.player_result.add(player_id=1, result=1)
        obs.player_result.add(player_id=2, result=2)
    return obs


class LanTests(unittest.TestCase):
    def test_tcp_frames_auth_and_agreed_results(self):
        with rooms() as (host, guest), ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(host.authenticate, True, "local-test-key")
            b = pool.submit(guest.authenticate, False, "local-test-key")
            a.result(timeout=2)
            b.result(timeout=2)
            self.assertEqual(host.session_id, guest.session_id)
            host.send(dict(kind="hello", text="真人"))
            self.assertEqual(guest.receive("hello")["text"], "真人")
            a = pool.submit(host.confirm_result, {1: 1, 2: 2})
            b = pool.submit(guest.confirm_result, {1: 1, 2: 2})
            a.result(timeout=2)
            b.result(timeout=2)

    def test_bad_key_is_rejected_before_game(self):
        with rooms() as (host, guest), ThreadPoolExecutor(max_workers=2) as pool:
            def reject_host():
                with self.assertRaises(ValueError):
                    host.authenticate(True, "correct-key")
                host.socket.close()
            a = pool.submit(reject_host)
            with self.assertRaises(lan.PeerDisconnected):
                guest.authenticate(False, "incorrect-key")
            a.result(timeout=2)

    def test_bounded_frames_eof_and_timeout(self):
        for length in (0, lan.MAX_MESSAGE + 1):
            with rooms() as (a, b):
                a.socket.sendall(struct.pack("!I", length))
                with self.assertRaises(ValueError):
                    b.receive()
        with rooms() as (a, b):
            a.socket.sendall(struct.pack("!I", 5) + b"xx")
            a.socket.close()
            with self.assertRaises(lan.PeerDisconnected):
                b.receive()
        with rooms() as (_, b):
            with self.assertRaises(TimeoutError):
                b.receive(timeout=0.03)

    def test_bad_stage_and_bad_results_are_rejected(self):
        with rooms() as (a, b):
            a.send(dict(kind="loaded"))
            with self.assertRaises(ValueError):
                b.receive("hello")
            for results in ({}, {"1": True, "2": 2}, {"1": 1}, {"1": 9, "2": 2}):
                with self.assertRaises(ValueError):
                    b.accept_update(dict(kind="result", results=results))

    def test_compatibility_and_all_control_combinations(self):
        for local in ("human", "ai"):
            for peer in ("human", "ai"):
                lan.check_hello(hello(local), hello(peer))
        for field in ("protocol", "engine", "map_sha256", "rules_sha256", "control", "policy_sha256"):
            value = hello()
            value[field] = None
            with self.assertRaises(ValueError, msg=field):
                lan.check_hello(hello(), value)

    def test_join_uses_game_address_but_preserves_native_selection_and_fog(self):
        request = lan.join_request("192.168.1.9", 14380, "human", "host")
        self.assertEqual(request.host_ip, "192.168.1.9")
        self.assertEqual(request.player_name, "host-human")
        self.assertNotEqual(request.player_name, lan.join_request("192.168.1.9", 14380, "human", "join").player_name)
        self.assertEqual(request.race, common_pb2.Terran)
        self.assertEqual(request.server_ports.game_port, 14381)
        self.assertEqual(request.client_ports[0].base_port, 14384)
        self.assertFalse(request.options.raw_affects_selection)
        self.assertFalse(request.options.show_cloaked)

    def test_human_never_calls_policy_and_ai_respects_cadence(self):
        obs = observation(3)
        with patch.object(lan.agent, "decide", return_value=([], {}, {})) as decide:
            self.assertEqual(lan.local_actions("human", obs, {}, {}, 0)[0], [])
            decide.assert_not_called()
            result = lan.local_actions("ai", obs, {}, {}, 0)
            self.assertEqual(result[3], 7)
            obs.observation.game_loop = 6
            lan.local_actions("ai", obs, {}, {}, result[3])
            self.assertEqual(decide.call_count, 1)
            obs.observation.game_loop = 8
            lan.local_actions("ai", obs, {}, {}, result[3])
            self.assertEqual(decide.call_count, 2)

    def fake_play(self, control, peer_control, directory, disconnect=False, watchdog=False, role="host", bad_replay=False):
        args = lan.arguments([role, "--address", "127.0.0.1", "--control", control, "--yes", "--hidden", "--output", str(directory)])
        if watchdog:
            args.max_game_loops = 4
        rules = json.loads(args.rules.read_text(encoding="utf-8-sig"))
        units = {i: data_pb2.UnitTypeData(unit_id=i) for i in rules["unit_type_ids"]}
        unit_data = {str(k): proto_dict(v) for k, v in units.items()}
        encoded = json.dumps(unit_data, sort_keys=True).encode()
        identity = hello()["engine"]
        peer = hello(peer_control)
        peer.update(engine=identity,
            map_sha256=sha256((args.rules.parent/rules["map_file"]).read_bytes()),
            rules_sha256=sha256(args.rules.read_bytes()))
        room = Mock(session_id="session-1")
        room.socket.getsockname.return_value = ("127.0.0.1", 14380)
        room.socket.getpeername.return_value = ("127.0.0.1", 44444)
        room.receive.side_effect = [peer, dict(kind="map_saved")] + (
            [dict(kind="join")] if role == "join" else []) + [dict(player_id=2, unit_data_sha256=sha256(encoded))]
        if disconnect:
            room.poll.side_effect = lan.PeerDisconnected("test disconnect")
        controller = Mock()
        controller.ping.return_value = sc_pb.ResponsePing(**identity)
        controller.join_game.return_value = sc_pb.ResponseJoinGame(player_id=1)
        controller.observe.side_effect = [observation(), observation(4), observation(8, True)]
        controller.data.return_value = SimpleNamespace(unit_stats=units)
        controller.save_replay.return_value = b"test replay"
        controller.status = lan.protocol.Status.ended
        controller.actions.return_value = sc_pb.ResponseAction(result=[1])
        process = Mock(controller=controller, pid=123, port=54321)
        config = Mock()
        config.start.return_value = process
        action = sc_pb.Action()
        action.action_raw.unit_command.ability_id = 4
        action.action_raw.unit_command.unit_tags.append(123)
        with patch.object(lan, "open_room", return_value=room), \
                patch.object(lan.run_configs, "get", return_value=config), \
                patch("sc2.windows_env.set_client_window"), \
                patch.object(lan.agent, "decide", return_value=([action], {}, {"mode":"ai"})) as decide, \
                patch.object(lan, "replay_speed", return_value=4, side_effect=ValueError("bad replay") if bad_replay else None), \
                patch.object(lan.time, "sleep"), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            result = lan.play(args)
        process.close.assert_called_once()
        room.socket.close.assert_called_once()
        self.assertEqual(config.start.call_args.kwargs["host"], "127.0.0.1")
        if role == "host":
            self.assertTrue(controller.create_game.call_args.args[0].realtime)
        else:
            controller.create_game.assert_not_called()
        controller.step.assert_not_called()
        manifest = json.loads((directory/"manifest.json").read_text())
        return result, manifest, controller, decide, room

    def test_full_lifecycle_routes_each_pair_and_saves_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            for control in ("human", "ai"):
                for peer in ("human", "ai"):
                    folder = Path(temporary)/(control+"-"+peer)
                    result, manifest, controller, decide, room = self.fake_play(control, peer, folder)
                    self.assertEqual(result, 0)
                    self.assertEqual(manifest["status"], "complete")
                    self.assertEqual(manifest["peer_control"], peer)
                    self.assertEqual(manifest["player_results"], {"1":1,"2":2})
                    self.assertTrue((folder/"game.SC2Replay").exists())
                    room.confirm_result.assert_called_once_with({1:1,2:2})
                    room.wait_saved.assert_called_once()
                    self.assertEqual(controller.actions.call_count, int(control=="ai"))
                    self.assertEqual(decide.call_count, int(control=="ai"))

    def test_join_lifecycle_and_replay_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            for control in ("human", "ai"):
                folder = Path(temporary)/control
                result, manifest, controller, _, room = self.fake_play(control, "human", folder, role="join")
                self.assertEqual(result, 0)
                self.assertEqual(manifest["source"], "real_engine_diagnostic")
                self.assertEqual(controller.actions.call_count, int(control == "ai"))
                self.assertEqual(controller.join_game.call_args.args[0].player_name, "join-" + control)
                room.wait_saved.assert_called_once()
            folder = Path(temporary)/"bad-replay"
            result, manifest, _, _, room = self.fake_play("ai", "ai", folder, bad_replay=True)
            self.assertEqual(result, 2)
            self.assertEqual(manifest["status"], "error")
            self.assertIn("replay_error", manifest)
            self.assertFalse((folder/"game.SC2Replay").exists())
            room.wait_saved.assert_called_once()

    def test_replay_barrier_drains_heartbeats(self):
        with rooms() as (host, guest):
            host.send(dict(kind="ping"))
            host.send(dict(kind="saved"))
            guest.wait_saved()
            guest.send(dict(kind="saved"))
            host.wait_saved()

    def test_disconnect_and_watchdog_do_not_become_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            for kind in ("disconnect", "watchdog"):
                result, manifest, _, _, room = self.fake_play(
                    "human", "ai", Path(temporary)/kind, disconnect=kind=="disconnect", watchdog=kind=="watchdog")
                self.assertEqual(result, 2)
                self.assertEqual(manifest["status"], "disconnected" if kind=="disconnect" else "watchdog")
                self.assertEqual(manifest["player_results"], {})
                room.confirm_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()

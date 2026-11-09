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

from sc2 import lan
from sc2.record import proto_dict, sha256


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


def hello(control="human", speed=1):
    return dict(kind="hello", protocol=4, control=control, speed=speed,
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

    def test_default_empty_password_connects_without_prompt(self):
        for role in ("host", "join"):
            args = lan.arguments([role, "--address", "127.0.0.1", "--hidden"])
            self.assertEqual(args.key, "")
            connection = Mock()
            listener = Mock()
            listener.__enter__ = Mock(return_value=listener)
            listener.__exit__ = Mock(return_value=False)
            listener.accept.return_value = (connection, ("127.0.0.1", 1234))
            with patch.object(lan.socket, "socket", return_value=listener), \
                    patch.object(lan.socket, "create_connection", return_value=connection), \
                    patch.object(lan, "Room") as room, \
                    patch("builtins.input", side_effect=AssertionError("No password prompt")), \
                    contextlib.redirect_stdout(io.StringIO()):
                lan.open_room(args)
            room.return_value.authenticate.assert_called_once_with(role == "host", "")
        with rooms() as (host, guest), ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(host.authenticate, True, "")
            b = pool.submit(guest.authenticate, False, "")
            a.result(timeout=2)
            b.result(timeout=2)
            self.assertEqual(host.session_id, guest.session_id)
        self.assertEqual(lan.arguments(["host", "--hidden", "--key", "optional-key"]).key, "optional-key")

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
                lan.check_hello(hello(local), hello(peer, speed=None))
        for field in ("protocol", "engine", "map_sha256", "rules_sha256", "control", "policy_sha256"):
            value = hello(speed=None)
            value[field] = None
            with self.assertRaises(ValueError, msg=field):
                lan.check_hello(hello(), value)

    def test_join_uses_game_address_but_preserves_native_selection_and_fog(self):
        request = lan.join_request("192.168.1.9", 14380, "human", "host")
        self.assertEqual(request.host_ip, "192.168.1.9")
        self.assertEqual(request.player_name, "host-human")
        self.assertNotEqual(request.player_name, lan.join_request("192.168.1.9", 14380, "human", "join").player_name)
        self.assertEqual(request.race, common_pb2.Terran)
        self.assertEqual(request.shared_port, 0)
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

    def test_ai_yields_selected_units_and_resumes_on_deselection(self):
        obs = observation()
        obs.observation.raw_data.units.add(tag=456, alliance=1, display_type=1)
        obs.observation.raw_data.units.add(tag=789, alliance=4, display_type=1, is_selected=True)
        single = sc_pb.Action()
        single.action_raw.unit_command.ability_id = 4
        single.action_raw.unit_command.unit_tags.append(123)
        group = sc_pb.Action()
        group.action_raw.unit_command.ability_id = 23
        group.action_raw.unit_command.unit_tags.extend([123, 456])
        group.action_raw.unit_command.target_unit_tag = 789
        group.action_raw.unit_command.queue_command = True
        move = sc_pb.Action()
        move.action_raw.unit_command.ability_id = 16
        move.action_raw.unit_command.unit_tags.append(456)
        move.action_raw.unit_command.target_world_space_pos.x = 20
        move.action_raw.unit_command.target_world_space_pos.y = 21
        actions = [single, group, move]
        original = [a.SerializeToString() for a in actions]
        memory, reason = {"game_loop": 0}, {"mode": "ai"}
        policy = SimpleNamespace(CADENCE=4, decide=Mock(return_value=(actions, memory, reason)))
        group_123, group_456 = copy.deepcopy(group), copy.deepcopy(group)
        group_123.action_raw.unit_command.unit_tags[:] = [123]
        group_456.action_raw.unit_command.unit_tags[:] = [456]
        next_loop = 0
        for selected, expected in (((), actions), ((123,), [group_456, move]),
                                   ((123, 456), []), ((456,), [single, group_123]), ((), actions)):
            with self.subTest(selected=selected):
                obs.observation.game_loop = next_loop
                for unit in obs.observation.raw_data.units[:2]:
                    unit.is_selected = unit.tag in selected
                before = obs.SerializeToString()
                issued, new_memory, why, next_loop = lan.local_actions(
                    "ai", obs, memory, {}, next_loop, policy)
                self.assertEqual(issued, expected)
                lan.validate_actions(obs, issued)
                self.assertEqual(why.get("manual_unit_tags", []), list(selected))
                self.assertIs(new_memory, memory)
                self.assertEqual(obs.SerializeToString(), before)
                self.assertEqual([a.SerializeToString() for a in actions], original)
                self.assertEqual(reason, {"mode": "ai"})
        self.assertEqual(policy.decide.call_count, 5)
        self.assertFalse(lan.join_request("127.0.0.1", 14380, "ai", "host").options.raw_affects_selection)

    def test_selection_does_not_bypass_action_validation(self):
        obs = observation()
        obs.observation.raw_data.units[0].is_selected = True
        action = sc_pb.Action()
        action.action_raw.unit_command.ability_id = 23
        action.action_raw.unit_command.unit_tags.append(123)
        action.action_raw.unit_command.target_unit_tag = 999
        policy = SimpleNamespace(CADENCE=4, decide=Mock(return_value=([action], {}, {})))
        with self.assertRaisesRegex(ValueError, "non-visible"):
            lan.local_actions("ai", obs, {}, {}, 0, policy)
        action.action_raw.unit_command.ClearField("target_unit_tag")
        action.action_raw.unit_command.unit_tags.append(999)
        with self.assertRaisesRegex(ValueError, "non-owned"):
            lan.local_actions("ai", obs, {}, {}, 0, policy)

    def fake_play(self, control, peer_control, directory, disconnect=False, watchdog=False, role="host", bad_replay=False, display=None, rounds=1, ai="default", existing_room=False, speed=1, sound=False, remote=False):
        args = lan.arguments([role, "--address", "127.0.0.1", "--control", control, "--yes",
                              *(display if display is not None else ["--hidden"]), "--output", str(directory), "--ai", ai,
                              *(["--speed", str(speed)] if role == "host" else [])])
        if role == "host":
            args.rounds = rounds
        args.sound = sound
        if watchdog:
            args.max_game_loops = 4
        rules = json.loads(args.rules.read_text(encoding="utf-8-sig"))
        units = {i: data_pb2.UnitTypeData(unit_id=i) for i in rules["unit_type_ids"]}
        unit_data = {str(k): proto_dict(v) for k, v in units.items()}
        encoded = json.dumps(unit_data, sort_keys=True).encode()
        identity = hello()["engine"]
        peer = hello(peer_control, speed=None if role == "host" else speed)
        peer["rounds"] = rounds if role == "join" else None
        peer.update(engine=identity,
            map_sha256=sha256((args.rules.parent/rules["map_file"]).read_bytes()),
            rules_sha256=sha256(args.rules.read_bytes()))
        room = Mock(session_id="session-1")
        room.socket.getsockname.return_value = ("127.0.0.1", 14380)
        room.socket.getpeername.return_value = ("192.168.1.2" if remote else "127.0.0.1", 44444)
        room.receive.side_effect = [peer, dict(kind="map_saved")]
        room.wait_stage.side_effect = sum((
            ([dict(kind="join", round=n)] if role == "join" else []) +
            [dict(player_id=2, unit_data_sha256=sha256(encoded), round=n)]
            for n in range(1, rounds + 1)), [])
        if disconnect:
            room.poll.side_effect = lan.PeerDisconnected("test disconnect")
        controller = Mock()
        controller.ping.return_value = sc_pb.ResponsePing(**identity)
        controller.join_game.return_value = sc_pb.ResponseJoinGame(player_id=1)
        controller.observe.side_effect = [observation(), observation(116), observation(120, True)] * rounds
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
        with patch.object(lan, "GameRelay") as relay, \
                patch.object(lan, "open_room", return_value=room) as open_room, \
                patch.object(lan.run_configs, "get", return_value=config), \
                patch("sc2.windows_env.set_client_window") as window, \
                patch.object(lan.load_policy(ai), "decide", return_value=([action], {}, {"mode":"ai"})) as decide, \
                patch.object(lan, "replay_speed", return_value=4, side_effect=ValueError("bad replay") if bad_replay else None), \
                patch.object(lan.time, "sleep"), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            relay.return_value.error = None
            result = lan.play(args, room=room if existing_room else None)
        if remote:
            relay.assert_called_once_with("127.0.0.1", "192.168.1.2", args.port, role == "host")
            relay.return_value.close.assert_called_once()
            room.barrier.assert_any_call("transport_ready", 0)
            self.assertEqual(controller.join_game.call_args.args[0].host_ip, "127.0.0.1")
        else:
            relay.assert_not_called()
        if existing_room:
            open_room.assert_not_called()
        else:
            open_room.assert_called_once_with(args)
        config.start.assert_called_once()
        process.close.assert_called_once()
        room.socket.close.assert_called_once()
        self.assertEqual(config.start.call_args.kwargs["host"], "127.0.0.1")
        if role == "host":
            self.assertEqual(controller.create_game.call_args.args[0].realtime, speed in lan.NATIVE_SPEEDS)
        else:
            controller.create_game.assert_not_called()
        if lan.sys.platform == "win32":
            self.assertIn(f"-sound={'true' if sound else 'false'}", config.start.call_args.kwargs["extra_args"])
            self.assertIn(f"-soundglobal={'true' if sound else 'false'}", config.start.call_args.kwargs["extra_args"])
            self.assertIn(f"-music={'true' if sound else 'false'}", config.start.call_args.kwargs["extra_args"])
            self.assertEqual(config.start.call_args.kwargs["full_screen"],
                             not (args.hidden or args.no_window or args.windowed))
            if args.hidden or args.no_window:
                self.assertEqual([call.args for call in window.call_args_list], [(123,)] + [(123, None)] * controller.join_game.call_count)
        if speed in lan.NATIVE_SPEEDS:
            controller.step.assert_not_called()
        else:
            controller.step.assert_called_once_with(1)
        manifest = json.loads(((directory if rounds == 1 else directory/"round-001")/"manifest.json").read_text())
        return result, manifest, controller, decide, room

    def test_cross_machine_relay_used_and_closed_on_success_or_disconnect(self):
        with tempfile.TemporaryDirectory() as temporary:
            for role in ("host", "join"):
                for disconnect in (False, True):
                    result, manifest, _, _, _ = self.fake_play("ai", "ai",
                        Path(temporary)/(role + str(disconnect)), role=role, remote=True,
                        disconnect=disconnect)
                    self.assertEqual(result, 2 if disconnect else 0)
                    self.assertEqual(manifest["game_transport"], "forwarded")

    def test_old_game_protocol_rejected_before_join(self):
        peer = hello()
        peer["protocol"] = 3
        with self.assertRaisesRegex(ValueError, "Incompatible room protocol"):
            lan.check_hello(hello(), peer)

    def test_local_sound_can_be_enabled_or_muted(self):
        with patch.object(lan.sys, "platform", "win32"), tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(lan.arguments(["host"]).sound)
            self.assertTrue(lan.arguments(["host", "--sound"]).sound)
            self.assertFalse(lan.arguments(["host", "--sound", "--no-sound"]).sound)
            for role in ("host", "join"):
                for sound in (True, False):
                    result, manifest, _, _, _ = self.fake_play("ai", "ai",
                        Path(temporary)/(role + str(sound)), role=role, sound=sound)
                    self.assertEqual(result, 0)
                    self.assertEqual(manifest["sound"], sound)

    def test_sound_is_rejected_on_unsupported_platform(self):
        with patch.object(lan.sys, "platform", "linux"), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                lan.arguments(["host", "--control", "ai", "--no-window", "--sound"])

    def test_series_reuses_client_resets_policy_and_records_each_round(self):
        with tempfile.TemporaryDirectory() as temporary:
            for role in ("host", "join"):
                directory = Path(temporary) / role
                result, _, controller, decide, room = self.fake_play("ai", "human", directory, role=role, rounds=3)
                self.assertEqual(result, 0)
                self.assertEqual(controller.join_game.call_count, 3)
                self.assertEqual(controller.leave.call_count, 3)
                self.assertEqual(controller.save_replay.call_count, 3)
                self.assertEqual(controller.chat.call_count, 3)
                self.assertEqual(controller.create_game.call_count, 3 if role == "host" else 0)
                self.assertEqual([c.args for c in room.start_round.call_args_list], [(1,), (2,), (3,)])
                self.assertEqual([c.args for c in room.barrier.call_args_list], [("left", 1), ("left", 2), ("left", 3)])
                self.assertTrue(all(c.args[1] == {} for c in decide.call_args_list))
                for number in range(1, 4):
                    folder = directory / f"round-{number:03d}"
                    manifest = json.loads((folder / "manifest.json").read_text())
                    self.assertEqual((manifest["round"], manifest["rounds"], manifest["client_pid"]), (number, 3, 123))
                    self.assertTrue((folder / "game.SC2Replay").exists())
                self.assertEqual(len(json.loads((directory / "series.json").read_text())["games"]), 3)
            folder = Path(temporary) / "failed"
            result, _, controller, _, _ = self.fake_play("ai", "human", folder, rounds=3, bad_replay=True)
            self.assertEqual(result, 2)
            controller.join_game.assert_called_once()
            self.assertFalse((folder / "round-002").exists())

    def test_lobby_reuses_connected_room_and_shows_human_and_ai_windows(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(lan.sys, "platform", "win32"):
            for role in ("host", "join"):
                for control in ("human", "ai"):
                    result, manifest, _, _, _ = self.fake_play(control, "ai", Path(temporary)/(role+control),
                        role=role, display=[], ai="a4" if control == "ai" else "default", existing_room=True)
                    self.assertEqual(result, 0)
                    self.assertFalse(manifest["hidden"])
                    self.assertFalse(manifest["no_window"])

    def test_host_speed_is_adopted_by_joiner_and_steps_both_clients(self):
        with tempfile.TemporaryDirectory() as temporary:
            for role in ("host", "join"):
                for speed in (0.25, 0.5, 0.75, 1.5, 2):
                    result, manifest, controller, decide, _ = self.fake_play(
                        "ai", "human", Path(temporary)/(role+str(speed)), role=role, speed=speed)
                    self.assertEqual(result, 0)
                    self.assertEqual(manifest["requested_speed"], speed)
                    self.assertEqual(manifest["pacing_game_loops_per_wall_second"], 22.4 * speed)
                    self.assertEqual(manifest["environment_step_mul"], 1)
                    self.assertEqual(manifest["step_mul"], 4)
                    self.assertFalse(manifest["realtime"])
                    controller.step.assert_called_once_with(1)
                    decide.assert_called_once()

    def test_native_slow_speeds_keep_realtime_and_only_host_configures_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            for role in ("host", "join"):
                for speed in (0.43, 0.57, 0.71, 0.86):
                    result, manifest, controller, _, _ = self.fake_play(
                        "ai", "ai", Path(temporary)/(role+str(speed)), role=role, speed=speed)
                    self.assertEqual(result, 0)
                    self.assertTrue(manifest["realtime"])
                    self.assertEqual(manifest["native_speed"], lan.NATIVE_SPEEDS[speed])
                    self.assertIsNone(manifest["pacing_game_loops_per_wall_second"])
                    controller.step.assert_not_called()
                    commands = [c.args[0] for c in controller.chat.call_args_list if c.args[0].startswith("LAN speed:")]
                    self.assertEqual(commands, [f"LAN speed: {lan.NATIVE_SPEEDS[speed]}"] if role == "host" else [])

    def test_speed_is_host_only_and_untrusted_values_are_rejected(self):
        self.assertEqual(lan.arguments(["host"]).speed, 1)
        self.assertIsNone(lan.arguments(["join", "--address", "127.0.0.1"]).speed)
        for value in ("0", "nan", "3"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                lan.arguments(["host", "--speed", value])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            lan.arguments(["join", "--address", "127.0.0.1", "--speed", "1"])
        for speed in (None, True, "0.5", 0, float("nan"), 3):
            with self.assertRaises(ValueError):
                lan.check_hello(hello(speed=None), hello(speed=speed))
        with self.assertRaises(ValueError):
            lan.check_hello(hello(speed=0.5), hello(speed=2))

    def test_selected_ai_runs_and_records_identity_across_lan_rounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            for role in ("host", "join"):
                for name in ("a", "b", "a2", "a3", "a4", "a5", "a6"):
                    directory = Path(temporary) / (role + name)
                    with patch.object(lan.agent, "decide", side_effect=AssertionError("Wrong AI selected")):
                        result, manifest, controller, decide, _ = self.fake_play(
                            "ai", "human", directory, role=role, rounds=2, ai=name)
                    self.assertEqual(result, 0)
                    self.assertEqual(decide.call_count, 2)
                    for number in (1, 2):
                        manifest = json.loads((directory / f"round-{number:03d}" / "manifest.json").read_text())
                        self.assertEqual(manifest["policy_name"], name)
                        self.assertEqual(manifest["policy_sha256"], sha256(Path(lan.load_policy(name).__file__).read_bytes()))

    def test_round_count_and_stale_messages_are_rejected(self):
        self.assertEqual(lan.arguments(["host", "--hidden"]).rounds, 1)
        with contextlib.redirect_stderr(io.StringIO()):
            for command in (["host", "--rounds", "0"], ["host", "--rounds", "1001"],
                            ["join", "--address", "127.0.0.1", "--rounds", "2"]):
                with self.assertRaises(SystemExit):
                    lan.arguments(command)
        with rooms() as (a, b):
            b.start_round(1)
            for number in (True, 1.0, "1"):
                with self.assertRaises(ValueError):
                    b.accept_update(dict(kind="result", round=number, results={"1":1,"2":2}))
            b.start_round(2)
            with self.assertRaises(ValueError):
                b.accept_update(dict(kind="result", round=1, results={"1":1,"2":2}))
            a.send(dict(kind="left", round=1))
            with self.assertRaises(ValueError):
                b.wait_stage("left", 2)
            b.peer_results = {"1":1,"2":2}
            b.ended = True
            b.start_round(3)
            self.assertIsNone(b.peer_results)
            self.assertFalse(b.ended)

    def test_display_flags_and_normal_ai_no_window_lifecycle(self):
        with patch.object(lan.sys, "platform", "win32"), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(lan.arguments(["host"]).windowed)
            for command in (["host", "--no-window"],
                            ["host", "--control", "ai", "--no-window", "--windowed"]):
                with self.assertRaises(SystemExit):
                    lan.arguments(command)
            with tempfile.TemporaryDirectory() as temporary:
                for role in ("host", "join"):
                    for i, display in enumerate(([], ["--windowed"], ["--no-window"])):
                        folder = Path(temporary) / (role + str(i))
                        result, manifest, controller, decide, _ = self.fake_play(
                            "ai", "human", folder, role=role, display=display)
                        self.assertEqual(result, 0)
                        self.assertEqual(manifest["source"], "real_engine")
                        self.assertEqual(manifest["no_window"], "--no-window" in display)
                        self.assertFalse(manifest["hidden"])
                        controller.actions.assert_called_once()
                        decide.assert_called_once()
        with patch.object(lan.sys, "platform", "linux"), contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(lan.arguments(["host", "--control", "ai", "--no-window"]).no_window)
            with self.assertRaises(SystemExit):
                lan.arguments(["host", "--control", "ai", "--windowed"])

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

    def test_poll_leaves_saved_message_for_replay_barrier(self):
        with rooms() as (host, guest):
            host.send(dict(kind="result", round=0, results={"1":1,"2":2}))
            host.send(dict(kind="saved", round=0))
            guest.poll()
            guest.poll()
            self.assertEqual(guest.peer_results, {"1":1,"2":2})
            guest.wait_saved()

    def test_replay_barrier_drains_heartbeats(self):
        with rooms() as (host, guest):
            host.send(dict(kind="ping"))
            host.send(dict(kind="saved", round=0))
            guest.wait_saved()
            guest.send(dict(kind="saved", round=0))
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

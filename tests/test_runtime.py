"""Small offline checks: python -m unittest tests.test_runtime."""
import base64
import gzip
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from contextlib import nullcontext, redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch

from s2clientprotocol import raw_pb2 as raw_pb
from s2clientprotocol import sc2api_pb2 as sc_pb
from sc2.arena_env import ArenaRenderer, arena_hud, interface_options, load_map, projected_ring, validate_actions
from sc2.record import GameRecord, actual_results, sha256
from sc2.policies import load_policy
from sc2.run import arena_for_map, arena_for_replay, arguments, play, replay_map_data, save_rgb_frame, watch
from sc2.windows_env import set_client_window, window_options


def observation():
    obs = sc_pb.ResponseObservation()
    obs.observation.player_common.player_id = 7
    obs.observation.game_loop = 12
    obs.observation.raw_data.units.add(tag=123, alliance=raw_pb.Self, display_type=raw_pb.Visible)
    obs.observation.raw_data.units.add(tag=456, alliance=raw_pb.Enemy, display_type=raw_pb.Visible)
    obs.observation.raw_data.units.add(tag=789, alliance=raw_pb.Enemy, display_type=raw_pb.Snapshot)
    return obs


class RuntimeTest(unittest.TestCase):
    def test_rgb_capture_decodes_native_windows_bgr(self):
        import pygame
        obs = sc_pb.ResponseObservation()
        frame = obs.observation.render_data.map
        frame.size.x = frame.size.y = 1
        frame.bits_per_pixel = 24
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "red.png"
            for platform, data in (("win32", bytes((0, 0, 255))), ("linux", bytes((255, 0, 0)))):
                frame.data = data
                with patch("sc2.run.sys.platform", platform):
                    save_rgb_frame(obs, path)
                self.assertEqual(tuple(pygame.image.load(str(path)).get_at((0, 0)))[:3], (255, 0, 0))

    def test_interface_keeps_fog_information_options_strict(self):
        options = interface_options(visual=True, rgb=True)
        self.assertTrue(options.raw)
        self.assertFalse(options.show_cloaked)
        self.assertFalse(options.show_burrowed_shadows)
        self.assertFalse(options.feature_layer.allow_cheating_layers)
        self.assertEqual(options.render.resolution.x, 1024)
        self.assertEqual(options.render.resolution.y, 768)

    def test_commands_cannot_control_opponent_or_target_snapshot(self):
        obs = observation()
        action = sc_pb.Action()
        command = action.action_raw.unit_command
        command.ability_id = 23
        command.unit_tags.append(123)
        command.target_unit_tag = 456
        validate_actions(obs, [action])
        command.target_unit_tag = 789
        with self.assertRaises(ValueError):
            validate_actions(obs, [action])
        command.target_unit_tag = 456
        command.unit_tags[0] = 456
        with self.assertRaises(ValueError):
            validate_actions(obs, [action])

    def test_record_preserves_exact_protobuf_and_unknown_is_not_win(self):
        obs = observation()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "game"
            record = GameRecord(directory, {"seed": 4})
            record.observe(obs)
            record.finish("watchdog", [obs])
            manifest = json.loads((directory / "manifest.json").read_text())
            self.assertEqual(manifest["player_results"], {})
            with gzip.open(directory / "observations.jsonl.gz", "rt") as stream:
                stored = json.loads(next(stream))
            self.assertEqual(base64.b64decode(stored["response_observation_pb"]), obs.SerializeToString())
            with self.assertRaises(FileExistsError):
                GameRecord(directory, {})

    def test_record_omits_only_pixel_layers_without_changing_policy_input(self):
        obs = observation()
        obs.observation.render_data.map.data = b"pixel bytes"
        obs.observation.feature_layer_data.SetInParent()
        before = obs.SerializeToString()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "game"
            record = GameRecord(directory, {})
            record.observe(obs)
            record.finish("watchdog", [obs])
            with gzip.open(directory / "observations.jsonl.gz", "rt") as stream:
                stored = json.loads(next(stream))
            decoded = sc_pb.ResponseObservation.FromString(base64.b64decode(stored["response_observation_pb"]))
        self.assertEqual(obs.SerializeToString(), before)
        self.assertEqual(decoded.observation.raw_data, obs.observation.raw_data)
        self.assertFalse(decoded.observation.HasField("render_data"))
        self.assertFalse(decoded.observation.HasField("feature_layer_data"))

    def test_results_use_returned_player_ids_and_detect_disagreement(self):
        first, second = observation(), observation()
        first.player_result.add(player_id=7, result=sc_pb.Victory)
        second.player_result.add(player_id=7, result=sc_pb.Victory)
        self.assertEqual(actual_results([first, second]), {7: sc_pb.Victory})
        second.player_result[0].result = sc_pb.Defeat
        with self.assertRaises(ValueError):
            actual_results([first, second])
        with tempfile.TemporaryDirectory() as temporary:
            record = GameRecord(Path(temporary) / "conflict", {})
            record.finish("complete", [first, second])
            self.assertEqual(record.manifest["status"], "error")
            self.assertEqual(record.manifest["player_results"], {})

    def test_watch_requires_replay(self):
        with self.assertRaises(SystemExit):
            arguments(["watch"])

    def test_native_ui_has_no_python_rgb_view_and_requires_windows(self):
        with patch("sc2.run.sys.platform", "linux"), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                arguments(["human", "--native-ui"])
        with patch("sc2.run.sys.platform", "win32"), redirect_stderr(io.StringIO()):
            self.assertTrue(arguments(["human", "--native-ui"]).native_ui)
            for command in (["duel", "--native-ui"], ["human", "--native-ui", "--rgb"]):
                with self.assertRaises(SystemExit):
                    arguments(command)

    def test_display_flags_reject_hiding_humans_and_rgb(self):
        with patch("sc2.run.sys.platform", "win32"), redirect_stderr(io.StringIO()):
            self.assertFalse(arguments(["human", "--native-ui"]).windowed)
            self.assertTrue(arguments(["human", "--native-ui", "--windowed"]).windowed)
            self.assertTrue(arguments(["duel", "--no-window"]).no_window)
            for command in (["human", "--no-window"], ["watch", "--replay", "test", "--no-window"],
                            ["duel", "--no-window", "--rgb"], ["duel", "--windowed"],
                            ["duel", "--no-window", "--windowed"]):
                with self.subTest(command=command), self.assertRaises(SystemExit):
                    arguments(command)
        with patch("sc2.run.sys.platform", "linux"):
            self.assertTrue(arguments(["duel", "--no-window"]).no_window)

    def test_native_launch_fullscreen_only_for_the_visible_player(self):
        from sc2.windows_env import WindowsArenaEnv
        for human_slot, windowed in ((0, False), (1, True), (None, False)):
            with self.subTest(human_slot=human_slot, windowed=windowed):
                env = WindowsArenaEnv.__new__(WindowsArenaEnv)
                env.native_human_slot, env.native_windowed = human_slot, windowed
                env._num_agents = 2
                env._interface_options = [interface_options(), interface_options()]
                env._run_config = Mock()
                env._run_config.start.side_effect = [Mock(pid=101), Mock(pid=102)]
                with patch("sc2.windows_env.portspicker.pick_unused_ports", return_value=[1, 2, 3, 4]), \
                        patch("sc2.windows_env.set_client_window") as hide:
                    env._launch_game()
                for slot, call in enumerate(env._run_config.start.call_args_list):
                    self.assertEqual(call.kwargs["full_screen"], slot == human_slot and not windowed)
                    self.assertEqual(call.kwargs["window_size"], (1600, 900) if slot == human_slot else (640, 480))
                    self.assertFalse(call.kwargs["want_rgb"])
                self.assertEqual([call.args[0] for call in hide.call_args_list],
                                 [101 + slot for slot in range(2) if slot != human_slot])

    def test_window_setup_targets_owned_windows_and_checks_hiding(self):
        self.assertTrue(window_options(True)["full_screen"])
        self.assertFalse(window_options(True, windowed=True)["full_screen"])
        self.assertNotIn("-displayMode", window_options(True, windowed=True)["extra_args"])
        self.assertFalse(window_options(False)["full_screen"])
        self.assertEqual(window_options(True)["window_size"], (1600, 900))
        self.assertEqual(window_options(True)["extra_args"], ["-frameratecap=120", "-frameratecapglue=30", "-displayMode", "2"])
        self.assertEqual(window_options(False)["extra_args"], ["-frameratecap=30", "-frameratecapglue=30"])
        api = Mock()
        visible = {100: True, 200: True}
        api.EnumWindows.side_effect = lambda callback, arg: all(callback(w, arg) for w in visible)
        api.GetWindowThreadProcessId.side_effect = lambda w, owner: setattr(owner._obj, "value", w + 1)
        api.IsWindowVisible.side_effect = lambda w: visible[w]
        api.ShowWindow.side_effect = lambda w, flag: visible.update({w: False})
        with patch("sc2.windows_env.ctypes.WinDLL", return_value=api, create=True), \
                patch("sc2.windows_env.ctypes.WINFUNCTYPE", return_value=lambda f: f, create=True):
            set_client_window(201)
            api.ShowWindow.assert_called_once_with(200, 0)
            set_client_window(101, "Human: press Enter in PowerShell")
            api.SetWindowTextW.assert_called_once_with(100, "Human: press Enter in PowerShell")
            visible[200] = True
            api.ShowWindow.side_effect = None
            with self.assertRaisesRegex(RuntimeError, "Could not hide"):
                set_client_window(201)

    def test_policy_side_is_separate_from_view(self):
        args = arguments(["duel", "--policy-slot", "1", "--view-slot", "0", "--opponent", "hold"])
        self.assertEqual(args.policy_slot, 1)
        self.assertEqual(args.view_slot, 0)

    def test_policy_cadence_is_fixed_at_four(self):
        for cadence in (0, 1, 8, -4):
            with self.subTest(cadence=cadence), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                arguments(["duel", "--step-mul", str(cadence)])
        self.assertEqual(arguments(["human"]).step_mul, 4)

    def test_arena_binding_uses_map_content_instead_of_filename(self):
        rules = {"map_file": "official/ThreeSoldiersRing.SC2Map", "center": [16, 16]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            official = root / rules["map_file"]
            official.parent.mkdir()
            official.write_bytes(b"official-map-content")
            same_name = root / "ThreeSoldiersRing.SC2Map"
            same_name.write_bytes(b"unrelated-map-content")
            renamed = root / "renamed.SC2Map"
            renamed.write_bytes(official.read_bytes())
            self.assertIsNone(arena_for_map(rules, root / "rules.json", sha256(same_name.read_bytes())))
            self.assertEqual(arena_for_map(rules, root / "rules.json", sha256(renamed.read_bytes())), rules)

    def test_map_hash_uses_bytes_sent_to_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ThreeSoldiersRing.SC2Map"
            path.write_bytes(b"loaded-map-content")
            arena_map = load_map(str(path))
            loaded = arena_map.data(None)
            path.write_bytes(b"changed-after-loading")
            self.assertEqual(arena_map.data(None), loaded)

    def test_replay_overlay_requires_both_replay_and_map_binding(self):
        replay = b"replay-fixture"
        manifest = {"replay_sha256": sha256(replay), "map_sha256": sha256(b"map"),
                    "arena_map_sha256": sha256(b"map"), "arena": {"center": [16, 16]}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "game.SC2Replay"
            metadata = path.with_name("manifest.json")
            metadata.write_text(json.dumps(manifest))
            self.assertEqual(arena_for_replay(path, replay), manifest["arena"])
            self.assertIsNone(arena_for_replay(path, b"different-replay"))
            manifest["arena_map_sha256"] = sha256(b"different-map")
            metadata.write_text(json.dumps(manifest))
            self.assertIsNone(arena_for_replay(path, replay))
            del manifest["arena_map_sha256"]
            metadata.write_text(json.dumps(manifest))
            self.assertIsNone(arena_for_replay(path, replay))

    def test_watch_supplies_verified_map_bytes_and_rejects_changed_map(self):
        replay, map_data = b"replay-fixture", b"recorded-map-fixture"
        requests, launches = [], []
        info = sc_pb.ResponseReplayInfo(base_build=75689, data_version="recorded-data")
        info.player_info.add().player_info.player_id = 42
        controller = SimpleNamespace(replay_info=lambda data: info,
            ping=lambda: sc_pb.ResponsePing(base_build=75689, data_version="recorded-data"),
            start_replay=requests.append)

        def start(**kwargs):
            launches.append(kwargs)
            return nullcontext(controller)

        view = SimpleNamespace(run=lambda *a, **kw: None, close=lambda: None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay_path, map_path = root / "game.SC2Replay", root / "renamed.SC2Map"
            replay_path.write_bytes(replay)
            map_path.write_bytes(map_data)
            manifest = {"replay_sha256": sha256(replay), "map_sha256": sha256(map_data),
                        "arena_map_sha256": sha256(map_data), "arena": {"map_file": map_path.name}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            args = arguments(["watch", "--replay", str(replay_path), "--map", str(map_path), "--rgb"])
            with patch("pysc2.run_configs.get", return_value=SimpleNamespace(start=start)), \
                    patch("sc2.arena_env.ArenaRenderer", return_value=view):
                self.assertEqual(watch(args), 0)
                self.assertEqual(requests[0].map_data, map_data)
                self.assertEqual(requests[0].replay_data, replay)
                self.assertEqual(requests[0].observed_player_id, 42)
                self.assertFalse(requests[0].disable_fog)
                ended = observation()
                ended.player_result.add(player_id=42, result=sc_pb.Victory)
                controller.observe = Mock(side_effect=[observation(), ended])
                controller.step = Mock()
                args.native_ui, args.rgb, args.windowed = True, False, True
                with patch("sc2.windows_env.window_options", return_value={}) as options, \
                        patch("sc2.arena_env.ArenaRenderer", side_effect=AssertionError("No PyGame UI")), \
                        patch("sc2.run.time.sleep"):
                    self.assertEqual(watch(args), 0)
                options.assert_called_once_with(True, windowed=True)
                controller.step.assert_called_once_with(1)
                self.assertFalse(requests[1].options.HasField("render"))
                self.assertFalse(requests[1].options.HasField("feature_layer"))
                map_path.write_bytes(b"same-name-different-map")
                with self.assertRaisesRegex(ValueError, "map SHA256"):
                    watch(args)
                self.assertEqual(len(launches), 2)  # Reject before starting another engine.
                self.assertEqual(len(requests), 2)

    def test_watch_resolves_archived_maps_and_keeps_explicit_paths_and_hash_checks(self):
        replay, map_data = b"replay-fixture", b"recorded-map-fixture"
        requests = []
        info = sc_pb.ResponseReplayInfo(base_build=75689, data_version="recorded-data")
        info.player_info.add().player_info.player_id = 42
        controller = SimpleNamespace(replay_info=lambda data: info,
            ping=lambda: sc_pb.ResponsePing(base_build=75689, data_version="recorded-data"),
            start_replay=requests.append)
        start = Mock(side_effect=lambda **kwargs: nullcontext(controller))
        view = SimpleNamespace(run=lambda *a, **kw: None, close=lambda: None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay_path = root / "game.SC2Replay"
            replay_path.write_bytes(replay)
            map_path = root / "maps/old.SC2Map"
            map_path.parent.mkdir()
            map_path.write_bytes(map_data)
            manifest = {"replay_sha256": sha256(replay), "map_sha256": sha256(map_data),
                        "arena_map_sha256": sha256(map_data), "arena": {"map_file": "maps/old.SC2Map"}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            args = arguments(["watch", "--replay", str(replay_path), "--rules", str(root / "rules.json")])
            with patch("pysc2.run_configs.get", return_value=SimpleNamespace(start=start)), \
                    patch("sc2.arena_env.ArenaRenderer", return_value=view):
                self.assertEqual(watch(args), 0)
                archived = root / "legacy/maps/old.SC2Map"
                archived.parent.mkdir(parents=True)
                map_path.rename(archived)
                self.assertEqual(watch(args), 0)
                self.assertEqual([r.map_data for r in requests], [map_data, map_data])
                args.map = str(map_path)
                with self.assertRaises(FileNotFoundError):
                    watch(args)
                args.map = None
                archived.write_bytes(b"tampered-map")
                with self.assertRaisesRegex(ValueError, "map SHA256"):
                    watch(args)
                self.assertEqual(start.call_count, 2)

    def test_replay_map_bytes_reject_manifest_for_a_different_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay_path, map_path = root / "game.SC2Replay", root / "map.SC2Map"
            map_path.write_bytes(b"map")
            (root / "manifest.json").write_text(json.dumps(
                {"replay_sha256": sha256(b"original"), "map_sha256": sha256(b"map")}))
            with self.assertRaisesRegex(ValueError, "recorded manifest"):
                replay_map_data(replay_path, b"different-replay", map_path)

    def test_hud_uses_map_clock_and_exact_phase_boundary(self):
        rules = {"clock_source": "observation.score.score", "duration_seconds": 300,
                 "radii": [14, 11, 8], "phase_start_seconds": [0, 60, 120]}
        obs = observation()
        self.assertEqual(arena_hud(rules, obs), "Arena clock unavailable")
        self.assertEqual(arena_hud(dict(rules, ready_game_loops=112), obs), "GET READY | 5s")
        obs.observation.score.score = 60
        self.assertEqual(arena_hud(rules, obs), "Remaining 240s | radius 11 | shrink in 60s")
        obs.observation.score.score = 300
        self.assertEqual(arena_hud(rules, obs), "Remaining 0s | radius 8 | final circle")

    def test_ring_projection_tracks_camera_zoom_and_aspect_ratio(self):
        from pysc2.lib import point, transform
        for scale, camera in [(point.Point(2, -3), point.Point(16, 16)),
                              (point.Point(4, -6), point.Point(20, 12))]:
            projection = transform.Linear(scale, point.Point(320, 240) - camera * scale)
            points = projected_ring((16, 16), 14, projection)
            for pixel in points:
                world = projection.back_pt(pixel)
                self.assertAlmostEqual(math.hypot(world.x - 16, world.y - 16), 14)
            self.assertEqual(points[0], projection.fwd_pt(point.Point(30, 16)))
            self.assertAlmostEqual(points[24].y, projection.fwd_pt(point.Point(16, 30)).y)

    def test_all_circles_and_active_boundary_are_drawn_on_minimap(self):
        import pygame
        from pysc2.lib import point, renderer_human, transform
        obs = observation()
        obs.observation.score.score = 60
        rules = {"clock_source": "observation.score.score", "center": [16, 16],
                 "radii": [14, 11, 8, 5, 2], "phase_start_seconds": [0, 60, 120, 180, 240]}
        view = SimpleNamespace(arena=rules, _obs=obs)
        surf = SimpleNamespace(surf=pygame.Surface((80, 80)),
                   surf_type=renderer_human.SurfType.RGB | renderer_human.SurfType.MINIMAP,
                   world_to_surf=transform.Linear(2, point.Point(8, 8)))
        ArenaRenderer.draw_rings(view, surf)
        for index, radius in enumerate(rules["radii"]):
            expected = (255, 40, 40) if index <= 1 else (80, 220, 110)
            self.assertEqual(tuple(surf.surf.get_at((40 + radius * 2, 40)))[:3], expected)

    def test_duel_routes_policy_to_requested_participant_and_records_real_ids(self):
        called = []
        lifecycle = []
        first, second = observation(), observation()
        first.observation.game_loop = second.observation.game_loop = 0
        second.observation.player_common.player_id = 42
        terminal = []
        for original in (first, second):
            obs = sc_pb.ResponseObservation()
            obs.CopyFrom(original)
            obs.observation.game_loop = 8
            obs.player_result.add(player_id=7, result=sc_pb.Defeat)
            obs.player_result.add(player_id=42, result=sc_pb.Victory)
            terminal.append(obs)

        def steps(observations, last=False):
            return [SimpleNamespace(observation=o, last=lambda: last) for o in observations]

        class FakeEnv:
            def __init__(self, **kwargs):
                lifecycle.append("launch")
                self.options = kwargs
                self.loop = 3 if kwargs["realtime"] else 0

            def engine_identity(self):
                return [{"base_build": 75689}] * 2

            def map_bytes(self):
                return b"map-test-fixture"

            def unit_data(self, unit_types):
                return {}

            def reset(self):
                if self.options["realtime"]:
                    updates = []
                    for original in (first, second):
                        obs = sc_pb.ResponseObservation()
                        obs.CopyFrom(original)
                        obs.observation.game_loop = self.loop
                        updates.append(obs)
                    return steps(updates)
                return steps([first, second])

            def step(self, actions):
                self.loop += self.options["step_mul"]
                if self.options["realtime"] and self.loop == 7:
                    self.loop = 8  # Real-time observations can arrive beyond their target.
                if ((self.options["step_mul"] == 1 and self.loop < 6)
                        or (self.options["realtime"] and self.loop < 12)):
                    updates = []
                    for original in (first, second):
                        obs = sc_pb.ResponseObservation()
                        obs.CopyFrom(original)
                        obs.observation.game_loop = self.loop
                        updates.append(obs)
                    return steps(updates)
                if self.options["realtime"]:
                    updates = []
                    for original in terminal:
                        obs = sc_pb.ResponseObservation()
                        obs.CopyFrom(original)
                        obs.observation.game_loop = self.loop
                        updates.append(obs)
                    return steps(updates, last=True)
                return steps(terminal, last=True)

            def start_view(self, slot, arena):
                return SimpleNamespace(render=lambda obs: None, close=lambda: None, awaiting_start=False)

            def view_actions(self, view, slot):
                from pysc2.lib.renderer_human import ActionCmd
                return ActionCmd.STEP

            def replay_bytes(self):
                return b"replay-test-fixture"

            def close(self):
                pass

            def set_human_title(self, title):
                pass

        def decide(obs, memory, *, arena):
            called.append(obs.observation.player_common.player_id)
            return [], memory, {"test": True}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "game"
            args = arguments(["duel", "--policy-slot", "1", "--view-slot", "0",
                              "--opponent", "hold", "--output", str(output)])
            with patch("sc2.arena_env.ArenaEnv", FakeEnv), patch("sc2.windows_env.WindowsArenaEnv", FakeEnv), patch("sc2.agent.decide", decide), \
                    patch("sc2.run.replay_speed", return_value=4):
                self.assertEqual(play(args), 0)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(called, [42])
            self.assertEqual(manifest["participant_player_ids"], [7, 42])
            self.assertEqual(manifest["player_results"], {"7": sc_pb.Defeat, "42": sc_pb.Victory})
            self.assertEqual(manifest["game_speed"], 4)
            self.assertEqual(manifest["policy_slot"], 1)
            self.assertEqual(manifest["actual_policy_slots"], [1])
            called.clear()
            args = arguments(["human", "--view-slot", "0", "--step-mul", "4",
                              "--output", str(Path(temporary) / "human")])
            with patch("sc2.arena_env.ArenaEnv", FakeEnv), patch("sc2.windows_env.WindowsArenaEnv", FakeEnv), patch("sc2.agent.decide", decide), \
                    patch("sc2.run.replay_speed", return_value=4), patch("sc2.run.time.sleep"):
                self.assertEqual(play(args), 0)
            self.assertEqual(called, [42, 42])  # Game loops 0 and 4; UI advances every loop.
            human_manifest = json.loads((Path(temporary) / "human" / "manifest.json").read_text())
            self.assertEqual(human_manifest["human_slot"], 0)
            self.assertEqual(human_manifest["policy_slot"], 1)
            self.assertEqual(human_manifest["actual_policy_slots"], [1])
            self.assertEqual(human_manifest["step_mul"], 4)
            self.assertEqual(human_manifest["environment_step_mul"], 1)
            called.clear()
            lifecycle.clear()
            with patch("sc2.run.sys.platform", "win32"):
                args = arguments(["human", "--native-ui", "--start-paused", "--windowed",
                                  "--output", str(Path(temporary) / "native")])
                with patch("sc2.windows_env.WindowsArenaEnv", side_effect=FakeEnv) as native_env, \
                        patch.object(FakeEnv, "start_view", side_effect=AssertionError("No PyGame UI")), \
                        patch("sc2.agent.decide", decide), patch("sc2.run.replay_speed", return_value=4), \
                        patch("sc2.run.time.sleep") as native_sleep, \
                        patch("builtins.input", side_effect=lambda: lifecycle.append("input")) as gate:
                    self.assertEqual(play(args), 0)
                self.assertTrue(native_env.call_args.kwargs["windowed"])
                gate.assert_called_once()
                self.assertEqual(lifecycle, ["input", "launch"])
                native_sleep.assert_called_once_with(5)
            native_manifest = json.loads((Path(temporary) / "native" / "manifest.json").read_text())
            self.assertEqual(called, [42, 42])
            self.assertEqual(native_manifest["human_interface"], "native_windows")
            self.assertTrue(native_manifest["realtime"])
            self.assertEqual(native_manifest["environment_step_mul"], 4)
            self.assertIsNone(native_manifest["pacing_game_loops_per_wall_second"])
            self.assertFalse(native_manifest["interface_options"][0]["raw_affects_selection"])
            self.assertNotIn("render", native_manifest["interface_options"][0])
            called.clear()
            args = arguments(["duel", "--no-window", "--output", str(Path(temporary) / "mirror")])
            with patch("sc2.arena_env.ArenaEnv", FakeEnv), patch("sc2.windows_env.WindowsArenaEnv", FakeEnv), patch("sc2.agent.decide", decide), \
                    patch("sc2.run.replay_speed", return_value=4):
                self.assertEqual(play(args), 0)
            mirror_manifest = json.loads((Path(temporary) / "mirror" / "manifest.json").read_text())
            self.assertEqual(called, [7, 42])
            self.assertIsNone(mirror_manifest["policy_slot"])
            self.assertEqual(mirror_manifest["actual_policy_slots"], [0, 1])
            for name in ("a", "b", "a2", "a3", "a4"):
                called.clear()
                selected = load_policy(name)
                output = Path(temporary) / ("selected-" + name)
                args = arguments(["human", "--ai", name, "--output", str(output)])
                with patch("sc2.arena_env.ArenaEnv", FakeEnv), patch("sc2.windows_env.WindowsArenaEnv", FakeEnv), \
                        patch.object(selected, "decide", decide), \
                        patch("sc2.agent.decide", side_effect=AssertionError("Wrong AI selected")), \
                        patch("sc2.run.replay_speed", return_value=4), patch("sc2.run.time.sleep"):
                    self.assertEqual(play(args), 0)
                self.assertEqual(called, [42, 42])
                selected_manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(selected_manifest["policy_name"], name)
                self.assertEqual(selected_manifest["policy_sha256"], sha256(Path(selected.__file__).read_bytes()))



if __name__ == "__main__":
    unittest.main()

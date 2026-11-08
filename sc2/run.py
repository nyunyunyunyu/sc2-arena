"""Run: python -m sc2.run {duel,human,watch} --help."""
import argparse
import importlib.metadata
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .record import GameRecord, actual_results, proto_dict, replay_speed, sha256
from .policies import AI_CHOICES, load_policy


def save_rgb_frame(obs, path):
    import pygame
    frame = obs.observation.render_data.map
    if not frame.data or frame.bits_per_pixel != 24:
        raise ValueError("SC2 did not return the requested 24-bit RGB frame")
    # Windows 5.0.17 returns BGR bytes; checked against the native client capture.
    surface = pygame.image.frombuffer(frame.data, (frame.size.x, frame.size.y),
                                     "BGR" if sys.platform == "win32" else "RGB")
    pygame.image.save(surface, str(path))


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("duel", "human", "watch"))
    parser.add_argument("--map", help="PySC2 map name or .SC2Map path; defaults to rules.json map")
    parser.add_argument("--rules", type=Path, default=Path(__file__).with_name("rules.json"))
    parser.add_argument("--version", default="4.10.0")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--step-mul", type=int, choices=(4,), default=4,
                        help="Fixed policy decision cadence in game loops")
    parser.add_argument("--max-game-loops", type=int, default=12000,
                        help="Watchdog only; expiry is unknown, never a win")
    parser.add_argument("--view-slot", type=int, choices=(0, 1), default=0,
                        help="Participant index, not a hard-coded SC2 player id")
    parser.add_argument("--policy-slot", type=int, choices=(0, 1), default=0,
                        help="Policy side when duel uses the explicit hold baseline")
    parser.add_argument("--rgb", action="store_true", help="Request RGB as well as feature view")
    parser.add_argument("--native-ui", action="store_true",
                        help="Use the Windows SC2 client for human input or replay viewing")
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--windowed", action="store_true", help="Use a window instead of native full screen")
    display.add_argument("--no-window", action="store_true",
                         help="AI duel only; Windows hides clients, Linux runs headless; duel already defaults to this")
    parser.add_argument("--start-paused", action="store_true",
                        help="Wait for Enter before native client launch, or Pause/Esc in PyGame")
    parser.add_argument("--ai", type=str.lower, choices=AI_CHOICES, default="default",
                        help="AI opponent in human mode; both policy slots in duel (a: finals champion, b: finalist, a2: human-match revision, a3: ranged endgame revision)")
    parser.add_argument("--opponent", choices=("policy", "hold"), default="policy")
    parser.add_argument("--output", type=Path, help="New evidence directory; never overwritten")
    parser.add_argument("--replay", type=Path, help="Replay for watch mode")
    args = parser.parse_args(argv)
    if args.no_window and (args.mode != "duel" or args.rgb):
        parser.error("--no-window requires duel without --rgb")
    if args.windowed and not args.native_ui:
        parser.error("--windowed requires --native-ui (Windows human or watch)")
    if args.max_game_loops <= 0:
        parser.error("max-game-loops must be positive")
    if args.mode == "watch" and not args.replay:
        parser.error("watch requires --replay")
    if args.mode == "human" and args.opponent != "policy":
        parser.error("human always plays against the policy; hold is a duel baseline")
    if args.start_paused and args.mode != "human":
        parser.error("start-paused is only available in human mode")
    if args.native_ui and (sys.platform != "win32" or args.mode == "duel"):
        parser.error("native-ui requires Windows human or watch mode")
    if args.native_ui and args.rgb:
        parser.error("native-ui renders in SC2 itself; omit --rgb")
    return args


def arena_for_map(rules, rules_path, map_sha256):
    official_map = rules_path.parent / rules["map_file"]
    if official_map.is_file() and sha256(official_map.read_bytes()) == map_sha256:
        return rules
    return None


def arena_for_replay(replay_path, replay_data):
    manifest_path = replay_path.with_name("manifest.json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("replay_sha256") == sha256(replay_data)
                and manifest.get("arena_map_sha256")
                and manifest["arena_map_sha256"] == manifest.get("map_sha256")):
            return manifest.get("arena")
    return None


def replay_map_data(replay_path, replay_data, map_path):
    manifest = json.loads(replay_path.with_name("manifest.json").read_text(encoding="utf-8"))
    if manifest.get("replay_sha256") != sha256(replay_data):
        raise ValueError("Replay does not match its recorded manifest")
    map_data = map_path.read_bytes()
    if manifest.get("map_sha256") != sha256(map_data):
        raise ValueError("Replay map bytes do not match the recorded map SHA256")
    return map_data


def watch(args):
    from pysc2 import run_configs
    from s2clientprotocol import sc2api_pb2 as sc_pb
    from .arena_env import ArenaRenderer, interface_options
    config = run_configs.get(version=args.version)
    replay_data = args.replay.read_bytes()
    arena = arena_for_replay(args.replay, replay_data)
    map_data = None
    if arena is not None:
        map_path = Path(args.map) if args.map else args.rules.parent / arena["map_file"]
        map_data = replay_map_data(args.replay, replay_data, map_path)
    elif args.map:
        raise ValueError("An explicit replay map requires a matching recorded arena manifest")
    launch_options = {}
    if args.native_ui:
        from .windows_env import window_options
        launch_options = window_options(True, windowed=args.windowed)
    with config.start(want_rgb=args.rgb, **launch_options) as controller:
        info = controller.replay_info(replay_data)
        identity = controller.ping()
        if info.base_build != identity.base_build or info.data_version != identity.data_version:
            raise ValueError("Replay engine build/data version does not match the selected engine")
        players = info.player_info
        if args.view_slot >= len(players):
            raise ValueError("Replay does not contain the selected player")
        request = sc_pb.RequestStartReplay(replay_data=replay_data, disable_fog=False,
                    observed_player_id=players[args.view_slot].player_info.player_id,
                    options=interface_options(visual=not args.native_ui, rgb=args.rgb))
        if map_data is not None:
            request.map_data = map_data
        controller.start_replay(request)
        if args.native_ui:
            while not controller.observe().player_result:
                started = time.monotonic()
                controller.step(1)
                time.sleep(max(0, 1 / 22.4 - (time.monotonic() - started)))
            return 0
        view = ArenaRenderer(arena, replay=True, render_feature_grid=False)
        try:
            view.run(config, controller, max_episodes=1)
        finally:
            view.close()
    return 0


def play(args):
    from pysc2.env import sc2_env
    from pysc2.lib import renderer_human
    policy = load_policy(args.ai)
    from .arena_env import ArenaEnv, interface_options, load_map, validate_actions
    rules = json.loads(args.rules.read_text(encoding="utf-8-sig"))
    chosen_map = args.map or str(args.rules.parent / rules["map_file"])
    arena = None
    map_instance = load_map(chosen_map)
    human = args.mode == "human"
    realtime = human and args.native_ui
    visual = human and not args.native_ui
    environment_step = 1 if visual else args.step_mul
    human_slot = args.view_slot if args.mode == "human" else None
    if human_slot is not None:
        actual_policy_slots = [1 - human_slot]
    elif args.opponent == "hold":
        actual_policy_slots = [args.policy_slot]
    else:
        actual_policy_slots = [0, 1]
    interfaces = [interface_options(visual=visual and i == args.view_slot,
                                    rgb=args.rgb and i == args.view_slot) for i in range(2)]
    if args.native_ui:
        interfaces[human_slot].raw_affects_selection = False
    output = args.output or Path("sc2/runs") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    source = Path(policy.__file__).read_bytes()
    record = GameRecord(output, dict(mode=args.mode, requested_version=args.version,
                         seed=args.seed, step_mul=args.step_mul, realtime=realtime,
                         environment_step_mul=environment_step,
                         pacing_game_loops_per_wall_second=22.4 if visual else None,
                         selected_map=chosen_map, policy_name=args.ai, policy_sha256=sha256(source),
                         rules_sha256=sha256(args.rules.read_bytes()), arena=arena,
                         opponent=args.opponent, human_slot=human_slot,
                         policy_slot=actual_policy_slots[0] if len(actual_policy_slots) == 1 else None,
                         actual_policy_slots=actual_policy_slots, view_slot=args.view_slot,
                         human_interface=("native_windows" if args.native_ui else "pysc2") if human else None,
                         source="real_engine", game_speed=None,
                         rgb_backend="osmesa" if args.rgb and sys.platform == "linux" else None,
                         pysc2_version=importlib.metadata.version("pysc2"),
                         interface_options=[proto_dict(x) for x in interfaces]))
    print(f"AI: {args.ai}", flush=True)
    env = view = None
    observations = []
    status, failure = "unknown", None
    try:
        if realtime and args.start_paused:
            print("准备好后在此按 Enter。随后切到『三兵缩圈 — 玩家窗口』；地图加载完成后自动开局。", flush=True)
            input()
        environment_class = ArenaEnv
        environment_options = {}
        if sys.platform == "win32":
            from .windows_env import WindowsArenaEnv
            environment_class = WindowsArenaEnv
            environment_options["human_slot"] = human_slot if args.native_ui else None
            environment_options["windowed"] = args.windowed
        env = environment_class(map_name=map_instance,
             players=[sc2_env.Agent(sc2_env.Race.terran, "human" if i == human_slot else
                          "hold" if args.opponent == "hold" and i != args.policy_slot else "ai-%s-%d" % (args.ai, i))
                      for i in range(2)],
             agent_interface_format=interfaces, disable_fog=False, visualize=False,
             realtime=realtime, step_mul=environment_step, random_seed=args.seed,
             game_steps_per_episode=args.max_game_loops, score_index=-1, version=args.version,
             **environment_options)
        map_sha256 = sha256(env.map_bytes())
        arena = arena_for_map(rules, args.rules, map_sha256)
        record.manifest.update(engine=env.engine_identity(), map_sha256=map_sha256,
                               arena_map_sha256=map_sha256 if arena is not None else None)
        unit_data = env.unit_data(rules["unit_type_ids"])
        unit_bytes = json.dumps(unit_data, sort_keys=True).encode("utf-8")
        (record.path / "unit_data.json").write_bytes(unit_bytes)
        record.manifest["unit_data_sha256"] = sha256(unit_bytes)
        if arena is not None:
            arena = dict(arena, unit_data=unit_data)
            record.manifest["arena"] = arena
        record.write_manifest()
        timesteps = env.reset()
        record.manifest["participant_player_ids"] = [t.observation.observation.player_common.player_id
                                                     for t in timesteps]
        record.write_manifest()
        if args.rgb:
            save_rgb_frame(timesteps[args.view_slot].observation, record.path / "rgb-first.png")
        memories = [{}, {}]
        next_policy_loop = [0, 0]
        if visual:
            view = env.start_view(args.view_slot, arena)
            view.awaiting_start = args.start_paused
        while True:
            frame_started = time.monotonic()
            observations = [t.observation for t in timesteps]
            for obs in observations:
                record.observe(obs)
            results = actual_results(observations)
            if results:
                ids = record.manifest["participant_player_ids"]
                status = "complete" if all(results.get(i) in (1, 2, 3) for i in ids) else "incomplete_result"
                break
            if any(t.last() for t in timesteps):
                status = "watchdog"
                break
            if view:
                view.render(observations[args.view_slot])
                if view.awaiting_start:
                    import pygame
                    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_PAUSE))
                command = env.view_actions(view, args.view_slot)
                view.awaiting_start = False
                if command != renderer_human.ActionCmd.STEP:
                    status = "user_quit" if command == renderer_human.ActionCmd.QUIT else "user_restart"
                    break
            actions = []
            for slot, obs in enumerate(observations):
                if slot == human_slot:
                    issued, reason = [], {"mode": "human", "input": record.manifest["human_interface"]}
                elif args.opponent == "hold" and slot != args.policy_slot:
                    issued, reason = [], {"mode": "explicit_hold_baseline"}
                elif human and obs.observation.game_loop < next_policy_loop[slot]:
                    issued, reason = [], {"mode": "policy_cadence_wait"}
                else:
                    issued, memories[slot], reason = policy.decide(obs, memories[slot], arena=arena)
                    next_policy_loop[slot] = obs.observation.game_loop + args.step_mul
                validate_actions(obs, issued)
                record.decision(obs, issued, reason)
                actions.append(issued)
            timesteps = env.step(actions)
            if visual:
                time.sleep(max(0, environment_step / 22.4 - (time.monotonic() - frame_started)))
    except KeyboardInterrupt:
        status = "interrupted"
    except Exception as exc:
        status, failure = "error", repr(exc)
        record.event("error", error=failure)
        raise
    finally:
        if env:
            if args.rgb and observations:
                try:
                    save_rgb_frame(observations[args.view_slot], record.path / "rgb-last.png")
                except Exception as exc:
                    record.manifest["rgb_error"] = repr(exc)
            try:
                replay = env.replay_bytes()
                (record.path / "game.SC2Replay").write_bytes(replay)
                record.manifest["replay_sha256"] = sha256(replay)
                record.manifest["game_speed"] = replay_speed(replay, record.manifest["engine"][0]["base_build"])
                record.manifest["game_speed_source"] = "replay.details.m_gameSpeed"
            except Exception as exc:
                record.manifest["replay_error"] = repr(exc)
            try:
                if view:
                    view.close()
                if realtime and status == "complete":
                    time.sleep(rules.get("end_hold_seconds", 5))
                env.close()
            except Exception as exc:
                record.manifest["close_error"] = repr(exc)
        record.finish(status, observations, error=failure)
        print(record.path / "manifest.json")
    return 0 if status == "complete" else 3 if status == "user_restart" else 2


def main(argv=None):
    args = arguments(argv)
    from absl import flags
    # PySC2's library flags still need initialization when argparse owns our CLI.
    if not flags.FLAGS.is_parsed():
        flags.FLAGS([sys.argv[0]])
    if args.rgb and sys.platform == "linux":
        from pysc2.run_configs import platforms
        # WSL's detected EGL library has no usable display; setup verifies OSMesa.
        platforms.Linux.known_gl_libs = [("-osmesapath", "libOSMesa.so.8")]
    if args.mode == "watch":
        return watch(args)
    output_root = args.output
    while True:
        result = play(args)
        if result != 3:
            return result
        if output_root:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            args.output = output_root.with_name(output_root.name + "-restart-" + stamp)


if __name__ == "__main__":
    raise SystemExit(main())

"""Short offscreen world-marker and native-movement diagnostic, without revealing fog."""
import argparse
import json
from pathlib import Path

from absl import flags
import numpy as np
import pygame
from pysc2.env import sc2_env
from pysc2.run_configs import platforms
from s2clientprotocol import debug_pb2 as debug_pb
from s2clientprotocol import sc2api_pb2 as sc_pb

from sc2.arena_env import ArenaEnv, interface_options, load_map
from sc2.record import actual_results, proto_dict, sha256
from sc2.maps.probe import move, own_units


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rings", action="store_true", help="Hold through first shrink then diagnose native end")
    parser.add_argument("--width", type=int, default=1024, help="Actual engine RGB width (4:3)")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if not flags.FLAGS.is_parsed():
        flags.FLAGS(["render_probe"])
    platforms.Linux.known_gl_libs = [("-osmesapath", "libOSMesa.so.8")]
    rules_path = Path(__file__).parents[1] / "rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8-sig"))
    map_path = rules_path.parent / rules["map_file"]
    options = interface_options(rgb=True)
    options.render.resolution.x = args.width
    options.render.resolution.y = args.width * 3 // 4
    rows = []
    with ArenaEnv(map_name=load_map(str(map_path)),
         players=[sc2_env.Agent(sc2_env.Race.terran, "visual-probe-1"),
                  sc2_env.Agent(sc2_env.Race.terran, "visual-probe-2")],
         agent_interface_format=[options, interface_options()], disable_fog=False,
         visualize=False, realtime=False, step_mul=16, random_seed=1,
         game_steps_per_episode=2000, score_index=-1, version="4.10.0") as env:
        steps = env.reset()
        map_geometry = proto_dict(env.game_info[0].start_raw)
        initial_camera = proto_dict(steps[0].observation.observation.raw_data.player.camera)
        data = env.unit_data([*rules["unit_type_ids"], 317])
        camera = sc_pb.Action()
        camera.action_raw.camera_move.center_world_space.x = 16
        camera.action_raw.camera_move.center_world_space.y = 16
        steps = env.step([[camera], []])
        count = 61 if args.rings else 4
        for i in range(count):
            response = steps[0].observation
            obs = response.observation
            if args.rings and i not in (0, 58, 59, 60):
                steps = env.step([[], []])
                continue
            frame = obs.render_data.map
            rgb = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.size.y, frame.size.x, 3)
            pygame.image.save(pygame.surfarray.make_surface(rgb.swapaxes(0, 1)),
                              str(args.output / ("rgb-%03d.png" % obs.game_loop)))
            (args.output / ("obs-%03d.pb" % obs.game_loop)).write_bytes(response.SerializeToString())
            rows.append(dict(loop=obs.game_loop, seconds=obs.score.score,
                             camera=proto_dict(obs.raw_data.player.camera),
                             units=[dict(tag=u.tag, unit_type=u.unit_type, x=u.pos.x, y=u.pos.y,
                                         health=u.health) for u in own_units(response)],
                             neutral_units=[dict(unit_type=u.unit_type, x=u.pos.x, y=u.pos.y,
                                                 radius=u.radius, display_type=u.display_type)
                                            for u in obs.raw_data.units if u.alliance == 3],
                             visible_enemy_count=sum(u.alliance == 4 and u.display_type == 1
                                                     for u in obs.raw_data.units)))
            actions = [move(u.tag, 20, u.pos.y) for u in own_units(response)] if i == 0 and not args.rings else []
            if i < count - 1:
                steps = env.step([actions, []])
        diagnostic = None
        results = {}
        if args.rings:
            assert all(len(row["units"]) == 3 and len(row["neutral_units"]) == 280
                       and row["visible_enemy_count"] == 0 for row in rows)
            assert all(u["radius"] == 0 for row in rows for u in row["neutral_units"])
            assert all(len(own_units(step.observation)) == 3 for step in steps)
            diagnostic = debug_pb.DebugCommand(kill_unit=debug_pb.DebugKillUnit(
                tag=[u.tag for u in own_units(steps[0].observation)]))
            env._controllers[0].debug([diagnostic])
            steps = env.step([[], []])
            results = actual_results([step.observation for step in steps])
            assert results == {1: 2, 2: 1}, results
        result = dict(purpose="map_probe", map_sha256=sha256(map_path.read_bytes()),
                      engine=env.engine_identity(), disable_fog=False, rows=rows, unit_data=data,
                      map_size=map_geometry.get("map_size"),
                      playable_area=map_geometry.get("playable_area"), initial_camera=initial_camera,
                      diagnostic_command=proto_dict(diagnostic) if diagnostic else None,
                      player_results=results)
        (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (args.output / "game.SC2Replay").write_bytes(env.replay_bytes())
        print(json.dumps({"output": str(args.output), "map_sha256": result["map_sha256"],
                          "sample_seconds": [r["seconds"] for r in rows],
                          "player_results": results}))


if __name__ == "__main__":
    main()

"""Verify Linux SC2 state and RGB without creating a display window."""

import argparse
import hashlib
import json
from pathlib import Path

from absl import flags
import numpy as np
import pygame
from pysc2 import maps, run_configs
from pysc2.run_configs import platforms
from s2clientprotocol import common_pb2 as common_pb
from s2clientprotocol import raw_pb2 as raw_pb
from s2clientprotocol import sc2api_pb2 as sc_pb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--osmesa", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    flags.FLAGS(["bootstrap"])
    if args.osmesa:
        platforms.Linux.known_gl_libs = [("-osmesapath", "libOSMesa.so.8")]

    run_config = run_configs.get(version="4.10")
    arena_map = maps.get("MoveToBeacon")
    map_data = arena_map.data(run_config)
    interface = sc_pb.InterfaceOptions(raw=True)
    interface.render.resolution.x = 256
    interface.render.resolution.y = 256
    interface.render.minimap_resolution.x = 128
    interface.render.minimap_resolution.y = 128
    with run_config.start(want_rgb=True) as controller:
        ping = controller.ping()
        request = sc_pb.RequestCreateGame(
            local_map=sc_pb.LocalMap(map_path=arena_map.path, map_data=map_data),
            realtime=False,
            disable_fog=False,
            random_seed=1,
        )
        request.player_setup.add(type=sc_pb.Participant)
        controller.create_game(request)
        joined = controller.join_game(sc_pb.RequestJoinGame(
            race=common_pb.Terran, options=interface
        ))
        before = controller.observe()
        own = next(unit for unit in before.observation.raw_data.units
                   if unit.owner == joined.player_id)
        start = [own.pos.x, own.pos.y]
        command = sc_pb.Action(action_raw=raw_pb.ActionRaw(
            unit_command=raw_pb.ActionRawUnitCommand(
                ability_id=16, unit_tags=[own.tag], queue_command=False,
                target_world_space_pos=common_pb.Point2D(
                    x=own.pos.x + 2, y=own.pos.y
                ),
            )
        ))
        action_response = controller.actions(sc_pb.RequestAction(actions=[command]))
        assert list(action_response.result) == [1], action_response.result
        controller.step(32)
        after = controller.observe()
        moved = next(unit for unit in after.observation.raw_data.units
                     if unit.tag == own.tag)
        frame = after.observation.render_data.map
        assert frame.bits_per_pixel == 24, frame.bits_per_pixel
        rgb = np.frombuffer(frame.data, dtype=np.uint8).reshape(
            frame.size.y, frame.size.x, 3
        )
        assert rgb.shape == (256, 256, 3), rgb.shape
        assert int(rgb.max()) > int(rgb.min()), "RGB is a flat image"
        end = [moved.pos.x, moved.pos.y]
        assert np.linalg.norm(np.array(end) - np.array(start)) > 0.1, (start, end)
        assert after.observation.game_loop > before.observation.game_loop
        assert not after.action_errors, after.action_errors
        pygame.image.save(pygame.surfarray.make_surface(rgb.swapaxes(0, 1)),
                          str(args.out / "rgb.png"))
        replay = controller.save_replay()
        (args.out / "bootstrap.SC2Replay").write_bytes(replay)
        result = {
            "game_version": ping.game_version,
            "base_build": ping.base_build,
            "data_version": ping.data_version,
            "player_id": joined.player_id,
            "map": arena_map.path,
            "map_sha256": hashlib.sha256(map_data).hexdigest(),
            "unit_tag": own.tag,
            "unit_type": own.unit_type,
            "start": start,
            "end": end,
            "game_loop": after.observation.game_loop,
            "action_result": list(action_response.result),
            "action_errors": len(after.action_errors),
            "rgb_shape": list(rgb.shape),
            "rgb_min": int(rgb.min()),
            "rgb_max": int(rgb.max()),
            "replay_bytes": len(replay),
            "renderer": "osmesa" if args.osmesa else "auto",
            "visible_window": False,
        }
        (args.out / "result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()

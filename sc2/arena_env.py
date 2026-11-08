"""The few PySC2 integration points needed by our runner."""
import math
from functools import lru_cache
from pathlib import Path

import pygame

from pysc2 import maps
from pysc2.env import sc2_env
from pysc2.lib import colors, point, renderer_human
from s2clientprotocol import raw_pb2 as raw_pb
from s2clientprotocol import sc2api_pb2 as sc_pb

from .record import proto_dict


def interface_options(visual=False, rgb=False):
    options = sc_pb.InterfaceOptions(raw=True, score=True,
                                    show_cloaked=False, show_burrowed_shadows=False,
                                    show_placeholders=False, raw_affects_selection=True,
                                    raw_crop_to_playable_area=False)
    if visual:
        options.feature_layer.width = 32
        options.feature_layer.resolution.x = 84
        options.feature_layer.resolution.y = 84
        options.feature_layer.minimap_resolution.x = 64
        options.feature_layer.minimap_resolution.y = 64
        options.feature_layer.allow_cheating_layers = False
    if rgb:
        options.render.resolution.x = 1024
        options.render.resolution.y = 768
        options.render.minimap_resolution.x = 128
        options.render.minimap_resolution.y = 128
    return options


def load_map(value):
    path = Path(value)
    if path.suffix.lower() == ".sc2map":
        path = path.resolve(strict=True)

        class LocalArena(maps.lib.Map):
            directory = "sc2arena"
            filename = path.name
            players = 2

            def data(self, run_config):
                return path.read_bytes()

        instance = LocalArena()
    else:
        instance = maps.get(value)
    # Hash the same bytes sent to SC2, even if the source file changes after loading.
    instance.data = lru_cache(maxsize=1)(instance.data)
    return instance


def validate_actions(obs, actions):
    own = {u.tag for u in obs.observation.raw_data.units if u.alliance == raw_pb.Self}
    visible = {u.tag for u in obs.observation.raw_data.units if u.display_type == raw_pb.Visible}
    for action in actions:
        if not isinstance(action, sc_pb.Action):
            raise TypeError("Policy must return s2clientprotocol Action messages")
        if not action.HasField("action_raw") or action.action_raw.WhichOneof("action") != "unit_command":
            raise ValueError("Policy may only issue RAW unit commands")
        command = action.action_raw.unit_command
        if not command.unit_tags or not set(command.unit_tags) <= own:
            raise ValueError("Policy attempted to command a non-owned unit")
        if command.HasField("target_unit_tag") and command.target_unit_tag not in visible:
            raise ValueError("Policy attempted to target a non-visible unit")


def arena_clock(arena, observation):
    if not arena:
        return None, None
    obs = observation.observation
    if (arena.get("clock_source") != "observation.score.score"
            or not obs.HasField("score") or not obs.score.HasField("score")):
        return None, None
    seconds = obs.score.score
    phase = max(i for i, start in enumerate(arena["phase_start_seconds"]) if start <= seconds)
    return seconds, phase


def arena_hud(arena, observation):
    if not arena:
        return ""
    remaining = arena.get("ready_game_loops", 0) - observation.observation.game_loop
    if remaining > 0:
        return "GET READY | %ds" % math.ceil(remaining / 22.4)
    seconds, phase = arena_clock(arena, observation)
    if seconds is None:
        return "Arena clock unavailable"
    upcoming = next((s for s in arena["phase_start_seconds"] if s > seconds), None)
    next_text = "final circle" if upcoming is None else "shrink in %ds" % (upcoming - seconds)
    return "Remaining %ds | radius %g | %s" % (
        max(0, arena["duration_seconds"] - seconds), arena["radii"][phase], next_text)


def projected_ring(center, radius, world_to_surf):
    # Transform every point: RGB/minimap axes can have different pixel scales.
    return [world_to_surf.fwd_pt(point.Point(center[0] + radius * math.cos(i * math.tau / 96),
                                           center[1] + radius * math.sin(i * math.tau / 96)))
            for i in range(96)]


class ArenaRenderer(renderer_human.RendererHuman):
    def __init__(self, arena, replay=False, **kwargs):
        self.arena = arena
        self.replay = replay
        self.awaiting_start = False
        super().__init__(**kwargs)

    def draw_rings(self, surf):
        if not self.arena:
            return
        # shortcut: upstream RGB projection is approximate; use its exact minimap or feature view for boundaries.
        if surf.surf_type & renderer_human.SurfType.RGB and surf.surf_type & renderer_human.SurfType.SCREEN:
            return
        _, phase = arena_clock(self.arena, self._obs)
        for index, radius in enumerate(self.arena["radii"]):
            active = index == phase
            vertices = projected_ring(self.arena["center"], radius, surf.world_to_surf)
            color = (255, 40, 40) if phase and index <= phase else (255, 220, 40) if active else (80, 220, 110)
            pygame.draw.lines(surf.surf, color,
                              True, vertices, 3 if active else 1)

    def draw_screen(self, surf):
        super().draw_screen(surf)
        self.draw_rings(surf)
        surf.write_screen(self._font_large, colors.yellow, (1, 2), arena_hud(self.arena, self._obs))
        instruction = ("PAUSED: press Pause or Esc to start" if self.awaiting_start else
                       "Pause: pause/resume | F4: close | ?: help" if self.replay else
                       "Pause: pause/resume | F4: quit | F5: new round | ?: help")
        surf.write_screen(self._font_large, colors.white, (1, 3), instruction)
        if self.arena and self._render_rgb:
            surf.write_screen(self._font_large, colors.white, (1, 4),
                              "Exact rings: minimap | F9: full tactical view")

    def draw_mini_map(self, surf):
        super().draw_mini_map(surf)
        self.draw_rings(surf)


class ArenaEnv(sc2_env.SC2Env):
    # PySC2 exposes the multiplayer lifecycle publicly, but not controllers needed by its renderer.
    def engine_identity(self):
        return [proto_dict(c.ping()) for c in self._controllers]

    def map_bytes(self):
        return self._maps[0].data(self._run_config)

    def unit_data(self, unit_types):
        return {str(k): proto_dict(v) for k, v in self.static_data().unit_stats.items()
                if k in unit_types}

    def start_view(self, slot, arena):
        view = ArenaRenderer(arena, fps=22.4, step_mul=4, render_sync=True,
                             render_feature_grid=False)
        view.init(self.game_info[slot], self._controllers[slot].data())
        return view

    def view_actions(self, view, slot):
        return view.get_actions(self._run_config, self._controllers[slot])

    def replay_bytes(self):
        return self._controllers[0].save_replay()

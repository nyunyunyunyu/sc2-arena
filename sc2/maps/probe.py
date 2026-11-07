"""Explicit map-only diagnostic games; never a policy evaluation or human game."""
import argparse
import json
import sys
from pathlib import Path

from absl import flags
from pysc2.env import sc2_env
from s2clientprotocol import debug_pb2 as debug_pb
from s2clientprotocol import sc2api_pb2 as sc_pb
from s2clientprotocol import query_pb2

from sc2.arena_env import ArenaEnv, interface_options, load_map
from sc2.record import GameRecord, actual_results, proto_dict, sha256


CASES = ("clock", "simultaneous_hazard", "elimination", "timeout_tie", "timeout_count",
         "boundary_inside", "boundary_exact", "boundary_outside")


def own_units(response):
    return [u for u in response.observation.raw_data.units if u.alliance == 1]


def move(tag, x, y):
    action = sc_pb.Action()
    command = action.action_raw.unit_command
    command.ability_id = 16
    command.unit_tags.append(tag)
    command.target_world_space_pos.x = x
    command.target_world_space_pos.y = y
    return action


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="latest" if sys.platform == "win32" else "4.10.0")
    args = parser.parse_args(argv)
    if not flags.FLAGS.is_parsed():
        flags.FLAGS(["map_probe"])
    rules_path = Path(__file__).parents[1] / "rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8-sig"))
    map_path = rules_path.parent / rules["map_file"]
    record = GameRecord(args.output, dict(purpose="map_probe", case=args.case,
             source="real_engine", diagnostic_debug_allowed=True, disable_fog=False,
             map_sha256=sha256(map_path.read_bytes()), rules=rules))
    env = None
    responses = []
    status = "error"
    assertions = {}
    samples = []

    def debug(commands):
        record.event("diagnostic_debug", commands=[proto_dict(c) for c in commands])
        env._controllers[0].debug(commands)

    def life(units, value=None):
        debug([debug_pb.DebugCommand(unit_value=debug_pb.DebugSetUnitValue(
                   unit_value=debug_pb.DebugSetUnitValue.Life,
                   value=u.health_max if value is None else value, unit_tag=u.tag)) for u in units])

    def kill(units):
        debug([debug_pb.DebugCommand(kill_unit=debug_pb.DebugKillUnit(tag=[u.tag for u in units]))])

    try:
        environment = ArenaEnv
        if sys.platform == "win32":
            from sc2.windows_env import WindowsArenaEnv
            environment = WindowsArenaEnv
        env = environment(map_name=load_map(str(map_path)),
              players=[sc2_env.Agent(sc2_env.Race.terran, "map-probe-1"),
                       sc2_env.Agent(sc2_env.Race.terran, "map-probe-2")],
              agent_interface_format=[interface_options(), interface_options()],
              disable_fog=False, visualize=False, realtime=False, step_mul=16,
              random_seed=1, game_steps_per_episode=10000, score_index=-1, version=args.version)
        record.manifest["engine"] = env.engine_identity()
        record.write_manifest()
        timesteps = env.reset()
        initial = None
        boundary_tag = None
        boundary_max = None
        prepared = False
        while True:
            responses = [t.observation for t in timesteps]
            for response in responses:
                record.observe(response)
            seconds = responses[0].observation.score.score
            loop = responses[0].observation.game_loop
            units = [own_units(r) for r in responses]
            if initial is None:
                initial = [[u.tag for u in team] for team in units]
                assert [sorted(u.unit_type for u in team) for team in units] == [sorted(rules["unit_type_ids"])] * 2
                assert all(not r.observation.raw_data.player.upgrade_ids for r in responses)
                if rules.get("combat"):
                    ability_names = []
                    for controller, team in zip(env._controllers, units):
                        catalog = controller.data().abilities
                        available = controller.query(query_pb2.RequestQuery(
                            abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=u.tag) for u in team],
                            ignore_resource_requirements=True))
                        record.event("available_abilities", response=proto_dict(available))
                        # Smart (right click) is protocol ability 1 with an empty catalog link.
                        names = sorted({"smart" if a.ability_id == 1 else catalog[a.ability_id].link_name.lower()
                                        for unit in available.abilities for a in unit.abilities})
                        assert set(names) <= {"move", "attack", "stop", "smart"}, names
                        ability_names.append(names)
                    assertions["available_ability_names"] = ability_names
                record.manifest["participant_player_ids"] = [r.observation.player_common.player_id for r in responses]
                record.write_manifest()
            samples.append(dict(game_loop=loop, scenario_seconds=seconds,
                                teams=[[dict(tag=u.tag, type=u.unit_type, hp=u.health,
                                             x=u.pos.x, y=u.pos.y) for u in team] for team in units]))
            results = actual_results(responses)
            if results:
                ids = record.manifest["participant_player_ids"]
                expected = [3, 3] if args.case in ("simultaneous_hazard", "timeout_tie") else [2, 1]
                assert [results.get(i) for i in ids] == expected, (args.case, results, expected)
                if args.case.startswith("timeout"):
                    assert 300 <= seconds < 302, seconds
                if args.case == "simultaneous_hazard":
                    assert 120 <= seconds < 122, seconds
                assertions.update(player_results=results, end_scenario_seconds=seconds)
                status = "probe_pass"
                break
            assert not any(t.last() for t in timesteps), "Probe watchdog before native result"
            actions = [[], []]
            flat_units = units[0] + units[1]
            if args.case == "clock":
                if seconds >= 125:
                    assert all(abs((b["game_loop"] - a["game_loop"]) / 16 -
                                   (b["scenario_seconds"] - a["scenario_seconds"])) < 0.1
                               for a, b in zip(samples[2:], samples[3:])), "Clock is not 16 loops per scenario second"
                    assertions["game_seconds_per_loop"] = 1 / 16
                    status = "probe_pass"
                    break
            elif args.case == "elimination" and not prepared:
                kill(units[0])
                prepared = True
            elif args.case == "simultaneous_hazard" and seconds >= 119 and not prepared:
                assert all(len(team) == 3 for team in units), "Units died before simultaneous test"
                life(flat_units, 1)
                prepared = True
            elif args.case.startswith("timeout"):
                life(flat_units)
                if args.case == "timeout_count" and seconds >= 299 and not prepared:
                    kill([units[0][0]])
                    prepared = True
            elif args.case.startswith("boundary"):
                if boundary_tag is None:
                    marines = [[u for u in team if u.unit_type == 48][0] for team in units]
                    kill([u for u in flat_units if u.unit_type != 48])
                    boundary_tag = marines[0].tag
                    boundary_max = marines[0].health_max
                    x = {"boundary_inside": 26.75, "boundary_exact": 27.0,
                         "boundary_outside": 27.25}[args.case]
                    actions = [[move(marines[0].tag, x, 16)], [move(marines[1].tag, 3, 3)]]
                if seconds >= 59 and not prepared:
                    life(flat_units)
                    prepared = True
                if seconds >= 61:
                    marine = next(u for u in units[0] if u.tag == boundary_tag)
                    distance_squared = (marine.pos.x - 16) ** 2 + (marine.pos.y - 16) ** 2
                    outside = distance_squared > 121
                    assert outside == (args.case == "boundary_outside"), (marine.pos, distance_squared)
                    if args.case == "boundary_exact":
                        assert abs(distance_squared - 121) < 0.0001, "Move target was not reached exactly"
                    expected_loss = 4 if outside else 0
                    assert abs(boundary_max - marine.health - expected_loss) < 0.01, (marine.health, expected_loss)
                    assertions.update(position=proto_dict(marine.pos), distance_squared=distance_squared,
                                      expected_hazard_damage=expected_loss, actual_hp=marine.health)
                    status = "probe_pass"
                    break
            for slot, issued in enumerate(actions):
                if issued:
                    record.event("diagnostic_move", slot=slot, actions=[proto_dict(a) for a in issued])
            timesteps = env.step(actions)
    except Exception as exc:
        assertions["error"] = repr(exc)
        record.event("probe_error", error=repr(exc))
        raise
    finally:
        if env:
            try:
                (record.path / "game.SC2Replay").write_bytes(env.replay_bytes())
            except Exception as exc:
                record.event("replay_error", error=repr(exc))
            try:
                env.close()
            except Exception as exc:
                record.event("close_error", error=repr(exc))
        (record.path / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
        record.finish(status, responses, assertions=assertions)
        print(record.path / "manifest.json")


if __name__ == "__main__":
    main()

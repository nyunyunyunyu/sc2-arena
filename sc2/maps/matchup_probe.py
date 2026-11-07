"""Real-engine counter checks. Diagnostic isolation, never human/AI win-rate evidence."""
import argparse
import json
import math
from pathlib import Path
import sys

from absl import flags
from pysc2.env import sc2_env
from s2clientprotocol import debug_pb2

from sc2.agent import ATTACK, _action, _command
from sc2.arena_env import ArenaEnv, interface_options, load_map
from sc2.maps.probe import move, own_units
from sc2.record import GameRecord, actual_results, proto_dict, sha256


CASES = (("fast_vs_archer", 105, 48, False, 105),
         ("fast_vs_kiting_archer", 105, 48, True, 105),
         ("spear_vs_fast", 73, 105, False, 73),
         ("spear_vs_archer", 73, 48, False, 73),
         ("archer_kites_spear", 73, 48, True, 48))


def run_case(env, rules, output, case, reverse):
    name, a, b, kite, winner = case
    kinds = [b, a] if reverse else [a, b]
    record = GameRecord(output, dict(purpose="matchup_probe", case=name, reverse=reverse,
        source="real_engine", diagnostic_debug_allowed=True, disable_fog=False,
        rules=rules, engine=env.engine_identity(), map_sha256=sha256(env.map_bytes())))
    responses, samples, errors, status = [], [], [], "error"
    try:
        steps = env.reset()
        data = env.unit_data(rules["unit_type_ids"])
        (output / "unit_data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        responses = [t.observation for t in steps]
        ids = [r.observation.player_common.player_id for r in responses]
        selected = []
        for slot, response in enumerate(responses):
            assert not response.observation.raw_data.player.upgrade_ids
            units = own_units(response)
            for unit in units:
                spec = rules["combat"][data[str(unit.unit_type)]["name"]]
                assert unit.health == unit.health_max == spec["hp"]
                assert unit.shield == unit.shield_max == 0
                actual = data[str(unit.unit_type)]
                assert actual["armor"] == 0 and abs(actual["movement_speed"] - spec["speed"]) < 0.001
                weapon = actual["weapons"][0]
                for key, expected in (("damage", spec["damage"]), ("attacks", spec["attacks"]),
                                      ("speed", spec["period"]), ("range", spec["range"])):
                    assert abs(weapon[key] - expected) < 0.001, (unit.unit_type, key, weapon)
                assert sum(b["bonus"] for b in weapon.get("damage_bonus", [])) == spec["bonus_vs_armored"]
            selected.append(next(u.tag for u in units if u.unit_type == kinds[slot]))
        unwanted = [u.tag for r in responses for u in own_units(r) if u.tag not in selected]
        command = debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=unwanted))
        record.event("diagnostic_debug", commands=[proto_dict(command)])
        env._controllers[0].debug([command])
        goals = [(12, 16), (20, 16)]
        if kite:
            goals = [(8 if slot == 0 else 24, 16) if kind == 48 else (16, 16)
                     for slot, kind in enumerate(kinds)]
        steps = env.step([[move(tag, *point)] for tag, point in zip(selected, goals)])
        started = False
        while True:
            responses = [t.observation for t in steps]
            for response in responses:
                record.observe(response)
                errors.extend(proto_dict(e) for e in response.action_errors)
            seconds = responses[0].observation.score.score
            own = [own_units(r) for r in responses]
            samples.append(dict(loop=responses[0].observation.game_loop, seconds=seconds,
                units=[[dict(type=u.unit_type, hp=u.health, x=u.pos.x, y=u.pos.y,
                             cooldown=u.weapon_cooldown) for u in team] for team in own]))
            results = actual_results(responses)
            if results:
                expected = {ids[i]: 1 if kind == winner else 2 for i, kind in enumerate(kinds)}
                assert results == expected, (name, results, expected)
                assert not errors, errors
                status = "probe_pass"
                break
            assert seconds < 55, "Combat must finish before shrinking or hazard can decide it"
            assert not any(t.last() for t in steps), "Unexpected watchdog"
            if not started:
                started = all(math.hypot(team[0].pos.x - point[0], team[0].pos.y - point[1]) < 0.15
                              for team, point in zip(own, goals))
            actions = [[], []]
            if started:
                for slot, team in enumerate(own):
                    unit = team[0]
                    visible = [u for u in responses[slot].observation.raw_data.units
                               if u.alliance == 4 and u.display_type == 1 and u.tag == selected[1-slot]]
                    target = visible[0] if visible else None
                    if kite and unit.unit_type == 48:
                        distance = math.hypot(unit.pos.x-target.pos.x, unit.pos.y-target.pos.y) if target else math.inf
                        if target and unit.weapon_cooldown == 0 and distance <= 5 + unit.radius + target.radius:
                            actions[slot] = [_action(unit.tag, _command(ATTACK, int(target.tag)))]
                        else:
                            angle = math.atan2(unit.pos.y-16, unit.pos.x-16) + 0.3
                            actions[slot] = [move(unit.tag, 16+8*math.cos(angle), 16+8*math.sin(angle))]
                    elif target:
                        if not unit.orders or unit.orders[0].target_unit_tag != target.tag:
                            actions[slot] = [_action(unit.tag, _command(ATTACK, int(target.tag)))]
                    else:
                        actions[slot] = [move(unit.tag, 16, 16)]
            for slot, issued in enumerate(actions):
                if issued:
                    record.event("diagnostic_control", slot=slot, actions=[proto_dict(a) for a in issued])
            steps = env.step(actions)
    except Exception as exc:
        record.event("probe_error", error=repr(exc))
        raise
    finally:
        try:
            replay = env.replay_bytes()
            (output / "game.SC2Replay").write_bytes(replay)
            record.manifest["replay_sha256"] = sha256(replay)
        finally:
            (output / "samples.json").write_text(json.dumps(samples), encoding="utf-8")
            record.finish(status, responses, action_errors=errors, end_scenario_seconds=samples[-1]["seconds"] if samples else None)
    return dict(case=name, reverse=reverse, status=status, player_results=results,
                survivor=samples[-1]["units"], seconds=seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if not flags.FLAGS.is_parsed():
        flags.FLAGS(["matchup_probe"])
    rules_path = Path(__file__).parents[1] / "rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    environment = ArenaEnv
    if sys.platform == "win32":
        from sc2.windows_env import WindowsArenaEnv
        environment = WindowsArenaEnv
    results = []
    with environment(map_name=load_map(str(rules_path.parent / rules["map_file"])),
            players=[sc2_env.Agent(sc2_env.Race.terran, "matchup-probe-1"),
                     sc2_env.Agent(sc2_env.Race.terran, "matchup-probe-2")],
            agent_interface_format=[interface_options(), interface_options()],
            disable_fog=False, visualize=False, realtime=False, step_mul=4,
            random_seed=1, game_steps_per_episode=1000, score_index=-1,
            version="latest" if sys.platform == "win32" else "4.10.0") as env:
        for case in CASES:
            for reverse in (False, True):
                result = run_case(env, rules, args.output / (case[0]+f"-{int(reverse)}"), case, reverse)
                results.append(result)
                (args.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()

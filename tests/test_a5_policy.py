"""Regressions for cavalry fallback, attack commitment, and ranged firing reach."""
import copy
import unittest
from s2clientprotocol import raw_pb2 as raw
from sc2.policies import a5 as policy
from sc2.arena_env import validate_actions
from tests.test_agent import observation, unit
from tests.test_adaptive_policy import config as base_config


def config():
    arena = base_config()
    arena["game_seconds_per_loop"] = 1 / 16
    return arena


def fighter(tag, x, y, kind, hp, enemy=False):
    result = unit(tag, x, y, kind=kind, hp=hp, ally=raw.Enemy if enemy else raw.Self)
    result.radius = .5 if kind == 73 else .375
    result.health_max = 60 if kind == 48 else 100
    return result


def fallback_units():
    # Actual first target-release positions, after the first cavalry strike.
    return [fighter(1, 15.437744, 15.143311, 105, 88),
            fighter(2, 14.722168, 15.496582, 105, 78, True),
            fighter(3, 18.153076, 16.140869, 73, 100, True),
            fighter(4, 19.488037, 15.771240, 48, 60, True)]


def decide(obs, memory=None):
    actions, state, reason = policy.decide(obs, memory or {}, arena=config())
    validate_actions(obs, actions)
    return actions, state, {u["tag"]: u for u in reason.get("units", [])}


class A5PolicyTests(unittest.TestCase):
    def test_safe_cavalry_fallback_covers_the_full_four_loop_interval(self):
        obs = observation(*fallback_units(), loop=188)
        actions, _, why = decide(obs)
        self.assertEqual(why[1]["target_tag"], 2)
        self.assertEqual(why[1]["mode"], "attack")
        self.assertEqual(actions[0].action_raw.unit_command.target_unit_tag, 2)

    def test_safe_ranged_target_and_protected_cavalry_do_not_trigger_fallback(self):
        for case in ("ranged_open", "cavalry_guarded"):
            with self.subTest(case=case):
                units = fallback_units()
                if case == "ranged_open":
                    units[3].pos.x, units[3].pos.y = 16, 14
                else:
                    units[1].pos.x, units[1].pos.y = 18.3, 16.3
                _, _, why = decide(observation(*units, loop=188))
                self.assertEqual(why[1]["target_tag"], 4)

    def test_safe_current_attack_point_survives_partial_attack_circle_overlap(self):
        # Actual first release frame: the current point is safe although part of the attack arc is not.
        cavalry = fighter(1, 15.2958984375, 14.845947265625, 105, 88)
        enemy_cavalry = fighter(4, 15.1005859375, 15.6611328125, 105, 78, True)
        enemy_spear = fighter(5, 18.16064453125, 16.11767578125, 73, 100, True)
        enemy_ranged = fighter(6, 19.65234375, 15.832275390625, 48, 60, True)
        reach = policy._range(cavalry, enemy_cavalry, config())
        clearance = policy._range(enemy_spear, cavalry, config()) + (1.75 + 4) * 4 / 16
        self.assertLess(policy._distance(policy._xy(cavalry), policy._xy(enemy_cavalry)), reach)
        self.assertGreater(policy._distance(policy._xy(cavalry), policy._xy(enemy_spear)), clearance)
        self.assertLess(policy._distance(policy._xy(enemy_cavalry), policy._xy(enemy_spear)) - reach, clearance)
        obs = observation(cavalry, fighter(2, 13.748291015625, 15.072998046875, 73, 100),
                          fighter(3, 11.298095703125, 14.123779296875, 48, 60),
                          enemy_cavalry, enemy_spear, enemy_ranged, loop=188)
        actions, _, why = decide(obs)
        self.assertEqual((why[1]["mode"], why[1]["target_tag"]), ("attack", 4))
        self.assertTrue(any(1 in a.action_raw.unit_command.unit_tags and
                            a.action_raw.unit_command.target_unit_tag == 4 for a in actions))

    def test_safe_approach_endpoints_do_not_allow_crossing_a_spear(self):
        obs = observation(fighter(1, 12, 16, 105, 100), fighter(2, 12, 17, 73, 100),
                          fighter(3, 16, 16, 73, 100, True), fighter(4, 16.2, 16, 48, 60, True),
                          fighter(5, 20, 16, 105, 100, True), fighter(6, 12, 15, 48, 60), loop=176)
        _, _, why = decide(obs)
        self.assertEqual(why[1]["target_tag"], 4)
        self.assertEqual(why[1]["mode"], "avoid_spear")

    def test_safe_cavalry_replaces_guard_break_after_existing_attack_commitment(self):
        units = [fighter(1, 18.5, 16, 105, 100), fighter(2, 16, 16.9, 73, 100),
                 fighter(3, 16, 16, 73, 100, True), fighter(4, 16.2, 16, 48, 60, True),
                 fighter(5, 19.4, 16, 105, 100, True), fighter(6, 20, 16, 48, 60)]
        obs = observation(*units, loop=176)
        actions, _, why = decide(obs)
        self.assertEqual(why[1]["target_tag"], 5)
        self.assertTrue(any(1 in a.action_raw.unit_command.unit_tags and
                            a.action_raw.unit_command.target_unit_tag == 5 for a in actions))
        obs.observation.raw_data.units[0].orders.add(ability_id=23, target_unit_tag=3)
        memory = {"player_id": 1, "game_loop": 172, "units": {"1": {
            "cooldown": 0, "last_command": {"ability": 23, "target_tag": 3}, "command_loop": 172}}}
        _, state, why = decide(obs, memory)
        self.assertEqual((why[1]["mode"], why[1]["target_tag"]), ("await_shot", 3))
        self.assertEqual(state["units"]["1"]["command_loop"], 172)
        obs.observation.game_loop = 180
        _, _, why = decide(obs, state)
        self.assertEqual(why[1]["target_tag"], 5)

    def test_guard_break_is_preserved_when_cavalry_alternative_is_unsafe_or_ineligible(self):
        for case in ("guarded", "second_guard", "outside_candidates", "snapshot", "unsafe_start"):
            with self.subTest(case=case):
                units = [fighter(1, 18.5, 16, 105, 100), fighter(2, 16, 16.9, 73, 100),
                         fighter(3, 16, 16, 73, 100, True), fighter(4, 16.2, 16, 48, 60, True),
                         fighter(5, 19.4, 16, 105, 100, True), fighter(6, 20, 16, 48, 60)]
                if case == "guarded":
                    units[4].pos.x, units[4].pos.y = 16.2, 17
                elif case == "second_guard":
                    units.append(fighter(7, 19.6, 16, 73, 100, True))
                elif case == "outside_candidates":
                    units[4].pos.x = 28
                elif case == "snapshot":
                    units[4].display_type = raw.Snapshot
                else:
                    units[0].pos.x = 18
                _, _, why = decide(observation(*units, loop=176))
                self.assertEqual(why[1]["target_tag"], 3)
                self.assertEqual(why[1]["mode"], "attack")

    def test_auto_started_spear_attack_keeps_both_strikes_then_resumes(self):
        spear = fighter(1, 16, 16, 73, 100)
        spear.weapon_cooldown = 18
        spear.orders.add(ability_id=23, target_unit_tag=4)
        obs = observation(spear, fighter(2, 20, 16, 48, 60),
                          fighter(3, 20.5, 16, 105, 100, True),
                          fighter(4, 16.8, 16, 73, 100, True), loop=200)
        memory = {"player_id": 1, "game_loop": 196, "units": {"1": {
            "cooldown": 0, "last_command": {"ability": 16, "point": [16, 16]}, "command_loop": 196}}}
        for loop, cooldown in ((200, 18), (204, 14)):
            obs.observation.game_loop = loop
            obs.observation.raw_data.units[0].weapon_cooldown = cooldown
            actions, memory, why = decide(obs, memory)
            self.assertFalse(any(1 in a.action_raw.unit_command.unit_tags for a in actions))
            self.assertEqual(why[1]["mode"], "finish_double_strike")
            self.assertEqual(memory["units"]["1"]["shot_loop"], 200)
            self.assertEqual(memory["units"]["1"]["command_loop"], 196)
        obs.observation.game_loop = 208
        obs.observation.raw_data.units[0].weapon_cooldown = 10
        actions, _, why = decide(obs, memory)
        self.assertNotEqual(why[1]["mode"], "finish_double_strike")
        self.assertTrue(any(1 in a.action_raw.unit_command.unit_tags for a in actions))

    def test_circle_escape_overrides_spear_attack_commitment(self):
        spear = fighter(1, 2, 2, 73, 100)
        spear.weapon_cooldown = 18
        obs = observation(spear, loop=200)
        memory = {"player_id": 1, "game_loop": 196, "units": {"1": {"cooldown": 0}}}
        actions, _, why = decide(obs, memory)
        self.assertEqual(why[1]["mode"], "enter_circle")
        self.assertEqual(actions[0].action_raw.unit_command.ability_id, 16)

    def test_missing_attack_retries_after_four_loops_but_actual_order_is_not_repeated(self):
        for actual_target in (2, 3):
            with self.subTest(actual_target=actual_target):
                marine = fighter(1, 16, 16, 48, 60)
                marine.orders.add(ability_id=3674, target_unit_tag=actual_target)
                obs = observation(marine, fighter(2, 21, 16, 48, 24, True),
                                  fighter(3, 17, 16, 73, 100, True), loop=116)
                memory = {"player_id": 1, "game_loop": 112, "units": {"1": {
                    "cooldown": 0, "last_command": {"ability": 23, "target_tag": 2}, "command_loop": 112}}}
                actions, _, why = decide(obs, memory)
                self.assertEqual(why[1]["target_tag"], 2)
                if actual_target == 2:
                    self.assertFalse(actions)
                else:
                    self.assertEqual(actions[0].action_raw.unit_command.target_unit_tag, 2)

    def test_cavalry_moves_laterally_toward_target_outside_spear_exclusion_band(self):
        cavalry = fighter(1, 14.6, 16.5, 105, 100)
        guard = fighter(2, 15.8, 16.7, 73, 100)
        spear = fighter(3, 16.7, 16.4, 73, 100, True)
        ranged = fighter(4, 18.7, 16.9, 48, 60, True)
        point = policy._cavalry_route(cavalry, ranged, [cavalry, guard], [spear, ranged], (16, 16), 14)
        self.assertGreater(policy._distance(point, policy._xy(spear)), 2.2)
        self.assertLess(policy._distance(point, policy._xy(ranged)),
                        policy._distance(policy._xy(cavalry), policy._xy(ranged)))

    def test_hidden_snapshot_does_not_change_the_fallback_or_mutate_inputs(self):
        obs = observation(*fallback_units(), loop=188)
        baseline = decide(obs)[0]
        hidden = fighter(5, 15.5, 15.2, 105, 1, True)
        hidden.display_type = raw.Snapshot
        obs.observation.raw_data.units.append(hidden)
        before = obs.SerializeToString()
        memory = {}
        arena = config()
        previous = copy.deepcopy(arena)
        actions, _, _ = policy.decide(obs, memory, arena=arena)
        self.assertEqual([a.SerializeToString() for a in actions], [a.SerializeToString() for a in baseline])
        self.assertEqual(obs.SerializeToString(), before)
        self.assertEqual(memory, {})
        self.assertEqual(arena, previous)

    def test_equal_range_duel_keeps_firing_reach(self):
        marine = fighter(1, 16, 16, 48, 60)
        target = fighter(2, 21.4, 16, 48, 60, True)
        point = policy._kite_position(marine, target, [marine], [target], (16, 16), 14, config())
        self.assertLessEqual(policy._distance(point, policy._xy(target)), policy._range(marine, target, config()))

    def test_non_target_ranged_enemy_remains_a_kiting_threat(self):
        marine = fighter(1, 16, 16, 48, 60)
        target = fighter(2, 21, 16, 48, 60, True)
        other = fighter(3, 16, 20, 48, 60, True)
        plain = policy._kite_position(marine, target, [marine], [target], (16, 16), 14, config())
        threatened = policy._kite_position(marine, target, [marine], [target, other], (16, 16), 14, config())
        self.assertGreater(policy._distance(threatened, policy._xy(other)), policy._distance(plain, policy._xy(other)) + .5)


if __name__ == "__main__":
    unittest.main()

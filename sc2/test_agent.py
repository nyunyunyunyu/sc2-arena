"""Behavior checks on real protocol messages; no engine or omniscient state."""

import copy
import json
import math
import unittest

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2.agent import ATTACK, MOVE, STOP, _damage, decide


def unit(tag, x, y, *, ally=raw_pb2.Self, kind=48, hp=45, cooldown=0,
         display=raw_pb2.Visible, owner=1):
    result = raw_pb2.Unit(tag=tag, unit_type=kind, alliance=ally, owner=owner,
                         display_type=display, health=hp, health_max=45,
                         radius=0.375, cloak=raw_pb2.NotCloaked)
    result.pos.x, result.pos.y = x, y
    if ally == raw_pb2.Self:
        result.weapon_cooldown = cooldown
    return result


def observation(*units, loop=0, player=1, dead=()):
    result = sc2api_pb2.ResponseObservation()
    result.observation.game_loop = loop
    result.observation.player_common.player_id = player
    result.observation.raw_data.units.extend(units)
    result.observation.raw_data.event.dead_units.extend(dead)
    return result


def arena():
    # Synthetic fixture values deliberately differ from real multiplayer balance.
    return {"center": [16, 16], "radii": [14, 11, 8, 5, 2],
            "phase_start_seconds": [0, 60, 120, 180, 240],
            "clock_verified": True, "game_seconds_per_loop": 0.125,
            "unit_data": {"48": {"movement_speed": 2, "armor": 0, "attributes": [1],
                                  "weapons": [{"damage": 6, "range": 5, "attacks": 1}]}}}


class PolicyTests(unittest.TestCase):
    def test_spear_bonus_targets_only_cavalry(self):
        config = arena()
        config["unit_data"].update({
            "73": {"armor": 0, "attributes": [1, 3], "weapons": [{
                "damage": 4, "attacks": 2, "range": 0.1,
                "damage_bonus": [{"attribute": 2, "bonus": 11}]}]},
            "105": {"armor": 0, "attributes": [2, 3]},
        })
        spear = unit(1, 16, 16, kind=73)
        for kind, expected in ((105, 30), (73, 8), (48, 8)):
            target = unit(2, 16.5, 16, kind=kind, ally=raw_pb2.Enemy, hp=100)
            self.assertEqual(_damage(spear, target, config), expected)

    def test_native_shields_and_two_strikes_apply_armor_per_hit(self):
        config = arena()
        config["unit_data"]["73"] = {"armor": 1, "attributes": [1],
            "weapons": [{"damage": 8, "attacks": 2, "range": 0.1}]}
        attacker = unit(1, 16, 16, kind=73)
        enemy = unit(2, 16.7, 16, kind=73, ally=raw_pb2.Enemy)
        for shield, expected in ((50, 16), (8, 15), (4, 14), (0, 14)):
            enemy.shield = shield
            self.assertEqual(_damage(attacker, enemy, config), expected)

    def test_melee_keeps_attacking_during_cooldown(self):
        for kind in (73, 105):
            config = arena()
            config["unit_type_ids"] = [73, 105, 48]
            config["unit_data"][str(kind)] = {"armor": 0, "attributes": [1],
                "weapons": [{"damage": 5, "attacks": 1, "range": 0.1}]}
            own = unit(1, 16, 16, kind=kind)
            enemy = unit(2, 16.7, 16, ally=raw_pb2.Enemy)
            actions, memory, _ = decide(observation(own, enemy), {}, arena=config)
            self.assertEqual(actions[0].action_raw.unit_command.ability_id, ATTACK)
            own.orders.add(ability_id=ATTACK, target_unit_tag=2)
            own.weapon_cooldown = 30
            _, memory, _ = decide(observation(own, enemy, loop=4), memory, arena=config)
            own.weapon_cooldown = 22
            actions, _, reason = decide(observation(own, enemy, loop=12), memory, arena=config)
            self.assertFalse(actions)
            self.assertEqual(reason["units"][0]["mode"], "attack")

    def test_visible_only_and_current_player_identity(self):
        for player in (1, 2):
            self_unit = unit(10, 16, 16, owner=player)
            enemy = unit(20, 18, 16, ally=raw_pb2.Enemy, owner=3 - player)
            snapshot = unit(21, 16, 17, ally=raw_pb2.Enemy, display=raw_pb2.Snapshot)
            hidden = unit(22, 16, 17, ally=raw_pb2.Enemy, display=raw_pb2.Hidden)
            cloaked = unit(23, 16, 17, ally=raw_pb2.Enemy)
            cloaked.cloak = raw_pb2.Cloaked
            blip = unit(24, 16, 17, ally=raw_pb2.Enemy)
            blip.is_blip = True
            actions, _, reason = decide(observation(self_unit, enemy, snapshot, hidden, cloaked, blip, player=player), {}, arena=arena())
            self.assertEqual(reason["visible_enemy_tags"], [20])
            self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags), [10])
            self.assertEqual(actions[0].action_raw.unit_command.target_unit_tag, 20)

    def test_no_enemy_private_fields_affect_decision(self):
        own, enemy = unit(1, 16, 16), unit(2, 19, 16, ally=raw_pb2.Enemy)
        original = decide(observation(own, enemy), {}, arena=arena())
        enemy.weapon_cooldown = 999
        enemy.orders.add(ability_id=ATTACK, target_unit_tag=1)
        enemy.engaged_target_tag = 1
        poisoned = decide(observation(own, enemy), {}, arena=arena())
        self.assertEqual(original, poisoned)

    def test_lost_and_dead_targets_are_cleared(self):
        own, enemy = unit(1, 16, 16), unit(2, 18, 16, ally=raw_pb2.Enemy)
        _, memory, _ = decide(observation(own, enemy), {}, arena=arena())
        own.orders.add(ability_id=ATTACK, target_unit_tag=2)
        for next_obs in (observation(own, loop=4), observation(own, enemy, loop=4, dead=[2])):
            actions, new_memory, _ = decide(next_obs, memory, arena=arena())
            self.assertEqual(actions[0].action_raw.unit_command.ability_id, STOP)
            self.assertNotIn("target_tag", new_memory["units"]["1"])
        actions, new_memory, _ = decide(observation(loop=8), memory, arena=arena())
        self.assertFalse(actions)
        self.assertEqual(new_memory["units"], {})

    def test_empty_observation_and_missing_health_are_safe(self):
        self.assertFalse(decide(observation(), {})[0])
        own, enemy = unit(1, 16, 16), unit(2, 18, 16, ally=raw_pb2.Enemy)
        enemy.ClearField("health")
        _, memory, reason = decide(observation(own, enemy), {}, arena=arena())
        json.dumps([memory, reason], allow_nan=False)

    def test_circle_escape_boundary_and_clock(self):
        for x, expected in ((30, "search_center"), (30.001, "enter_circle")):
            actions, _, reason = decide(observation(unit(1, x, 16)), {}, arena=arena())
            self.assertEqual(reason["units"][0]["mode"], expected)
            point = actions[0].action_raw.unit_command.target_world_space_pos
            self.assertLessEqual(math.hypot(point.x - 16, point.y - 16), 14)
        # 480 loops means 60 seconds only because this fixture explicitly says so.
        _, _, reason = decide(observation(unit(1, 29, 16), loop=480), {}, arena=arena())
        self.assertEqual(reason["ring"]["phase"], 1)
        self.assertEqual(reason["units"][0]["mode"], "enter_circle")
        config = arena()
        config.update(clock_verified=False, game_seconds_per_loop=None)
        _, _, reason = decide(observation(unit(1, 29, 16), loop=9999), {}, arena=config)
        self.assertEqual(reason["ring"]["phase"], 0)
        self.assertIsNone(reason["ring"]["game_seconds"])

    def test_advance_before_shrink_and_origin(self):
        config = arena()
        config["origin_game_loop"] = 80
        actions, _, reason = decide(observation(unit(1, 29, 16), loop=80 + 56 * 8), {}, arena=config)
        self.assertTrue(reason["ring"]["anticipating"])
        self.assertEqual(reason["ring"]["phase"], 0)
        self.assertEqual(reason["units"][0]["mode"], "enter_circle")
        self.assertLessEqual(actions[0].action_raw.unit_command.target_world_space_pos.x, 27)

    def test_public_map_clock_does_not_need_a_guessed_loop_ratio(self):
        config = arena()
        config.update(clock_verified=False, game_seconds_per_loop=None,
                      clock_source="observation.score.score")
        obs = observation(unit(1, 29, 16), loop=7)
        obs.observation.score.score = 120
        _, _, reason = decide(obs, {}, arena=config)
        self.assertEqual(reason["ring"]["game_seconds"], 120)
        self.assertEqual(reason["ring"]["phase"], 2)
        self.assertFalse(reason["ring"]["clock_verified"])
        self.assertEqual(reason["ring"]["clock_source"], "observation.score.score")

    def test_cooldown_move_requires_observed_shot_and_protection(self):
        own, enemy = unit(1, 16, 16), unit(2, 20, 16, ally=raw_pb2.Enemy)
        _, memory, _ = decide(observation(own, enemy), {}, arena=arena())
        own.orders.add(ability_id=ATTACK, target_unit_tag=2)
        own.weapon_cooldown = 30
        actions, memory, reason = decide(observation(own, enemy, loop=4), memory, arena=arena())
        self.assertFalse(actions)
        self.assertFalse(reason["units"][0]["move_after_shot"])
        own.weapon_cooldown = 26
        self.assertFalse(decide(observation(own, enemy, loop=8), memory, arena=arena())[0])
        own.weapon_cooldown = 22
        actions, _, reason = decide(observation(own, enemy, loop=12), memory, arena=arena())
        self.assertEqual(actions[0].action_raw.unit_command.ability_id, MOVE)
        self.assertEqual(reason["units"][0]["mode"], "cooldown_move")
        self.assertLess(actions[0].action_raw.unit_command.target_world_space_pos.x, 16)
        # Seeing a nonzero cooldown at cold start does not prove a fresh shot.
        self.assertFalse(decide(observation(own, enemy, loop=12), {}, arena=arena())[2]["units"][0]["move_after_shot"])

    def test_debounce_and_retry_when_order_is_absent(self):
        own, enemy = unit(1, 16, 16), unit(2, 18, 16, ally=raw_pb2.Enemy)
        _, memory, _ = decide(observation(own, enemy), {}, arena=arena())
        self.assertFalse(decide(observation(own, enemy, loop=4), memory, arena=arena())[0])
        self.assertEqual(len(decide(observation(own, enemy, loop=16), memory, arena=arena())[0]), 1)
        own.orders.add(ability_id=3674, target_unit_tag=2)
        self.assertFalse(decide(observation(own, enemy, loop=20), memory, arena=arena())[0])

    def test_target_switch_cannot_cancel_a_protected_shot(self):
        own, enemy = unit(1, 16, 16), unit(2, 20, 16, ally=raw_pb2.Enemy)
        _, memory, _ = decide(observation(own, enemy), {}, arena=arena())
        own.orders.add(ability_id=ATTACK, target_unit_tag=2)
        own.weapon_cooldown = 30
        easier = unit(3, 17, 16, ally=raw_pb2.Enemy, hp=1)
        actions, _, reason = decide(observation(own, enemy, easier, loop=4), memory, arena=arena())
        self.assertFalse(actions)
        self.assertEqual(reason["units"][0]["target_tag"], 2)
        self.assertEqual(reason["units"][0]["mode"], "await_shot")

    def test_weak_outnumbered_unit_rejoins_its_teammate(self):
        weak, support = unit(1, 16, 16, hp=10), unit(2, 13, 16)
        enemies = [unit(10, 20, 16, ally=raw_pb2.Enemy), unit(11, 20, 17, ally=raw_pb2.Enemy)]
        _, memory, _ = decide(observation(weak, support, *enemies), {}, arena=arena())
        weak.weapon_cooldown = 30
        _, memory, _ = decide(observation(weak, support, *enemies, loop=4), memory, arena=arena())
        weak.weapon_cooldown = 22
        actions, _, reason = decide(observation(weak, support, *enemies, loop=12), memory, arena=arena())
        decision = next(r for r in reason["units"] if r["tag"] == 1)
        self.assertEqual(decision["mode"], "regroup")
        action = next(a.action_raw.unit_command for a in actions if 1 in a.action_raw.unit_command.unit_tags)
        self.assertEqual(action.target_world_space_pos.x, 13)

    def test_split_fire_after_enough_damage_is_reserved(self):
        own1, own2 = unit(1, 16, 16), unit(2, 16, 17)
        weak = unit(10, 19, 16, ally=raw_pb2.Enemy, hp=5)
        healthy = unit(11, 20, 16, ally=raw_pb2.Enemy)
        actions, _, _ = decide(observation(own1, own2, weak, healthy), {}, arena=arena())
        self.assertEqual([a.action_raw.unit_command.target_unit_tag for a in actions], [10, 11])

    def test_pursuit_is_limited_and_does_not_leave_circle(self):
        own = unit(1, 16, 16)
        enemy = unit(2, 29, 16, ally=raw_pb2.Enemy)
        self.assertFalse(decide(observation(own, enemy), {}, arena=arena())[0])
        own.pos.x, enemy.pos.x = 28, 31
        _, _, reason = decide(observation(own, enemy), {}, arena=arena())
        self.assertEqual(reason["units"][0]["mode"], "attack")
        enemy.pos.x = 36
        actions, _, reason = decide(observation(own, enemy), {}, arena=arena())
        self.assertEqual(actions[0].action_raw.unit_command.ability_id, MOVE)

    def test_memory_is_pure_and_does_not_cross_sides(self):
        obs = observation(unit(1, 16, 16), unit(2, 18, 16, ally=raw_pb2.Enemy))
        source = {"player_id": 2, "game_loop": 100, "units": {"1": {"target_tag": 999}}}
        before, obs_bytes = copy.deepcopy(source), obs.SerializeToString()
        _, memory, reason = decide(obs, source, arena=arena())
        self.assertEqual(source, before)
        self.assertEqual(obs.SerializeToString(), obs_bytes)
        self.assertEqual(memory["player_id"], 1)
        self.assertNotIn(999, reason["visible_enemy_tags"])
        json.dumps([memory, reason], allow_nan=False)

    def test_invalid_verified_clock_fails_instead_of_guessing(self):
        config = arena()
        config["game_seconds_per_loop"] = None
        with self.assertRaises(ValueError):
            decide(observation(unit(1, 16, 16)), {}, arena=config)


if __name__ == "__main__":
    unittest.main()

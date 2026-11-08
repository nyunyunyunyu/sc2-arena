"""Regression cases from the three human wins on 2026-11-07."""
import unittest
from s2clientprotocol import raw_pb2 as raw
from sc2.policies import a, a2
from sc2.arena_env import validate_actions
from .test_agent import arena, observation, unit


def config():
    rules = arena()
    rules.update(unit_type_ids=[73, 105, 48], ready_game_loops=112)
    rules["unit_data"]["73"] = {"movement_speed": 1.75, "attributes": [1, 3], "weapons": [
        {"damage": 4, "attacks": 2, "range": .1, "damage_bonus": [{"attribute": 2, "bonus": 11}]}]}
    rules["unit_data"]["105"] = {"movement_speed": 4, "attributes": [2, 3], "weapons": [
        {"damage": 10, "attacks": 1, "range": .1}]}
    return rules


def orders(obs):
    actions, memory, why = a2.decide(obs, {}, arena=config())
    validate_actions(obs, actions)
    return {u["tag"]: u for u in why["units"]}


class AdaptivePolicyTests(unittest.TestCase):
    def test_cavalry_rejoins_support_when_intercepted_away_from_the_squad(self):
        obs = observation(unit(1, 13.5, 16, kind=105, hp=100), unit(2, 19, 16, kind=73, hp=100),
                          unit(3, 20, 17, hp=60), unit(4, 12, 16, hp=60, ally=raw.Enemy),
                          unit(5, 13, 16, kind=105, hp=100, ally=raw.Enemy), loop=112)
        self.assertEqual(orders(obs)[1]["mode"], "rejoin_support")

    def test_cavalry_still_attacks_an_isolated_marine(self):
        obs = observation(unit(1, 15, 16, kind=105, hp=100), unit(2, 19, 16, kind=73, hp=100),
                          unit(4, 12, 16, hp=60, ally=raw.Enemy), loop=112)
        result = orders(obs)[1]
        self.assertEqual(result["command"]["ability"], a.ATTACK)
        self.assertEqual(result["target_tag"], 4)

    def test_spear_intercepts_a_cavalry_raiding_its_marine(self):
        obs = observation(unit(1, 16, 16, kind=73, hp=100), unit(2, 20, 16, hp=60),
                          unit(3, 22, 16, kind=105, hp=100, ally=raw.Enemy),
                          unit(4, 15, 16, kind=73, hp=100, ally=raw.Enemy), loop=112)
        result = orders(obs)[1]
        self.assertEqual(result["mode"], "protect_marine")
        self.assertEqual(result["target_tag"], 3)
        self.assertGreater(result["command"]["point"][0], 20)

    def test_distant_cavalry_does_not_distract_the_spear(self):
        obs = observation(unit(1, 16, 16, kind=73, hp=100), unit(2, 20, 16, hp=60),
                          unit(3, 23.5, 16, kind=105, hp=100, ally=raw.Enemy),
                          unit(4, 15, 16, kind=73, hp=100, ally=raw.Enemy), loop=112)
        self.assertEqual(orders(obs)[1]["target_tag"], 4)

    def test_marine_focuses_an_immediate_cavalry_threat(self):
        obs = observation(unit(1, 16, 16, hp=60), unit(2, 18.4, 16, kind=105, hp=100, ally=raw.Enemy),
                          unit(3, 21, 16, hp=60, ally=raw.Enemy), loop=112)
        self.assertEqual(orders(obs)[1]["target_tag"], 2)

    def test_kiting_returns_to_spear_shelter_instead_of_running_away(self):
        own = [unit(1, 16, 19, hp=60), unit(2, 16, 16, kind=73, hp=100)]
        enemy = unit(3, 15, 18, kind=105, hp=100, ally=raw.Enemy)
        point = a2._kite_position(own[0], enemy, own, [enemy], (16, 16), 14, config())
        self.assertLess(point[1], 19)

    def test_preparation_and_circle_escape_keep_priority(self):
        obs = observation(unit(1, 2, 2, kind=105, hp=100), loop=100)
        self.assertEqual(a2.decide(obs, {"old": True}, arena=config())[0], [])
        obs.observation.game_loop = 112
        self.assertEqual(orders(obs)[1]["mode"], "enter_circle")


if __name__ == "__main__":
    unittest.main()

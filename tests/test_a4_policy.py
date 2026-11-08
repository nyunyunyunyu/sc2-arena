"""Regressions from the six completed A3 human matches on 2026-11-07."""
import unittest
from s2clientprotocol import raw_pb2 as raw
from sc2.policies import a4
from sc2.arena_env import validate_actions
from .test_agent import unit, observation
from .test_adaptive_policy import config


def decisions(obs):
    actions, _, why = a4.decide(obs, {}, arena=config())
    validate_actions(obs, actions)
    return {u["tag"]: u for u in why["units"]}


class A4Tests(unittest.TestCase):
    def test_kiting_keeps_melee_clearance_while_returning_to_its_guard(self):
        marine = unit(1, 9.08, 15.63, hp=20)
        guard = unit(2, 9.86, 15.4, kind=73, hp=100)
        cavalry = unit(3, 8.68, 18.36, kind=105, hp=40, ally=raw.Enemy)
        spear = unit(4, 10.81, 15.61, kind=73, hp=80, ally=raw.Enemy)
        ranged = unit(5, 4.49, 14.22, hp=48, ally=raw.Enemy)
        point = a4._kite_position(marine, cavalry, [marine, guard], [cavalry, spear, ranged],
                                  (16, 16), 14, config())
        self.assertGreater(a4._distance(point, a4._xy(spear)), 1.5)

    def test_guard_route_does_not_cross_the_raider(self):
        marine = unit(1, 16, 16, hp=60)
        guard = unit(2, 16, 12, kind=73, hp=100)
        raider = unit(3, 16, 14, kind=105, hp=100, ally=raw.Enemy)
        point = a4._kite_position(marine, raider, [marine, guard], [raider], (16, 16), 14, config())
        # Orbiting the guard may close distance, but must keep the travel segment clear.
        for t in (.25, .5, .75, 1):
            along = (16 + (point[0] - 16) * t, 16 + (point[1] - 16) * t)
            self.assertGreater(a4._distance(along, a4._xy(raider)), 1.5)

    def test_cavalry_finishes_a_nearby_raider_instead_of_abandoning_the_marine(self):
        obs = observation(unit(1, 16, 17, kind=105, hp=100), unit(2, 18, 16, kind=73, hp=100),
                          unit(3, 16, 16, hp=12), unit(4, 16.7, 16, kind=105, hp=15, ally=raw.Enemy),
                          unit(5, 17.5, 17, kind=73, hp=100, ally=raw.Enemy), loop=112)
        result = decisions(obs)[1]
        self.assertEqual(result["target_tag"], 4)
        self.assertEqual(result["command"]["ability"], a4.ATTACK)

    def test_cavalry_does_not_use_rescue_as_an_excuse_to_charge_a_healthy_raider(self):
        obs = observation(unit(1, 16, 17, kind=105, hp=100), unit(2, 18, 16, kind=73, hp=100),
                          unit(3, 16, 16, hp=12), unit(4, 16.7, 16, kind=105, hp=100, ally=raw.Enemy),
                          unit(5, 17.5, 17, kind=73, hp=100, ally=raw.Enemy), loop=112)
        self.assertEqual(decisions(obs)[1]["mode"], "avoid_spear")

    def test_preparation_and_ring_escape_still_take_priority(self):
        obs = observation(unit(1, 2, 2, hp=60), loop=100)
        self.assertEqual(a4.decide(obs, {}, arena=config())[0], [])
        obs.observation.game_loop = 112
        self.assertEqual(decisions(obs)[1]["mode"], "enter_circle")


if __name__ == "__main__":
    unittest.main()

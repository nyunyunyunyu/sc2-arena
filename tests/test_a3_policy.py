"""Regressions from the latest A2 human series."""
import unittest
from s2clientprotocol import raw_pb2 as raw
from sc2.policies import a3
from .test_agent import unit, observation
from .test_adaptive_policy import config
from sc2.arena_env import validate_actions


def decisions(obs):
    actions, _, why = a3.decide(obs, {}, arena=config())
    validate_actions(obs, actions)
    return {u["tag"]: u for u in why["units"]}


class A3Tests(unittest.TestCase):
    def test_marine_keeps_firing_range_while_slow_spear_is_still_far_away(self):
        own = unit(1, 16, 16, hp=60)
        ranged = unit(2, 21.4, 16, hp=18, ally=raw.Enemy)
        spear = unit(3, 18.6, 16, kind=73, hp=34, ally=raw.Enemy)
        point = a3._kite_position(own, ranged, [own], [ranged, spear], (16, 16), 14, config())
        self.assertLessEqual(a3._distance(point, a3._xy(ranged)), a3._range(own, ranged, config()))
        self.assertGreater(a3._distance(point, a3._xy(spear)), 1.1)

    def test_unescorted_marine_still_evades_fast_cavalry(self):
        own = unit(1, 16, 16, hp=60)
        ranged = unit(2, 21.4, 16, hp=18, ally=raw.Enemy)
        cavalry = unit(3, 18.6, 16, kind=105, hp=100, ally=raw.Enemy)
        point = a3._kite_position(own, ranged, [own], [ranged, cavalry], (16, 16), 14, config())
        self.assertGreater(a3._distance(point, a3._xy(cavalry)), 2.6)
    def test_low_hp_marine_does_not_take_the_same_trade(self):
        own = unit(1, 16, 16, hp=12)
        ranged = unit(2, 21.4, 16, hp=18, ally=raw.Enemy)
        spear = unit(3, 18.6, 16, kind=73, hp=34, ally=raw.Enemy)
        point = a3._kite_position(own, ranged, [own], [ranged, spear], (16, 16), 14, config())
        self.assertGreater(a3._distance(point, a3._xy(spear)), 2.6)



if __name__ == "__main__":
    unittest.main()

"""Offline selection, frozen-source and action-boundary checks."""
import copy
import hashlib
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import unittest

from sc2 import agent, lan, run
from sc2.arena_env import validate_actions
from sc2.policies import AI_CHOICES, load_policy
from .test_agent import arena, observation, unit
from s2clientprotocol import raw_pb2


class PolicySelectionTests(unittest.TestCase):
    def test_finalists_are_exact_frozen_sources_and_default_is_unchanged(self):
        self.assertIs(load_policy("default"), agent)
        expected = {"a": "efd0a00a2402706529e92635cf379bec99c544f5166262899fddc3aa4ce9cda9",
                    "b": "dff90014ee400c19974dc2d2f85f524c541c9c6d8748d815a08892d7c0c298c8"}
        for name, digest in expected.items():
            policy = load_policy(name)
            self.assertEqual(hashlib.sha256(Path(policy.__file__).read_bytes()).hexdigest(), digest)
            self.assertEqual(policy.CADENCE, 4)

    def test_selection_accepts_names_and_rejects_unknown_or_remote_human_choice(self):
        for name in AI_CHOICES:
            self.assertEqual(run.arguments(["human", "--ai", name.upper()]).ai, name)
            self.assertEqual(lan.arguments(["host", "--control", "ai", "--no-window", "--ai", name]).ai, name)
        with redirect_stderr(io.StringIO()):
            for args in (["human", "--ai", "missing"], ["human", "--ai", "../agent.py"]):
                with self.assertRaises(SystemExit):
                    run.arguments(args)
            with self.assertRaises(SystemExit):
                lan.arguments(["host", "--control", "human", "--ai", "a", "--hidden"])

    def test_each_policy_obeys_visible_action_boundary_and_does_not_mutate_inputs(self):
        for name in AI_CHOICES:
            policy = load_policy(name)
            config = arena()
            obs = observation(unit(1, 16, 16), unit(2, 18, 16, ally=raw_pb2.Enemy),
                              unit(3, 16, 17, ally=raw_pb2.Enemy, display=raw_pb2.Snapshot), loop=112)
            before = obs.SerializeToString()
            config_before = copy.deepcopy(config)
            actions, memory, reason = policy.decide(obs, {}, arena=config)
            self.assertTrue(actions)
            validate_actions(obs, actions)
            self.assertEqual(obs.SerializeToString(), before)
            self.assertEqual(config, config_before)
            json.dumps((memory, reason), allow_nan=False)
            obs.observation.game_loop += 4
            saved = copy.deepcopy(memory)
            actions, _, _ = policy.decide(obs, memory, arena=config)
            validate_actions(obs, actions)
            self.assertEqual(memory, saved)


if __name__ == "__main__":
    unittest.main()

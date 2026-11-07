"""Synthetic fixtures only: no SC2 process, GPU, renderer or human evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sc2 import eval as evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sc2 = self.root / "sc2"
        self.sc2.mkdir()
        for name in ("run.py", "record.py", "arena_env.py", "agent.py", "rules.json"):
            (self.sc2 / name).write_text("synthetic fixture: " + name, encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(evaluation, "SC2", self.sc2).start()
        patch.object(evaluation, "ROOT", self.root).start()
        self.manifest = dict(
            source="real_engine", mode="duel", status="complete", requested_version="4.10.0",
            selected_map="SyntheticFixture", seed=1, policy_slot=0, realtime=False, step_mul=4,
            game_speed=4, pysc2_version="fixture", opponent="hold", participant_player_ids=[7, 9],
            player_results={"7": 1, "9": 2}, map_sha256="a"*64, unit_data_sha256="b"*64,
            policy_sha256=evaluation.digest(self.sc2 / "agent.py"),
            rules_sha256=evaluation.digest(self.sc2 / "rules.json"),
            engine=[dict(game_version="fixture", base_build=75689, data_version="fixture-data", data_build=75689)]*2)
        self.reference = self.root / "reference.json"
        evaluation.save(self.reference, self.manifest)
        self.path = evaluation.create_group(self.root / "group", self.reference)

    def fixture_attempt(self, game=1, **changes):
        group = evaluation.load_group(self.path)
        row = group["definition"]["schedule"][game-1]
        directory = self.path.parent / ("game-%02d-attempt-01" % game)
        directory.mkdir()
        (directory / "game.SC2Replay").write_bytes(b"synthetic-not-a-replay")
        (directory / "observations.jsonl.gz").write_bytes(b"synthetic-not-observations")
        (directory / "events.jsonl").write_text('{"kind":"observation","action_errors":[]}\n', encoding="utf-8")
        manifest = dict(self.manifest, seed=row["seed"], policy_slot=row["policy_slot"],
                        replay_sha256=evaluation.digest(directory / "game.SC2Replay"), **changes)
        evaluation.save(directory / "manifest.json", manifest)
        group["attempts"].append(dict(game=game, attempt=1, state="finished", exit_code=0,
                                      run_directory=directory.name))
        evaluation.save(self.path, group)
        return directory

    def test_preregisters_exactly_four_seeds_on_both_sides_without_engine(self):
        group = evaluation.load_group(self.path)
        self.assertEqual([(r["seed"],r["policy_slot"]) for r in group["definition"]["schedule"]],
                         [(s, side) for s in (1,2,3,4) for side in (0,1)])
        self.assertEqual(evaluation.report(self.path)["outcomes"], {"pending":8})
        with self.assertRaises(FileExistsError):
            evaluation.create_group(self.path.parent, self.reference)

    def test_actual_player_ids_and_policy_side_define_result(self):
        self.fixture_attempt(1)
        self.fixture_attempt(2)
        summary = evaluation.report(self.path)
        self.assertEqual(summary["outcomes"], {"policy_win":1,"policy_loss":1,"pending":6})
        self.assertFalse(summary["all_eight_eligible"])

    def test_incomplete_end_and_synthetic_source_stay_unknown(self):
        self.fixture_attempt(1, player_results={"7":1})
        self.fixture_attempt(2, source="synthetic")
        self.fixture_attempt(3, status="watchdog", player_results={})
        self.assertEqual(evaluation.report(self.path)["outcomes"], {"unknown":3,"pending":5})

    def test_identity_drift_and_missing_replay_are_retained(self):
        self.fixture_attempt(1, game_speed=2)
        directory = self.fixture_attempt(2)
        (directory / "game.SC2Replay").unlink()
        summary = evaluation.report(self.path)
        self.assertEqual(summary["outcomes"], {"unknown":2,"pending":6})
        self.assertIn("fixed_conditions_changed", summary["rows"][0]["rejection_reasons"])

    def test_tie_is_not_win_and_action_errors_are_reported(self):
        directory = self.fixture_attempt(1, player_results={"7":3,"9":3})
        (directory / "events.jsonl").write_text('{"kind":"observation","delta_game_loops":8,"action_errors":[{"result":1}]}\n', encoding="utf-8")
        summary = evaluation.report(self.path)
        self.assertEqual(summary["outcomes"]["tie"], 1)
        self.assertEqual(summary["rows"][0]["action_errors"], 1)
        self.assertEqual(summary["rows"][0]["observed_step_counts"], {"8":1})

    def test_resume_does_not_repeat_failed_attempts(self):
        group = evaluation.load_group(self.path)
        group["attempts"].append(dict(game=1, attempt=1, state="started", exit_code=None,
                                      run_directory="game-01-attempt-01"))
        evaluation.save(self.path, group)
        with patch.object(evaluation.subprocess, "run", side_effect=OSError("test launch failure")) as launch:
            summary = evaluation.run_group(self.path)
        self.assertEqual(launch.call_count, 7)
        self.assertEqual(summary["outcomes"], {"unknown":8})
        with patch.object(evaluation.subprocess, "run") as launch:
            evaluation.run_group(self.path)
        launch.assert_not_called()

    def test_duplicate_attempt_or_external_path_is_rejected(self):
        self.fixture_attempt(1)
        group = evaluation.load_group(self.path)
        group["attempts"].append(dict(group["attempts"][0]))
        evaluation.save(self.path, group)
        with self.assertRaises(ValueError):
            evaluation.report(self.path)
        group["attempts"] = group["attempts"][:1]
        group["attempts"][0]["run_directory"] = "../elsewhere"
        evaluation.save(self.path, group)
        with self.assertRaises(ValueError):
            evaluation.report(self.path)

    def test_definition_and_source_changes_stop_execution(self):
        (self.sc2 / "agent.py").write_text("changed", encoding="utf-8")
        with patch.object(evaluation.subprocess, "run") as launch:
            with self.assertRaises(ValueError):
                evaluation.run_group(self.path)
        launch.assert_not_called()
        group = evaluation.load_group(self.path)
        group["definition"]["seeds"][0] = 99
        evaluation.save(self.path, group)
        with self.assertRaises(ValueError):
            evaluation.load_group(self.path)


if __name__ == "__main__":
    unittest.main()

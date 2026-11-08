"""Roster validation without running a compiler or game."""
import copy
import json
import re
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from sc2.maps.build import rule_source, combat_catalog, combat_strings


class BuildTest(unittest.TestCase):
    def test_current_and_preserved_native_rosters(self):
        root = (Path(__file__).parents[1] / "sc2")
        for name in ("rules.json", "legacy/rules-terran.json", "legacy/rules-melee-native.json"):
            rules = json.loads((root / name).read_text())
            self.assertTrue((root / name).parent.joinpath(rules["map_file"]).is_file())
            source = rule_source(rules)
            for index, unit in enumerate(rules["units"]):
                self.assertIn(f'arenaTypes[{index}] = "{unit}";', source)
            invalid = copy.deepcopy(rules)
            invalid["unit_type_ids"][0] = 999
            with self.assertRaises(AssertionError):
                rule_source(invalid)

    def test_preparation_loop_count_is_nonnegative_integer(self):
        rules = json.loads(((Path(__file__).parents[1] / "sc2") / "rules.json").read_text())
        self.assertIn("const int arenaReadyLoops = 112;", rule_source(rules))
        for value in (-1, 1.5, True):
            with self.assertRaises(AssertionError):
                rule_source(dict(rules, ready_game_loops=value))

    def test_map_localized_labels_resolve_to_unique_nonempty_text(self):
        root = Path(__file__).parents[1] / "sc2"
        rules = json.loads((root / "rules.json").read_text(encoding="utf-8"))
        strings = (root / "maps/src/enUS.SC2Data/LocalizedData/GameStrings.txt").read_text(encoding="utf-8")
        entries = [line.split("=", 1) for line in (strings + combat_strings(rules)).splitlines() if "=" in line]
        localized = dict(entries)
        self.assertEqual(len(entries), len(localized), "Duplicate localization keys")
        script = (root / "maps/src/MapScript.galaxy").read_text(encoding="utf-8")
        for key in re.findall(r'StringExternal\("([^"\n]+)"\)', script):
            with self.subTest(key=key):
                self.assertTrue(localized.get(key, "").strip(), "Missing visible label text")

    def test_counter_triangle_and_public_data(self):
        rules = json.loads(((Path(__file__).parents[1] / "sc2") / "rules.json").read_text())
        fast, slow, ranged = (rules["combat"][name] for name in ("Zergling", "Zealot", "Marine"))
        self.assertGreater(fast["speed"], ranged["speed"])
        self.assertLess(slow["speed"], ranged["speed"])
        self.assertGreater(slow["bonus_vs_armored"], 0)
        tree = ET.fromstring(combat_catalog(rules))
        armored = [u.get("id") for u in tree.findall("CUnit")
                   if u.find("Attributes[@index='Armored']").get("value") == "1"]
        self.assertEqual(armored, ["Zergling"])
        for unit in tree.findall("CUnit"):
            for field in ("LifeArmor", "LifeRegenRate", "ShieldsMax", "ShieldRegenRate"):
                self.assertEqual(unit.find(field).get("value"), "0")
        for stats in rules["combat"].values():
            self.assertIn(stats["role"], combat_strings(rules))
        invalid = copy.deepcopy(rules)
        invalid["combat"]["Zealot"]["period"] = float("nan")
        with self.assertRaises(AssertionError):
            combat_catalog(invalid)

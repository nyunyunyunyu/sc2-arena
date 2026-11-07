"""Roster validation without running a compiler or game."""
import copy
import json
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from .build import rule_source, combat_catalog, combat_strings


class BuildTest(unittest.TestCase):
    def test_current_and_preserved_native_rosters(self):
        root = Path(__file__).parents[1]
        for name in ("rules.json", "rules-terran.json", "rules-melee-native.json"):
            rules = json.loads((root / name).read_text())
            source = rule_source(rules)
            for index, unit in enumerate(rules["units"]):
                self.assertIn(f'arenaTypes[{index}] = "{unit}";', source)
            invalid = copy.deepcopy(rules)
            invalid["unit_type_ids"][0] = 999
            with self.assertRaises(AssertionError):
                rule_source(invalid)

    def test_counter_triangle_and_public_data(self):
        rules = json.loads((Path(__file__).parents[1] / "rules.json").read_text())
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

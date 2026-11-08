"""Overlay this scenario on Blizzard's Flat32 using the existing StormLib smpq tool."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import math
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
RULES = HERE.parent / "rules.json"
TEMPLATE = HERE / "template" / "Flat32.SC2Map"
TEMPLATE_SHA256 = "718949ed134275bb071078bb522ecb3991b021b06e7d09f676293e888fac802a"


def combat_catalog(rules):
    """Override shared map data: both players receive exactly the same rules."""
    root = ET.Element("Catalog")
    combat = rules.get("combat", {})
    if not combat:
        return ET.tostring(root, encoding="unicode")
    assert set(combat) == set(rules["units"]) == {"Zergling", "Zealot", "Marine"}
    weapons = {"Zergling": "Claws", "Zealot": "PsiBlades", "Marine": "GuassRifle"}
    for name, stats in combat.items():
        assert stats["attacks"] == (2 if name == "Zealot" else 1)
        for field in ("hp", "speed", "damage", "period", "range"):
            assert math.isfinite(stats[field]) and stats[field] > 0, (name, field)
        assert math.isfinite(stats["bonus_vs_armored"]) and stats["bonus_vs_armored"] >= 0
        unit = ET.SubElement(root, "CUnit", id=name)
        for field, value in dict(LifeMax=stats["hp"], LifeStart=stats["hp"],
                LifeArmor=0, LifeRegenRate=0, ShieldsMax=0, ShieldsStart=0,
                ShieldRegenRate=0, Speed=stats["speed"], SpeedMultiplierCreep=1).items():
            ET.SubElement(unit, field, value=str(value))
        # Only the fast melee is Armored, so the spear bonus targets that role alone.
        ET.SubElement(unit, "Attributes", index="Light", value="0" if stats["armored"] else "1")
        ET.SubElement(unit, "Attributes", index="Armored", value="1" if stats["armored"] else "0")
        weapon = ET.SubElement(root, "CWeaponLegacy", id=weapons[name])
        ET.SubElement(weapon, "Range", value=str(stats["range"]))
        ET.SubElement(weapon, "Period", value=str(stats["period"]))
        effect = ET.SubElement(root, "CEffectDamage", id=weapons[name])
        ET.SubElement(effect, "Amount", value=str(stats["damage"]))
        ET.SubElement(effect, "AttributeBonus", index="Armored", value=str(stats["bonus_vs_armored"]))
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def combat_strings(rules):
    lines, hud = [], []
    for name, stats in rules.get("combat", {}).items():
        role = stats["role"]
        damage = stats["damage"] * stats["attacks"]
        bonus = stats["bonus_vs_armored"] * stats["attacks"]
        detail = f'{role}: HP {stats["hp"]} | 移速 {stats["speed"]:g} | 攻击 {damage}'
        if bonus:
            detail += f' (+{bonus} 对轻骑)'
        detail += f' / {stats["period"]:g}秒 | 射程 {stats["range"]:g}'
        lines += [f"Unit/Name/{name}={role}", f"Unit/InfoTooltip/{name}={detail}"]
        hud.append(detail)
    hud += ["双方属性相同；无护甲/护盾/回血/技能。数值按场景秒。"]
    lines.append("Arena/CombatRules=" + "<n/>".join(hud))
    return "\n".join(lines) + "\n"


def rule_source(rules):
    assert rules["schema_version"] == 1 and rules["players"] == 2
    ready_loops = rules.get("ready_game_loops", 0)
    assert type(ready_loops) is int and ready_loops >= 0
    native_ids = {"Marine": 48, "Marauder": 51, "Hellion": 53, "Zealot": 73, "Zergling": 105}
    assert len(rules["units"]) == 3
    assert rules["unit_type_ids"] == [native_ids[name] for name in rules["units"]]
    assert len(rules["radii"]) == len(rules["phase_start_seconds"]) == len(rules["damage_per_second"]) == 5
    assert rules["phase_start_seconds"] == sorted(set(rules["phase_start_seconds"]))
    assert all(a > b > 0 for a, b in zip(rules["radii"], rules["radii"][1:]))
    assert rules["boundary_inclusive"] and rules["equal_count_result"] == "tie"
    lines = ["// Generated from sc2/rules.json by maps/build.py.",
             f"const bool arenaCustomCombat = {str(bool(rules.get('combat'))).lower()};",
             f"const fixed arenaCenterX = {rules['center'][0]};",
             f"const fixed arenaCenterY = {rules['center'][1]};",
             f"const int arenaDuration = {rules['duration_seconds']};",
             f"const int arenaReadyLoops = {rules.get('ready_game_loops', 0)};",
             "fixed[5] arenaRadii;", "int[5] arenaStarts;", "fixed[5] arenaDamage;",
             "string[3] arenaTypes;", "fixed[6] arenaSpawnX;", "fixed[6] arenaSpawnY;",
             "void InitArenaRules () {"]
    for field, name in [("radii", "arenaRadii"), ("phase_start_seconds", "arenaStarts"),
                        ("damage_per_second", "arenaDamage"), ("units", "arenaTypes")]:
        for i, value in enumerate(rules[field]):
            lines.append(f"    {name}[{i}] = {json.dumps(value)};")
    for i, (x, y) in enumerate(rules["spawn_positions"]):
        lines.extend([f"    arenaSpawnX[{i}] = {x};", f"    arenaSpawnY[{i}] = {y};"])
    return "\n".join(lines + ["}", ""])


def build():
    if hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Flat32 template does not match Blizzard's recorded source")
    rules = json.loads(RULES.read_text(encoding="utf-8-sig"))
    source = rule_source(rules)
    (HERE / "src" / "ArenaRules.galaxy").write_text(source, encoding="utf-8")
    (HERE / "src" / "Base.SC2Data" / "GameData" / "ArenaCombat.xml").write_text(
        combat_catalog(rules), encoding="utf-8")
    destination = (HERE.parent / rules["map_file"]).resolve()
    if destination.parent != HERE or destination.suffix != ".SC2Map":
        raise ValueError("map_file must name a .SC2Map directly inside sc2/maps")
    with tempfile.TemporaryDirectory(prefix="arena-map-", dir=HERE) as tmp:
        stage = Path(tmp)
        archive = stage / "ThreeSoldiersRing.SC2Map"
        shutil.copy2(TEMPLATE, archive)
        sources = sorted(path for path in (HERE / "src").rglob("*") if path.is_file())
        names = []
        for path in sources:
            name = path.relative_to(HERE / "src")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if str(name).replace('\\', '/') == "enUS.SC2Data/LocalizedData/GameStrings.txt":
                with target.open("a", encoding="utf-8") as stream:
                    stream.write("\n" + combat_strings(rules))
            names.append(str(name))
        subprocess.run(["smpq", "-a", "-f", str(archive), *names], cwd=stage, check=True)
        verify = stage / "verify"
        verify.mkdir()
        subprocess.run(["smpq", "-x", str(archive), *names], cwd=verify, check=True)
        for name in names:
            assert (stage / name).read_bytes() == (verify / name).read_bytes(), name
        shutil.copy2(archive, destination)
    print(json.dumps({"map": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                      "overlay_files_verified": len(names), "clock_verified": rules["clock_verified"]}))


if __name__ == "__main__":
    build()

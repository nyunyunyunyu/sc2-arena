# Three Soldiers Ring map

The current `ThreeSoldiersCounterRingClose.SC2Map` is built from Blizzard's old `Flat32.SC2Map` by replacing
the placed objects, Galaxy scenario script, trigger-editor metadata, title and
neutral marker data.
The original 32×32 terrain, pathing, player slots and native `VoidMulti.SC2Mod`
dependency are retained. Shared custom combat data implements cavalry, spear and archer
using Zergling, Zealot and Marine models. Each side gets the same data; normal vision
and zero upgrades remain. A neutral `Beacon_Terran` decoration has zero sight, radius and collision;
its fog visibility is public. This override never applies to either player's soldiers.

The authoritative match implementation is `src/MapScript.galaxy`. Shared parameters
come from `../rules.json`; `build.py` writes the corresponding `src/ArenaRules.galaxy`.
The map creates exactly one Zealot, Zergling and Marine per player. It never calls
the melee initialization, built-in computer AI or melee victory functions.

Both the original Terran-only `ThreeSoldiersRing.SC2Map` and native mixed-melee
`ThreeSoldiersMeleeRing.SC2Map` are preserved byte-for-byte. Their configurations
are `../rules-terran.json` and `../rules-melee-native.json`, so old replay map bindings
still resolve. The new default has no armor, shields, regeneration or special abilities.
Hazard damage still subtracts life directly.

`rules.json.combat` is the source for generated `ArenaCombat.xml` and localized unit
names/stat text. Cavalry is the only Armored unit, giving spear an exclusive bonus.
The standard weapon effects and visual actors are reused, including the spear's two
strikes per cycle. Data fields follow the original catalogs published by
[SC2Mapster](https://github.com/SC2Mapster/SC2GameData). No third-party runtime is added.
The map enables only move/attack/stop abilities; extra native skills are hidden and disabled.

All five circles use 280 small native neutral Beacon models at exact world-space
circle coordinates (96/76/56/36/16 points). They are invulnerable, unselectable and
untargetable, grant no vision and are excluded from the six combat-unit references
used for damage and results. The active circle has larger markers. Markers remain
visible in unexplored areas without revealing enemy units. The camera is centered
and set to distance 40 once at initialization, without changing pitch or yaw; it is
never periodically locked. The previous distance-60 `ThreeSoldiersCounterRing.SC2Map`
is retained byte-for-byte for replay bindings.

A map dialog displays the next shrink and time remaining in the native client.
A second native dialog displays all three custom stat lines and the shared rules.
API RGB omits dialogs and text tags, so the PySC2 renderer adds the public-clock HUD.
The battlefield circles are rendered by SC2 itself; they do not rely on the
approximate RGB projection in PySC2's renderer.

The script uses one 0.0625 `c_timeGame` periodic trigger. Sixteen map ticks equal one
scenario second. `observation.score.score` publishes elapsed scenario seconds;
it is **not** a reward or winner. API `player_result` is authoritative for winners.
Linux 4.10 calibration measured 4800 loops at exactly 300 scenario seconds;
`rules.json` records 0.0625 scenario seconds per loop. These are game/scenario
seconds, not wall seconds. Native movement speed is also per scenario second.
The hazard applies at integer scenario seconds, including the shrink instant, before
both teams are counted. An exact circle boundary is safe. Equal survivors, including
simultaneous elimination, is a tie. The map has a single completion latch.

## Rebuild

Use Python 3 and the existing [StormLib `smpq` tool](https://github.com/ladislav-zezula/StormLib).
On Ubuntu the package is `smpq`. From the repository root:

```sh
python3 sc2/maps/build.py
```

The builder checks the source template hash, invokes `smpq`, then re-extracts every
overlay and checks byte equality. It does not generate terrain or an MPQ format.
The empty `src/Triggers` removes stale melee editor actions. Galaxy is deliberately
the source of truth: saving this map from the graphical Trigger Editor would replace
the hand-authored script, so rebuild with this command after editing its source.

## Upstream source and licenses

- Template: [Blizzard's official Melee map pack](https://blzdistsc2-a.akamaihd.net/MapPacks/Melee.zip),
  linked from [the official SC2 API repository](https://github.com/Blizzard/s2client-proto#map-packs).
- `template/Flat32.SC2Map` SHA-256:
  `718949ed134275bb071078bb522ecb3991b021b06e7d09f676293e888fac802a`.
- Blizzard map/game content remains subject to the official
  [AI and Machine Learning License](https://blzdistsc2-a.akamaihd.net/AI_AND_MACHINE_LEARNING_LICENSE.html)
  and game terms; the source licenses of PySC2 or StormLib do not relicense it.
- The template's dependencies are Liberty, Swarm, Void and Void Multi, all resolved
  from the selected game installation. Target engine for the first release: Linux
  4.10.0.75689, data version `B89B5D6FA7CBF6452E721311BFBC6CB2`.
- Beacon unit/model: original `Beacon_Terran`, also used in Blizzard's official
  `MoveToBeacon.SC2Map`. It uses the installed game's
  `Assets/UI/Feedback/BeaconTerran/BeaconTerran.m3`; no game asset is extracted or
  redistributed separately. Only the map's decoration fields are overridden.

No Windows Editor invocation or SMAC/RL unit data is involved in this build.

## Diagnostics

`python -m sc2.maps.probe --case <case> --output <new-dir>` exercises clock,
simultaneous hazard elimination, one-sided elimination, timeout tie/unequal counts,
and inside/exact/outside boundary cases. All eight passed for the original Terran
roster on Linux 4.10; those results are not new-roster battle results. These are
explicit `purpose=map_probe` games and may refill health or remove units to isolate
rules; they are not unmodified policy evaluations.

`python -m sc2.maps.matchup_probe --output <new-dir>` runs five isolated matchup
checks on both sides. Its archer follows a simple circular attack/move path; it is
a diagnostic controller, not a human or the training policy. Wins must occur before
55 scenario seconds, excluding ring damage. Exact loaded HP, shields, weapon data,
speed and empty upgrades are asserted, and observations/replays/results are retained.
Both probes select hidden Windows clients on Windows and Linux 4.10 otherwise.

`python -m sc2.maps.render_probe --rings --output <new-dir>` captures the first
shrink in real OSMesa RGB, checks neutral/native counts and fog, then removes one
team diagnostically and requires the map to emit the correct `player_result`.
The OSMesa library and environment setup are documented in `../setup/README.md`.

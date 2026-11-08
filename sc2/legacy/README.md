# Historical replay assets

These files are retained for replay bindings and reproducibility, not used by the
default game. Map and rules bytes are unchanged; `map_file` stays relative to its
rules file. The current map remains in `../maps/` and the current rules in `../rules.json`.

| Map | Historical variant |
|---|---|
| `maps/ThreeSoldiersRing.SC2Map` | Original Terran roster |
| `maps/ThreeSoldiersMeleeRing.SC2Map` | Native mixed-melee roster |
| `maps/ThreeSoldiersCounterRing.SC2Map` | Custom counter roster, camera distance 60 |
| `maps/ThreeSoldiersCounterRingClose.SC2Map` | Camera distance 40 |
| `maps/ThreeSoldiersCounterRingNear.SC2Map` | Camera distance 30, before series preparation |

From the repository root, old roster configurations can still be selected explicitly:

```powershell
.\start.cmd duel --rules sc2/legacy/rules-terran.json
.\start.cmd duel --rules sc2/legacy/rules-melee-native.json
```

`watch` automatically finds archived maps for old manifests when their original
map location is absent. Keep `manifest.json` beside the replay and use the matching
engine version. An explicit `--map sc2/legacy/maps/<name>.SC2Map` is also supported;
replay and map SHA256 checks remain mandatory.

Historical acceptance notes are in [docs/history](../../docs/history/acceptance.md).

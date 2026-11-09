# Built-in AI policies

`default` uses the existing `sc2/agent.py`. `a` and `b` are byte-identical copies of the frozen finalists from the 2026-11-07 sealed tournament (Linux SC2 4.10). A won the final series 3–0; that result is not a human win-rate estimate or a Windows-version guarantee.

| Selection | Source SHA256 |
|---|---|
| a | `efd0a00a2402706529e92635cf379bec99c544f5166262899fddc3aa4ce9cda9` |
| b | `dff90014ee400c19974dc2d2f85f524c541c9c6d8748d815a08892d7c0c298c8` |

Both use the existing `decide(observation, memory, *, arena)` contract and four-loop cadence. They receive only their own normal-vision observation; action validation and map rules remain in the runner. Keep these snapshots unchanged when adding later AI versions.

## A2: human-match revision

`a2` keeps the original A snapshot available and addresses the three completed human wins in the 2026-11-07 18:54 series (`20261108T025425.166689Z-lan-join`, rounds 1–3). It brings the marine back behind its spear while kiting, makes the spear intercept a nearby cavalry raid before chasing other targets, and withdraws an unsupported cavalry intercepted away from its ranged support. Unit attributes, fog, cadence and map rules are unchanged.

The retained candidate won 3 of 4 Linux 4.10 regression games: 1–1 versus frozen A and 2–0 versus frozen B, with both sides swapped and zero action errors. Two earlier candidates failed and were not registered. This is AI regression evidence, not a human win rate or a guarantee on the Windows engine. Evidence: `sc2/runs/adaptation-20261108-a2-r3/validation.json`; each match retains its native replay and manifest.

## A3: ranged endgame revision

The latest completed human series (`20261108T032635.155474Z-lan-join`) contains one A2 win followed by two human wins. A3 keeps A2's team behavior and changes kiting only after its own spear is gone: when an opposing marine needs at most three shots to kill and our marine can absorb that marine's remaining HP plus two visible spear volleys, maintain firing range while keeping separation from the slow spear. Otherwise retain A2's retreat behavior. The margin is a tactical estimate, not a combat simulator.

The retained candidate completed six Linux 4.10 regression games (3 wins, 3 losses) with zero action errors, including side swaps against A2, A and B. Four separate A2 baseline games use the same seeds and side schedule against A/B. This small sample does not establish a higher human win rate. Prior broader candidates were discarded. Validation and recorded-observation comparisons are in `sc2/runs/adaptation-20261108-a3-r4/validation.json`; all native replays are retained there. A2 and the original finalists remain selectable.

## A4: retreat routes and cavalry rescue

The next six completed A3 human matches ended 4–2 for the human (`20261108T042325.292448Z-lan-join` and `20261108T042646.565497Z-lan-join`). A4 adds a penalty when the marine's retreat segment passes close to a visible melee attacker. A nearby cavalry also prioritizes finishing a raider that needs at most two hits and is threatening its marine, instead of automatically routing away from the enemy spear. A3's other targeting, timing and endgame behavior remain intact; all earlier policies stay selectable. This is a local risk estimate, not a prediction of enemy commands.

The retained candidate won both side-swapped games against A3. Across the six Linux 4.10 regression games against A3/A/B it went 3–3; two further games against a synthetic marine-raiding opponent were both wins, as were A3's matching baseline games. These results do not establish a higher human win rate or universal superiority. A broader first candidate went 1–5 and was discarded; a route-only second candidate went 3–3 but only 1–1 against A3. All candidates and native replays remain under `sc2/runs/adaptation-20261108-a4-r*/`; the retained evidence is `sc2/runs/adaptation-20261108-a4-r3/validation.json`. Recorded-human-state checks validate actions and input purity, not counterfactual wins. No A4 human rematch has been played yet.

## A5: frozen trial release

`a5` is the byte-identical `v5-target-v2` candidate, SHA256 `4dbe4f7cd09176e4d7bf049be0339bfb0a781d7c6ac1ac43bc8110991e813581`. Choose `7` / `a5` in the existing menu, or `start.cmd human --ai a5`. The default and all earlier selections remain available. Keep this trial snapshot fixed while later candidates are evaluated.

It preserves the spear's second strike after an automatic attack, retries missing attack orders after four loops, and keeps its ranged target inside firing reach when kiting. When the enemy ranged unit is fully protected by a visible spear, the cavalry can instead intercept a safely reachable enemy cavalry using the full four-loop public movement budget. Safety checks apply to the actual attack point and approach segment, rather than requiring every point around the enemy to be safe. Unit attributes, normal vision, map rules and decision cadence are unchanged. The subsequent ranged cooldown-window and cavalry return-route experiments are not included.

Linux SC2 4.10 headless screening: **14/14 wins**, zero action or infrastructure errors. The following complete validation series used six frozen opponents, both participant slots and seeds 201/202: **22/24 wins**, two losses, no draws, zero action or infrastructure errors. The losses were B / candidate slot 0 / seed 201 and A2 / candidate slot 0 / seed 202. All 24 native replays and identities were independently audited. This is a trial release, not a clean sweep. Subsequent human matches on Windows SC2 5.0.17 at 0.57× ended with one A5 win and two human wins (three recorded rounds in `sc2/runs/20261108T085050.253290Z-lobby-join/`).

| Opponent | Wins / games |
|---|---:|
| default | 4 / 4 |
| A | 4 / 4 |
| B | 3 / 4 |
| A2 | 3 / 4 |
| A3 | 4 / 4 |
| A4 | 4 / 4 |

Local evidence is retained under `.retro/goals/20261108-3e8d6a41/tasks/referee/runs/v5-target-v2-p2/` and `v5-target-v2-final-p2/`, including summaries, manifests and native replays. Thirteen self-contained policy regressions are in `tests/test_a5_policy.py`; launcher and runner tests also cover A5 selection and the recorded source hash. Offline checks validate behavior and integration, not counterfactual wins.

## A6: separately validated revision

`a6` is the byte-identical `v5-target-v8` candidate, SHA256 `b3dcc78c589d3cee62f20e2d57d4f466e66bacf24865c347c4c40dfaeda07f91`. Choose `8` / `a6` in the menu, or `start.cmd human --ai a6`. A5 remains its original fixed snapshot; defaults and all prior choices are unchanged.

A6 prepares ranged attacks before the next four-loop decision, routes cavalry returns around spears, and avoids detour segments that deepen collisions with visible units. It removes forced cavalry attacks on guarded spears. Safe cavalry alternatives require the ranged target itself to be inside an existing spear guard band and outside current attack reach; their actual approach still receives the full four-loop safety check. With its ranged ally gone, the spear favors a target already in reach over chasing a faster low-health enemy. Normal vision, equal attributes and decision cadence remain unchanged. These are local rules, not guarantees about future enemy movement.

Linux SC2 4.10 screening: **16/16 wins**. The following complete independent validation against **default, A, B, A2, A3 and A4** produced **24/24 wins** (four per opponent), using both participant slots and seeds **301/302**. A separate series against the fixed **A5** snapshot produced **4/4 wins**, again both slots and seeds 301/302. Both series had zero draws, action errors or infrastructure errors; native replays, source identities and actual uploaded map bytes were audited. The map used by these series is the frozen evaluation map, SHA256 `9c3580eef153647b821988a7e7870d75adbbe59572ca146f1b6fee4f93bf5d05`; this policy release does not change maps. A6 has not yet been validated in Windows-engine or human matches, and these results do not imply an unbeatable human opponent.

Local evidence: `.retro/goals/20261108-3e8d6a41/tasks/referee/runs/v5-target-v8-p2/`, `v5-target-v8-final-p2/` and `v5-target-v8-vs-a5/`. Twenty-two self-contained regressions are in `tests/test_a6_policy.py`; selection, source hashes and launcher paths are covered by the existing integration tests. A5's source and thirteen regressions remain intact.

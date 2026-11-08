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

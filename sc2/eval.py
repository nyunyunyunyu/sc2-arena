"""Fixed four-seed, both-seat evaluation through the existing sc2.run CLI."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SC2 = Path(__file__).resolve().parent
ROOT = SC2.parent
SEEDS = (1, 2, 3, 4)
ENGINE_FIELDS = ("game_version", "base_build", "data_version", "data_build")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def identity(manifest):
    engines = manifest.get("engine", [])
    if len(engines) != 2 or any(not all(e.get(k) for k in ENGINE_FIELDS[:3]) for e in engines):
        raise ValueError("Two actual engine identities are required")
    normalized = [{k: e.get(k) for k in ENGINE_FIELDS} for e in engines]
    if normalized[0] != normalized[1]:
        raise ValueError("Participant engine identities differ")
    fields = ("map_sha256", "policy_sha256", "rules_sha256", "unit_data_sha256", "step_mul", "game_speed", "pysc2_version")
    if any(not manifest.get(k) for k in fields):
        raise ValueError("Incomplete fixed conditions")
    for k in fields[:4]:
        value = manifest[k]
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Invalid artifact hash: " + k)
    if manifest.get("realtime") is not False:
        raise ValueError("Fixed evaluation requires non-realtime stepping")
    return dict({k: manifest[k] for k in fields}, engine=normalized, realtime=False)


def create_group(output, reference, seeds=SEEDS, max_game_loops=12000):
    seeds = list(seeds)
    if len(seeds) != 4 or len(set(seeds)) != 4 or any(not 0 <= s <= 2**32-1 for s in seeds):
        raise ValueError("Exactly four distinct seeds are required")
    manifest = read(reference)
    if manifest.get("source") != "real_engine" or manifest.get("mode") != "duel":
        raise ValueError("Reference must be a real-engine duel manifest")
    conditions = identity(manifest)
    source_names = ("run.py", "record.py", "arena_env.py", "agent.py", "rules.json")
    sources = {name: digest(SC2 / name) for name in source_names}
    if sources["agent.py"] != conditions["policy_sha256"] or sources["rules.json"] != conditions["rules_sha256"]:
        raise ValueError("Local policy/rules do not match the reference; run a fresh preflight")
    if max_game_loops <= 0:
        raise ValueError("Watchdog limit must be positive")
    definition = dict(schema_version=1, created_utc=now(), seeds=seeds,
                      fixed_conditions=conditions, source_sha256=sources,
                      reference_manifest=str(Path(reference).resolve()), reference_sha256=digest(reference),
                      selected_map=manifest["selected_map"], requested_version=manifest["requested_version"],
                      opponent="hold", max_game_loops=max_game_loops,
                      purpose="functional_fixed_opponent_evaluation",
                      schedule=[dict(game=i + 1, seed=seed, policy_slot=slot)
                                for i, (seed, slot) in enumerate((s, slot) for s in seeds for slot in (0, 1))])
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    group = dict(definition=definition, definition_sha256=fingerprint(definition), attempts=[])
    save(output / "group.json", group)
    return output / "group.json"


def load_group(path):
    group = read(path)
    if fingerprint(group["definition"]) != group["definition_sha256"]:
        raise ValueError("Evaluation definition changed")
    schedule = group["definition"]["schedule"]
    expected = [(s, side) for s in group["definition"]["seeds"] for side in (0, 1)]
    if len(schedule) != 8 or [(r["seed"], r["policy_slot"]) for r in schedule] != expected:
        raise ValueError("Invalid fixed schedule")
    games = [a["game"] for a in group["attempts"]]
    if len(games) != len(set(games)) or any(g not in range(1, 9) for g in games):
        raise ValueError("Repeated or unregistered attempt; never double-count a run")
    return group


def command(definition, row, output):
    return [sys.executable, "-m", "sc2.run", "duel", "--map", definition["selected_map"],
            "--version", definition["requested_version"], "--rules", str(SC2 / "rules.json"),
            "--seed", str(row["seed"]), "--policy-slot", str(row["policy_slot"]),
            "--step-mul", str(definition["fixed_conditions"]["step_mul"]),
            "--max-game-loops", str(definition["max_game_loops"]),
            "--opponent", "hold", "--output", str(output)]


def run_group(path):
    path = Path(path).resolve()
    group = load_group(path)
    definition = group["definition"]
    for row in definition["schedule"]:
        if any(a["game"] == row["game"] for a in group["attempts"]):
            continue  # A crash/unknown is retained, never silently replayed until it wins.
        if any(digest(SC2 / name) != expected for name, expected in definition["source_sha256"].items()):
            raise ValueError("Runtime/policy/rules changed after preregistration")
        directory = path.parent / ("game-%02d-attempt-01" % row["game"])
        if directory.exists():
            raise FileExistsError(directory)
        cmd = command(definition, row, directory)
        attempt = dict(game=row["game"], attempt=1, started_utc=now(), state="started",
                       run_directory=directory.name, command=cmd, exit_code=None)
        group["attempts"].append(attempt)
        save(path, group)  # Persist before launching, so interruption cannot erase the attempt.
        interrupted = False
        try:
            with (path.parent / (directory.name + ".stdout.log")).open("x", encoding="utf-8") as stdout, \
                 (path.parent / (directory.name + ".stderr.log")).open("x", encoding="utf-8") as stderr:
                result = subprocess.run(cmd, cwd=ROOT, stdout=stdout, stderr=stderr, check=False)
            attempt["exit_code"] = result.returncode
            attempt["state"] = "finished"
        except KeyboardInterrupt:
            attempt.update(state="interrupted", error="Evaluator interrupted; inspect owned runtime cleanup")
            interrupted = True
        except OSError as error:
            attempt.update(state="launch_error", error=str(error))
        finally:
            attempt["finished_utc"] = now()
            save(path, group)
        if interrupted:
            break
    return report(path)


def assess(directory, definition, row, attempt):
    reasons = []
    result = dict(outcome="unknown", eligible=False, rejection_reasons=reasons, action_errors=0,
                  observed_step_counts={})
    try:
        manifest_path = directory / "manifest.json"
        manifest = read(manifest_path)
        result.update(manifest_sha256=digest(manifest_path), status=manifest.get("status"))
        if manifest.get("source") != "real_engine":
            reasons.append("not_real_engine")
        if identity(manifest) != definition["fixed_conditions"]:
            reasons.append("fixed_conditions_changed")
        for key, expected in (("seed", row["seed"]), ("policy_slot", row["policy_slot"]),
                              ("mode", "duel"), ("opponent", "hold")):
            if manifest.get(key) != expected:
                reasons.append(key + "_mismatch")
        if manifest.get("status") != "complete" or attempt.get("exit_code") != 0:
            reasons.append("not_complete_engine_terminal")
        ids = manifest.get("participant_player_ids", [])
        outcomes = manifest.get("player_results", {})
        if len(ids) != 2 or len(set(ids)) != 2:
            reasons.append("missing_participant_mapping")
            pair = ()
        else:
            pair = tuple(outcomes.get(str(pid)) for pid in ids)
        if pair not in ((1, 2), (2, 1), (3, 3)):
            reasons.append("unknown_or_inconsistent_engine_result")
        for filename in ("events.jsonl", "observations.jsonl.gz", "game.SC2Replay"):
            if not (directory / filename).is_file() or not (directory / filename).stat().st_size:
                reasons.append("missing_" + filename)
        if (directory / "game.SC2Replay").is_file():
            if digest(directory / "game.SC2Replay") != manifest.get("replay_sha256"):
                reasons.append("replay_hash_mismatch")
        if (directory / "events.jsonl").is_file():
            for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                result["action_errors"] += len(event.get("action_errors", []))
                delta = event.get("delta_game_loops")
                if event.get("kind") == "observation" and isinstance(delta, int) and delta > 0:
                    key = str(delta)
                    result["observed_step_counts"][key] = result["observed_step_counts"].get(key, 0) + 1
        if not reasons:
            value = pair[row["policy_slot"]]
            result.update(outcome={1: "policy_win", 2: "policy_loss", 3: "tie"}[value], eligible=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        reasons.append("unreadable_or_incomplete_evidence: " + str(error))
    return result


def report(path):
    path = Path(path).resolve()
    group = load_group(path)
    rows = []
    for row in group["definition"]["schedule"]:
        attempts = [a for a in group["attempts"] if a["game"] == row["game"]]
        if attempts:
            attempt = attempts[0]
            expected_dir = "game-%02d-attempt-01" % row["game"]
            if attempt["run_directory"] != expected_dir:
                raise ValueError("Attempt path is not its registered child directory")
            evidence = assess(path.parent / expected_dir, group["definition"], row, attempt)
            rows.append(dict(row, attempt=attempt, **evidence))
        else:
            rows.append(dict(row, outcome="pending", eligible=False, rejection_reasons=[]))
    counts = dict(Counter(row["outcome"] for row in rows))
    summary = dict(schema_version=1, definition_sha256=group["definition_sha256"],
                   planned_games=8, attempted_games=len(group["attempts"]), outcomes=counts, rows=rows,
                   all_eight_eligible=all(row["eligible"] for row in rows),
                   limitations=["Fixed hold-opponent functionality check, not human strength or independent random trials.",
                                "Unknown/error rows stay in the eight-row denominator; no automatic retries.",
                                "Replay hash and terminal manifest checked; replay decoding and real UI are separate acceptance checks."])
    save(path.parent / "summary.json", summary)
    lines = ["# Fixed eight-game evaluation", "", "Planned: 8; attempted: %d. Outcomes: %s." %
             (len(group["attempts"]), json.dumps(counts, sort_keys=True)), "",
             "| Game | Seed | Policy slot | Outcome | Evidence problems |", "|---:|---:|---:|---|---|"]
    lines.extend("| %d | %d | %d | %s | %s |" % (r["game"], r["seed"], r["policy_slot"], r["outcome"],
                 "; ".join(r["rejection_reasons"])) for r in rows)
    lines += ["", *summary["limitations"]]
    (path.parent / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    plan = commands.add_parser("plan", help="Register eight attempts without starting an engine")
    plan.add_argument("--identity-from", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--seeds", nargs=4, type=int, default=SEEDS)
    plan.add_argument("--max-game-loops", type=int, default=12000)
    for name in ("run", "report"):
        child = commands.add_parser(name)
        child.add_argument("--group", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "plan":
        result = str(create_group(args.output, args.identity_from, args.seeds, args.max_game_loops))
    elif args.operation == "run":
        result = run_group(args.group)
    else:
        result = report(args.group)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

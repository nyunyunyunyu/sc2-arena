"""Per-game evidence; retain gameplay protobufs and save images separately."""
import base64
import gzip
import hashlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def proto_dict(message):
    from google.protobuf.json_format import MessageToDict
    return MessageToDict(message, preserving_proto_field_name=True,
                         use_integers_for_enums=True)


def actual_results(observations):
    results = {}
    for obs in observations:
        for result in obs.player_result:
            if result.player_id in results and results[result.player_id] != result.result:
                raise ValueError("Players reported conflicting engine results")
            results[result.player_id] = result.result
    return results


def replay_speed(replay, base_build):
    import mpyq
    from s2protocol import versions
    archive = mpyq.MPQArchive(io.BytesIO(replay))
    details = versions.build(base_build).decode_replay_details(archive.read_file("replay.details"))
    return details["m_gameSpeed"]


class GameRecord:
    def __init__(self, directory, metadata):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=False)
        self.events = (self.path / "events.jsonl").open("x", encoding="utf-8")
        self.observations = gzip.open(self.path / "observations.jsonl.gz", "xt",
                                      encoding="utf-8", compresslevel=1)
        self.manifest = dict(metadata, schema_version=1, status="running",
                             started_utc=datetime.now(timezone.utc).isoformat(),
                             observation_encoding="base64 ResponseObservation protobuf",
                             observation_omitted_fields=["observation.render_data",
                                                         "observation.feature_layer_data"])
        self.started = time.monotonic()
        self.previous_loop = {}
        self.write_manifest()

    def write_manifest(self):
        temporary = self.path / "manifest.json.tmp"
        temporary.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        temporary.replace(self.path / "manifest.json")

    def event(self, kind, **fields):
        self.events.write(json.dumps(dict(kind=kind, elapsed_wall_seconds=time.monotonic() - self.started,
                                          **fields), ensure_ascii=False) + "\n")
        self.events.flush()

    def observe(self, obs):
        player = obs.observation.player_common.player_id
        loop = obs.observation.game_loop
        recorded = obs.__class__()
        recorded.CopyFrom(obs)
        recorded.observation.ClearField("render_data")
        recorded.observation.ClearField("feature_layer_data")
        encoded = base64.b64encode(recorded.SerializeToString()).decode("ascii")
        self.observations.write(json.dumps(dict(player_id=player, game_loop=loop,
                                               response_observation_pb=encoded)) + "\n")
        self.event("observation", player_id=player, game_loop=loop,
                   delta_game_loops=loop - self.previous_loop.get(player, loop),
                   score=obs.observation.score.score,
                   action_errors=[proto_dict(e) for e in obs.action_errors],
                   executed_actions=[proto_dict(a) for a in obs.actions],
                   player_result=[proto_dict(r) for r in obs.player_result])
        self.previous_loop[player] = loop

    def decision(self, obs, actions, reason):
        self.event("decision", player_id=obs.observation.player_common.player_id,
                   game_loop=obs.observation.game_loop, reason=reason,
                   requested_actions=[proto_dict(a) for a in actions])

    def finish(self, status, observations=(), **fields):
        try:
            results = actual_results(observations)
        except ValueError as exc:
            status, results = "error", {}
            fields["result_error"] = str(exc)
        self.manifest.update(status=status, player_results=results,
                             finished_utc=datetime.now(timezone.utc).isoformat(), **fields)
        self.observations.close()
        self.events.close()
        self.write_manifest()

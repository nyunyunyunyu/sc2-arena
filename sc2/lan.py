"""Two independent SC2 clients: LAN/VPN host/join, each controlled by a human or AI."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import select
import socket
import struct
import sys
import time

from absl import flags
from pysc2 import run_configs
from pysc2.lib import protocol
from s2clientprotocol import common_pb2, sc2api_pb2 as sc_pb

from . import agent
from .arena_env import interface_options, validate_actions
from .record import GameRecord, actual_results, proto_dict, replay_speed, sha256

MAX_MESSAGE = 65536
ENGINE_FIELDS = ("game_version", "base_build", "data_version", "data_build")


class PeerDisconnected(ConnectionError):
    pass


class Room:
    """Bounded JSON coordination only; SC2 carries game traffic itself."""
    def __init__(self, connection, timeout=120):
        self.socket = connection
        self.timeout = timeout
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.last_seen = self.last_ping = time.monotonic()
        self.peer_results = None
        self.ended = False
        self.session_id = None

    def send(self, message):
        body = json.dumps(message, allow_nan=False).encode("utf-8")
        if not 0 < len(body) <= MAX_MESSAGE:
            raise ValueError("Room message is too large")
        self.socket.settimeout(min(self.timeout, 5))
        self.socket.sendall(struct.pack("!I", len(body)) + body)

    def receive(self, expected=None, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)

        def exact(size):
            chunks = bytearray()
            while len(chunks) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Waiting for the other player timed out")
                self.socket.settimeout(remaining)
                part = self.socket.recv(size - len(chunks))
                if not part:
                    raise PeerDisconnected("The other player disconnected")
                chunks.extend(part)
            return bytes(chunks)

        size = struct.unpack("!I", exact(4))[0]
        if not 0 < size <= MAX_MESSAGE:
            raise ValueError("Invalid room message length")
        message = json.loads(exact(size))
        if not isinstance(message, dict):
            raise ValueError("Invalid room message")
        self.last_seen = time.monotonic()
        if message.get("kind") == "abort":
            raise PeerDisconnected("Peer stopped: " + str(message.get("reason", ""))[:500])
        if expected is not None and message.get("kind") != expected:
            raise ValueError("Unexpected room message: " + str(message.get("kind")))
        return message

    def authenticate(self, host, key):
        if host:
            self.session_id = secrets.token_hex(16)
            self.send(dict(kind="challenge", protocol=1, nonce=self.session_id))
            response = self.receive("auth")
            proof = hmac.new(key.encode(), self.session_id.encode(), hashlib.sha256).hexdigest()
            if not isinstance(response.get("proof"), str) or not hmac.compare_digest(response["proof"], proof):
                raise ValueError("房间口令不正确")
            self.send(dict(kind="authenticated", proof=hmac.new(
                key.encode(), ("host:" + self.session_id).encode(), hashlib.sha256).hexdigest()))
        else:
            challenge = self.receive("challenge")
            nonce = challenge.get("nonce")
            if challenge.get("protocol") != 1 or not isinstance(nonce, str) or len(nonce) != 32:
                raise ValueError("Incompatible room protocol")
            self.session_id = nonce
            self.send(dict(kind="auth", proof=hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest()))
            reply = self.receive("authenticated")
            expected = hmac.new(key.encode(), ("host:" + nonce).encode(), hashlib.sha256).hexdigest()
            if not isinstance(reply.get("proof"), str) or not hmac.compare_digest(reply["proof"], expected):
                raise ValueError("Host authentication failed")

    def accept_update(self, message):
        if message.get("kind") == "ping":
            return
        if message.get("kind") == "result":
            results = message.get("results")
            if (not isinstance(results, dict) or set(results) != {"1", "2"}
                    or any(type(v) is not int or v not in (1, 2, 3) for v in results.values())):
                raise ValueError("Invalid peer result")
            self.peer_results = results
            return
        raise ValueError("Unexpected in-game room message")

    def poll(self):
        if self.ended:
            return
        now = time.monotonic()
        if now - self.last_ping >= 2:
            self.send(dict(kind="ping"))
            self.last_ping = now
        for _ in range(8):
            if not select.select([self.socket], [], [], 0)[0]:
                break
            try:
                self.accept_update(self.receive(timeout=2))
            except PeerDisconnected:
                if self.peer_results is None:
                    raise
                self.ended = True
                break
        if self.peer_results is None and now - self.last_seen > 20:
            raise PeerDisconnected("No heartbeat from the other player for 20 seconds")

    def wait_saved(self):
        deadline = time.monotonic() + 30
        while True:
            message = self.receive(timeout=max(0, deadline - time.monotonic()))
            if message.get("kind") == "saved":
                return
            self.accept_update(message)

    def confirm_result(self, results):
        normalized = {str(k): v for k, v in results.items()}
        self.send(dict(kind="result", results=normalized))
        deadline = time.monotonic() + 10
        while self.peer_results is None:
            self.accept_update(self.receive(timeout=max(0, deadline - time.monotonic())))
        if self.peer_results != normalized:
            raise ValueError("The clients reported different results")


def open_room(args):
    host = args.mode == "host"
    key = args.key
    if host:
        key = key or secrets.token_urlsafe(12)
        print(f"房间口令：{key}\n等待另一位玩家连接 {args.address}:{args.port}", flush=True)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.settimeout(args.timeout)
            listener.bind((args.address, args.port))
            listener.listen(1)
            connection, _ = listener.accept()
    else:
        key = key or getpass.getpass("房间口令：")
        connection = socket.create_connection((args.address, args.port), timeout=args.timeout)
    room = Room(connection, args.timeout)
    try:
        room.authenticate(host, key)
        return room
    except BaseException:
        connection.close()
        raise


def check_hello(local, peer):
    if peer.get("kind") != "hello" or peer.get("protocol") != 1:
        raise ValueError("Incompatible room protocol")
    if peer.get("control") not in ("human", "ai"):
        raise ValueError("Invalid player control")
    for key in ("engine", "map_sha256", "rules_sha256"):
        if peer.get(key) != local[key]:
            raise ValueError(f"双方 {key} 不一致，请使用相同游戏版本、地图和规则")
    policy_hash = peer.get("policy_sha256")
    if (not isinstance(policy_hash, str) or len(policy_hash) != 64
            or any(c not in "0123456789abcdef" for c in policy_hash)):
        raise ValueError("Invalid peer policy identity")


def join_request(host_ip, port, control, role):
    options = interface_options()
    options.raw_affects_selection = False
    # SC2 may assign duplicate player names to the same slot.
    request = sc_pb.RequestJoinGame(
        race=common_pb2.Terran, options=options, player_name=f"{role}-{control}", host_ip=host_ip,
        server_ports=sc_pb.PortSet(game_port=port + 1, base_port=port + 2),
        client_ports=[sc_pb.PortSet(game_port=port + 3, base_port=port + 4)])
    return request


def local_actions(control, obs, memory, arena, next_loop):
    loop = obs.observation.game_loop
    if control == "human":
        return [], memory, {"mode": "human", "input": "native_windows"}, next_loop
    if loop < next_loop:
        return [], memory, {"mode": "policy_cadence_wait"}, next_loop
    actions, memory, reason = agent.decide(obs, memory, arena=arena)
    validate_actions(obs, actions)
    return actions, memory, reason, loop + agent.CADENCE


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("host", "join"))
    parser.add_argument("--control", choices=("human", "ai"), default="human")
    parser.add_argument("--address", help="Host bind IPv4 (default 0.0.0.0), or host LAN/VPN IPv4 to join")
    parser.add_argument("--port", type=int, default=14380, help="Room TCP port; game uses the next four ports")
    parser.add_argument("--key", help="Room password; host generates one if omitted, join prompts")
    parser.add_argument("--version", default="latest" if sys.platform == "win32" else "4.10.0")
    parser.add_argument("--rules", type=Path, default=Path(__file__).with_name("rules.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-game-loops", type=int, default=12000, help="Watchdog only; never a win")
    parser.add_argument("--hidden", action="store_true", help="Hide the client for diagnostics; no human input")
    parser.add_argument("--yes", action="store_true", help="Skip the local pre-launch ready prompt")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65531 or not 1 <= args.timeout <= 600 or args.max_game_loops <= 0:
        parser.error("Invalid port, timeout or loop limit")
    if args.mode == "join" and not args.address:
        parser.error("join requires --address with the host LAN/VPN IPv4")
    args.address = args.address or "0.0.0.0"
    try:
        address = ipaddress.IPv4Address(args.address)
        if args.mode == "join" and address.is_unspecified:
            raise ValueError()
    except ValueError:
        parser.error("--address must be a usable IPv4 address")
    if args.key is not None and not 8 <= len(args.key) <= 128:
        parser.error("Room key must contain 8 to 128 characters")
    if sys.platform != "win32" and not args.hidden:
        parser.error("Visible LAN play currently requires Windows; headless AI may use --hidden")
    if sys.platform != "win32" and args.control == "human":
        parser.error("Native human LAN control currently requires Windows")
    return args


def play(args):
    rules_bytes = args.rules.read_bytes()
    rules = json.loads(rules_bytes.decode("utf-8-sig"))
    map_path = args.rules.parent / rules["map_file"]
    map_bytes = map_path.read_bytes()
    output = args.output or Path(__file__).parent / "runs" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-lan-" + args.mode)
    record = GameRecord(output, dict(mode="lan", connection_role=args.mode,
        local_control=args.control, hidden=args.hidden, source="real_engine_diagnostic" if args.hidden else "real_engine",
        runner_sha256=sha256(Path(__file__).read_bytes()),
        realtime=True, step_mul=agent.CADENCE, map_sha256=sha256(map_bytes),
        arena_map_sha256=sha256(map_bytes), rules_sha256=sha256(rules_bytes),
        policy_sha256=sha256(Path(agent.__file__).read_bytes()), selected_map=str(map_path),
        requested_version=args.version, observations_scope="local player only"))
    room = process = controller = observation = None
    status, failure, joined, errors = "error", None, False, 0
    try:
        if not args.yes:
            input(f"本方选择 {args.control}。准备好后按 Enter；双方连接并加载完成会自动开始。")
        room = open_room(args)
        record.manifest["room_id"] = room.session_id
        record.event("peer_connected", address=room.socket.getpeername()[0])
        if not flags.FLAGS.is_parsed():
            flags.FLAGS(["sc2_lan"])
        options = {}
        if sys.platform == "win32":
            from .windows_env import set_client_window, window_options
            options = window_options(not args.hidden)
        config = run_configs.get(version=args.version)
        process = config.start(want_rgb=False, host="127.0.0.1",
            extra_ports=list(range(args.port + 1, args.port + 5)),
            timeout_seconds=min(args.timeout, 60), **options)
        controller = process.controller
        record.event("client_started", pid=process.pid, api_port=process.port)
        if sys.platform == "win32" and args.hidden:
            set_client_window(process.pid)
        ping = proto_dict(controller.ping())
        engine = {key: ping[key] for key in ENGINE_FIELDS}
        hello = dict(kind="hello", protocol=1, control=args.control, engine=engine,
                     **{k: record.manifest[k] for k in ("map_sha256", "rules_sha256", "policy_sha256")})
        room.send(hello)
        peer = room.receive("hello")
        check_hello(hello, peer)
        record.manifest.update(peer_control=peer["control"], peer_policy_sha256=peer["policy_sha256"],
                               engine=[engine, peer["engine"]])
        record.write_manifest()
        print(f"已连接：本方 {args.control}，对方 {peer['control']}。版本与地图一致，正在加载。", flush=True)
        map_name = "sc2arena/" + map_path.name
        host = args.mode == "host"
        host_ip = room.socket.getsockname()[0] if host else room.socket.getpeername()[0]
        # Same-machine clients share SC2's map cache: serialize writes before either opens it.
        if host:
            controller.save_map(map_name, map_bytes)
            room.send(dict(kind="map_saved"))
            room.receive("map_saved")
        else:
            room.receive("map_saved")
            controller.save_map(map_name, map_bytes)
            room.send(dict(kind="map_saved"))
        if host:
            create = sc_pb.RequestCreateGame(
                local_map=sc_pb.LocalMap(map_path=map_name), realtime=True, disable_fog=False, random_seed=1)
            create.player_setup.add(type=sc_pb.Participant)
            create.player_setup.add(type=sc_pb.Participant)
            controller.create_game(create)
            room.send(dict(kind="join"))
        else:
            room.receive("join")
        response = controller.join_game(join_request(host_ip, args.port, args.control, args.mode))
        joined = True
        player_id = response.player_id
        if sys.platform == "win32":
            set_client_window(process.pid, None if args.hidden else
                              f"三兵缩圈 — {'真人操作' if args.control == 'human' else 'AI 对战观战'} — 玩家 {player_id}")
        observation = controller.observe()
        record.observe(observation)
        data = controller.data()
        unit_data = {str(k): proto_dict(v) for k, v in data.unit_stats.items() if k in rules["unit_type_ids"]}
        encoded = json.dumps(unit_data, sort_keys=True).encode()
        (record.path / "unit_data.json").write_bytes(encoded)
        arena = dict(rules, unit_data=unit_data)
        record.manifest.update(arena=arena, unit_data_sha256=sha256(encoded), local_player_id=player_id)
        room.send(dict(kind="loaded", player_id=player_id, unit_data_sha256=sha256(encoded)))
        loaded = room.receive("loaded")
        if type(loaded.get("player_id")) is not int or set((player_id, loaded.get("player_id"))) != {1, 2}:
            raise ValueError(f"Invalid player slots: local={player_id}, peer={loaded.get('player_id')}")
        if loaded.get("unit_data_sha256") != sha256(encoded):
            raise ValueError("Loaded unit data do not match")
        record.manifest["participant_player_ids"] = [1, 2]
        record.manifest["peer_player_id"] = loaded["player_id"]
        record.write_manifest()
        print("比赛已开始。真人直接在 SC2 窗口操作；AI 模式请只观战。", flush=True)
        memory, next_loop, last_loop = {}, 0, -1
        while True:
            started = time.monotonic()
            room.poll()
            observation = controller.observe()
            record.observe(observation)
            errors += len(observation.action_errors)
            results = actual_results([observation])
            if results:
                if set(results) != {1, 2} or any(v not in (1, 2, 3) for v in results.values()):
                    raise ValueError("Incomplete native result")
                room.confirm_result(results)
                status = "complete"
                break
            loop = observation.observation.game_loop
            if loop >= args.max_game_loops:
                status = "watchdog"
                break
            if loop != last_loop:
                actions, memory, reason, next_loop = local_actions(args.control, observation, memory, arena, next_loop)
                record.decision(observation, actions, reason)
                if actions:
                    reply = controller.actions(sc_pb.RequestAction(actions=actions))
                    if reply:
                        rejected = [r for r in reply.result if r != 1]
                        errors += len(rejected)
                        if rejected:
                            record.event("action_rejected", codes=rejected, game_loop=loop)
                last_loop = loop
            # Realtime simulation never waits for this observation/policy sampler.
            time.sleep(max(0, agent.CADENCE / 22.4 - (time.monotonic() - started)))
    except KeyboardInterrupt:
        status, failure = "interrupted", "Local user interrupted"
    except (OSError, PeerDisconnected) as exc:
        status, failure = "disconnected", str(exc)
    except Exception as exc:
        status, failure = "error", repr(exc)
    finally:
        if failure:
            record.event("error", error=failure)
            print(f"对局未正常完成：{failure}", file=sys.stderr, flush=True)
        if room and status != "complete":
            try:
                room.send(dict(kind="abort", reason=failure or status))
            except OSError:
                pass
        if controller and joined:
            # Serialize replay generation and keep both engines alive until both saves finish.
            complete = status == "complete"
            if complete and args.mode == "join":
                try:
                    room.wait_saved()
                except Exception as exc:
                    record.event("replay_sync_error", error=repr(exc))
            try:
                replay = controller.save_replay()
                speed = replay_speed(replay, record.manifest["engine"][0]["base_build"])
                (record.path / "game.SC2Replay").write_bytes(replay)
                record.manifest.update(replay_sha256=sha256(replay),
                    game_speed=speed,
                    game_speed_source="replay.details.m_gameSpeed")
            except Exception as exc:
                record.event("replay_error", error=repr(exc))
                record.manifest["replay_error"] = repr(exc)
                if complete:
                    status, failure = "error", "Replay save failed: " + repr(exc)
            if complete:
                try:
                    room.send(dict(kind="saved"))
                    if args.mode == "host":
                        room.wait_saved()
                except Exception as exc:
                    record.event("replay_sync_error", error=repr(exc))
            try:
                if controller.status in (protocol.Status.in_game, protocol.Status.ended):
                    controller.leave()
            except Exception as exc:
                record.event("leave_error", error=repr(exc))
        if room:
            room.socket.close()
        if process:
            try:
                process.close()
            except Exception as exc:
                record.event("close_error", error=repr(exc))
                # PySC2 close may fail on a dead API socket before reaching its process cleanup.
                process._controller = None
                process.close()
        record.finish(status, [observation] if observation else (), error=failure, action_errors=errors)
        print(record.path / "manifest.json", flush=True)
    return 0 if status == "complete" else 2


def main(argv=None):
    return play(arguments(argv))


if __name__ == "__main__":
    raise SystemExit(main())

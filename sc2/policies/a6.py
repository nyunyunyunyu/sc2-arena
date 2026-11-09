"""Transparent three-unit policy; memory and engine-derived arena data are explicit."""

from __future__ import annotations

import math

from s2clientprotocol import raw_pb2, sc2api_pb2

ATTACK, MOVE, STOP = 23, 16, 4
UNIT_TYPES = (48, 51, 53)
CADENCE = 4


def _xy(unit):
    return (unit.pos.x, unit.pos.y)


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _inside(point, center, radius):
    distance = _distance(point, center)
    if distance <= radius:
        return point
    return tuple(c + (p - c) * radius / distance for p, c in zip(point, center))


def _unit_data(unit, arena):
    return arena.get("unit_data", {}).get(str(unit.unit_type), {})


def _weapon(unit, arena):
    return next((w for w in _unit_data(unit, arena).get("weapons", [])
                 if w.get("type", 1) in (1, 3)), None)


def _damage(unit, enemy, arena):
    weapon = _weapon(unit, arena)
    if weapon is None:
        return 0.0
    enemy_data = _unit_data(enemy, arena)
    bonus = sum(b["bonus"] for b in weapon.get("damage_bonus", [])
                if b["attribute"] in enemy_data.get("attributes", []))
    # shortcut: reserve primary-target damage only; add splash if replay evidence warrants it.
    shield, damage = float(enemy.shield), 0.0
    for _ in range(weapon.get("attacks", 1)):
        hit = weapon["damage"] + bonus
        # Zero-upgrade shields have no armor; body armor applies after shield overflow.
        absorbed = min(shield, hit)
        shield -= absorbed
        damage += absorbed
        if hit > absorbed:
            damage += max(0.5, hit - absorbed - enemy_data.get("armor", 0))
    return damage


def _range(unit, enemy, arena):
    weapon = _weapon(unit, arena)
    return (weapon["range"] + unit.radius + enemy.radius) if weapon else None


def _circle(arena, obs, own):
    if "center" not in arena:
        return None, None, {"clock_verified": False}
    center = tuple(arena["center"])
    radii, starts = arena["radii"], arena["phase_start_seconds"]
    if (len(center) != 2 or not all(math.isfinite(x) for x in center)
            or not radii or len(radii) != len(starts)
            or any(not math.isfinite(r) or r <= 0 for r in radii)
            or starts[0] != 0 or any(not math.isfinite(s) for s in starts)
            or any(b <= a for a, b in zip(starts, starts[1:]))
            or any(b > a for a, b in zip(radii, radii[1:]))):
        raise ValueError("Invalid arena circle geometry or schedule")
    verified = arena.get("clock_verified", False)
    phase, seconds = 0, None
    source = "unavailable"
    if (arena.get("clock_source") == "observation.score.score"
            and obs.HasField("score") and obs.score.HasField("score")):
        seconds, source = float(obs.score.score), "observation.score.score"
    elif verified:
        scale = arena.get("game_seconds_per_loop")
        if scale is None or not math.isfinite(scale) or scale <= 0:
            raise ValueError("A verified arena clock needs game_seconds_per_loop > 0")
        seconds = max(0, obs.game_loop - arena.get("origin_game_loop", 0)) * scale
        source = "calibrated_game_loop"
    if seconds is not None:
        phase = max(i for i, start in enumerate(starts) if start <= seconds)
    radius = radii[phase]
    anticipate = False
    if seconds is not None and phase + 1 < len(radii):
        travel = 0.0
        for unit in own:
            speed = _unit_data(unit, arena).get("movement_speed", 0)
            if speed > 0:
                travel = max(travel, max(0, _distance(_xy(unit), center) - radii[phase + 1] + 0.5) / speed)
        # shortcut: native speed is an ETA estimate; lock its clock relation in engine calibration.
        anticipate = starts[phase + 1] - seconds <= max(5.0, travel + 3.0)
        if anticipate:
            radius = radii[phase + 1]
    return center, radius, {"clock_verified": bool(verified), "game_seconds": seconds,
                            "clock_source": source,
                            "phase": phase, "radius": radii[phase],
                            "movement_radius": radius, "anticipating": anticipate}


def _command(ability, target=None):
    command = {"ability": ability}
    if isinstance(target, int):
        command["target_tag"] = target
    elif target is not None:
        command["point"] = [round(target[0], 3), round(target[1], 3)]
    return command


def _same(a, b):
    if not a or not b or a["ability"] != b["ability"]:
        return False
    if a.get("target_tag") != b.get("target_tag"):
        return False
    if "point" in a or "point" in b:
        return "point" in a and "point" in b and _distance(a["point"], b["point"]) <= 0.4
    return True


def _ordered(unit, command):
    if not unit.orders:
        return False
    order = unit.orders[0]
    # General and concrete Move/Attack ability IDs both occur in RAW orders.
    ability = {3794: MOVE, 3674: ATTACK, 3665: STOP}.get(order.ability_id, order.ability_id)
    current = _command(ability)
    if order.HasField("target_unit_tag"):
        current["target_tag"] = int(order.target_unit_tag)
    elif order.HasField("target_world_space_pos"):
        current["point"] = [order.target_world_space_pos.x, order.target_world_space_pos.y]
    return _same(current, command)


def _action(tag, command):
    result = sc2api_pb2.Action()
    raw = result.action_raw.unit_command
    raw.ability_id = command["ability"]
    raw.unit_tags.append(tag)
    raw.queue_command = False
    if "target_tag" in command:
        raw.target_unit_tag = command["target_tag"]
    elif "point" in command:
        raw.target_world_space_pos.x, raw.target_world_space_pos.y = command["point"]
    return result


def _kite_position(unit, target, own, enemies, center, radius, arena):
    pos = _xy(unit)
    guards = [a for a in own if a.unit_type == 73]
    threats = [e for e in enemies if (_weapon(e, arena) or {}).get("range", 0) < 2
               and _distance(pos, _xy(e)) < 7]
    reach = _range(unit, target, arena) or 5.5
    slow_hit = max((_damage(e, unit, arena) for e in threats if e.unit_type == 73), default=0)
    finish_ranged = (not guards and target.unit_type == 48
                     and target.health <= 3 * _damage(unit, target, arena)
                     and unit.health > target.health + 2 * slow_hit)
    def cost(p):
        danger = sum(max(0, (2.2 if finish_ranged and e.unit_type == 73 else 4.2) - _distance(p, _xy(e))) ** 2 *
                     (2.8 if e.unit_type == 105 else 1.3) for e in threats)
        guard = min((_distance(p, _xy(a)) for a in guards), default=0)
        danger += sum(max(0, (_range(e, unit, arena) or 0) + .5 - _distance(p, _xy(e))) ** 2 * 2
                      for e in enemies if e.unit_type == 48 and e.tag != target.tag)
        cavalry = [e for e in threats if e.unit_type == 105]
        shelter = 0
        if guards and cavalry:
            guard_unit = min(guards, key=lambda a: _distance(pos, _xy(a)))
            raider = min(cavalry, key=lambda e: _distance(pos, _xy(e)))
            dx, dy = guard_unit.pos.x - raider.pos.x, guard_unit.pos.y - raider.pos.y
            length = math.hypot(dx, dy) or 1
            behind = (guard_unit.pos.x + dx / length, guard_unit.pos.y + dy / length)
            shelter = _distance(p, behind) * 10
        # Protect the whole retreat segment, not only the destination near our spear.
        for enemy in threats:
            dx, dy = p[0] - pos[0], p[1] - pos[1]
            length2 = dx * dx + dy * dy
            t = max(0, min(1, ((enemy.pos.x - pos[0]) * dx + (enemy.pos.y - pos[1]) * dy) / length2)) if length2 else 0
            along = (pos[0] + t * dx, pos[1] + t * dy)
            clearance = (_range(enemy, unit, arena) or 1) + .6
            danger += max(0, clearance - _distance(along, _xy(enemy))) ** 2 * 45
        return (danger + max(0, _distance(p, _xy(target)) - reach + .3) * 1.8
                + max(0, guard - 1.5) * 3 + shelter)
    points = [pos]
    for i in range(16):
        angle = i * math.tau / 16
        p = (pos[0] + 1.1 * math.cos(angle), pos[1] + 1.1 * math.sin(angle))
        points.append(_inside(p, center, radius - .2) if center is not None else p)
    return min(points, key=cost)


def _cavalry_route(unit, target, own, enemies, center, radius):
    pos, goal = _xy(unit), _xy(target)
    dx, dy = goal[0] - pos[0], goal[1] - pos[1]
    length2 = dx * dx + dy * dy
    if length2 < .01:
        return None
    guard = next((a for a in own if a.unit_type == 73), None)
    def route_cost(q):
        cohesion = max(0, _distance(q, _xy(guard)) - 1.6) * 2 if guard is not None else 0
        return _distance(pos, q) + _distance(q, goal) + cohesion
    def unblocked(q):
        dx, dy = q[0] - pos[0], q[1] - pos[1]
        length2 = dx * dx + dy * dy
        for blocker in own + enemies:
            if blocker.tag == unit.tag:
                continue
            t = max(0, min(1, ((blocker.pos.x - pos[0]) * dx + (blocker.pos.y - pos[1]) * dy) / length2)) if length2 else 0
            separation = min(_distance(pos, _xy(blocker)), unit.radius + blocker.radius)
            if _distance((pos[0] + t * dx, pos[1] + t * dy), _xy(blocker)) + .001 < separation:
                return False
        return True
    obstacles = [e for e in enemies if e.unit_type == 73 and e.health > 10]
    for enemy in sorted(obstacles, key=lambda e: _distance(pos, _xy(e))):
        ep = _xy(enemy)
        distance = _distance(pos, ep)
        clearance = 2.2
        t = max(0, min(1, ((ep[0] - pos[0]) * dx + (ep[1] - pos[1]) * dy) / length2))
        segment = (pos[0] + dx * t, pos[1] + dy * t)
        if _distance(segment, ep) >= clearance:
            continue
        if distance < clearance:
            vx, vy = pos[0] - ep[0], pos[1] - ep[1]
            norm = math.hypot(vx, vy)
            if norm < .01:
                vx, vy, norm = -dx, -dy, math.sqrt(length2)
            point = (ep[0] + vx / norm * (clearance + .4), ep[1] + vy / norm * (clearance + .4))
            if guard is not None:
                base = math.atan2(vy, vx)
                options = [(ep[0] + (clearance + .4) * math.cos(base + offset),
                            ep[1] + (clearance + .4) * math.sin(base + offset))
                           for offset in (-.9, -.45, 0, .45, .9)]
                options = [q for q in options if unblocked(q)] or options
                # Inside the exclusion band, lateral progress must outrank the shortest backstep.
                point = min(options, key=lambda q: (_distance(q, goal), route_cost(q)))
        else:
            # Tangents keep a moving spear between the route and neither flank.
            base = math.atan2(pos[1] - ep[1], pos[0] - ep[0])
            angle = math.acos(min(1, clearance / distance)) + .25
            options = [(ep[0] + (clearance + .3) * math.cos(base + sign * angle),
                        ep[1] + (clearance + .3) * math.sin(base + sign * angle)) for sign in (-1, 1)]
            options = [q for q in options if unblocked(q)] or options
            point = min(options, key=route_cost)
        return _inside(point, center, radius - .2) if center is not None else point
    return None


def _safe_cavalry_fallback(unit, target, enemies, center, radius, arena):
    seconds_per_loop = arena.get("game_seconds_per_loop", 1 / 16)
    guards = [(e, (_range(e, unit, arena) or 1)
               + _unit_data(e, arena).get("movement_speed", 0) * CADENCE * seconds_per_loop
               + _unit_data(unit, arena).get("movement_speed", 0) * CADENCE * seconds_per_loop)
              for e in enemies if e.unit_type == 73 and e.health > 10]
    if _distance(_xy(unit), _xy(target)) <= (_range(unit, target, arena) or 0):
        return []
    if not any(_distance(_xy(target), _xy(guard)) < 2.2 for guard, _ in guards):
        return []
    alternatives = []
    for enemy in enemies:
        if enemy.unit_type != 105:
            continue
        reach = _range(unit, enemy, arena) or 0
        pos = _xy(unit)
        distance = _distance(pos, _xy(enemy))
        point = pos if distance <= reach else (enemy.pos.x + (pos[0] - enemy.pos.x) * max(0, reach - .05) / distance,
                                               enemy.pos.y + (pos[1] - enemy.pos.y) * max(0, reach - .05) / distance)
        if center is not None and _distance(point, center) > radius:
            continue
        dx, dy = point[0] - pos[0], point[1] - pos[1]
        length2 = dx * dx + dy * dy
        safe = True
        for guard, clearance in guards:
            t = max(0, min(1, ((guard.pos.x - pos[0]) * dx + (guard.pos.y - pos[1]) * dy) / length2)) if length2 else 0
            if _distance((pos[0] + t * dx, pos[1] + t * dy), _xy(guard)) < clearance:
                safe = False
                break
        if safe:
            alternatives.append(enemy)
    return alternatives


def decide(observation: sc2api_pb2.ResponseObservation, memory: dict, *,
           arena: dict | None = None) -> tuple[list[sc2api_pb2.Action], dict, dict]:
    """Use only this player's RAW observation; never retain enemy hidden state.

    arena adds map geometry/verified clock and optional engine ``unit_data``.
    ``attack_commit_loops`` defaults to a provisional 8-loop protection window.
    All returned memory/reasons are JSON compatible; inputs are not mutated.
    """
    arena = arena or {}
    obs = observation.observation
    loop, player_id = int(obs.game_loop), int(obs.player_common.player_id)
    if loop < arena.get("ready_game_loops", 0):
        return [], {}, {"mode": "preparation"}
    if memory.get("player_id") != player_id or loop < memory.get("game_loop", 0):
        memory = {}
    allowed = set(arena.get("unit_type_ids", UNIT_TYPES))
    dead = set(obs.raw_data.event.dead_units)
    units = [u for u in obs.raw_data.units if u.unit_type in allowed and u.tag not in dead
             and u.HasField("pos") and (not u.HasField("health") or u.health > 0)]
    own = sorted((u for u in units if u.alliance == raw_pb2.Self), key=lambda u: u.tag)
    enemies = [u for u in units if u.alliance == raw_pb2.Enemy
               and u.display_type == raw_pb2.Visible and not u.is_blip
               and u.cloak != raw_pb2.Cloaked]
    marine = next((u for u in own if u.unit_type == 48), None)
    spear = next((u for u in own if u.unit_type == 73), None)
    raiders = [e for e in enemies if e.unit_type == 105 and marine is not None
               and _distance(_xy(e), _xy(marine)) < 3.25]
    visible = {int(u.tag) for u in enemies}
    center, radius, ring = _circle(arena, obs, own)
    squad = tuple(sum(_xy(u)[i] for u in own) / len(own) for i in (0, 1)) if own else center
    actions, states, reasons, reserved = [], {}, [], {}
    commit = int(arena.get("attack_commit_loops", 6))
    if commit < 0:
        raise ValueError("attack_commit_loops must be nonnegative")

    # Larger shots are allocated first, so smaller shots can finish remaining health.
    own.sort(key=lambda u: (-max((_damage(u, e, arena) for e in enemies), default=0), u.tag))
    for unit in own:
        tag, pos = int(unit.tag), _xy(unit)
        prior = memory.get("units", {}).get(str(tag), {})
        cooldown = float(unit.weapon_cooldown) if unit.HasField("weapon_cooldown") else None
        shot = prior.get("shot_loop")
        if cooldown is not None and prior.get("cooldown") is not None and cooldown > prior["cooldown"] + 0.5:
            shot = loop
        unit_commit = 0 if unit.unit_type == 48 else commit
        can_move = (cooldown is not None and cooldown > (CADENCE if unit.unit_type == 48 else 2)
                    and shot is not None and loop - shot >= unit_commit)
        state = {"cooldown": cooldown, "shot_loop": shot}
        command, mode, target = None, "hold", None
        candidates = []
        outside = center is not None and _distance(pos, center) > radius
        if outside:
            command = _command(MOVE, _inside(pos, center, max(0, radius - 0.5)))
            mode = "enter_circle"
        else:
            for enemy in enemies:
                distance, reach = _distance(pos, _xy(enemy)), _range(unit, enemy, arena)
                in_range = reach is not None and distance <= reach
                safe_target = center is None or _distance(_xy(enemy), center) <= radius
                if (in_range or safe_target) and distance <= (max(reach + 4, 9) if reach is not None else 9) and _distance(_xy(enemy), squad) <= 10:
                    candidates.append(enemy)

            def target_score(enemy):
                hp = enemy.health + enemy.shield if enemy.HasField("health") else None
                left = None if hp is None else hp - reserved.get(int(enemy.tag), 0)
                damage = _damage(unit, enemy, arena)
                role = {105: {48: -7, 105: 0, 73: 8},
                        73: {105: 4}, 48: {105: -1.5, 48: -1}}.get(unit.unit_type, {}).get(enemy.unit_type, 0)
                if unit.unit_type == 73 and enemy.unit_type == 105 and _distance(pos, _xy(enemy)) <= (_range(unit, enemy, arena) or 0) + .15:
                    role = -12
                if unit.unit_type == 73 and enemy in raiders:
                    role = -14
                if (unit.unit_type == 105 and enemy.unit_type == 105 and marine is not None
                        and enemy.health <= 2 * damage and _distance(_xy(enemy), _xy(marine)) < 3.25):
                    role = -16
                if unit.unit_type == 48:
                    distance = _distance(pos, _xy(enemy))
                    role = (-6 if distance <= (_range(unit, enemy, arena) or 0) + .1 else 2) if enemy.unit_type == 48 else 0
                    if enemy.unit_type == 105 and distance < 3.5:
                        role = -8
                return (left is not None and left <= 0,
                        unit.unit_type == 73 and bool(raiders) and enemy not in raiders,
                        unit.unit_type == 73 and marine is None
                        and _distance(pos, _xy(enemy)) > (_range(unit, enemy, arena) or 0),
                        not (left is not None and 0 < left <= damage),
                        _distance(pos, _xy(enemy)) + role - damage * 0.12,
                        left if left is not None else float("inf"), int(enemy.tag))

            if candidates:
                target = min(candidates, key=target_score)
                if unit.unit_type == 105 and target.unit_type == 48:
                    alternatives = [e for e in _safe_cavalry_fallback(unit, target, enemies, center, radius, arena)
                                    if e in candidates]
                    if alternatives:
                        target = min(alternatives, key=target_score)
                command, mode = _command(ATTACK, int(target.tag)), "attack"
                reach = _range(unit, target, arena)
                in_range = reach is not None and _distance(pos, _xy(target)) <= reach
                neighbours = [ally for ally in own if ally.tag != tag and _distance(pos, _xy(ally)) <= 6]
                threats = [e for e in enemies if _distance(pos, _xy(e)) <= 6]
                weak = unit.health_max > 0 and unit.health / unit.health_max < 0.35
                isolated = _distance(pos, squad) > 4
                if unit.unit_type != 48 and can_move and (weak or isolated) and threats and len(threats) > len(neighbours):
                    others = [ally for ally in own if ally.tag != tag]
                    retreat = _xy(min(others, key=lambda a: _distance(pos, _xy(a)))) if others else center
                    if retreat is not None and _distance(pos, retreat) > 0.75:
                        command, mode = _command(MOVE, retreat), "regroup"
                elif can_move and in_range and _weapon(unit, arena).get("range", 0) > 1:
                    retreat = _kite_position(unit, target, own, enemies, center, radius, arena)
                    if _distance(pos, retreat) > .25:
                        command, mode = _command(MOVE, retreat), "guarded_kite"
                if unit.unit_type == 73 and target in raiders and not in_range and not (shot is not None and loop - shot < commit):
                    guard_point = _inside(_xy(target), _xy(marine), 1.4)
                    command, mode = _command(MOVE, guard_point), "protect_marine"
                if unit.unit_type == 105:
                    rescue = (target.unit_type == 105 and marine is not None
                              and target.health <= 2 * _damage(unit, target, arena)
                              and _distance(_xy(target), _xy(marine)) < 3.25
                              and _distance(pos, _xy(target)) <= (_range(unit, target, arena) or 0) + 1)
                    route = None if rescue else _cavalry_route(unit, target, own, enemies, center, radius)
                    if route is not None:
                        command, mode = _command(MOVE, route), "avoid_spear"
                    interceptors = [e for e in enemies if e.unit_type == 105 and _distance(pos, _xy(e)) < 1.5]
                    if target.unit_type == 48 and interceptors and spear is not None and marine is not None and _distance(pos, _xy(marine)) > 5.5:
                        support_route = _cavalry_route(unit, spear, own, enemies, center, radius)
                        command, mode = _command(MOVE, support_route if support_route is not None else _xy(spear)), "rejoin_support"

            else:
                old_target = prior.get("target_tag")
                order_target = unit.orders[0].target_unit_tag if unit.orders else 0
                stale = bool(old_target or order_target)
                if center is not None and _distance(pos, center) > 1:
                    destination = center
                    guard = next((a for a in own if a.unit_type == 73), None)
                    if unit.unit_type in (48, 105) and guard is not None:
                        destination = _inside(center, _xy(guard), 1.4)
                    command, mode = _command(MOVE, destination), "search_center"
                elif stale:
                    command, mode = _command(STOP), "clear_target"

        if command is not None and "point" in command and center is not None:
            # Leave room for coordinate rounding at non-cardinal circle edges.
            command = _command(command["ability"], _inside(command["point"], center, max(0, radius - 0.01)))
        last = prior.get("last_command")
        # A short missing order is not an idle signal. Preserve a submitted, still-valid shot.
        pending = (last and last["ability"] == ATTACK
                   and last.get("target_tag") in {int(e.tag) for e in candidates}
                   and ((shot is not None and loop - shot < unit_commit)
                        or (loop - prior.get("command_loop", -100) < (4 if unit.unit_type == 48 else 8)
                            and (shot is None or shot < prior.get("command_loop", 0)))))
        if pending and not outside and mode not in ("avoid_spear", "rejoin_support", "protect_marine") and command is not None and not _same(command, last):
            command, mode = last.copy(), "await_shot"
            target = next(e for e in enemies if e.tag == last["target_tag"])
        if target is not None:
            state["target_tag"] = int(target.tag)
            reach = _range(unit, target, arena)
            if (command is not None and command["ability"] == ATTACK
                    and reach is not None and _distance(pos, _xy(target)) <= reach
                    and (cooldown is None or cooldown <= CADENCE)):
                reserved[int(target.tag)] = reserved.get(int(target.tag), 0) + _damage(unit, target, arena)
        if unit.unit_type == 73 and shot is not None and loop - shot < commit and not outside:
            # Auto-attacks can start after a move; preserve both strikes regardless of prior intent.
            command, mode = None, "finish_double_strike"
            if last:
                state["last_command"], state["command_loop"] = last, prior.get("command_loop", loop)
        issued = False
        if command is not None:
            same = _same(command, last)
            if not _ordered(unit, command) and not (same and loop - prior.get("command_loop", -100) < 4):
                actions.append(_action(tag, command))
                state["last_command"], state["command_loop"] = command, loop
                issued = True
            elif last:
                state["last_command"], state["command_loop"] = last, prior.get("command_loop", loop)
        states[str(tag)] = state
        reasons.append({"tag": tag, "mode": mode, "target_tag": state.get("target_tag"),
                        "cooldown": cooldown, "shot_loop": shot, "move_after_shot": can_move,
                        "command": command, "issued": issued})

    new_memory = {"player_id": player_id, "game_loop": loop, "units": states}
    reason = {"game_loop": loop, "player_id": player_id, "visible_enemy_tags": sorted(visible),
              "ring": ring, "attack_commit_loops": commit,
              "unit_data_available": bool(arena.get("unit_data")), "units": reasons}
    return actions, new_memory, reason

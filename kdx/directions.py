"""
kdx/directions.py - which way the stick takes Mario from a point, and making a road leave in a chosen direction.

The game links a point's roads by compass direction worked out from the positions (calcConnect_ 0x601A3C64, see
rules.heading): the stick gives only north/south/east/west (getDirFromKeyMask_ 0x601D8424), and a point keeps one road
per direction. At a junction (three or four roads) every road must leave in its own direction, or the game keeps only
the last one. A road leaves in the direction of its first segment, so putting a bend (K) a short way out in the chosen
direction makes it leave that way without moving the point.

    roads = roads_at(world, pid)                         # [(route id, neighbour, direction or None)]
    table = compass(world, pid)                          # {"north": [route ids], ...} (2+ ids = a clash)
    plan = bend_for(world, pid, rid, "east", ground)     # (bend name or None, position): the bend to add or move
"""
from __future__ import annotations

import math

from . import rules

DIRS = ("north", "east", "south", "west")
VECTORS = {"north": (0.0, -1.0), "south": (0.0, 1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0)}
ARROWS = {"north": "↑", "south": "↓", "east": "→", "west": "←", None: "↕"}
REACH = 40.0             # how far out the bend goes


def roads_at(world, pid):
    out = []
    for r in world.route_rows:
        ends = rules.route_ends(r[0]) if r else None
        if not ends or pid not in ends:
            continue
        other = ends[1] if ends[0] == pid else ends[0]
        a, b = world.position(pid), world.position(other)
        out.append((r[0], other, rules.heading(a, b) if a is not None and b is not None else None))
    return out


def compass(world, pid):
    table = {d: [] for d in DIRS}
    table[None] = []
    for rid, _other, h in roads_at(world, pid):
        table[h].append(rid)
    return table


def far_end(world, pid, rid):
    """The point a road reaches beyond a run of editor bends: for display ('east: W102')."""
    seen = {pid}
    cur, road = pid, rid
    while True:
        ends = rules.route_ends(road)
        nxt = ends[1] if ends[0] == cur else ends[0]
        if nxt[0] != "K" or nxt in seen:
            return nxt
        seen.add(nxt)
        more = [r for r, o, _h in roads_at(world, nxt) if r != road]
        if len(more) != 1:
            return nxt
        cur, road = nxt, more[0]


def bend_for(world, pid, rid, direction, ground_at=None):
    """Where the road's first bend should be so it leaves pid going `direction`: (existing bend to move, or None for a
    new one, position). An existing bend is reused when it is the road's own (a K with exactly two roads)."""
    p = world.position(pid)
    ends = rules.route_ends(rid)
    other = ends[1] if ends[0] == pid else ends[0]
    q = world.position(other)
    dist = math.dist((p[0], p[2]), (q[0], q[2]))
    reach = min(REACH, max(8.0, dist * 0.4))
    vx, vz = VECTORS[direction]
    x, z = p[0] + vx * reach, p[2] + vz * reach
    y = p[1] + (q[1] - p[1]) * (reach / max(dist, 1e-6))
    if ground_at is not None:
        h = ground_at(x, z, y)
        if h is not None:
            y = h
    reuse = other if other[0] == "K" and len(roads_at(world, other)) == 2 else None
    return reuse, (x, y, z)


def free_direction(world, pid, want_towards, exclude=None):
    """The free direction closest to the bearing of want_towards (a position) from pid, or None if all four are used."""
    used = {d for d, rids in compass(world, pid).items() if d and [r for r in rids if r != exclude]}
    p = world.position(pid)
    ang = math.atan2(want_towards[2] - p[2], want_towards[0] - p[0])
    best = None
    for d in DIRS:
        if d in used:
            continue
        vx, vz = VECTORS[d]
        diff = abs((math.atan2(vz, vx) - ang + math.pi) % (2 * math.pi) - math.pi)
        if best is None or diff < best[0]:
            best = (diff, d)
    return best[1] if best else None

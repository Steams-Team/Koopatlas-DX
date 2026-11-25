"""
kdx/ground.py - roads that go through the ground.

The game moves Mario in a straight line from one point (or bend) to the next, at the heights of the two bones, and
nothing keeps him on the ground in between. A road drawn over a hill, or a point placed by interpolation, can therefore
look fine from above while Mario walks inside the hill in game (seen in a W1 test: Toad sunk to the waist on a domed
plateau). Climbs (ladders, vines) and jumps are left out: they leave the ground on purpose.

Only ground that faces up and lies a little above the line counts (at most REACH): bridges, arches and the roofs of
tunnels over a road are not "the ground Mario sinks into".

    g = Ground(scene)                           # the ground models (*_World) in a grid, for quick lookups
    depth, t = g.sink(a, b)                     # how far the straight line dips under the ground, and where (0..1)
    bends = g.follow(a, b)                      # bend positions (on the ground) that keep the walk above it
    g.problems(world) -> {route id: depth}
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from . import routeactions, rules

TOLERANCE = 6.0          # world units; Mario's feet may touch grass tufts and seams
WARN = 15.0              # the check: no vanilla road dips more than 14 (W4/W5b/W6a bends over bumps)
FIT = 4.0                # Follow the ground keeps every piece within this
REACH = 80.0             # ground further above the line than this is a roof or a bridge, not a hill
STEP = 20.0              # sample spacing along a road
CELL = 64.0


def walks_on_ground(rtype):
    return rtype not in rules.CLIMB_TYPES and routeactions.action(rtype).style not in ("jump", "climb")


class Ground:
    def __init__(self, scene):
        tris = [d.reshape(-1, 3, 9) for name, d in scene.meshes.items() if name.endswith("_World")]
        T = np.concatenate(tris) if tris else np.zeros((0, 3, 9), np.float32)
        up = T[:, 0, 4] > 0.2 if len(T) else np.zeros(0, bool)                # faces up (flat-shaded normal y)
        self.T = T[up][:, :, 0:3].astype(np.float64)
        self.grid = defaultdict(list)
        if len(self.T):
            lo = np.floor(self.T[:, :, [0, 2]].min(1) / CELL).astype(int)
            hi = np.floor(self.T[:, :, [0, 2]].max(1) / CELL).astype(int)
            for i, (a, b) in enumerate(zip(lo, hi)):
                for cx in range(a[0], b[0] + 1):
                    for cz in range(a[1], b[1] + 1):
                        self.grid[(cx, cz)].append(i)
        self.grid = {k: np.array(v) for k, v in self.grid.items()}

    def heights(self, x, z):
        """Heights of every upward-facing ground surface at (x, z)."""
        idx = self.grid.get((int(math.floor(x / CELL)), int(math.floor(z / CELL))))
        if idx is None:
            return np.zeros(0)
        T = self.T[idx]
        a, b, c = T[:, 0], T[:, 1], T[:, 2]
        v0x, v0z = c[:, 0] - a[:, 0], c[:, 2] - a[:, 2]
        v1x, v1z = b[:, 0] - a[:, 0], b[:, 2] - a[:, 2]
        v2x, v2z = x - a[:, 0], z - a[:, 2]
        den = v0x * v1z - v1x * v0z
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (v2x * v1z - v1x * v2z) / den
            v = (v0x * v2z - v2x * v0z) / den
            ok = (np.abs(den) > 1e-9) & (u >= 0) & (v >= 0) & (u + v <= 1)
        return a[ok, 1] + u[ok] * (c[ok, 1] - a[ok, 1]) + v[ok] * (b[ok, 1] - a[ok, 1])

    def above(self, x, y, z):
        """How far the ground is above a point (0 if it isn't, or only a roof is)."""
        h = self.heights(x, z)
        h = h[(h > y) & (h <= y + REACH)]
        return float(h.max() - y) if len(h) else 0.0

    def ground_at(self, x, z, near_y):
        """The ground surface at (x, z) closest to near_y (None if there is none)."""
        h = self.heights(x, z)
        return float(h[np.argmin(np.abs(h - near_y))]) if len(h) else None

    def sink(self, a, b, step=STEP):
        """(deepest dip below the ground, its place 0..1) of the straight line a-b."""
        n = max(2, int(math.dist((a[0], a[2]), (b[0], b[2])) / step))
        best, where = 0.0, None
        for i in range(1, n):
            t = i / n
            d = self.above(*(a[k] + (b[k] - a[k]) * t for k in range(3)))
            if d > best:
                best, where = d, t
        return best, where

    def follow(self, a, b, tol=TOLERANCE, depth=0):
        """Bend positions between a and b, each on the ground, so that no straight piece dips more than tol under it
        (splits at the deepest dip, then each half again)."""
        d, t = self.sink(a, b)
        if d <= tol or t is None or depth > 6:
            return []
        x, y, z = (a[k] + (b[k] - a[k]) * t for k in range(3))
        m = (x, y + d, z)
        return self.follow(a, m, tol, depth + 1) + [m] + self.follow(m, b, tol, depth + 1)

    def problems(self, world, tol=TOLERANCE):
        """{route id: (depth, place 0..1)} of the roads Mario would walk through the ground on."""
        out = {}
        for r in world.route_rows:
            ends = rules.route_ends(r[0]) if r else None
            if not ends or not walks_on_ground(r[1] if len(r) > 1 else ""):
                continue
            a, b = world.position(ends[0]), world.position(ends[1])
            if a is None or b is None:
                continue
            d, t = self.sink(a, b)
            if d > tol:
                out[r[0]] = (d, t)
        return out

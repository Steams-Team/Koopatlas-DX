"""
kdx/ridealong.py - the ride-along camera preview: Mario walks a path and the view shows what the map camera would.

    ride = Ride(world, ["RW101W102"], blend="linear")
    pos, offset, label = ride.sample(distance)          # where Mario is, the camera offset there, what is shown

The walk is the game's own: a path bone's walk points (rules.paths_from_bones), at their bone positions. The camera
sits at Mario + the MarioOffset vector of the points he passes (the camera table, kdx.offsets). How the game blends
between two points' cameras is not known yet, so two rules are offered: "linear" (blend by distance along the walk)
and "hold" (keep a point's camera until the next point). Points without a camera record, or with a zero vector, keep
the camera of the point before them (the first ones take the next one, or DEFAULT).
"""
from __future__ import annotations

import bisect
import math

from . import rules

DEFAULT = (0.0, 400.0, 600.0)     # a camera above and behind Mario when no point has one
BLENDS = ("linear", "hold")


def path_walks(world):
    """[(path id, [point names from one end to the other])] in bone order, as the game reads them."""
    return [(p, [p[1:5]] + list(w) + [p[5:9]]) for p, w in rules.paths_from_bones([b.name for b in world.marker.bones])]


class Ride:
    def __init__(self, world, paths=None, blend="linear", camera=None):
        """paths: path ids to ride in order (default: every path). camera: callable(point) -> offset or None
        (default: the world's MarioOffset records)."""
        self.blend = blend if blend in BLENDS else "linear"
        if camera is None:
            def camera(pid):
                rec = world.mario_record(pid) if getattr(world, "mario", None) else None
                return tuple(rec.vector) if rec is not None and any(abs(v) > 1e-6 for v in rec.vector) else None
        walks = dict(path_walks(world))
        order = paths or [p for p, _ in path_walks(world)]
        self.legs = []                         # [(path id, [(name, position, offset)])]
        for p in order:
            pts = [(n, world.position(n)) for n in walks.get(p, []) if world.position(n) is not None]
            if len(pts) >= 2:
                self.legs.append((p, [(n, tuple(pos), camera(n)) for n, pos in pts]))
        self._fill()
        self._index()

    def _fill(self):
        """Points with no camera take the previous one (the first take the next, or DEFAULT)."""
        flat = [pt for _, pts in self.legs for pt in pts]
        first = next((o for _, _, o in flat if o is not None), DEFAULT)
        last = first
        for _, pts in self.legs:
            for i, (n, pos, off) in enumerate(pts):
                last = off if off is not None else last
                pts[i] = (n, pos, last)

    def _index(self):
        self.samples = []                      # (distance at the start, leg, i) for every segment
        d = 0.0
        for li, (_, pts) in enumerate(self.legs):
            for i in range(len(pts) - 1):
                self.samples.append((d, li, i))
                d += math.dist(pts[i][1], pts[i + 1][1])
        self.length = d
        self._starts = [s[0] for s in self.samples]

    def sample(self, distance):
        """(Mario's position, camera offset, label) at a distance along the ride (clamped to its ends)."""
        if not self.samples:
            return None, DEFAULT, "no path"
        distance = max(0.0, min(self.length, distance))
        k = max(0, bisect.bisect_right(self._starts, distance) - 1)
        start, li, i = self.samples[k]
        path, pts = self.legs[li]
        (na, a, oa), (nb, b, ob) = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        t = 0.0 if seg < 1e-9 else min(1.0, (distance - start) / seg)
        pos = tuple(x + (y - x) * t for x, y in zip(a, b))
        if self.blend == "hold":
            off = ob if t >= 1.0 else oa
        else:
            off = tuple(x + (y - x) * t for x, y in zip(oa, ob))
        return pos, off, "%s: %s → %s (camera %s)" % (path, na, nb, ", ".join("%.0f" % v for v in off))

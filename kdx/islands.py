"""
kdx/islands.py - Koopatlas-style raised islands for World Maker maps (phase 5).

The reference maps (Custom World Maps, NSMBU) are painted 2D maps lifted into tiers: flat tops (grass, sand, snow,
rock) standing on vertical cliff walls, stacked two or three high, joined by roads, ladders and bridges. A project's
tier map (Project.tiers: 0-3 per tile) gives that look:
  - every tile with tier > 0 gets a top at tier * tier_height above the ground, textured with the painted map itself, so
    the roads, sand rims and flowers painted there sit on the island;
  - wherever a tile is higher than its neighbour a cliff wall is built down to the neighbour's top, textured with the
    theme's cliff (drawn here, no files needed).

    parts = island_parts(project, map_png)        # [objmodel.Part]: "islands/top" and "islands/cliff"
    THEMES                                        # name -> cliff colours (grass, desert, snow, jungle, lava, castle, sky)
"""
from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path

import numpy as np

THEMES = {
    # sea: what surrounds the map (the canvas and the exported picture's background); road: painted roads (Map >
    # Paint roads), in the thick orange-ish style of the reference maps, darker outline from the same colour
    "grass": {"label": "Grassland", "cliff": ("#7a4b2a", "#5e3a20", "#93613a"), "top_edge": "#4caf50",
              "sea": "#3a84d6", "road": "#e8a74a"},
    "desert": {"label": "Desert", "cliff": ("#c99a5b", "#a87a40", "#e0b77a"), "top_edge": "#e9d29a",
               "sea": "#2f9fc9", "road": "#c9763a"},
    "snow": {"label": "Snow", "cliff": ("#7fb3e0", "#5e93c6", "#b9dcf5"), "top_edge": "#ffffff",
             "sea": "#5a8fc4", "road": "#8fb8e6"},
    "jungle": {"label": "Jungle", "cliff": ("#6b5a3a", "#4f4229", "#857149"), "top_edge": "#3e8e41",
               "sea": "#2d7a8a", "road": "#d9a35a"},
    "lava": {"label": "Lava", "cliff": ("#4a2a22", "#2e1813", "#6b3b2c"), "top_edge": "#ff7a1a",
             "sea": "#5a140a", "road": "#b0a090"},
    "castle": {"label": "Castle", "cliff": ("#7c7f86", "#5b5e64", "#9ea1a8"), "top_edge": "#c9ccd2",
               "sea": "#3b2440", "road": "#c9b48a"},
    "sky": {"label": "Sky", "cliff": ("#d6c7a1", "#b8a77f", "#efe3c2"), "top_edge": "#ffffff",
            "sea": "#8cc8f0", "road": "#f2d37a"},
}
WALL_TEX_UNITS = 60.0          # one repeat of the cliff texture per 60 world units


def cliff_texture(theme):
    """A 128x128 cliff texture for a theme (strata and cracks), cached as a PNG; returns its path."""
    from PyQt5 import QtCore, QtGui
    base, dark, light = THEMES.get(theme, THEMES["grass"])["cliff"]
    key = hashlib.sha1(("%s%s%s%s" % (theme, base, dark, light)).encode()).hexdigest()[:10]
    path = Path(tempfile.gettempdir()) / ("kdx_cliff_%s.png" % key)
    if path.exists():
        return str(path)
    img = QtGui.QImage(128, 128, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(base))
    p = QtGui.QPainter(img)
    rng = np.random.RandomState(7)
    for y in range(0, 128, 16):                          # strata
        p.fillRect(0, y + 12, 128, 4, QtGui.QColor(dark))
        p.fillRect(0, y, 128, 2, QtGui.QColor(light))
    p.setPen(QtGui.QPen(QtGui.QColor(dark), 2))
    for _ in range(14):                                  # cracks and stones
        x, y = rng.randint(0, 128, 2)
        p.drawLine(int(x), int(y), int(x + rng.randint(-10, 10)), int(y + rng.randint(4, 14)))
    p.setPen(QtCore.Qt.NoPen)
    for _ in range(10):
        x, y = rng.randint(0, 120, 2)
        p.setBrush(QtGui.QColor(light)); p.drawEllipse(int(x), int(y), 6, 4)
    p.end()
    img.save(str(path))
    return str(path)


def _quad(P, N, U, a, b, c, d, n, uv):
    """Two triangles a-b-c, a-c-d facing n (the winding is turned round if it faces the other way: the game draws
    front faces only), with uvs (4 tuples)."""
    q, w = list((a, b, c, d)), list(uv)
    e1, e2 = np.subtract(q[1], q[0]), np.subtract(q[2], q[0])
    if float(np.dot(np.cross(e1, e2), n)) < 0:
        q, w = [q[0], q[3], q[2], q[1]], [w[0], w[3], w[2], w[1]]
    for i in (0, 1, 2, 0, 2, 3):
        P.append(q[i]); N.append(n); U.append(w[i])


ROUND = 0.5         # tiles: the radius of a rounded island corner
ROUND_STEPS = 4
_CORNERS = ((-1, -1, 180.0), (1, -1, 270.0), (1, 1, 0.0), (-1, 1, 90.0))   # nw, ne, se, sw: (dx, dy, arc start)


def tile_outline(tier, x, y, t, rounded=True):
    """The outline of an island tile (tile corners as floats, going round), with each corner whose two sides and
    diagonal are lower replaced by a quarter circle (marching-squares style rounding). Returns (points, rounded corner
    list [(corner point, arc points, lowest neighbour tier)])."""
    pts, arcs = [], []
    for dx, dy, a0 in _CORNERS:
        cx, cy = x + (dx > 0), y + (dy > 0)
        lows = (tier(x + dx, y), tier(x, y + dy), tier(x + dx, y + dy))
        if rounded and all(v < t for v in lows):
            ox, oy = cx - dx * ROUND, cy - dy * ROUND
            arc = [(ox + ROUND * math.cos(math.radians(a0 + 90.0 * k / ROUND_STEPS)),
                    oy + ROUND * math.sin(math.radians(a0 + 90.0 * k / ROUND_STEPS))) for k in range(ROUND_STEPS + 1)]
            pts += arc
            arcs.append(((cx, cy), arc, min(lows)))
        else:
            pts.append((float(cx), float(cy)))
    return pts, arcs


def island_parts(project, map_png):
    """The tops and cliff walls of a project's tiers as objmodel Parts (empty if it has none)."""
    from .objmodel import Part
    T = project.tiers
    if not T or not any(T):
        return []
    W, H, ts, th = project.width, project.height, project.tile_size, project.tier_height
    MW, MH = W * ts, H * ts
    tier = lambda x, y: T[y * W + x] if 0 <= x < W and 0 <= y < H else 0
    base = project.ground_corner                       # hills under the tiers (corner heights)
    tops_P, tops_N, tops_U = [], [], []
    wall_P, wall_N, wall_U = [], [], []
    rounded = bool(getattr(project, "island_round", False))

    def hb(i, j):                                      # ground height anywhere (bilinear between tile corners)
        i0, j0 = int(math.floor(i)), int(math.floor(j))
        fi, fj = i - i0, j - j0
        h = lambda a, b: base(min(max(a, 0), W), min(max(b, 0), H))
        return (h(i0, j0) * (1 - fi) * (1 - fj) + h(i0 + 1, j0) * fi * (1 - fj) + h(i0, j0 + 1) * (1 - fi) * fj
                + h(i0 + 1, j0 + 1) * fi * fj)
    up = (0.0, 1.0, 0.0)
    for y in range(H):
        for x in range(W):
            t = tier(x, y)
            if t <= 0:
                continue
            lift = t * th
            if rounded:
                outline, arcs = tile_outline(tier, x, y, t)
                if arcs:
                    mx, my = x + 0.5, y + 0.5
                    centre = (mx * ts, hb(mx, my) + lift, my * ts)
                    for (i0, j0), (i1, j1) in zip(outline, outline[1:] + outline[:1]):
                        a = (i0 * ts, hb(i0, j0) + lift, j0 * ts)
                        b = (i1 * ts, hb(i1, j1) + lift, j1 * ts)
                        _quad(tops_P, tops_N, tops_U, centre, a, b, b, up,
                              [(mx * ts / MW, my * ts / MH), (i0 * ts / MW, j0 * ts / MH),
                               (i1 * ts / MW, j1 * ts / MH), (i1 * ts / MW, j1 * ts / MH)])
                        mid = ((i0 + i1) / 2, (j0 + j1) / 2)
                        on_arc = not (abs(mid[1] - y) < 1e-6 or abs(mid[1] - y - 1) < 1e-6 or
                                      abs(mid[0] - x) < 1e-6 or abs(mid[0] - x - 1) < 1e-6)
                        if on_arc:
                            lower = next(lo for (_c, arc, lo) in arcs if (i0, j0) in arc)
                        else:
                            nx = x - 1 if abs(mid[0] - x) < 1e-6 else x + 1 if abs(mid[0] - x - 1) < 1e-6 else x
                            ny = y - 1 if abs(mid[1] - y) < 1e-6 else y + 1 if abs(mid[1] - y - 1) < 1e-6 else y
                            lower = tier(nx, ny)
                        if lower >= t:
                            continue
                        nrm = (mid[0] - mx, 0.0, mid[1] - my)
                        ln = math.hypot(nrm[0], nrm[2]) or 1.0
                        nrm = (nrm[0] / ln, 0.0, nrm[2] / ln)
                        A, B = a, b
                        C = (i1 * ts, hb(i1, j1) + lower * th, j1 * ts)
                        D = (i0 * ts, hb(i0, j0) + lower * th, j0 * ts)
                        u0 = (i0 + j0) * ts / WALL_TEX_UNITS
                        u1 = u0 + math.hypot(i1 - i0, j1 - j0) * ts / WALL_TEX_UNITS
                        _quad(wall_P, wall_N, wall_U, A, B, C, D, nrm,
                              [(u0, A[1] / WALL_TEX_UNITS), (u1, B[1] / WALL_TEX_UNITS),
                               (u1, C[1] / WALL_TEX_UNITS), (u0, D[1] / WALL_TEX_UNITS)])
                    for (ci, cj), arc, lo in arcs:           # the cut-off corner, at the lowest neighbour's height
                        if lo <= 0:
                            continue
                        cp = (ci * ts, hb(ci, cj) + lo * th, cj * ts)
                        for (i0, j0), (i1, j1) in zip(arc, arc[1:]):
                            _quad(tops_P, tops_N, tops_U, cp, (i0 * ts, hb(i0, j0) + lo * th, j0 * ts),
                                  (i1 * ts, hb(i1, j1) + lo * th, j1 * ts), (i1 * ts, hb(i1, j1) + lo * th, j1 * ts), up,
                                  [(ci * ts / MW, cj * ts / MH), (i0 * ts / MW, j0 * ts / MH),
                                   (i1 * ts / MW, j1 * ts / MH), (i1 * ts / MW, j1 * ts / MH)])
                    continue
            c = [(x, y), (x, y + 1), (x + 1, y + 1), (x + 1, y)]
            pts = [(i * ts, base(i, j) + lift, j * ts) for i, j in c]
            uvs = [(i * ts / MW, j * ts / MH) for i, j in c]
            _quad(tops_P, tops_N, tops_U, *pts, (0.0, 1.0, 0.0), uvs)
            # walls down to each lower neighbour: (neighbour, edge corners in order seen from outside, outward normal)
            for (nx, ny), (e0, e1), n in (((x, y - 1), ((x + 1, y), (x, y)), (0.0, 0.0, -1.0)),
                                          ((x, y + 1), ((x, y + 1), (x + 1, y + 1)), (0.0, 0.0, 1.0)),
                                          ((x - 1, y), ((x, y), (x, y + 1)), (-1.0, 0.0, 0.0)),
                                          ((x + 1, y), ((x + 1, y + 1), (x + 1, y)), (1.0, 0.0, 0.0))):
                lower = tier(nx, ny)
                if lower >= t:
                    continue
                (i0, j0), (i1, j1) = e0, e1
                b0, b1 = base(i0, j0), base(i1, j1)
                top0, top1 = b0 + lift, b1 + lift
                bot0, bot1 = b0 + lower * th, b1 + lower * th
                u0 = (i0 + j0) * ts / WALL_TEX_UNITS
                u1 = u0 + ts / WALL_TEX_UNITS
                A, B = (i0 * ts, top0, j0 * ts), (i1 * ts, top1, j1 * ts)
                C, D = (i1 * ts, bot1, j1 * ts), (i0 * ts, bot0, j0 * ts)
                _quad(wall_P, wall_N, wall_U, A, B, C, D, n,
                      [(u0, top0 / WALL_TEX_UNITS), (u1, top1 / WALL_TEX_UNITS),
                       (u1, bot1 / WALL_TEX_UNITS), (u0, bot0 / WALL_TEX_UNITS)])
    parts = []
    f = lambda a: np.asarray(a, np.float32)
    if tops_P:
        parts.append(Part("islands/top", f(tops_P), f(tops_N), f(tops_U), str(map_png), (1.0, 1.0, 1.0), "opaque", False))
    if wall_P:
        uv = f(wall_U); uv[:, 1] = -uv[:, 1]
        parts.append(Part("islands/cliff", f(wall_P), f(wall_N), uv, cliff_texture(project.theme), (1.0, 1.0, 1.0),
                          "opaque", False))
    return parts

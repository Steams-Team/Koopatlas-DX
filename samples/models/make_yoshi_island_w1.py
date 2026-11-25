#!/usr/bin/env python3
"""
samples/models/make_yoshi_island_w1.py - "Yoshi's Island": a World 1 for NSMBUDX built on the game's own World 1.

    py -3 samples/models/make_yoshi_island_w1.py <out folder> [--romfs <romfs>] [--nsmbw <NSMBW dump>]

Writes <out folder>/Yoshi's Island.kdxmap (a World Maker project, World 1, template CS_W1) and its models.

The ground is NSMBUDX World 1's own (grass, cliffs, pipes, flowers), minus what made it Acorn Plains (the acorn trees,
the white rock range) and its roads. The levels and houses are World 1's, in the same order (1-1, 1-2, a house and the
cannon's secret exit, the tower halfway, 1-3, then 1-4 and 1-5 with the second house, the castle), on new roads; the
castle, the cannon and the pipe island keep their places, so the walks on to Worlds 2 and 3 stay the game's own.
Roads are built here (the game's road texture along each path) and hidden until their path opens, as the game does
(materials mt<path><a-h> with a reveal mask, kdx.export2d route parts). The tower gets Boshi's colours, the castle King
Wart's. Scenery comes from the games: the Wii's World 1 hills (as they are, and the tallest as snow-capped peaks out at
sea east of the meadow) and its fruit bushes, Sparkling Waters' palms, Soda Jungle's fan plants and log posts, Meringue
Clouds' round clouds, and Yoshi eggs made here. North of the castle stays clear: the walk on to World 2 goes that way.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent
sys.path.insert(0, str(APP))

import numpy as np                                                    # noqa: E402

ROAD_W = 60.0              # vanilla road width (CS_W1_World hoge69: 60 across, texture band v 0.75-1.0)
ROAD_U = 150.0             # world units per repeat of the road texture along the road (vanilla 0.13-1.75 over 240)
ROAD_V = (0.75, 1.0)       # RouteW1_alb's straight road band (rows 192-256 of 256)
ROAD_LIFT = 0.8            # over the ground
FILLET = 110.0             # bends are rounded with this radius (World 1's own corners: about 120)
MASK_PER_UNIT = 1.0 / 120  # reveal mask v per world unit of path (vanilla RW101W102: about 2 over 240)

# ---- layout: World 1's points in the same unlock order on new roads ------------------------------------------------
# (x, z) in World 1's own coordinates (x east, z south); heights come from the ground. Kept where the game's walks need
# them: W1C0 (walk on to World 2, north: kept clear), W1S0 (the cannon and its rainbow to World 3), Fd02/Fs01/W1X0 (the
# pipe island), W1T0 and W103 (the ramps from the meadow to the tower ledge and over the 1-3 mound are the ground's own).
# Like World 1: one road on from each level, branches only at a level or a stop, corners rounded wide, no two roads side
# by side. The meadow runs west to east (start, 1-1, 1-2, the stop at the ramp's foot with the house branching east);
# 1-2's secret exit takes the south coast to the game's own rainbow. Over the mound the road comes down onto the upper
# plain at one junction (the descent comes in from the east): 1-4 north, 1-5 west (its house further west, the Piranha
# Plant on its way round), the baby Yoshi south on the plain's edge; both ways meet at the merge stop under the castle.
# (The game keeps one link per direction at a point: every road leaves a point in a direction of its own.)
POINTS = {
    "W1M0": (-1000, 110), "Fd01": (-1000, -230), "W101": (-520, 40), "W102": (130, 60),
    "F001": (660, -240), "W1K1": (1000, -430),
    "W1T0": None, "W103": None,
    "Fx01": (-420, -1080), "Fys1": (-460, -910), "W104": (-400, -1310),
    "W105": (-780, -1180), "W1K2": (-1030, -1120), "Fb01": (-740, -1480), "F008": (-380, -1530),
    "W1C0": None,
    "W1S0": None,
    "Fd02": None, "Fs01": None, "W1X0": None,
}
KEPT = {p for p, v in POINTS.items() if v is None}     # where World 1 has them (bones), to the unit
FLAGS = {"W103": "restart", "W104": "restart", "W105": "restart", "W1K1": "fixY", "F001": "W1Item,fix,stop",
         "Fd01": "dokan", "F008": "merge,restart,stop", "Fb01": "W1PakkunA", "Fd02": "dokan2", "Fs01": "stop",
         "Fys1": "balloon1"}
# paths: (from, to, bends between: (x, z) on the ground or a World 1 walk point by name; None: World 1's own walk)
PATHS = [
    ("W1M0", "Fd01", []),                                           # the pipe, just north of the start
    ("W1M0", "W101", []),
    ("W101", "W102", []),
    ("W102", "F001", [(330, -70), (560, -100)]),
    ("F001", "W1T0", ["K026", "K027", "K028", "K029", "K02a", "K02b", "K02c", "K02d", "F02e", "K02f", "K02g",
                      "K02h"]),                                     # up World 1's ramp to the tower ledge
    ("F001", "W1K1", [(820, -300)]),
    ("W102", "W1S0", [(190, 220), (900, 230), "K206", "K207", "K208", "K209", "F20a", "K20b", "K20c", "Fc02",
                      "K20e", "K20f", "K20g", "K20h", "K20i", "K20j", "K20k", "K20l", "K20m",
                      "K20n"]),                                     # 1-2's secret exit: the coast, then the rainbow
    ("W1T0", "W103", None),                     # the ground's own ramp: World 1's walk, renamed
    ("W103", "Fx01", ["F051", "F052", "F053", "F054", "F055", "F056", "F057", "F058", "F059", "F05a", "F05b",
                      "F05c"]),                                     # down the mound's west side
    ("Fx01", "Fys1", []),
    ("Fx01", "W104", []),
    ("Fx01", "W105", []),
    ("W105", "W1K2", []),
    ("W105", "Fb01", []),
    ("Fb01", "F008", [(-560, -1540)]),
    ("W104", "F008", []),
    ("F008", "W1C0", [(90, -1530), "K072", "K073", "K074", "F075"]),   # World 1's ramp up to the castle
    ("Fd02", "Fs01", None),
    ("Fs01", "W1X0", None),
]
OPENS = {          # point -> (normal exit: points, paths), (secret exit: points, paths)
    "W101": (["W102"], ["RW101W102"]),
    "W102": (["W1T0", "W1K1"], ["RW102F001", "RF001W1T0", "RF001W1K1"], ["W1S0"], ["RW102W1S0"]),
    "W1T0": (["W103"], ["RW1T0W103"]),
    "W103": (["W104", "W105"], ["RW103Fx01", "RFx01W104", "RFx01W105", "RFx01Fys1"]),
    "W104": ([], ["RW104F008", "RF008W1C0"]),
    "W105": (["W1K2"], ["RW105W1K2", "RW105Fb01", "RFb01F008", "RF008W1C0"]),
    "W1C0": (["W201"], ["RW1C0W2J0", "RW2J0W2M0", "RW2M0W201"]),
    "W1S0": (["W501"], ["RW1S0W3J0", "RW3J0W5J0", "RW5J0W5J2", "RW5J2W5M0"]),
}
# (World 1's walks are whole paths: Fx01 is part of RW103W104, Fb01 of RW105F008; the part between the two points is used)
NATIVE_WALKS = {("W1T0", "W103"): "RW1T0W103", ("Fd02", "Fs01"): "RFd02W1X0", ("Fs01", "W1X0"): "RFd02W1X0"}


def walk_between(walks, path, a, b):
    """The points strictly between a and b on World 1's path `path` (either direction)."""
    chain = [path[1:5]] + list(walks[path]) + [path[5:9]]
    i, j = chain.index(a), chain.index(b)
    return chain[i + 1:j] if i < j else chain[j + 1:i][::-1]

# ---- what of World 1's ground goes ---------------------------------------------------------------------------------
DROP_MATERIALS = {"mtCourseRing", "mtIshiyama", "mtLeafIshiyama", "mtDonguriFace", "mtRouteOpague", "mtObject",
                  "mtObjectBase", "mtStart", "mtTreeTrunk", "mtLeafPlant1st", "mtLeafPlant2nd", "mtLeafPlant3rd",
                  "mtLeafPlantBack"}
DROP_SYNCHRO = ("mtLeafDonguri", "mtDongriMini")     # the acorn trees (CS_W1_Synchro)
GROUND_MODELS = {"CS_W1_World", "CS_W1_Synchro", "RFd02W1X0", "RW102W1S0", "RW1S0W3J0"}
# World 1's own road pieces kept, as fades that appear when their path opens (material -> name the game looks up)
FADE_ROUTES = {"mtRW102W1S0b": "mtRW102W1S0b", "mtRW102W1S0c": "mtRW102W1S0c", "mtRW102W1S0d": "mtRW102W1S0d",
               "mtRW102W1S0e": "mtRW102W1S0e", "mtRW102W1S0f": "mtRW102W1S0f",        # the rainbow to the cannon
               "mtRW1S0W3J0a": "mtRW1S0W3J0a", "mtRW1S0W3J0b": "mtRW1S0W3J0b",        # the cannon's rainbow on
               "mtRW1C0W2J0a": "mtRW1C0W2J0a"}                                        # the castle's road on
FADE_BY_MODEL = {"RW102W1S0": "mtRW102W1S0g"}          # its ripples round the rainbow's feet (mtWaterEdgeMini)
SHOWN_ROUTES = {"mtRFd02W1X0a"}                        # the pipe island's road, open from the start
FADE_MASK = ((0.5, 1.0), -3.0)
GROUND_SURFACES = {"mtGrass", "mtGake", "mtGakeAO", "mtGakeGray", "mtGrassReflection"}


def romfs_paths(romfs, nsmbw):
    from kdx import romfs as R
    root = R.normalize(romfs or os.environ.get("KDX_ROMFS", ""))
    if root is None:
        raise SystemExit("Give the NSMBUDX romfs (--romfs or KDX_ROMFS).")
    wii = Path(nsmbw or os.environ.get("KDX_NSMBW", "")) / "WorldMap"
    return Path(root), R.model_dir(root), wii


class Ground:
    """Heights of World 1's ground (its grass and cliff tops), vectorised over a raster of 10 units."""
    STEP = 10.0

    def __init__(self, parts):
        tri = np.concatenate([p.positions.reshape(-1, 3, 3) for p in parts]).astype(np.float64)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        tri = tri[n[:, 1] > 1e-6]
        lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
        self.x0, self.z0 = math.floor(lo[0] / self.STEP) * self.STEP, math.floor(lo[2] / self.STEP) * self.STEP
        nx, nz = int((hi[0] - self.x0) / self.STEP) + 2, int((hi[2] - self.z0) / self.STEP) + 2
        H = np.full((nz, nx), np.nan)
        xs, zs = self.x0 + np.arange(nx) * self.STEP, self.z0 + np.arange(nz) * self.STEP
        for a, b, c in tri:
            i0, i1 = int(math.ceil((min(a[0], b[0], c[0]) - self.x0) / self.STEP)), int((max(a[0], b[0], c[0]) - self.x0) // self.STEP)
            j0, j1 = int(math.ceil((min(a[2], b[2], c[2]) - self.z0) / self.STEP)), int((max(a[2], b[2], c[2]) - self.z0) // self.STEP)
            if i1 < i0 or j1 < j0:
                continue
            X, Z = np.meshgrid(xs[i0:i1 + 1], zs[j0:j1 + 1])
            d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
            if abs(d) < 1e-9:
                continue
            l1 = ((b[2] - c[2]) * (X - c[0]) + (c[0] - b[0]) * (Z - c[2])) / d
            l2 = ((c[2] - a[2]) * (X - c[0]) + (a[0] - c[0]) * (Z - c[2])) / d
            l3 = 1 - l1 - l2
            m = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
            sub = H[j0:j1 + 1, i0:i1 + 1]
            sub[m] = np.fmax(sub[m], (l1 * a[1] + l2 * b[1] + l3 * c[1])[m])
        self.H = H

    def __call__(self, x, z):
        """Height at (x, z) (arrays fine), bilinear; NaN off the ground."""
        x, z = np.asarray(x, np.float64), np.asarray(z, np.float64)
        fx, fz = (x - self.x0) / self.STEP, (z - self.z0) / self.STEP
        i, j = np.clip(np.floor(fx).astype(int), 0, self.H.shape[1] - 2), np.clip(np.floor(fz).astype(int), 0, self.H.shape[0] - 2)
        tx, tz = np.clip(fx - i, 0, 1), np.clip(fz - j, 0, 1)
        h = self.H
        return (h[j, i] * (1 - tx) * (1 - tz) + h[j, i + 1] * tx * (1 - tz) + h[j + 1, i] * (1 - tx) * tz
                + h[j + 1, i + 1] * tx * tz)


# ---- roads ---------------------------------------------------------------------------------------------------------
def rounded(poly, radius=FILLET, step=6.0):
    """The polyline with its bends rounded off (arcs of `radius`), sampled about every `step` units: (n, 2)."""
    P = [np.asarray(p, np.float64) for p in poly]
    out = [P[0]]
    for k in range(1, len(P) - 1):
        a, b, c = P[k - 1], P[k], P[k + 1]
        d1, d2 = (b - a) / np.linalg.norm(b - a), (c - b) / np.linalg.norm(c - b)
        turn = math.acos(np.clip(np.dot(d1, d2), -1, 1))
        if turn < 1e-3:
            out.append(b)
            continue
        r = min(radius, 0.45 * np.linalg.norm(b - a), 0.45 * np.linalg.norm(c - b))
        t = r * math.tan(turn / 2)
        p1, p2 = b - d1 * t, b + d2 * t
        side = np.sign(d1[0] * d2[1] - d1[1] * d2[0])
        nrm = np.array([-d1[1], d1[0]]) * side
        ctr = p1 + nrm * r
        a1, a2 = math.atan2(*(p1 - ctr)[::-1]), math.atan2(*(p2 - ctr)[::-1])
        da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        out.append(p1)
        for s in np.linspace(0, 1, max(2, int(abs(da) * r / step)) + 1)[1:-1]:
            ang = a1 + da * s
            out.append(ctr + r * np.array([math.cos(ang), math.sin(ang)]))
        out.append(p2)
    out.append(P[-1])
    dense = [out[0]]
    for a, b in zip(out, out[1:]):
        n = max(1, int(np.linalg.norm(b - a) / step))
        dense += [a + (b - a) * (i / n) for i in range(1, n + 1)]
    return np.array(dense)


def road_part(name, poly, ground, texture, trim=(0.0, 0.0)):
    """A road along poly [(x, z)...] as a route part: the game's road texture band across, repeating along; the reveal
    mask's v runs from the path's start (high: shown first) to 1.0 at its end; hidden at -(start + 0.25).
    trim: lengths left off at the ends (under a level ring)."""
    from kdx.objmodel import Part
    pts = rounded(poly)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    keep = (s >= trim[0]) & (s <= s[-1] - trim[1])
    pts, s = pts[keep], s[keep]
    L = s[-1] - s[0]
    tan = np.gradient(pts, axis=0)
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-9)
    side = np.c_[-tan[:, 1], tan[:, 0]]
    left, right = pts + side * ROAD_W / 2, pts - side * ROAD_W / 2
    hl, hr, hc = ground(left[:, 0], left[:, 1]), ground(right[:, 0], right[:, 1]), ground(pts[:, 0], pts[:, 1])
    on = ~np.isnan(hc)                                   # over the sea (the rainbow to the cannon) there is no road
    hl, hr = np.where(np.isnan(hl), hc, hl), np.where(np.isnan(hr), hc, hr)
    yl, yr = np.fmax(hl, hc) + ROAD_LIFT, np.fmax(hr, hc) + ROAD_LIFT    # never under the ground across the road
    L3 = np.c_[left[:, 0], yl, left[:, 1]]
    R3 = np.c_[right[:, 0], yr, right[:, 1]]
    u = (s - s[0]) / ROAD_U
    mv = 1.0 + (s[-1] - s) * MASK_PER_UNIT
    pos, uv, uv2 = [], [], []
    for i in range(len(pts) - 1):
        if not (on[i] and on[i + 1]):
            continue
        quad = [(L3[i], (u[i], ROAD_V[0]), mv[i]), (R3[i], (u[i], ROAD_V[1]), mv[i]),
                (R3[i + 1], (u[i + 1], ROAD_V[1]), mv[i + 1]), (L3[i + 1], (u[i + 1], ROAD_V[0]), mv[i + 1])]
        for k in (0, 2, 1, 0, 3, 2):                     # counter-clockwise seen from above
            pos.append(quad[k][0]); uv.append(quad[k][1]); uv2.append((0.5, quad[k][2]))
    pos = np.asarray(pos, np.float32)
    part = Part(name, pos, np.tile(np.float32([0, 1, 0]), (len(pos), 1)), np.asarray(uv, np.float32), texture,
                mode="mask")
    part.uvs2 = np.asarray(uv2, np.float32)
    part.mask_offset = -(1.0 + L * MASK_PER_UNIT) - 0.25
    return part


# ---- the project ---------------------------------------------------------------------------------------------------
def native_walks(world):
    """{path: [walk points]} of World 1's own paths, with their bends' positions {name: (x, y, z)}."""
    from kdx import rules
    bones = {b.name: tuple(b.world_position) for b in world.marker.bones}
    return dict(rules.map_paths(world.point_rows, world.route_rows)), bones


def build_layout(world, ground):
    """(nodes, routes, polylines {path: [(x, z)...]}) for the project."""
    from kdx import rules
    walks, bones = native_walks(world)
    nodes, routes, polys = {}, [], {}
    bend_no = iter(range(256))

    def kind(pid):
        return ("start" if pid == "W1M0" else "level" if pid[0] == "W" else "stop")

    for pid, xz in POINTS.items():
        if xz is None:
            x, y, z = bones[pid]                                  # kept where the game has it, to the unit
        else:
            x, z = xz
            y = float(ground(x, z))
            if math.isnan(y):
                raise SystemExit("%s at (%g, %g) is off the ground." % (pid, x, z))
        row = ["", pid, FLAGS.get(pid, ""), "", "", "", "", "", ""]
        o = OPENS.get(pid)
        if o:
            row[3], row[4] = ",".join(o[0]), ",".join(o[1])
            if len(o) > 2:
                row[6], row[7] = ",".join(o[2]), ",".join(o[3])
        nodes[pid] = {"kind": kind(pid), "x": x / 30.0, "y": z / 30.0, "pos": [float(x), float(y), float(z)],
                      "row": row, "camera": [0.0, 350.0, 400.0] if pid[0] == "W" else [0.0, 0.0, 0.0]}
    for a, b, bends in PATHS:
        if bends is None:
            mids = [bones[p] for p in walk_between(walks, NATIVE_WALKS[(a, b)], a, b)]
        else:                                   # (x, z) on the ground, or a World 1 walk point by name
            mids = [bones[q] if isinstance(q, str) else (q[0], float(ground(*q)), q[1]) for q in bends]
        chain = [a]
        for x, y, z in mids:
            k = "Ky%02x" % next(bend_no)
            nodes[k] = {"kind": "bend", "x": x / 30.0, "y": z / 30.0, "pos": [float(x), float(y), float(z)],
                        "row": ["", k, "", "", "", "", "", "", ""], "camera": [0.0, 0.0, 0.0]}
            chain.append(k)
        chain.append(b)
        for p, q in zip(chain, chain[1:]):
            routes.append({"id": "R%s%s" % (p, q), "type": "道"})
        polys["R%s%s" % (a, b)] = [(nodes[p]["pos"][0], nodes[p]["pos"][2]) for p in chain]
    return nodes, routes, polys


def ground_parts(world, polys):
    """World 1's ground without the acorn trees, rock range, rings, roads and buildings; small decorations (flowers,
    grass, pipes) standing on a new road are left out too. Returns (parts, surfaces)."""
    from kdx import assetkit
    parts, surfaces, fades = [], [], []
    for mname, _s, m, p in __import__("kdx.worldedit", fromlist=["x"]).world_shapes(world, GROUND_MODELS):
        p = p.copy()
        p.material = FADE_BY_MODEL.get(mname, m) if m == "mtWaterEdgeMini" else m
        m = p.material
        if m in FADE_ROUTES or m in FADE_BY_MODEL.values():
            p.material = FADE_ROUTES.get(m, m)
            p.uvs2 = np.tile(np.float32(FADE_MASK[0]), (len(p.positions), 1))
            p.mask_offset = FADE_MASK[1]
            fades.append(p)
            continue
        if m in DROP_MATERIALS or m.startswith(DROP_SYNCHRO) or (m.startswith("mtR") and m[3:4] in "WF" and m not in SHOWN_ROUTES):
            continue
        (surfaces if m in GROUND_SURFACES else parts).append(p)
    lines = [rounded(poly) for poly in polys.values()]
    keep = []
    for obj in assetkit.pieces(parts, merge_inside=False):
        lo, hi = obj.bounds()
        c = np.array([(lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2])
        r = 0.5 * math.hypot(hi[0] - lo[0], hi[2] - lo[2])
        if r < 400 and any(np.min(np.linalg.norm(ln - c, axis=1)) < r + ROAD_W * 0.6 for ln in lines):
            continue
        keep += obj.parts
    return merged(keep + surfaces) + fades, surfaces


def merged(parts):
    """Parts of the same material, texture and mode as one (World 1's ground split per piece came back as hundreds)."""
    from kdx.objmodel import Part
    groups = {}
    for p in parts:
        groups.setdefault((p.material, p.texture, p.mode, p.double_sided), []).append(p)
    out = []
    for (mat, tex, mode, double), ps in groups.items():
        cols = [q.colors if q.colors is not None else np.ones((len(q.positions), 4), np.float32) for q in ps]
        q = Part(mat, np.concatenate([x.positions for x in ps]), np.concatenate([x.normals for x in ps]),
                 np.concatenate([x.uvs for x in ps]), tex, ps[0].color, mode, double,
                 np.concatenate(cols) if any(x.colors is not None for x in ps) else None)
        q.wrap = ps[0].wrap
        out.append(q)
    return out


# ---- scenery -------------------------------------------------------------------------------------------------------
TOWER_AT, CASTLE_AT = (480.0, -740.0), (90.0, -1870.0)     # World 1's tower and castle buildings (mtObject)
# Scenery spread round the whole island, nothing north of the castle (the walk on to World 2 goes that way) and nothing
# tall south of a road (the camera looks north and would lose the road behind it).
MOUNTAINS = [  # (x, z, scale xz, scale y, colour) snow-capped peaks rising from the sea east of the meadow
    (1260, -840, 1.6, 1.3, (176, 202, 240)), (1480, -960, 1.8, 1.6, (166, 192, 236)),
    (1290, -1110, 1.5, 1.2, (186, 210, 242)), (1560, -1200, 1.4, 1.1, (160, 188, 232))]
WII_HILLS = [  # (x, z, scale) the Wii's own spotted and wavy hills (upright ones), one or two to a place
    (-1430, -300, 1.0), (-1360, -1100, 1.1),                               # the lowland under the plain's west cliff
    (-560, -260, 0.9), (1260, -420, 0.9),                                  # the meadow, north of its road
    (760, -730, 0.9),                                                      # the tower ledge's east end
    (-950, -1590, 1.0)]                                                    # the upper plain's north-west
PALMS = [(-1250, 470, 1.4), (-560, 510, 1.2), (1180, -160, 1.3), (1500, -200, 1.5), (590, -770, 1.2),
         (-1070, -1320, 1.4), (-650, -870, 1.2), (-200, -1270, 1.1), (-1050, -1650, 1.3)]
FANS = [(320, -330, 0.26), (-830, -120, 0.24), (-600, -1330, 0.27), (-920, -1440, 0.25), (1320, -40, 0.25)]
FRUIT = [(-830, -40, 1.2), (-260, -150, 1.1), (350, 110, 1.2), (900, -110, 1.2), (-120, 300, 1.1), (1150, -560, 1.2),
         (-760, -1010, 1.3), (-560, -1660, 1.2), (-1090, -950, 1.2)]   # the Wii's fruit bushes (scale 1: as on the Wii)
EGGS = [  # clusters (x, z, [(dx, dz, height, colour)])
    (-1130, 380, [(0, 0, 62, "green"), (40, 18, 46, "pink"), (-34, 24, 40, "yellow")]),           # by Yoshi's house
    (-300, 280, [(0, 0, 64, "pink"), (-40, 12, 46, "blue"), (36, 18, 42, "yellow"), (6, 40, 38, "green")]),  # meadow hill
    (1180, -290, [(0, 0, 58, "blue"), (36, -16, 44, "green"), (-30, 10, 40, "red")]),             # by the house
    (-620, -960, [(0, 0, 56, "yellow"), (34, 10, 42, "green"), (-30, 16, 40, "pink")]),           # the baby Yoshi's nest
    (-850, -1700, [(0, 0, 50, "pink"), (30, 20, 38, "blue")])]                                    # by the volcano
YOSHI_HOUSE = (-1300, 260, 230)                    # the giant egg Yoshi lives in, by the start
LOG_STEPS = [(-690, -1390, 0.32, 0), (-500, -1290, 0.36, 1), (-660, -1230, 0.3, 0)]   # Soda Jungle's log posts
VOLCANO = (-880, -1830, 0.6)
CLOUDS = [(-1750, 560, -700, 420), (-1700, 620, -1600, 380), (1850, 600, -600, 400), (1750, 680, -1900, 360)]  # x y z w


def scenery(proj, w1, ground, polys, models, wii):
    """Scenery objects for the project; returns notes (anything placed too near a road or off the ground)."""
    import yi_scenery as Y
    from kdx import assetkit, world as W
    from kdx.objmodel import ObjModel
    notes, tex = [], models / "textures"
    lines = [rounded(p) for p in polys.values()]
    pts = {k: np.array(n["pos"]) for k, n in proj.nodes.items() if n["kind"] != "bend"}

    samples = np.concatenate(lines + [np.array([p[[0, 2]] for p in pts.values()])])

    def check(name, x, z, r, height=0.0, ground_only=False, wide=None):
        """Ground height at (x, z); notes what is off the ground, too near a road or point, or hides one: the camera
        looks north from 350 up and 400 back (41 degrees), so a tall thing hides about 1.15 times its height of ground
        north of it."""
        h = float(ground(x, z))
        why = []
        if not ground_only:
            road = min(float(np.min(np.linalg.norm(ln - np.array([x, z]), axis=1))) for ln in lines)
            pt = min(float(np.hypot(*(p[[0, 2]] - (x, z)))) for p in pts.values())
            if math.isnan(h):
                why.append("off the ground")
            elif road < r + ROAD_W / 2 + 4 or pt < r + 40:
                why.append("%d from a road, %d from a point (needs %d)" % (road, pt, r))
        d = z - samples[:, 1]
        hid = (np.abs(samples[:, 0] - x) < (r if wide is None else wide) + ROAD_W / 2) & (d > 0) & (d < 1.15 * height)
        if hid.any():
            k = int(np.argmax(hid))
            why.append("hides the road or point at (%d, %d) (height %d)" % (samples[k, 0], samples[k, 1], height))
        if why:
            notes.append("%s at (%d, %d): %s" % (name, x, z, "; ".join(why)))
        return 0.0 if math.isnan(h) else h

    def add(path, x, z, scale=1.0, yaw=0.0, name=None, r=0.0, y=None, sink=0.0, sea=False, tall=0.0, wide=None):
        sy = scale[1] if isinstance(scale, (list, tuple)) else scale
        sx = scale[0] if isinstance(scale, (list, tuple)) else scale
        h = check(name or Path(path).stem, x, z, r, tall * sy, ground_only=sea, wide=None if wide is None else wide * sx)
        if sea:
            h = float(np.nan_to_num(ground(x, z), nan=-15.0))
        h = h if y is None else y
        s = [float(c) for c in scale] if isinstance(scale, (list, tuple)) else float(scale)
        i = proj.add_object(path, position=[float(x), h - sink, float(z)], rotation=[0.0, float(yaw), 0.0], scale=s,
                            name=name)
        return i

    def tall_of(model):
        lo, hi = model.bounds()
        return float(hi[1] - lo[1])

    def half_width(model):
        lo, hi = model.bounds()
        return 0.5 * float(max(hi[0] - lo[0], hi[2] - lo[2]))

    def cut(parts, pick=0):
        objs = assetkit.pieces(parts)
        return assetkit.centred(objs[pick])

    # World 1's tower and castle, in Boshi's and King Wart's colours, where the game has them
    objs = assetkit.pieces(assetkit.native_parts(w1, models={"CS_W1_World"}, materials={"mtObject", "mtObjectBase"}))
    tower, castle, other = [], [], []
    for o in objs:
        lo, hi = o.bounds()
        c = ((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2)
        (tower if math.hypot(c[0] - TOWER_AT[0], c[1] - TOWER_AT[1]) < 260 else
         castle if math.hypot(c[0] - CASTLE_AT[0], c[1] - CASTLE_AT[1]) < 320 else other).append(o)
    for label, group, fn in (("Boshi's tower", tower, Y.boshi_stone), ("King Wart's castle", castle, Y.wart_castle),
                             ("World 1 buildings", other, None)):
        if not group:
            continue
        m = ObjModel([p for o in group for p in o.parts])
        if fn:
            Y.recoloured(m, tex, label.split("'")[0].lower(), fn)
        cm, base = assetkit.centred(m)
        dae = assetkit.save(cm, models / ("%s.dae" % label.replace("'", "").replace(" ", "_").lower()))
        proj.add_object(dae, position=[float(c) for c in base], name=label)
        if label.startswith("Boshi"):
            lo, hi = cm.bounds()
            top, rad = float(hi[1]), 0.5 * min(hi[0] - lo[0], hi[2] - lo[2])
            sp = Y.spikes_model(models / "boshi_spikes.dae", tex, rad * 0.86, 12, 30.0, (150, 30, 40), "BoshiSpikes", 4.0)
            proj.add_object(sp, position=[float(base[0]), float(base[1]) + top * 0.82, float(base[2])],
                            name="Boshi's spiked collar")
        if label.startswith("King"):
            crown_y, crown_r, cx, cz = _roof_base(cm, base)
            band = Y.band_model(models / "wart_crown_band.dae", tex, crown_r * 1.04, 12.0, (236, 190, 60), "WartCrownBand")
            pts_ = Y.spikes_model(models / "wart_crown.dae", tex, crown_r * 1.04, 6, 26.0, (240, 196, 64), "WartCrown")
            proj.add_object(band, position=[cx, crown_y - 2.0, cz], name="King Wart's crown band")
            proj.add_object(pts_, position=[cx, crown_y + 8.0, cz], name="King Wart's crown")

    # level rings (World 1's own ring) at every level point but the start; the cannon's appears with its rainbow
    ring, _b = cut(assetkit.native_parts(w1, models={"CS_W1_World"}, materials={"mtCourseRing"}))
    ring_dae = assetkit.save(ring, models / "course_ring.dae")
    sring = ObjModel([p.copy() for p in ring.parts])
    for p in sring.parts:
        p.material = "mtRW102W1S0h"
        p.uvs2, p.mask_offset = np.tile(np.float32(FADE_MASK[0]), (len(p.positions), 1)), FADE_MASK[1]
    sring_dae = assetkit.save(sring, models / "course_ring_secret.dae")
    for pid, n in proj.nodes.items():
        if pid[0] == "W" and n["kind"] != "start":
            x, y, z = n["pos"]
            proj.add_object(sring_dae if pid == "W1S0" else ring_dae, position=[x, y - 2.0, z], name="Ring %s" % pid)

    # the start's own patch of road, moved with the start
    start, sb = cut(assetkit.native_parts(w1, models={"CS_W1_World"}, materials={"mtStart"}))
    bones = {b.name: np.array(b.world_position) for b in w1.marker.bones}
    off = np.array(proj.nodes["W1M0"]["pos"]) - bones["W1M0"]
    proj.add_object(assetkit.save(start, models / "start_patch.dae"), position=[float(c) for c in (sb + off)],
                    name="Start patch")

    # snow-capped mountains: the Wii's World 1 hill, tallest one
    hills = assetkit.pieces(assetkit.wii_parts(wii / "CS_W1.arc", {"mtYamaSkyBlue", "mtYamaSkyBlueTop", "mtYamaGreen",
                                                                   "mtYamaGreenTop"}, tex, shade_levels=0))
    tall = max(hills, key=lambda o: o.bounds()[1][1] - o.bounds()[0][1])
    for i, (x, z, sxz, sy, col) in enumerate(MOUNTAINS):
        m, _ = assetkit.centred(ObjModel([p.copy() for p in tall.parts]))
        Y.yoshi_mountain(m, tex, col, 50 + i, "Mountain%d" % i)
        add(assetkit.save(m, models / ("mountain_%d.dae" % i)), x, z, [sxz, sy, sxz], yaw=37 * i,
            name="Mountain %d" % (i + 1), r=60 * sxz, sink=10, sea=True, tall=tall_of(m))   # out of the sea
    every = [o for o in assetkit.pieces(assetkit.wii_parts(wii / "CS_W1.arc", {
        "mtYama%s%s" % (c, t) for c in ("Blue", "SkyBlue", "Green", "PinkDot", "YellowDot", "GreenWavy", "PinkWavy",
                                        "YellowWavy") for t in ("", "Top")}, tex)) if len(o.parts) == 2 and _upright(o)]
    for i, (x, z, s) in enumerate(WII_HILLS):
        m, _ = assetkit.centred(every[(i * 5) % len(every)])
        lo, hi = m.bounds()
        add(assetkit.save(m, models / ("wii_hill_%d.dae" % i)), x, z, s, yaw=29 * i, name="Wii hill %d" % (i + 1),
            r=0.5 * max(hi[0] - lo[0], hi[2] - lo[2]) * s, sink=6, tall=tall_of(m))

    # Sparkling Waters' palms, Soda Jungle's fan plants and log posts, the Wii's fruit bushes, Meringue Clouds' clouds
    md = Path(w1.path).parent
    w3, w5 = (W.World(md / ("CS_%s.Nin_NX_NVN.szs" % n)) for n in ("W3", "W5a"))
    palm, _ = cut(assetkit.native_parts(w3, materials={"mtParmLeef", "mtParmTrunk"}))
    palm_dae = assetkit.save(palm, models / "palm.dae")
    for i, (x, z, s) in enumerate(PALMS):
        add(palm_dae, x, z, s, yaw=71 * i, name="Palm %d" % (i + 1), r=20 * s, tall=tall_of(palm),
            wide=half_width(palm))
    # the fan plant is drawn as two flat halves (left and right fans): both, facing the camera as in Soda Jungle
    fan, _ = assetkit.centred(assetkit.clusters(assetkit.pieces(assetkit.native_parts(w5, materials={"mtSmallPlant"})),
                                                gap=4.0)[0])
    Y.recoloured(fan, tex, "yi", lambda a: Y.hue_to(a, 130.0, 122.0, sat=1.05, val=1.7, width=60.0))   # sunnier leaves
    fan_dae = assetkit.save(fan, models / "fan_plant.dae")
    flo, fhi = fan.bounds()
    for i, (x, z, s) in enumerate(FANS):
        add(fan_dae, x, z, s, yaw=(i % 3 - 1) * 8, name="Fan plant %d" % (i + 1), r=0.5 * (fhi[0] - flo[0]) * s * 0.8,
            tall=tall_of(fan))
    # the Wii's fruit bush: its leaves and fruit are separate pieces; the bush with fruit
    bushes = assetkit.clusters(assetkit.pieces(assetkit.wii_parts(wii / "CS_W1.arc", {"mtKusaFruits", "mtFruit"}, tex)),
                               gap=2.0)
    fruit, _ = assetkit.centred(max(bushes, key=lambda c: (any(p.material == "mtFruit" for p in c.parts),
                                                           sum(p.triangle_count for p in c.parts))))
    fruit_dae = assetkit.save(fruit, models / "fruit_bush.dae")
    blo, bhi = fruit.bounds()
    for i, (x, z, s) in enumerate(FRUIT):
        add(fruit_dae, x, z, s, yaw=(i * 47) % 360, name="Fruit bush %d" % (i + 1), r=0.5 * (bhi[0] - blo[0]) * s, sink=2,
            tall=tall_of(fruit))
    posts = [assetkit.centred(o)[0] for o in assetkit.pieces(assetkit.native_parts(
        w5, models={"RW5J5W5J2", "RW5J0W5J2"}, materials={"mtMarutaFloorSmall", "mtYokoMaruta"}))[:2]]
    post_dae = [assetkit.save(p, models / ("log_post_%d.dae" % k)) for k, p in enumerate(posts)]
    for k, (x, z, s, which) in enumerate(LOG_STEPS):
        add(post_dae[which], x, z, s, yaw=40 * k, name="Log post %d" % (k + 1), r=45 * s, sink=8,
            tall=tall_of(posts[which]))
    w7 = W.World(md / "CS_W7.Nin_NX_NVN.szs")                  # Meringue Clouds' white clouds, puffy all round
    clouds = [o for o in assetkit.pieces(assetkit.native_parts(w7, materials={"mtCloudWhite"})) if
              sum(p.triangle_count for p in o.parts) < 6000][:3]
    cdae = []
    for k, c in enumerate(clouds):
        cm, _ = assetkit.centred(c)
        lo, hi = cm.bounds()
        cdae.append((assetkit.save(cm, models / ("cloud_%d.dae" % k)), max(hi[0] - lo[0], hi[2] - lo[2])))
    for k, (x, y, z, width) in enumerate(CLOUDS):
        path, size = cdae[k % len(cdae)]
        proj.add_object(path, position=[x, y, z], rotation=[0.0, 30.0 * k, 0.0], scale=float(width / size),
                        name="Cloud %d" % (k + 1))

    # Yoshi eggs, Yoshi's egg house, the volcano
    for k, (x, z, eggs) in enumerate(EGGS):
        for j, (dx, dz, hgt, col) in enumerate(eggs):
            seed = 11 + ("green", "pink", "yellow", "blue", "red").index(col)     # one texture per colour
            dae = Y.egg_model(models / ("egg_%s_%d_%d.dae" % (col, k, j)), tex, col, seed, height=float(hgt))
            add(dae, x + dx, z + dz, 1.0, yaw=25 * j, name="Egg %d-%d" % (k + 1, j + 1), r=0.4 * hgt, sink=3,
                tall=hgt)
    hx, hz, hh = YOSHI_HOUSE
    add(Y.egg_model(models / "yoshi_house.dae", tex, "green", 7, height=float(hh), door=True), hx, hz, 1.0,
        name="Yoshi's house", r=0.4 * hh, sink=6, tall=hh)
    vx, vz, vs = VOLCANO
    add(Y.volcano_model(models / "volcano.dae", tex), vx, vz, vs, name="Volcano", r=200 * vs, sink=8, tall=224.0)
    return notes


def _upright(model):
    """A hill standing straight: its cap right over its foot and its footprint round (some Wii hills lean)."""
    lo, hi = model.bounds()
    w, d = hi[0] - lo[0], hi[2] - lo[2]
    top = next((p for p in model.parts if p.material.endswith("Top")), None)
    if top is None or max(w, d) > 1.3 * min(w, d):
        return False
    c = top.positions.mean(0)
    return math.hypot(c[0] - (lo[0] + hi[0]) / 2, c[2] - (lo[2] + hi[2]) / 2) < 0.12 * min(w, d)


def _roof_base(model, base):
    """(y, radius, x, z) of the castle's pointed roof foot: its purple texels' lowest ring, in world units."""
    import yi_scenery as Y
    best = None
    for p in model.parts:
        if not p.texture:
            continue
        img = Y.read_png(p.texture)
        h, w = img.shape[:2]
        u = np.clip((p.uvs[:, 0] % 1.0) * w, 0, w - 1).astype(int)
        v = np.clip((p.uvs[:, 1] % 1.0) * h, 0, h - 1).astype(int)
        hue, sat, val = Y.rgb_hsv(img[v, u, :3].astype(np.float64))
        roof = (np.abs(((hue - 108.0) + 180) % 360 - 180) < 40) & (sat > 0.2)    # (already recoloured green)
        if roof.sum() > 20:
            P = p.positions[roof]
            apex = P[np.argmax(P[:, 1])]                    # the tallest roof: the main tower's
            cone = P[(np.hypot(P[:, 0] - apex[0], P[:, 2] - apex[2]) < 60) & (P[:, 1] > apex[1] - 110)]
            cand = (float(apex[1]), cone)
            best = cand if best is None or cand[0] > best[0] else best
    if best is None:
        lo, hi = model.bounds()
        return float(base[1] + hi[1] * 0.7), 40.0, float(base[0]), float(base[2])
    _apex, P = best
    y = float(P[:, 1].min())
    foot = P[P[:, 1] < y + 8]
    cx, cz = foot[:, 0].mean(), foot[:, 2].mean()
    r = float(np.max(np.hypot(foot[:, 0] - cx, foot[:, 2] - cz)))
    return float(base[1] + y), r, float(base[0] + cx), float(base[2] + cz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--romfs")
    ap.add_argument("--nsmbw")
    args = ap.parse_args()
    from PyQt5 import QtWidgets
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from kdx import assetkit, project2d, world as W
    from kdx.objmodel import ObjModel
    root, md, wii = romfs_paths(args.romfs, args.nsmbw)
    out = Path(args.out)
    models = out / "models"
    models.mkdir(parents=True, exist_ok=True)
    w1 = W.World(md / "CS_W1.Nin_NX_NVN.szs")
    surfaces = assetkit.native_parts(w1, models=GROUND_MODELS, materials=GROUND_SURFACES)
    ground = Ground(surfaces)
    nodes, routes, polys = build_layout(w1, ground)
    parts, _ = ground_parts(w1, polys)
    road_tex = assetkit.native_parts(w1, materials={"mtRW101W102a"})[0].texture
    for pid, poly in polys.items():
        a, b = pid[1:5], pid[5:9]
        trim = (34.0 if a[0] == "W" else 0.0, 34.0 if b[0] == "W" else 0.0)
        parts.append(road_part("mt%sa" % pid, poly, ground, road_tex, trim))
    gm = ObjModel(parts)
    ground_dae = assetkit.save(gm, models / "yoshi_island_ground.dae")
    proj = project2d.Project(160, 120, 30.0, 1, name="Yoshi's Island")
    proj.set_model3d(ground_dae, scale=1.0, offset=[0.0, 0.0, 0.0])
    proj.nodes, proj.routes = nodes, routes
    proj.link_next = True
    proj.path = str(out / "Yoshi's Island.kdxmap")
    sys.path.insert(0, str(HERE))
    for note in scenery(proj, w1, ground, polys, models, wii):
        print("placement:", note)
    proj.save(out / "Yoshi's Island.kdxmap")
    rep = proj.check("W1")
    print("ground: %d parts, %d triangles; %d points, %d routes" % (len(gm.parts), sum(p.triangle_count for p in gm.parts),
                                                                  len(nodes), len(routes)))
    print("errors:", rep.errors[:12])
    print("warnings:", rep.warnings[:12])


if __name__ == "__main__":
    main()

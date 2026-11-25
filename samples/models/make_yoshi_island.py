#!/usr/bin/env python3
"""
samples/models/make_yoshi_island.py - builds yoshi_island.obj/.mtl and its textures (original art, made here).

    py -3 samples/models/make_yoshi_island.py

A Yoshi's Island style world for the "Yoshi Island 3D" sample, in the look of the game's own world maps: a grassy island
with rolling hills and striped soil cliffs in a sea with foam, one hand-painted ground texture (crayon grass, flowers,
dirt paths, stone plazas, soft contact shadows), spotted Yoshi eggs, palms, round bushes, smiley flowers, a warp pipe,
rock pillars in the sea, a wooden bridge carrying the walk on to World 2, Boshi's Castle behind the tower (W1T0) and
King Wart's Castle beside the castle (W1C0).

Where things go is fixed by the game: the points of samples/Yoshi Island 3D.kdxmap (ROUTE), and the vanilla walk on to
World 2 as the exporter places it next to W1C0 (WALK). World 2's own map then starts north of z = 375 (its ground was
found there after the shift, up to 185 high), so the island and its sea stay south of NORTH_LIMIT and the walk leaves
over a bridge. The route and its points are kept flat at PLATEAU with nothing standing on or over them.

The model is in world units (scale 1, offset 0) over a 64 x 40-tile map of 30 units. Every triangle winds
counter-clockwise seen from outside, as the game draws them (front faces only); every material stays under 21,845
triangles so its part keeps 16-bit indices, like the parts tested in game.
"""
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
W, D, TILE = 64 * 30.0, 40 * 30.0, 30.0
PLATEAU, SEA_Y, CLIFF_FOOT = 24.0, -8.0, -34.0
NORTH_LIMIT = 400.0                        # World 2's ground reaches z = 375 (after the export's shift)
ROUTE = [(10.5, 20.5), (18.5, 20.5), (30.5, 20.5), (30.5, 24.5), (30.5, 28.5), (40.5, 28.5), (50.5, 28.5)]
POINTS = [(x * TILE, z * TILE) for x, z in (ROUTE[0], ROUTE[1], ROUTE[3], ROUTE[5], ROUTE[6])]
WALK = [(1515.0, 24.0, 855.0), (1515.0, 25.8, 715.0), (1471.8, 27.9, 695.0), (1445.0, 24.0, 655.0), (1445.0, 24.0, 541.6),
        (1445.0, 41.1, 477.9), (1445.0, 72.3, 427.6), (1445.0, 101.3, 384.1), (1445.0, 125.0, 335.9),
        (1445.0, 144.4, 283.7), (1445.0, 154.0, 239.5), (1445.0, 154.0, 195.3), (1445.0, 144.4, 151.4),
        (1445.0, 125.0, 111.4), (1445.0, 72.3, 28.8)]
BRIDGE_FROM = 520.0                        # the bridge carries the walk from here (z) to its end
CLEAR = 75.0
CX, CZ, AX, AZ = 960.0, 808.0, 880.0, 372.0
BOSHI = (1215.0, 735.0)                    # Boshi's Castle, behind the tower W1T0 (1215, 855), gate to the south
WART = (1645.0, 760.0)                     # King Wart's Castle, north-east of the castle point W1C0 (1515, 855)
PADS = [(BOSHI[0], BOSHI[1], 95.0), (WART[0], WART[1], 110.0)]
HILLS = [(280, 930, 85, 70), (600, 1020, 105, 80), (880, 1110, 60, 55), (1340, 1085, 95, 72), (1630, 1010, 70, 58),
         (1720, 600, 48, 42), (1050, 1010, 55, 50)]
EGGS = [(430, 950, 1.0), (505, 990, 0.55), (1180, 1045, 1.15), (690, 1080, 0.85), (1110, 560, 0.85)]
PALMS = [(185, 800), (255, 1040), (560, 1120), (1000, 1135), (1480, 1110), (1765, 905), (330, 480), (800, 485)]
PIPE = (440, 520)
PILLARS = [(-170, 700, 42, 95), (2080, 640, 46, 120), (90, 1290, 38, 80), (1880, 1270, 40, 70), (980, 1330, 34, 60)]
TX0, TX1, TZ0, TZ1 = 0.0, W, NORTH_LIMIT, 1216.0     # the ground picture covers this rectangle
rng = np.random.default_rng(1990)


# --------------------------------------------------------------------------------------------- the land
def smooth(e0, e1, v):
    t = np.clip((v - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def rho(theta):
    """Coast radius factor of the island (a wobbly superellipse) at an angle round its centre."""
    c, s = np.abs(np.cos(theta)), np.abs(np.sin(theta))
    return (c ** 4 + s ** 4) ** -0.25 * (1 + 0.035 * np.sin(3 * theta + 0.4) + 0.025 * np.sin(5 * theta + 1.3)
                                        + 0.015 * np.sin(9 * theta))


def island_xz(r, theta):
    k = r * rho(theta)
    return CX + k * AX * np.cos(theta), CZ + k * AZ * np.sin(theta)


def radius(x, z):
    """< 1 on the island, 1 on the coast."""
    u, v = (x - CX) / AX, (z - CZ) / AZ
    return np.hypot(u, v) / rho(np.arctan2(v, u))


def nearest(x, z, pts, ys=None):
    best = np.full(np.shape(x), 1e9)
    hy = np.zeros(np.shape(x))
    for k, ((ax, az), (bx, bz)) in enumerate(zip(pts, pts[1:])):
        vx, vz = bx - ax, bz - az
        t = np.clip(((x - ax) * vx + (z - az) * vz) / (vx * vx + vz * vz), 0, 1)
        d = np.hypot(x - (ax + t * vx), z - (az + t * vz))
        if ys is not None:
            hy = np.where(d < best, ys[k] + t * (ys[k + 1] - ys[k]), hy)
        best = np.minimum(best, d)
    return best, hy


def route_xz():
    return [(x * TILE, z * TILE) for x, z in ROUTE]


def land_walk():
    """The walk up to the bridge (x, y, z), cut at BRIDGE_FROM."""
    out = []
    for a, b in zip(WALK, WALK[1:]):
        out.append(a)
        if a[2] >= BRIDGE_FROM > b[2]:
            t = (a[2] - BRIDGE_FROM) / (a[2] - b[2])
            out.append(tuple(a[k] + t * (b[k] - a[k]) for k in range(3)))
            return out
    return out


def dist_route(x, z):
    return nearest(x, z, route_xz())[0]


def dist_walk(x, z):
    return nearest(x, z, [(p[0], p[2]) for p in land_walk()])[0]


def dist_points(x, z):
    return np.min([np.hypot(x - px, z - pz) for px, pz in POINTS], axis=0)


def height(x, z):
    r = radius(x, z)
    land = 1 - smooth(0.60, 0.86, r)
    h = np.full(np.shape(x), PLATEAU, dtype=np.float64)
    for hx, hz, amp, sig in HILLS:                                # rounded hills, Yoshi's Island style
        h += land * amp * np.exp(-((x - hx) ** 2 + (z - hz) ** 2) / (2 * sig * sig))
    h += land * 3.5 * np.sin(x / 61.0) * np.cos(z / 47.0) + 1.5 * (1 - smooth(0.94, 1.0, r))   # rolls, a raised rim
    h = PLATEAU + (h - PLATEAU) * smooth(CLEAR * 0.6, CLEAR * 1.6, dist_route(x, z))   # the route stays flat
    for px, pz, rad in PADS:                                      # the castles stand on flat plazas
        h = PLATEAU + (h - PLATEAU) * smooth(rad * 0.8, rad * 1.4, np.hypot(x - px, z - pz))
    lw = land_walk()
    dw, wy = nearest(x, z, [(p[0], p[2]) for p in lw], [p[1] for p in lw])
    on = 1 - smooth(30.0, 70.0, dw)
    return h * (1 - on) + wy * on                                 # the walk keeps its own heights


# --------------------------------------------------------------------------------------------- texture helpers
def save_png(name, rgb):
    from PyQt5 import QtGui
    a = np.ascontiguousarray(np.clip(rgb, 0, 255).astype(np.uint8))
    h, w = a.shape[:2]
    QtGui.QImage(a.tobytes(), w, h, w * 3, QtGui.QImage.Format_RGB888).copy().save(str(HERE / name))


def value_noise(h, w, cell, seed):
    r = np.random.default_rng(seed)
    gh, gw = h // cell + 2, w // cell + 2
    g = r.random((gh, gw)).astype(np.float32)
    ys, xs = np.arange(h) / cell, np.arange(w) / cell
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    a, b = g[y0][:, x0], g[y0][:, x0 + 1]
    c, d = g[y0 + 1][:, x0], g[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def streaks(h, w, seed, length=10, dx=1, dy=-1, density=0.06):
    """Crayon strokes: sparse dots smeared along a direction (wraps, so it tiles). Returns -1..1."""
    r = np.random.default_rng(seed)
    dots = (r.random((h, w)) < density) * r.choice([-1.0, 1.0], (h, w))
    out = np.zeros((h, w), np.float32)
    for k in range(length):
        out += np.roll(np.roll(dots, k * dy, axis=0), k * dx, axis=1) * (1 - k / length)
    return np.clip(out, -1, 1)


def paper(img, seed, grain=5.0, stroke=18.0, length=10):
    h, w = img.shape[:2]
    r = np.random.default_rng(seed)
    img += r.normal(0, grain, (h, w, 1)).astype(np.float32)
    img += (streaks(h, w, seed + 1, length) * stroke)[..., None]
    return img


def fill(h, w, color):
    img = np.empty((h, w, 3), np.float32)
    img[:] = color
    return img


def disc(img, cx, cy, rad, color, outline=None, width=2.0, alpha=1.0):
    h, w = img.shape[:2]
    y0, y1 = int(max(0, cy - rad - width - 1)), int(min(h, cy + rad + width + 2))
    x0, x1 = int(max(0, cx - rad - width - 1)), int(min(w, cx + rad + width + 2))
    if y0 >= y1 or x0 >= x1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx - cx, yy - cy)
    view = img[y0:y1, x0:x1]
    if outline is not None:
        view[d <= rad + width] = view[d <= rad + width] * (1 - alpha) + np.asarray(outline) * alpha
    view[d <= rad] = view[d <= rad] * (1 - alpha) + np.asarray(color, np.float32) * alpha


def bricks(h, w, bw, bh, base, mortar, seed, var=14.0):
    r = np.random.default_rng(seed)
    img = fill(h, w, base)
    yy, xx = np.mgrid[0:h, 0:w]
    row = yy // bh
    xs = xx + (row % 2) * (bw // 2)
    col = xs // bw
    tint = r.normal(0, var, (row.max() + 2, col.max() + 2))
    img += tint[row, col][..., None]
    img[((yy % bh) < 2) | ((xs % bw) < 2)] = mortar
    top = (yy % bh) == 2
    img[top] += 18
    return paper(img, seed + 7, grain=3.0, stroke=8.0, length=5)


# --------------------------------------------------------------------------------------------- the ground picture
def ground_texture(decor, size=(1024, 2048)):
    """The island seen from above (planar UVs over TX0..TX1 x TZ0..TZ1), painted per pixel so grass, paths and plazas
    blend smoothly. `decor` = [(x, z, radius, strength)] contact shadows under the scenery."""
    h, w = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    X = TX0 + (xx + 0.5) / w * (TX1 - TX0)
    Z = TZ0 + (yy + 0.5) / h * (TZ1 - TZ0)
    H = height(X, Z)
    R = radius(X, Z)
    px = (TX1 - TX0) / w
    gz, gx = np.gradient(H, (TZ1 - TZ0) / h, px)
    n = np.dstack([-gx, np.ones_like(gx), -gz])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    light = np.clip(n @ np.array([-0.35, 0.85, -0.40]) / np.linalg.norm([-0.35, 0.85, -0.40]), 0, 1)
    big = value_noise(h, w, 96, 11)
    mid = value_noise(h, w, 24, 12)
    deep, bright = np.array([88, 170, 62], np.float32), np.array([150, 214, 86], np.float32)
    img = deep + (bright - deep) * np.clip(0.35 + 0.5 * big + 0.25 * mid, 0, 1)[..., None]
    up = np.clip((H - PLATEAU) / 100.0, 0, 1)[..., None]
    img += up * np.array([26, 18, -6])                                        # sunny hill tops
    img *= (0.80 + 0.30 * light)[..., None]                                   # painted-in shading of the slopes
    img = paper(img, 13, grain=4.0, stroke=16.0, length=12)
    for hx, hz, amp, sig in HILLS:                                            # light polka dots on the hills
        for _ in range(int(sig / 3)):
            a, rr = rng.uniform(0, 2 * math.pi), abs(rng.normal(0, sig * 0.55))
            x, z = hx + rr * math.cos(a), hz + rr * math.sin(a)
            disc(img, (x - TX0) / px, (z - TZ0) / (TZ1 - TZ0) * h, rng.uniform(4, 7), (190, 236, 140), alpha=0.55)
    rim = smooth(0.955, 0.985, R)[..., None]                                  # a bright lip, then the edge's shadow
    img = img * (1 - rim * 0.25) + rim * 0.25 * np.array([200, 240, 130])
    img *= (1 - 0.35 * smooth(0.985, 1.0, R))[..., None]
    for x, z, rad, k in decor:                                                # soft contact shadows
        d = np.hypot(X - x, Z - z) / rad
        img *= (1 - k * np.exp(-d * d * 2.2))[..., None]
    for (cx, cz, rad), color in zip(PADS, ((206, 214, 222), (210, 200, 160))):   # stone plazas
        d = np.hypot(X - cx, Z - cz)
        cell = ((np.floor(X / 14) + np.floor(Z / 9)) % 2)
        stone = np.asarray(color, np.float32) + (cell * 14 - 7)[..., None] + (mid[..., None] - 0.5) * 30
        mort = ((np.abs((X % 14) - 7) > 6.2) | (np.abs((Z % 9) - 4.5) > 3.8))[..., None]
        stone = np.where(mort, stone * 0.72, stone)
        m = (1 - smooth(rad * 0.62, rad * 0.70, d))[..., None]
        img = img * (1 - m) + stone * m
        edge = ((d > rad * 0.66) & (d < rad * 0.72))[..., None]
        img = np.where(edge, img * 0.7, img)
    for _ in range(700):                                                      # meadow flowers
        x, z = rng.uniform(TX0, TX1), rng.uniform(TZ0, TZ1)
        xa, za = np.array(x), np.array(z)
        if (radius(xa, za) > 0.93 or dist_route(xa, za) < 34 or dist_walk(xa, za) < 34
                or any(math.hypot(x - cx, z - cz) < rad * 0.75 for cx, cz, rad in PADS)):
            continue
        petal = [(250, 110, 120), (255, 214, 80), (150, 140, 245), (255, 255, 255), (255, 160, 60)][rng.integers(0, 5)]
        fx, fy = (x - TX0) / px, (z - TZ0) / (TZ1 - TZ0) * h
        for k in range(5):
            a = k * 2 * math.pi / 5 + rng.uniform(0, 1)
            disc(img, fx + 2.6 * math.cos(a), fy + 2.6 * math.sin(a), 2.0, petal, (80, 70, 60), 0.6)
        disc(img, fx, fy, 1.4, (255, 232, 90))
    d = np.minimum(dist_route(X, Z), dist_walk(X, Z))                         # the dirt paths
    dirt = np.array([226, 190, 128], np.float32) + (mid[..., None] - 0.5) * 26
    dirt = paper(dirt, 14, grain=6.0, stroke=10.0, length=6)
    img = np.where((d <= 15)[..., None], dirt, img)
    img = np.where(((d > 15) & (d <= 19))[..., None], np.array([140, 98, 62], np.float32), img)
    img = np.where(((d > 19) & (d <= 23))[..., None], img * 0.78, img)        # the grass's shadow along the edge
    for line in (route_xz(), [(p[0], p[2]) for p in land_walk()]):            # white stepping dots
        for (ax, az), (bx, bz) in zip(line, line[1:]):
            n_ = int(math.hypot(bx - ax, bz - az) / 26)
            for k in range(1, n_):
                t = k / n_
                disc(img, (ax + t * (bx - ax) - TX0) / px, (az + t * (bz - az) - TZ0) / (TZ1 - TZ0) * h, 3.2,
                     (255, 250, 236), (196, 160, 110), 1.0)
    save_png("yi_ground.png", img)


# --------------------------------------------------------------------------------------------- other textures
def cliff_texture():
    """Soil cliffs: a scalloped grass fringe on top (v = 0), pastel soil bands with wavy edges, darker at the foot."""
    h, w = 256, 512
    img = fill(h, w, (204, 146, 98))
    yy, xx = np.mgrid[0:h, 0:w]
    wave = 5 * np.sin(xx / w * 2 * math.pi * 4) + 3 * np.sin(xx / w * 2 * math.pi * 9 + 1)
    band = ((yy + wave - 40) // 34).astype(int) % 4
    img += np.array([[0, 0, 0], [30, 22, 6], [-22, -18, -8], [34, 30, 22]], np.float32)[band]
    img[np.abs(((yy + wave - 40) % 34)) < 1.6] = (128, 84, 56)
    img = paper(img, 21, grain=4.0, stroke=14.0, length=8)
    scallop = 34 + 9 * np.abs(np.sin(xx / w * math.pi * 16))
    grass = yy < scallop
    img[grass] = (120, 196, 74)
    img[grass & (yy > scallop - 6)] = (82, 150, 56)
    img[(yy >= scallop) & (yy < scallop + 5)] *= 0.7                          # the fringe's shadow
    img *= (1 - 0.45 * smooth(170, 256, yy))[..., None]
    img += (smooth(200, 256, yy) * 30)[..., None] * np.array([-0.4, 0.1, 1.0])  # wet, bluish at the waterline
    save_png("yi_cliff.png", img)


def sea_texture():
    h = w = 256
    img = fill(h, w, (92, 170, 232))
    yy, xx = np.mgrid[0:h, 0:w]
    img += (value_noise(h, w, 32, 31)[..., None] - 0.5) * np.array([20, 24, 18])
    for k in range(9):                                                        # wavy crayon lines
        y0 = (k * 29 + 7) % h
        wv = y0 + 4 * np.sin(xx / w * 2 * math.pi * 2 + k)
        m = np.abs(((yy - wv + h / 2) % h) - h / 2) < 1.4
        img[m & (((xx + k * 37) % 96) < 58)] = (190, 230, 252)
    img = paper(img, 32, grain=3.0, stroke=10.0, length=9)
    save_png("yi_sea.png", img)


def foam_texture():
    h, w = 64, 256
    yy, xx = np.mgrid[0:h, 0:w]
    t = yy / (h - 1)                                                          # 0 at the coast, 1 out at sea
    sea = np.array([92, 170, 232], np.float32)
    img = sea + (np.array([250, 252, 255]) - sea) * (1 - smooth(0.0, 0.7, t + 0.12 * np.sin(xx / w * 2 * math.pi * 12)))[..., None]
    img[np.abs(t - 0.55 - 0.05 * np.sin(xx / w * 2 * math.pi * 7)) < 0.03] = (210, 238, 252)
    save_png("yi_foam.png", paper(img, 33, grain=2.0, stroke=6.0, length=6))


EGG_RX, EGG_RY = 40.0, 54.0


def egg_point(t, phi):
    """A point on the unit Yoshi egg (latitude t from the top, angle phi from +x towards +z): narrower at the top."""
    r = EGG_RX * np.sin(t) * (1 - 0.12 * np.cos(t))
    return np.stack([r * np.cos(phi), EGG_RY * np.cos(t), r * np.sin(phi)], axis=-1)


EGG_SPOTS = [(1.45, math.pi / 2, 15.0), (1.30, math.pi / 2 + 2.1, 14.0), (1.35, math.pi / 2 - 2.0, 14.0),
             (0.55, math.pi / 2 + 1.0, 11.0), (0.65, math.pi / 2 - 1.3, 10.5), (0.70, math.pi / 2 + 3.4, 10.0),
             (2.20, math.pi / 2 + 1.1, 13.0), (2.15, math.pi / 2 - 1.1, 13.0), (2.30, math.pi / 2 + 3.1, 12.0),
             (0.05, 0.0, 8.0)]


def egg_texture():
    """Lat-long texture (u = phi / 2pi, v = t / pi) with round green spots placed on the egg's surface itself, so
    they stay round on the stretched shape."""
    h, w = 256, 512
    yy, xx = np.mgrid[0:h, 0:w]
    P = egg_point((yy + 0.5) / h * math.pi, (xx + 0.5) / w * 2 * math.pi)
    img = fill(h, w, (252, 250, 240))
    img = paper(img, 41, grain=2.0, stroke=4.0, length=5)
    for t, phi, rad in EGG_SPOTS:
        c = egg_point(np.array(t), np.array(phi))
        d = np.linalg.norm(P - c, axis=-1)
        img[d <= rad + 1.3] = (64, 150, 52)
        spot = d <= rad
        img[spot] = (96, 192, 70)
        img[spot & (d > rad * 0.55) & (d < rad * 0.72)] += np.array([10, 14, 8])
    save_png("yi_egg.png", img)


def simple_textures():
    trunk = fill(128, 128, (176, 120, 74))
    yy = np.mgrid[0:128, 0:128][0]
    trunk[(yy % 32) < 14] += np.array([34, 26, 12], np.float32)
    trunk[(yy % 32) == 14] = (110, 72, 46)
    save_png("yi_trunk.png", paper(trunk, 51, 3.0, 10.0, 6))
    frond = fill(128, 128, (84, 186, 76))
    xx = np.mgrid[0:128, 0:128][1]
    frond[np.abs(xx - 64) < 4] = (190, 232, 120)
    frond[(np.abs(xx - 64) > 4) & ((xx + yy // 3) % 16 < 2)] = (60, 150, 60)
    save_png("yi_frond.png", paper(frond, 52, 3.0, 14.0, 7))
    bush = fill(128, 128, (86, 176, 66)) + (value_noise(128, 128, 16, 53)[..., None] - 0.5) * 40
    save_png("yi_bush.png", paper(bush, 54, 3.0, 22.0, 8))
    stem = fill(64, 64, (70, 160, 60))
    save_png("yi_stem.png", paper(stem, 55, 2.0, 8.0, 4))
    flower = fill(128, 128, (255, 255, 255))                                  # a smiley flower head
    yy, xx = np.mgrid[0:128, 0:128]
    ang = np.arctan2(yy - 64, xx - 64)
    d = np.hypot(xx - 64, yy - 64)
    flower[(d > 30) & (np.cos(ang * 5) > 0.2)] = (255, 236, 244)
    flower[d > 58] = (226, 90, 120)
    flower[(d > 50) & (d <= 58)] = (240, 130, 160)
    disc(flower, 64, 64, 30, (255, 222, 60), (220, 160, 30), 3.0)
    disc(flower, 54, 56, 4.0, (40, 30, 30)); disc(flower, 74, 56, 4.0, (40, 30, 30))
    smile = (np.abs(np.hypot(xx - 64, yy - 60) - 14) < 2.2) & (yy > 66)
    flower[smile] = (40, 30, 30)
    disc(flower, 48, 70, 4.0, (255, 160, 140), alpha=0.6); disc(flower, 80, 70, 4.0, (255, 160, 140), alpha=0.6)
    save_png("yi_flower.png", paper(flower, 56, 2.0, 5.0, 4))
    pipe = fill(128, 128, (70, 186, 72))                                      # u round the pipe, v: inside first
    xx = np.mgrid[0:128, 0:128][1]
    pipe[(xx % 128 > 40) & (xx % 128 < 56)] += 60
    pipe[(xx % 128 > 90) & (xx % 128 < 110)] *= 0.75
    pipe[:20] = (20, 60, 24)
    pipe[20:24] = (40, 110, 44)
    save_png("yi_pipe.png", paper(pipe, 57, 2.0, 6.0, 5))
    wood = fill(128, 128, (186, 128, 76))
    yy = np.mgrid[0:128, 0:128][0]
    wood[(yy % 16) < 2] = (110, 70, 42)
    wood += (value_noise(128, 128, 8, 58)[..., None] - 0.5) * 24
    save_png("yi_wood.png", paper(wood, 59, 3.0, 12.0, 10))
    save_png("yi_boshi_stone.png", bricks(256, 256, 48, 22, (176, 208, 236), (110, 140, 176), 61))
    roof = fill(128, 128, (226, 52, 48))
    xx = np.mgrid[0:128, 0:128][1]
    roof[(xx % 32) < 10] += np.array([24, 30, 30])
    save_png("yi_boshi_roof.png", paper(roof, 62, 3.0, 10.0, 6))
    dome = fill(256, 256, (66, 132, 230))                                     # Boshi's head: blue, black shades
    yy, xx = np.mgrid[0:256, 0:256]
    dome[yy > 200] = (40, 96, 200)
    shades = (yy > 92) & (yy < 132) & (np.abs(((xx - 64) % 256) - 128) > 22)   # round the front, a nose gap behind
    dome[shades] = (24, 24, 32)
    dome[shades & (yy > 98) & (yy < 104) & ((xx % 128) < 30)] = (230, 236, 255)
    for k in range(10):
        disc(dome, (k * 26 + 5) % 256, 40 + (k % 3) * 12, 5.0, (120, 176, 246))
    save_png("yi_boshi_dome.png", paper(dome, 63, 2.0, 8.0, 6))
    save_png("yi_wart_stone.png", bricks(256, 256, 40, 24, (128, 150, 82), (78, 92, 48), 64))
    eye = fill(256, 512, (250, 250, 246))                                     # u round the eye; +z (front) at u 0.25
    yy, xx = np.mgrid[0:256, 0:512]
    disc(eye, 128, 120, 36, (30, 24, 28), (60, 40, 40), 3.0)
    disc(eye, 116, 106, 9, (255, 255, 255))
    eye[yy > 210] = (110, 150, 70)                                            # the eyelid base, the castle's green
    save_png("yi_wart_eye.png", paper(eye, 65, 2.0, 4.0, 4))
    gold = fill(128, 128, (240, 196, 54))
    xx = np.mgrid[0:128, 0:128][1]
    gold[(xx % 24) < 6] = (255, 236, 140)
    save_png("yi_gold.png", paper(gold, 66, 2.0, 8.0, 5))
    cap = fill(256, 512, (226, 40, 44))                                       # SMB2 mushroom cap: red, white spots
    for u, v, rad in ((64, 70, 34), (192, 60, 30), (320, 74, 34), (448, 62, 30), (128, 150, 22), (384, 150, 22), (256, 0, 30)):
        for du in (-512, 0, 512):
            disc(cap, u + du, v, rad, (255, 255, 250), (200, 190, 190), 2.0)
    cap[200:] = (240, 220, 190)                                               # underside
    save_png("yi_mushcap.png", paper(cap, 67, 2.0, 6.0, 5))
    jar = fill(128, 128, (210, 120, 60))
    jar[:18] = (60, 36, 26)
    jar[18:26] = (240, 170, 100)
    jar[60:66] = (150, 80, 40)
    save_png("yi_jar.png", paper(jar, 68, 2.0, 8.0, 5))
    door = fill(64, 64, (70, 40, 34))
    door[:, ::16] = (40, 22, 20)
    save_png("yi_door.png", paper(door, 69, 2.0, 4.0, 4))
    mouth = fill(64, 64, (60, 16, 24))
    mouth[44:] = (220, 90, 110)
    save_png("yi_mouth.png", paper(mouth, 70, 2.0, 4.0, 4))
    red = fill(64, 64, (220, 40, 60))
    save_png("yi_red.png", paper(red, 71, 2.0, 4.0, 4))


# --------------------------------------------------------------------------------------------- geometry
def normalize(v):
    v = np.asarray(v, np.float64)
    return v / max(np.linalg.norm(v), 1e-12)


class Obj:
    def __init__(self):
        self.v, self.vt, self.vn, self.faces = [], [], [], {}

    def tri(self, mat, corners):
        """corners: 3 x (position, uv, normal); counter-clockwise seen from the front; uv with v running down."""
        out = []
        for p, t, n in corners:
            self.v.append(tuple(float(c) for c in p)); self.vt.append((float(t[0]), float(t[1])))
            self.vn.append(tuple(float(c) for c in n))
            out.append(len(self.v))
        self.faces.setdefault(mat, []).append(out)

    def quad(self, mat, ps, uvs, outward):
        """A flat quad p0..p3 (either winding), turned to face `outward`."""
        ps = [np.asarray(p, np.float64) for p in ps]
        n = normalize(np.cross(ps[1] - ps[0], ps[2] - ps[0]))
        if np.dot(n, outward) < 0:
            ps, uvs, n = ps[::-1], uvs[::-1], -n
        c = [(ps[k], uvs[k], n) for k in range(4)]
        self.tri(mat, [c[0], c[1], c[2]])
        self.tri(mat, [c[0], c[2], c[3]])

    def write(self, path, mtl):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Yoshi Island 3D - made by make_yoshi_island.py (Koopatlas DX sample, original art)\n")
            f.write("mtllib %s\n" % mtl)
            for p in self.v:
                f.write("v %.3f %.3f %.3f\n" % p)
            for t in self.vt:
                f.write("vt %.5f %.5f\n" % (t[0], 1.0 - t[1]))      # OBJ v runs upwards
            for n in self.vn:
                f.write("vn %.4f %.4f %.4f\n" % n)
            for mat, faces in self.faces.items():
                f.write("usemtl %s\n" % mat)
                for a, b, c in faces:
                    f.write("f %d/%d/%d %d/%d/%d %d/%d/%d\n" % (a, a, a, b, b, b, c, c, c))


def box(obj, mat, base, size, yaw=0.0, uv=40.0, top=True):
    """An upright box standing on base (x, y, z) with size (w, h, d), turned yaw radians about y; sides textured in
    world units (uv per repeat), image top at the box's top."""
    w, h, d = size
    cy, sy = math.cos(yaw), math.sin(yaw)

    def P(x, y, z):
        return (base[0] + x * cy - z * sy, base[1] + y, base[2] + x * sy + z * cy)

    def R(v):
        return (v[0] * cy - v[2] * sy, v[1], v[0] * sy + v[2] * cy)
    hw, hd = w / 2, d / 2
    sides = [((-hw, hd), (hw, hd), (0, 0, 1), w), ((hw, hd), (hw, -hd), (1, 0, 0), d),
             ((hw, -hd), (-hw, -hd), (0, 0, -1), w), ((-hw, -hd), (-hw, hd), (-1, 0, 0), d)]
    for (ax, az), (bx, bz), n, length in sides:
        obj.quad(mat, [P(ax, 0, az), P(bx, 0, bz), P(bx, h, bz), P(ax, h, az)],
                 [(0, h / uv), (length / uv, h / uv), (length / uv, 0), (0, 0)], R(n))
    if top:
        obj.quad(mat, [P(-hw, h, -hd), P(hw, h, -hd), P(hw, h, hd), P(-hw, h, hd)],
                 [(0, 0), (w / uv, 0), (w / uv, d / uv), (0, d / uv)], (0, 1, 0))


def lathe(obj, mat, base, profile, seg=16, urep=1.0, vrep=1.0, v_by_index=False, smooth_deg=40.0):
    """A surface of revolution round the vertical axis through base. profile: [(r, y)] from the bottom, with the outside
    on the right (bottom centre -> out -> up -> top centre); u goes round from +x towards +z, v from the top (0)."""
    prof = [(float(r), float(y)) for r, y in profile]
    segn = []
    for (r0, y0), (r1, y1) in zip(prof, prof[1:]):
        t = normalize((r1 - r0, y1 - y0))
        segn.append(np.array([t[1], -t[0]]))                    # (radial, up) normal: the right of the direction
    lens = [0.0]
    for (r0, y0), (r1, y1) in zip(prof, prof[1:]):
        lens.append(lens[-1] + math.hypot(r1 - r0, y1 - y0))
    total = max(lens[-1], 1e-9)
    cos_lim = math.cos(math.radians(smooth_deg))

    def vnorm(i, side):                                          # the normal at profile point i for segment `side`
        own = segn[side]
        other = side - 1 if i == side else side + 1
        if 0 <= other < len(segn) and np.dot(own, segn[other]) > cos_lim:
            return normalize(own + segn[other])
        return own
    for i in range(len(prof) - 1):
        (r0, y0), (r1, y1) = prof[i], prof[i + 1]
        n0, n1 = vnorm(i, i), vnorm(i + 1, i)
        v0 = (1 - (i / (len(prof) - 1) if v_by_index else lens[i] / total)) * vrep
        v1 = (1 - ((i + 1) / (len(prof) - 1) if v_by_index else lens[i + 1] / total)) * vrep
        for s in range(seg):
            f0, f1 = 2 * math.pi * s / seg, 2 * math.pi * (s + 1) / seg

            def C(r, y, f, nn, v):
                return ((base[0] + r * math.cos(f), base[1] + y, base[2] + r * math.sin(f)),
                        (f / (2 * math.pi) * urep, v), (nn[0] * math.cos(f), nn[1], nn[0] * math.sin(f)))
            a, b = C(r0, y0, f0, n0, v0), C(r1, y1, f0, n1, v1)
            c, d = C(r0, y0, f1, n0, v0), C(r1, y1, f1, n1, v1)
            if r0 > 1e-6:
                obj.tri(mat, [a, b, c])
            if r1 > 1e-6:
                obj.tri(mat, [c, b, d])


def sphere_profile(rad, ry=None, sink=0.0, rings=10):
    ry = rad if ry is None else ry
    return [(rad * math.sin(t), ry - sink + ry * math.cos(t)) for t in np.linspace(math.pi, 0, rings + 1)]


def ground_y(x, z):
    return float(height(np.array(x), np.array(z)))


# ---- the island ------------------------------------------------------------------------------------------------------
def island(obj, na=192, nr=52):
    th = np.linspace(0, 2 * math.pi, na + 1)[:-1]
    rs = np.linspace(0, 1, nr + 1)
    T, Rr = np.meshgrid(th, rs)
    X, Z = island_xz(Rr, T)
    H = height(X, Z)
    e = 1.5
    gx = (height(X + e, Z) - height(X - e, Z)) / (2 * e)
    gz = (height(X, Z + e) - height(X, Z - e)) / (2 * e)
    N = np.dstack([-gx, np.ones_like(gx), -gz])
    N /= np.linalg.norm(N, axis=2, keepdims=True)

    def C(k, j):
        j %= na
        return ((X[k, j], H[k, j], Z[k, j]), ((X[k, j] - TX0) / (TX1 - TX0), (Z[k, j] - TZ0) / (TZ1 - TZ0)), N[k, j])
    for j in range(na):                                          # centre fan, then rings (counter-clockwise from above)
        obj.tri("YIGround", [C(0, 0), C(1, j + 1), C(1, j)])
    for k in range(1, nr):
        for j in range(na):
            a, b, c, d = C(k, j), C(k + 1, j), C(k, j + 1), C(k + 1, j + 1)
            obj.tri("YIGround", [a, c, b])
            obj.tri("YIGround", [c, d, b])
    per = 0.0                                                    # the cliff all round, down into the sea
    edge = [(X[nr, j], H[nr, j], Z[nr, j]) for j in range(na)] + [(X[nr, 0], H[nr, 0], Z[nr, 0])]
    for j in range(na):
        (x0, y0, z0), (x1, y1, z1) = edge[j], edge[j + 1]
        L = math.hypot(x1 - x0, z1 - z0)
        out = normalize((z1 - z0, 0, -(x1 - x0)))               # up x tangent: away from the island
        u0, u1 = per / 220.0, (per + L) / 220.0
        foot0, foot1 = min(1.0, (y0 - CLIFF_FOOT) / 70.0), min(1.0, (y1 - CLIFF_FOOT) / 70.0)   # grass fringe at the top
        obj.quad("YICliff", [(x0, CLIFF_FOOT, z0), (x1, CLIFF_FOOT, z1), (x1, y1, z1), (x0, y0, z0)],
                 [(u0, foot0), (u1, foot1), (u1, 0.0), (u0, 0.0)], out)
        per += L
    for j in range(na):                                          # foam round the island
        t0, t1 = th[j], th[(j + 1) % na] + (2 * math.pi if j == na - 1 else 0)
        pts = [island_xz(1.0, t0), island_xz(1.0, t1), island_xz(1.09, t1), island_xz(1.09, t0)]
        obj.quad("YIFoam", [(p[0], SEA_Y + 0.6, p[1]) for p in pts],
                 [(j / 24.0, 0), ((j + 1) / 24.0, 0), ((j + 1) / 24.0, 1), (j / 24.0, 1)], (0, 1, 0))


def sea(obj, x0=-1200.0, x1=3100.0, z0=NORTH_LIMIT, z1=2400.0, nx=24, nz=12):
    xs, zs = np.linspace(x0, x1, nx + 1), np.linspace(z0, z1, nz + 1)
    for i in range(nx):
        for k in range(nz):
            ps = [(xs[i], SEA_Y, zs[k]), (xs[i + 1], SEA_Y, zs[k]), (xs[i + 1], SEA_Y, zs[k + 1]), (xs[i], SEA_Y, zs[k + 1])]
            obj.quad("YISea", ps, [(p[0] / 260.0, p[2] / 260.0) for p in ps], (0, 1, 0))


# ---- scenery --------------------------------------------------------------------------------------------------------
def egg(obj, x, z, s):
    g = ground_y(x, z)
    ts = np.linspace(math.pi, 0, 17)                             # bottom to top, even latitude (matches egg_texture)
    prof = [(float(EGG_RX * s * math.sin(t) * (1 - 0.12 * math.cos(t))), float(EGG_RY * s * (1 + math.cos(t))))
            for t in ts]
    lathe(obj, "YIEgg", (x, g - 7 * s, z), prof, seg=24, v_by_index=True, smooth_deg=60)


def palm(obj, base, height_, lean, seed):
    r = np.random.default_rng(seed)
    bx, by, bz = base
    lx, lz = lean
    segs, rad = 8, 9.0
    rings = []
    for k in range(segs + 1):
        t = k / segs
        cx, cz = bx + lx * t * t, bz + lz * t * t
        cy = by - 6 + height_ * t
        rr = rad * (1 - 0.35 * t)
        rings.append([((cx + rr * math.cos(2 * math.pi * s / 10), cy, cz + rr * math.sin(2 * math.pi * s / 10)),
                       (s / 10, t * 3), (math.cos(2 * math.pi * s / 10), 0.0, math.sin(2 * math.pi * s / 10)))
                      for s in range(11)])
    for k in range(segs):
        for s in range(10):
            a, b, c, d = rings[k][s], rings[k + 1][s], rings[k][s + 1], rings[k + 1][s + 1]
            obj.tri("YITrunk", [a, b, c])
            obj.tri("YITrunk", [c, b, d])
    top = (bx + lx, by - 6 + height_, bz + lz)
    lathe(obj, "YITrunk", (top[0], top[1] - 4, top[2]), sphere_profile(10, 8, rings=6), seg=10)   # coconut crown
    for f in range(8):
        ang = 2 * math.pi * f / 8 + r.uniform(-0.2, 0.2)
        length, width = r.uniform(72, 92), 19.0
        dx, dz = math.cos(ang), math.sin(ang)
        sx, sz = -dz, dx
        strip = []
        for k in range(7):
            t = k / 6
            px, pz = top[0] + dx * length * t, top[2] + dz * length * t
            py = top[1] + 14 * t - 54 * t * t
            wdt = width * math.sin(math.pi * (0.15 + 0.85 * t)) * (1 - 0.6 * t)
            strip.append(((px + sx * wdt, py, pz + sz * wdt), (px - sx * wdt, py, pz - sz * wdt), t))
        for (l0, r0, t0), (l1, r1, t1) in zip(strip, strip[1:]):
            q = [l0, r0, r1, l1]
            uvs = [(0, t0), (1, t0), (1, t1), (0, t1)]
            n = normalize(np.cross(np.subtract(r0, l0), np.subtract(l1, l0)))
            up = n if n[1] > 0 else -n
            obj.quad("YIFrond", q, uvs, up)                      # both sides: seen from above and below
            obj.quad("YIFrond", q, uvs, -up)


def bush(obj, x, z, seed):
    r = np.random.default_rng(seed)
    g = ground_y(x, z)
    for k in range(3):
        a = 2 * math.pi * k / 3 + r.uniform(0, 1)
        rad = r.uniform(13, 19)
        cx, cz = x + math.cos(a) * 11, z + math.sin(a) * 8
        lathe(obj, "YIBush", (cx, ground_y(cx, cz) - 5, cz), sphere_profile(rad, rad * 0.8, rings=7), seg=12, urep=2)


def flower(obj, x, z, size=1.0, tilt=math.radians(35)):
    g = ground_y(x, z)
    hgt = 30 * size
    box(obj, "YIStem", (x, g - 2, z), (3, hgt, 3), uv=20)
    for side in (-1, 1):                                         # leaves
        obj.quad("YIStem", [(x, g + 6, z), (x + side * 12 * size, g + 12 * size, z - 3),
                            (x + side * 14 * size, g + 16 * size, z + 3), (x, g + 10, z)],
                 [(0, 1), (1, 1), (1, 0), (0, 0)], (0, 1, 0.3))
    R = 16 * size
    c = np.array([x, g + hgt + R * 0.6, z])
    ex, ey = np.array([1.0, 0, 0]), np.array([0, math.cos(tilt), -math.sin(tilt)])
    front = np.cross(ex, ey)
    pts = []
    for k in range(20):
        a = 2 * math.pi * k / 20
        rr = R if k % 2 == 0 else R * 0.72
        pts.append((rr * math.cos(a), rr * math.sin(a)))
    for k in range(20):
        (ax, ay), (bx, by) = pts[k], pts[(k + 1) % 20]
        P = [c, c + ax * ex + ay * ey, c + bx * ex + by * ey]
        U = [(0.5, 0.5), (0.5 + ax / (2 * R), 0.5 - ay / (2 * R)), (0.5 + bx / (2 * R), 0.5 - by / (2 * R))]
        obj.tri("YIFlower", [(P[0], U[0], front), (P[1], U[1], front), (P[2], U[2], front)])
        obj.tri("YIFlower", [(P[0], U[0], -front), (P[2], U[2], -front), (P[1], U[1], -front)])


def pipe(obj, x, z):
    g = ground_y(x, z)
    prof = [(19, -8), (19, 32), (24, 32), (24, 46), (18, 46), (18, 40), (0, 40)]
    lathe(obj, "YIPipe", (x, g, z), prof, seg=20, smooth_deg=20)


def pillar(obj, x, z, rad, top):
    prof = [(rad, CLIFF_FOOT - 10), (rad * 0.92, top), (rad * 0.78, top + 8), (rad * 0.4, top + 13), (0, top + 14)]
    lathe(obj, "YICliff", (x, 0, z), prof, seg=20, urep=round(2 * math.pi * rad / 220.0) or 1, smooth_deg=50)


def bridge(obj):
    pts = [p for p in WALK if p[2] < BRIDGE_FROM]
    a, b = next((p, q) for p, q in zip(WALK, WALK[1:]) if p[2] >= BRIDGE_FROM > q[2])
    t = (a[2] - BRIDGE_FROM) / (a[2] - b[2])
    pts = [tuple(a[k] + t * (b[k] - a[k]) for k in range(3))] + pts
    (px_, py_, pz_), (qx_, qy_, qz_) = pts[-2], pts[-1]
    L = math.hypot(qx_ - px_, qz_ - pz_)
    pts.append((qx_ + (qx_ - px_) / L * 14, qy_, qz_ + (qz_ - pz_) / L * 14))   # a landing past the walk's last bend
    hw, along = 23.0, 0.0
    for p, q in zip(pts, pts[1:]):
        L = math.hypot(q[0] - p[0], q[2] - p[2], q[1] - p[1])
        y0, y1 = p[1] + 0.6, q[1] + 0.6
        dz = 1 if q[2] > p[2] else -1
        dirx, dirz = (q[0] - p[0]) / max(L, 1e-9), (q[2] - p[2]) / max(L, 1e-9)
        sx, sz = -dirz, dirx
        l0 = (p[0] + sx * hw, y0, p[2] + sz * hw); r0 = (p[0] - sx * hw, y0, p[2] - sz * hw)
        l1 = (q[0] + sx * hw, y1, q[2] + sz * hw); r1 = (q[0] - sx * hw, y1, q[2] - sz * hw)
        uv = [(0, along / 14.0), (1, along / 14.0), (1, (along + L) / 14.0), (0, (along + L) / 14.0)]
        obj.quad("YIWood", [l0, r0, r1, l1], uv, (0, 1, 0))
        for e0, e1, side in ((l0, l1, 1), (r0, r1, -1)):         # deck edges, then rails on posts
            obj.quad("YIWood", [e0, e1, (e1[0], e1[1] - 5, e1[2]), (e0[0], e0[1] - 5, e0[2])],
                     [(0, 0), (L / 40, 0), (L / 40, 0.12), (0, 0.12)], (side * sx, 0, side * sz))
            r0_ = (e0[0], e0[1] + 13, e0[2]); r1_ = (e1[0], e1[1] + 13, e1[2])
            obj.quad("YIWood", [r0_, r1_, (r1_[0], r1_[1] + 3, r1_[2]), (r0_[0], r0_[1] + 3, r0_[2])],
                     [(0, 0), (L / 40, 0), (L / 40, 0.1), (0, 0.1)], (side * sx, 0, side * sz))
            obj.quad("YIWood", [(r0_[0], r0_[1] + 3, r0_[2]), (r1_[0], r1_[1] + 3, r1_[2]),
                                (r1_[0] - side * sx * 3, r1_[1] + 3, r1_[2] - side * sz * 3),
                                (r0_[0] - side * sx * 3, r0_[1] + 3, r0_[2] - side * sz * 3)],
                     [(0, 0), (L / 40, 0), (L / 40, 0.1), (0, 0.1)], (0, 1, 0))
        for e, side in ((l0, 1), (r0, -1)):
            box(obj, "YIWood", (e[0], CLIFF_FOOT if p[2] < 470 else e[1] - 30, e[2]), (5, e[1] + 17 - (CLIFF_FOOT if p[2] < 470 else e[1] - 30), 5), uv=30)
        along += L
    q = pts[-1]
    for side in (1, -1):
        box(obj, "YIWood", (q[0] + side * hw, CLIFF_FOOT, q[2]), (5, q[1] + 17 - CLIFF_FOOT, 5), uv=30)


# ---- the castles ----------------------------------------------------------------------------------------------------
def boshi_castle(obj):
    """Boshi's Castle: pale blue stone, round towers with red spiked collars, a blue Boshi-head dome in black shades."""
    x, z = BOSHI
    g = PLATEAU
    box(obj, "YIBoshiStone", (x, g - 2, z), (92, 62, 56), uv=48)
    for k in range(7):                                           # battlements along the front and back
        for zz in (z + 26, z - 26):
            box(obj, "YIBoshiStone", (x - 42 + k * 14, g + 60, zz), (8, 9, 6), uv=24)
    for dx in (-50, 50):
        for dz in (-30, 30):
            tx, tz = x + dx, z + dz
            lathe(obj, "YIBoshiStone", (tx, g - 2, tz), [(0, 0), (16, 0), (16, 74), (19, 76), (19, 84), (0, 84)],
                  seg=16, urep=2, vrep=2, smooth_deg=10)
            lathe(obj, "YIBoshiRoof", (tx, g + 82, tz), [(0, 0), (21, 0), (0, 34)], seg=16, urep=2)
            for s in range(8):                                   # the spiked collar
                a = 2 * math.pi * s / 8
                lathe(obj, "YIBoshiRoof", (tx + 19 * math.cos(a), g + 80, tz + 19 * math.sin(a)),
                      [(0, 0), (3.5, 0), (0, 10)], seg=6)
    lathe(obj, "YIBoshiDome", (x, g + 58, z), sphere_profile(32, 30, sink=6, rings=10), seg=24, v_by_index=True)
    for s in range(14):                                          # Boshi's red collar round the dome
        a = 2 * math.pi * s / 14
        lathe(obj, "YIBoshiRoof", (x + 31 * math.cos(a), g + 58, z + 31 * math.sin(a)), [(0, 0), (4.5, 0), (0, 12)], seg=6)
    box(obj, "YIDoor", (x, g - 2, z + 28.5), (24, 36, 3), uv=36)   # the gate, and windows
    for wx in (-30, 30):
        box(obj, "YIDoor", (x + wx, g + 30, z + 28.3), (9, 14, 2), uv=16)
    box(obj, "YIWood", (x, g + 112, z), (2, 30, 2), uv=30)       # flag
    obj.quad("YIBoshiRoof", [(x + 1, g + 134, z), (x + 24, g + 130, z), (x + 24, g + 120, z), (x + 1, g + 124, z)],
             [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 0, 1))
    obj.quad("YIBoshiRoof", [(x + 1, g + 134, z), (x + 24, g + 130, z), (x + 24, g + 120, z), (x + 1, g + 124, z)],
             [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 0, -1))


def wart_castle(obj):
    """King Wart's Castle: an olive brick hall with a frog face (big eyes, a mouth gate), a gold crown, towers with red
    spotted mushroom caps and two big vases, after Super Mario Bros. 2."""
    x, z = WART
    g = PLATEAU
    box(obj, "YIWartStone", (x, g - 2, z), (112, 58, 72), uv=40)
    box(obj, "YIWartStone", (x, g + 56, z - 6), (76, 12, 44), uv=40)
    for ex in (-26, 26):                                         # the frog's eyes, looking south (+z)
        lathe(obj, "YIWartEye", (x + ex, g + 52, z + 22), sphere_profile(17, 17, rings=10), seg=24, v_by_index=True)
    box(obj, "YIMouth", (x, g - 2, z + 36.5), (54, 28, 3), uv=54)
    lathe(obj, "YIGold", (x, g + 66, z - 6), [(0, 0), (22, 0), (22, 14), (0, 14)], seg=20, urep=3, smooth_deg=5)
    for s in range(7):                                           # the crown's points and a jewel
        a = 2 * math.pi * s / 7 + 0.3
        lathe(obj, "YIGold", (x + 20 * math.cos(a), g + 80, z - 6 + 20 * math.sin(a)), [(0, 0), (5, 0), (0, 15)], seg=6)
    lathe(obj, "YIRed", (x, g + 80, z - 6), sphere_profile(7, 7, rings=6), seg=10)
    for dx in (-66, 66):
        tx, tz = x + dx, z - 8
        lathe(obj, "YIWartStone", (tx, g - 2, tz), [(0, 0), (17, 0), (17, 76), (0, 76)], seg=16, urep=2, vrep=2, smooth_deg=5)
        cap = [(0, 0), (16, 0), (30, 2), (31, 7)] + [(31 * math.sin(t), 7 + 24 * math.cos(t)) for t in np.linspace(math.pi / 2 - 0.1, 0, 7)]
        lathe(obj, "YIMushCap", (tx, g + 72, tz), cap, seg=20, v_by_index=True, smooth_deg=50)
    for jx in (-44, 44):
        jar = [(0, 0), (9, 0), (14, 8), (15, 18), (11, 28), (8, 31), (10, 35), (10, 37), (7, 37), (0, 36)]
        lathe(obj, "YIJar", (x + jx, g - 1, z + 52), jar, seg=16, v_by_index=True, smooth_deg=45)


# --------------------------------------------------------------------------------------------- placing the scenery
def placements():
    """Bushes along the paths and smiley flowers in the meadows, clear of the points, the paths and everything else."""
    taken = [(x, z, 50 * s) for x, z, s in EGGS] + [(x, z, 40) for x, z in PALMS] + [(PIPE[0], PIPE[1], 40)]
    taken += [(px, pz, rad * 0.9) for px, pz, rad in PADS]

    def free(x, z, need):
        xa, za = np.array(x), np.array(z)
        return (radius(xa, za) < 0.86 and dist_points(xa, za) > 90 and dist_route(xa, za) > need
                and dist_walk(xa, za) > need and all(math.hypot(x - a, z - b) > r + 24 for a, b, r in taken))
    bushes = []
    line = route_xz() + [(p[0], p[2]) for p in land_walk()[1:]]
    for (ax, az), (bx, bz) in zip(line, line[1:]):
        L = math.hypot(bx - ax, bz - az)
        sx, sz = -(bz - az) / L, (bx - ax) / L
        for k in range(int(L // 85)):
            t = (k + 0.5) / max(1, int(L // 85))
            for side in (1, -1):
                x, z = ax + t * (bx - ax) + side * sx * rng.uniform(56, 70), az + t * (bz - az) + side * sz * rng.uniform(56, 70)
                if free(x, z, 50):
                    bushes.append((x, z)); taken.append((x, z, 26))
    flowers = []
    tries = 0
    while len(flowers) < 16 and tries < 4000:
        tries += 1
        x, z = rng.uniform(150, 1800), rng.uniform(450, 1170)
        if free(x, z, 60):
            flowers.append((x, z, rng.uniform(0.85, 1.2))); taken.append((x, z, 26))
    return bushes, flowers


def main():
    sys.path.insert(0, str(HERE.parent.parent))
    from PyQt5 import QtWidgets
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    for x, z, s in EGGS:
        assert dist_points(np.array(x), np.array(z)) > 100 and dist_route(np.array(x), np.array(z)) > CLEAR + 40 * s, (x, z)
    for x, z in PALMS:
        assert dist_points(np.array(x), np.array(z)) > 110 and dist_route(np.array(x), np.array(z)) > 60, (x, z)
    bushes, flowers = placements()
    decor = ([(x, z, 46 * s, 0.45) for x, z, s in EGGS] + [(x, z, 30, 0.35) for x, z in bushes]
             + [(x + 10, z + 10, 40, 0.30) for x, z in PALMS] + [(x, z, 14, 0.3) for x, z, _s in flowers]
             + [(PIPE[0], PIPE[1], 32, 0.4), (BOSHI[0], BOSHI[1] + 6, 75, 0.35), (WART[0], WART[1] + 6, 85, 0.35)])
    ground_texture(decor); cliff_texture(); sea_texture(); foam_texture(); egg_texture(); simple_textures()
    obj = Obj()
    island(obj)
    sea(obj)
    bridge(obj)
    for x, z, s in EGGS:
        egg(obj, x, z, s)
    for i, (x, z) in enumerate(PALMS):
        palm(obj, (x, ground_y(x, z), z), rng.uniform(150, 190), (rng.uniform(-30, 30), rng.uniform(-30, 30)), 10 + i)
    for i, (x, z) in enumerate(bushes):
        bush(obj, x, z, 100 + i)
    for x, z, s in flowers:
        flower(obj, x, z, s)
    pipe(obj, *PIPE)
    for x, z, rad, top in PILLARS:
        pillar(obj, x, z, rad, top)
    boshi_castle(obj)
    wart_castle(obj)
    obj.write(HERE / "yoshi_island.obj", "yoshi_island.mtl")
    tex = {"YIGround": "yi_ground.png", "YICliff": "yi_cliff.png", "YISea": "yi_sea.png", "YIFoam": "yi_foam.png",
           "YIEgg": "yi_egg.png", "YITrunk": "yi_trunk.png", "YIFrond": "yi_frond.png", "YIBush": "yi_bush.png",
           "YIStem": "yi_stem.png", "YIFlower": "yi_flower.png", "YIPipe": "yi_pipe.png", "YIWood": "yi_wood.png",
           "YIBoshiStone": "yi_boshi_stone.png", "YIBoshiRoof": "yi_boshi_roof.png", "YIBoshiDome": "yi_boshi_dome.png",
           "YIDoor": "yi_door.png", "YIWartStone": "yi_wart_stone.png", "YIWartEye": "yi_wart_eye.png",
           "YIMouth": "yi_mouth.png", "YIGold": "yi_gold.png", "YIRed": "yi_red.png", "YIMushCap": "yi_mushcap.png",
           "YIJar": "yi_jar.png"}
    with open(HERE / "yoshi_island.mtl", "w", encoding="utf-8") as f:
        for name in obj.faces:
            f.write("newmtl %s\nKd 0.80 0.80 0.80\nmap_Kd %s\n\n" % (name, tex[name]))
    for old in ("yi_grass.png", "yi_sand.png", "yi_soil.png", "yi_grasstile.png"):
        (HERE / old).unlink(missing_ok=True)
    counts = {m: len(f) for m, f in obj.faces.items()}
    assert max(counts.values()) <= 21845, counts              # 16-bit indices per part
    print("yoshi_island.obj: %d triangles in %d materials, %d bushes, %d flowers" % (
        sum(counts.values()), len(counts), len(bushes), len(flowers)))
    print(counts)


if __name__ == "__main__":
    main()

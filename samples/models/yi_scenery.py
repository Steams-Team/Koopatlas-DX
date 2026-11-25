"""
samples/models/yi_scenery.py - the scenery of the "Yoshi's Island" World 1 (make_yoshi_island_w1.py).

Pictures are painted here in the soft grain of the game's world textures; shapes are cut out of the games
(kdx.assetkit) or turned here (eggs, the volcano, Boshi's spikes, King Wart's crown, a stump's grass top).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


# ---- pictures ------------------------------------------------------------------------------------------------------
def read_png(path):
    from PyQt5 import QtGui
    img = QtGui.QImage(str(path)).convertToFormat(QtGui.QImage.Format_RGBA8888)
    b = img.constBits()
    b.setsize(img.byteCount())
    return np.frombuffer(bytes(b), np.uint8).reshape(img.height(), img.width(), 4).copy()


def write_png(path, rgba):
    from kdx import model3d
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    model3d.write_png(path, np.ascontiguousarray(np.clip(rgba, 0, 255).astype(np.uint8)))
    return str(path)


def rgb_hsv(rgb):
    r, g, b = [rgb[..., k] / 255.0 for k in range(3)]
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    d = mx - mn + 1e-9
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    return h, np.where(mx > 0, (mx - mn) / (mx + 1e-9), 0), mx


def hsv_rgb(h, s, v):
    c = v * s
    x = c * (1 - np.abs((h / 60) % 2 - 1))
    m = v - c
    k = (np.floor(h / 60).astype(int)) % 6
    z = 0 * c
    r = np.choose(k, [c, x, z, z, x, c])
    g = np.choose(k, [x, c, c, x, z, z])
    b = np.choose(k, [z, z, x, c, c, x])
    return np.stack([(r + m) * 255, (g + m) * 255, (b + m) * 255], -1)


def noise(h, w, cell, seed):
    rng = np.random.default_rng(seed)
    g = rng.random((h // cell + 2, w // cell + 2))
    y, x = np.mgrid[0:h, 0:w] / cell
    i, j = y.astype(int), x.astype(int)
    fy, fx = y - i, x - j
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    return (g[i, j] * (1 - fx) * (1 - fy) + g[i, j + 1] * fx * (1 - fy) + g[i + 1, j] * (1 - fx) * fy
            + g[i + 1, j + 1] * fx * fy)


def painted(h, w, color, seed, amount=14.0):
    """A flat colour with the soft hand-painted grain of the game's world textures (RGBA, float)."""
    n = 0.6 * noise(h, w, 24, seed) + 0.4 * noise(h, w, 7, seed + 1) - 0.5
    img = np.zeros((h, w, 4), np.float64)
    img[..., :3] = np.asarray(color, np.float64) + n[..., None] * amount
    img[..., 3] = 255
    return img


# ---- shapes turned here --------------------------------------------------------------------------------------------
def lathe(material, texture, profile, seg=24, mode="opaque"):
    """A surface of revolution about y: profile [(radius, y, v)...] from bottom to top, wound counter-clockwise seen
    from outside (the game draws front faces only)."""
    from kdx.objmodel import Part
    pos, nrm, uv = [], [], []
    prof = np.asarray(profile, np.float64)
    segs = []                                   # each band's outward (radial, up) direction
    for k in range(len(prof) - 1):
        dn = np.array([prof[k + 1][1] - prof[k][1], -(prof[k + 1][0] - prof[k][0])])
        segs.append(dn / max(np.linalg.norm(dn), 1e-9))
    smooth = []                                 # shared by the bands meeting at a ring: no visible facets
    for k in range(len(prof)):
        n = sum(segs[i] for i in (k - 1, k) if 0 <= i < len(segs))
        smooth.append(n / max(np.linalg.norm(n), 1e-9))
    for k in range(len(prof) - 1):
        (r0, y0, v0), (r1, y1, v1) = prof[k], prof[k + 1]
        n0, n1 = smooth[k], smooth[k + 1]
        for j in range(seg):
            a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
            c = [(r0, y0, v0, a0, j / seg, n0), (r0, y0, v0, a1, (j + 1) / seg, n0),
                 (r1, y1, v1, a1, (j + 1) / seg, n1), (r1, y1, v1, a0, j / seg, n1)]
            for q in (0, 1, 2, 0, 2, 3):
                r, y, v, a, u, dn = c[q]
                pos.append((r * math.sin(a), y, r * math.cos(a)))
                nrm.append((dn[0] * math.sin(a), dn[1], dn[0] * math.cos(a)))
                uv.append((u, v))
    p = Part(material, np.float32(pos), np.float32(nrm), np.float32(uv), texture, mode=mode)
    _outward(p)
    return p


def _outward(part):
    """Turn triangles whose winding disagrees with their normals (counter-clockwise outside, as the game draws)."""
    t = part.positions.reshape(-1, 3, 3)
    fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n = part.normals.reshape(-1, 3, 3).mean(1)
    flip = (fn * n).sum(1) < 0
    if flip.any():
        order = np.arange(len(part.positions)).reshape(-1, 3)
        order[flip] = order[flip][:, [0, 2, 1]]
        part.reorder(order.ravel())


EGG_COLOURS = {"green": (66, 180, 66), "blue": (72, 174, 234), "pink": (244, 122, 178), "yellow": (250, 198, 54),
               "red": (232, 72, 62)}


def _egg_r(th):
    return 0.5 * np.sin(th) * (1.0 + 0.10 * np.cos(th)) * 0.78


def egg_profile(height, rings=20):
    """A Yoshi egg's outline (bottom to top): widest a little below the middle, rounder at the top."""
    out = []
    for k in range(rings + 1):
        t = k / rings
        th = math.pi * t
        out.append((max(float(_egg_r(th)), 1e-4) * height, height * (0.5 - 0.5 * math.cos(th)), 1.0 - t))
    return out


def egg_texture(path, colour, spots, seed, size=256, door=False):
    """White shell with big round spots, round on the egg itself: each texel's place on the shell is measured."""
    h = w = size
    v, u = ((np.mgrid[0:h, 0:w] + 0.5) / np.array([h, w])[:, None, None])
    th = math.pi * (1 - v)
    r = _egg_r(th)
    P = np.stack([r * np.sin(2 * np.pi * u), 0.5 - 0.5 * np.cos(th), r * np.cos(2 * np.pi * u)], -1)
    img = painted(h, w, (250, 250, 246), seed, 5)
    col = np.asarray(EGG_COLOURS[colour], np.float64)
    tone = (0.93 + 0.07 * noise(h, w, 16, seed + 7))[..., None]
    for sx, sy, srad in spots:                             # (turns round, height 0-1, radius in egg heights)
        ths = math.pi * sy
        rs = float(_egg_r(ths))
        c = np.array([rs * math.sin(2 * math.pi * sx), 0.5 - 0.5 * math.cos(ths), rs * math.cos(2 * math.pi * sx)])
        d = np.linalg.norm(P - c, axis=-1)
        a = np.clip((srad - d) / 0.010, 0, 1)[..., None]
        img[..., :3] = img[..., :3] * (1 - a) + col * tone * a
    if door:                                               # Yoshi's house: a round-topped door and a window, south
        du = ((u - 0.0 + 0.5) % 1.0) - 0.5
        arch = (np.abs(du) < 0.07) & (v > 0.66) | (((du / 0.07) ** 2 + ((v - 0.66) / 0.08) ** 2) < 1)
        frame = (np.abs(du) < 0.085) & (v > 0.645) | (((du / 0.085) ** 2 + ((v - 0.66) / 0.095) ** 2) < 1)
        img[frame & ~arch, :3] = (120, 76, 40)
        img[arch, :3] = np.array([176, 116, 62]) + 10 * noise(h, w, 4, seed + 9)[arch][:, None]
        win = (((u - 0.13) / 0.035) ** 2 + ((v - 0.52) / 0.055) ** 2) < 1
        winf = (((u - 0.13) / 0.045) ** 2 + ((v - 0.52) / 0.07) ** 2) < 1
        img[winf & ~win, :3] = (120, 76, 40)
        img[win, :3] = (150, 210, 240)
    return write_png(path, img)


def egg_model(path, tex_dir, colour, seed, height=60.0, door=False):
    from kdx import assetkit
    from kdx.objmodel import ObjModel
    rng = np.random.default_rng(seed)
    n = int(rng.integers(3, 5))
    spots = [((k / n + rng.uniform(-0.08, 0.08)) % 1.0, rng.uniform(0.32, 0.74), rng.uniform(0.12, 0.16))
             for k in range(n)]
    if door:
        spots = [(0.2, 0.32, 0.13), (0.8, 0.36, 0.13), (0.3, 0.72, 0.12), (0.72, 0.7, 0.12), (0.5, 0.5, 0.16)]
    tex = egg_texture(Path(tex_dir) / ("yi_egg_%s_%d.png" % (colour, seed)), colour, spots, seed, door=door)
    return assetkit.save(ObjModel([lathe("mtYoshiEgg", tex, egg_profile(height), seg=28)]), path)


def volcano_model(path, tex_dir):
    """Yoshi's Island's little volcano: an orange cone with brown spots and a dark crater."""
    from kdx import assetkit
    from kdx.objmodel import ObjModel
    img = painted(256, 256, (232, 150, 72), 31, 16)
    rng = np.random.default_rng(32)
    yy, xx = np.mgrid[0:256, 0:256]
    for _ in range(30):
        cx, cy, rr = rng.uniform(0, 256), rng.uniform(40, 250), rng.uniform(7, 15)
        d = np.hypot(((xx - cx + 128) % 256) - 128, (yy - cy) * 1.4)
        a = np.clip((rr - d) / 2.0, 0, 1)[..., None]
        img[..., :3] = img[..., :3] * (1 - a) + np.array([178, 94, 46]) * a
    img[:30, :, :3] = img[:30, :, :3] * 0.4 + np.array([66, 34, 22]) * 0.6
    tex = write_png(Path(tex_dir) / "yi_volcano.png", img)
    prof = [(260, -10, 1.0), (240, 30, 0.88), (196, 95, 0.64), (134, 162, 0.38), (88, 204, 0.18), (70, 214, 0.12),
            (56, 208, 0.07), (30, 176, 0.0)]
    return assetkit.save(ObjModel([lathe("mtVolcano", tex, prof, seg=28)]), path)


def spikes_model(path, tex_dir, radius, count, height, colour, name, lean=0.0):
    """A ring of cones: Boshi's spiked collar round his tower's top, the points of King Wart's crown."""
    from kdx import assetkit
    from kdx.objmodel import ObjModel, Part
    tex = write_png(Path(tex_dir) / ("yi_%s.png" % name), painted(64, 64, colour, 41, 10))
    pos, nrm, uv = [], [], []
    base_r = 0.62 * math.pi * radius / count
    for k in range(count):
        a = 2 * math.pi * k / count
        out_dir = np.array([math.sin(a), 0.0, math.cos(a)])
        c = radius * out_dir
        tip = c + out_dir * lean + np.array([0.0, height, 0.0])
        for j in range(10):
            b0, b1 = 2 * math.pi * j / 10, 2 * math.pi * (j + 1) / 10
            p0 = c + base_r * np.array([math.sin(b0), 0, math.cos(b0)])
            p1 = c + base_r * np.array([math.sin(b1), 0, math.cos(b1)])
            mid = (p0 + p1) / 2 - c
            n = mid / max(np.linalg.norm(mid), 1e-9) + np.array([0, base_r / height, 0])
            for q, t in zip((p0, p1, tip), ((0, 1), (1, 1), (0.5, 0))):
                pos.append(q); nrm.append(n / np.linalg.norm(n)); uv.append(t)
    part = Part("mt" + name, np.float32(pos), np.float32(nrm), np.float32(uv), tex)
    _outward(part)
    return assetkit.save(ObjModel([part]), path)


def band_model(path, tex_dir, radius, height, colour, name, seg=32):
    """A short open cylinder (a crown's band)."""
    from kdx import assetkit
    from kdx.objmodel import ObjModel
    tex = write_png(Path(tex_dir) / ("yi_%s.png" % name), painted(64, 64, colour, 43, 10))
    return assetkit.save(ObjModel([lathe("mt" + name, tex, [(radius, 0, 1), (radius, height, 0)], seg=seg)]), path)


def grass_cap_model(path, tex_dir, grass_png, radius, lobes=22):
    """A round grass top with a scalloped edge, to make a stump into a plateau (Yoshi's Island's stump hill)."""
    from kdx import assetkit
    from kdx.objmodel import ObjModel, Part
    g = read_png(grass_png).astype(np.float64)[:256, :256]
    h, w = g.shape[:2]
    yy, xx = (np.mgrid[0:h, 0:w] + 0.5) / np.array([h, w])[:, None, None] * 2 - 1
    ang, rad = np.arctan2(yy, xx), np.hypot(xx, yy)
    edge = 0.90 + 0.06 * np.abs(np.cos(ang * lobes / 2))
    img = g.copy()
    img[..., 3] = np.where(rad < edge, 255, 0)
    rim = np.clip((rad - (edge - 0.07)) / 0.07, 0, 1)[..., None]
    img[..., :3] = img[..., :3] * (1 - 0.22 * rim)
    tex = write_png(Path(tex_dir) / "yi_stump_grass.png", img)
    pos, uv = [], []
    seg = 48
    for k in range(seg):
        a0, a1 = 2 * math.pi * k / seg, 2 * math.pi * (k + 1) / seg
        for a, r in ((0.0, 0.0), (a0, 1.0), (a1, 1.0)):
            pos.append((r * radius * math.cos(a), 0.0, -r * radius * math.sin(a)))
            uv.append((0.5 + 0.5 * r * math.cos(a), 0.5 + 0.5 * r * math.sin(a)))
    part = Part("mtStumpGrass", np.float32(pos), np.tile(np.float32([0, 1, 0]), (len(pos), 1)), np.float32(uv), tex,
                mode="mask")
    _outward(part)
    return assetkit.save(ObjModel([part]), path)


# ---- the games' own shapes, recoloured -----------------------------------------------------------------------------
def recoloured(model, tex_dir, tag, fn):
    """The model with each texture passed through fn(rgba) -> rgba (saved as <tag>__<name>.png)."""
    done = {}
    for p in model.parts:
        if p.texture and p.texture not in done:
            done[p.texture] = write_png(Path(tex_dir) / ("%s__%s" % (tag, Path(p.texture).name)), fn(read_png(p.texture)))
        if p.texture:
            p.texture = done[p.texture]
    return model


def hue_to(rgba, hue_from, hue_new, sat=1.0, val=1.0, width=40.0):
    """Colours within `width` degrees of hue_from turned to hue_new (saturation and brightness scaled)."""
    h, s, v = rgb_hsv(rgba[..., :3].astype(np.float64))
    d = np.abs(((h - hue_from) + 180) % 360 - 180)
    m = (d < width) & (s > 0.08)
    out = rgba.astype(np.float64).copy()
    rec = hsv_rgb(np.full_like(h, hue_new), np.clip(s * sat, 0, 1), np.clip(v * val, 0, 1))
    out[..., :3] = np.where(m[..., None], rec, out[..., :3])
    return out


def boshi_stone(rgba):
    """The tower's pale stone in Boshi's blue."""
    h, s, v = rgb_hsv(rgba[..., :3].astype(np.float64))
    out = rgba.astype(np.float64).copy()
    m = (s < 0.5) & (v > 0.2)
    rec = hsv_rgb(np.full_like(h, 214.0), np.clip(s * 0.6 + 0.32, 0, 0.62), np.clip(v * 0.97, 0, 1))
    out[..., :3] = np.where(m[..., None], rec, out[..., :3])
    return out


def wart_castle(rgba):
    """The castle's purple roofs and gate in King Wart's frog green, its grey stone a little mossy."""
    out = hue_to(rgba, 265.0, 108.0, sat=1.05, val=1.08, width=48.0)
    h, s, v = rgb_hsv(out[..., :3])
    grey = (s < 0.14)[..., None]
    out[..., :3] = np.where(grey, out[..., :3] * np.array([0.93, 1.0, 0.88]), out[..., :3])
    return out


def snow_cap(rgba):
    """A hill's top as fresh snow: white with a cool shade, its painted grain kept."""
    g = rgba[..., :3].astype(np.float64).mean(-1, keepdims=True)
    out = rgba.astype(np.float64).copy()
    out[..., :3] = 238 + (g - g.mean()) * 0.22 + np.array([0, 2, 8])
    return out


def mountain_body(path, colour, seed, w=128, h=256, snow_rows=56):
    """A Yoshi's Island mountain's side: pale, softly streaked, the snow running down from its cap in drips."""
    img = painted(h, w, colour, seed, 12)
    xs = np.arange(w)
    streak = 6 * np.sin(xs / w * 2 * np.pi * 5 + seed) + 4 * np.sin(xs / w * 2 * np.pi * 11)
    img[..., :3] += streak[None, :, None] * 0.6
    drip = snow_rows + 14 * np.abs(np.sin(xs / w * np.pi * 6 + seed)) + 8 * noise(2, w, 6, seed)[0]
    yy = np.arange(h)[:, None]
    snow = np.clip((drip[None, :] - yy) / 2.0, 0, 1)[..., None]
    white = painted(h, w, (240, 243, 250), seed + 3, 6)[..., :3]
    img[..., :3] = img[..., :3] * (1 - snow) + white * snow
    return write_png(path, img)


def yoshi_mountain(hill, tex_dir, colour, seed, name):
    """A Wii hill as a snow-capped Yoshi's Island mountain: its cap snow, its sides pale with the snow running down."""
    body = next(p for p in hill.parts if not p.material.endswith("Top"))
    top = next(p for p in hill.parts if p.material.endswith("Top"))
    cap_y = float(top.positions[:, 1].min())
    lo = float(body.positions[:, 1].min())
    vv = np.clip((cap_y + 0.25 * (cap_y - lo) - body.positions[:, 1]) / max(1.25 * (cap_y - lo), 1e-6), 0, 1)
    body.uvs = np.c_[body.uvs[:, 0], 0.02 + 0.96 * vv].astype(np.float32)
    body.wrap = ("repeat", "clamp")
    body.texture = mountain_body(Path(tex_dir) / ("yi_%s_body.png" % name), colour, seed)
    body.material = "mtYI%sBody" % name
    top.texture = write_png(Path(tex_dir) / ("yi_%s_top.png" % name), snow_cap(read_png(top.texture)))
    top.material = "mtYI%sTop" % name
    return hill

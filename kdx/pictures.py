"""
kdx/pictures.py - Koopatlas-style 2D worlds from your own art (World Maker, phase 5).

A map's ground can be a picture you drew or painted (Project.picture: PNG / JPG / WebP / BMP, stretched over the map),
with or without tiles painted on top; paths and points go on it as usual. Separate images ("stickers": trees, ships,
waterfalls, clouds...) sit on the map and can move: a GIF plays its own frames, a PNG strip of N frames side by side
plays them, and any image can bob, sway, spin, drift or pulse. In the game a 2D world is a flat plane seen from above,
so the picture and the stickers become its texture.

    frames, delays = load_frames(path, strip=1)       # [QImage], [ms]
    img, dx, dy, angle, scale = pose(sticker, frames, delays, t_ms)   # what to draw at time t
    ANIMS                                             # kind -> label
"""
from __future__ import annotations

import math
from pathlib import Path

ANIMS = {"none": "Still", "frames": "Play its frames (GIF / strip)", "bob": "Bob up and down",
         "sway": "Sway (trees, flags)", "spin": "Spin", "drift": "Drift sideways (clouds, ships)",
         "pulse": "Pulse (glow, stars)", "keys": "Keyframes (Koopatlas)"}
IMAGE_FILTER = "Images and GIFs (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"

_cache = {}


def load_frames(path, strip=1):
    """The frames of an image: every frame of an animated GIF (with its delays), or a PNG strip cut into `strip`
    frames side by side, or the image itself."""
    from PyQt5 import QtGui
    key = (str(path), int(strip))
    try:
        key += (Path(path).stat().st_mtime,)
    except OSError:
        pass
    if key in _cache:
        return _cache[key]
    frames, delays = [], []
    reader = QtGui.QImageReader(str(path))
    if reader.supportsAnimation() and reader.imageCount() > 1:
        for _ in range(reader.imageCount()):
            img = reader.read()
            if img.isNull():
                break
            frames.append(img.convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied))
            delays.append(max(20, reader.nextImageDelay() or 100))
    else:
        img = QtGui.QImage(str(path))
        if not img.isNull():
            img = img.convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied)
            n = max(1, int(strip))
            if n > 1 and img.width() >= n:
                w = img.width() // n
                frames = [img.copy(i * w, 0, w, img.height()) for i in range(n)]
                delays = [100] * n
            else:
                frames, delays = [img], [100]
    _cache[key] = (frames, delays)
    return frames, delays


def track_value(tr, frame, period=None):
    """A Koopatlas keyframe track [loop, curve, frames, type, start, end, delay, delay offset] at a frame (60 per
    second). period: the cycle stretched to this many frames (so every track fits the export's one loop)."""
    loop, curve, n, _type, a, b, delay, offset = (list(tr) + [0, 0])[:8]
    n, delay = max(1.0, float(n)), float(delay or 0)
    cyc = (n + delay) * (2 if loop == "Reversible Loop" else 1)
    if period and loop != "Contiguous":
        k = period / cyc
        n, delay, cyc = n * k, delay * k, period
    t = frame - float(offset or 0)
    if t <= 0:
        p = 0.0
    elif loop == "Contiguous":
        p = min(1.0, t / n)
    elif loop == "Loop":
        p = min(1.0, (t % cyc) / n)
    else:                                                       # Reversible Loop: there and back
        u, half = t % cyc, cyc / 2.0
        p = min(1.0, u / n) if u < half else 1.0 - min(1.0, (u - half) / n)
    if curve == "Sinusoidial":                                  # Qt's sine curve: 0 -> 1 -> 0 over the run
        p = (math.sin(2 * math.pi * p - math.pi / 2) + 1) / 2
    elif curve == "Cosinoidial":
        p = (math.cos(2 * math.pi * p - math.pi / 2) + 1) / 2
    return float(a) + (float(b) - float(a)) * p


def track_cycle(tr):
    """Frames of one cycle of a looping track (None for a play-once one)."""
    loop, _c, n, _t, _a, _b, delay = (list(tr) + [0, 0])[:7]
    if loop == "Contiguous":
        return None
    return (max(1.0, float(n)) + float(delay or 0)) * (2 if loop == "Reversible Loop" else 1)


KEY_PX = 24.0                       # Koopatlas positions are in its 24 px tiles


def keys_pose(sticker, frame, periods=None):
    """(dx, dy in tiles, angle, (sx, sy), opacity) of a keyframed sticker at a frame."""
    v = {"X Position": 0.0, "Y Position": 0.0, "Angle": 0.0, "X Scale": 100.0, "Y Scale": 100.0, "Opacity": 100.0}
    for i, tr in enumerate(sticker.get("tracks", [])):
        if tr[3] in v:
            v[tr[3]] = track_value(tr, frame, periods[i] if periods else None)
    return (v["X Position"] / KEY_PX, v["Y Position"] / KEY_PX, v["Angle"], (v["X Scale"] / 100.0, v["Y Scale"] / 100.0),
            max(0.0, min(1.0, v["Opacity"] / 100.0)))


def pose(sticker, frames, delays, t_ms):
    """(frame image, dx, dy, angle in degrees, scale) of a sticker at time t_ms. dx/dy are in tiles."""
    a = sticker.get("anim", "none")
    speed = float(sticker.get("speed", 1.0)) or 1.0
    amount = float(sticker.get("amount", 1.0))
    t = t_ms * speed / 1000.0
    img = frames[0] if frames else None
    if a == "frames" and len(frames) > 1:
        total = sum(delays)
        k, acc = 0, (t * 1000.0) % total
        while acc >= delays[k]:
            acc -= delays[k]; k += 1
        img = frames[k]
    dx = dy = ang = 0.0
    sc = 1.0
    if a == "keys":
        dx, dy, ang, sc, _op = keys_pose(sticker, t_ms * speed * FPS / 1000.0)
    elif a == "bob":
        dy = -0.25 * amount * math.sin(2 * math.pi * t / 2.0)
    elif a == "sway":
        ang = 6.0 * amount * math.sin(2 * math.pi * t / 2.5)
    elif a == "spin":
        ang = (360.0 * t / 4.0) % 360.0
    elif a == "drift":
        dx = 1.5 * amount * math.sin(2 * math.pi * t / 12.0)
    elif a == "pulse":
        sc = 1.0 + 0.08 * amount * math.sin(2 * math.pi * t / 1.5)
    return img, dx, dy, ang, sc


# ---- in the game ---------------------------------------------------------------------------------------------------
# Every world builds the decoration model CS_W<n>_Synchro and loops the skeletal animation of that name (vanilla World
# 1 sways its palm leaves with it; nsmbud-reverse F0335). A moving image becomes quads in that model on bones of its
# own: bob, sway, spin, drift and pulse are bone keys matching pose(); GIF / strip frames are one quad per frame, each
# shown (scale 1) only during its frame (stepped scale keys). Still images stay baked into the map picture.
PERIODS = {"bob": 2.0, "sway": 2.5, "spin": 4.0, "drift": 12.0, "pulse": 1.5}   # seconds at speed 1, as in pose()
FPS = 60
MAX_CELL, MAX_ATLAS = 256, 512      # px: a frame, and a GIF's frame sheet (RGBA8: 1 MB at most per image)
LIFT = 1.0                          # world units above the ground (no z-fighting with the map picture)
PX_PER_TILE = 24                    # texture pixels per map tile for a moving image (sharper than the map picture)
MAX_FRAMES = 24                     # frames of one GIF in the game (each is a quad and three bones)


def moves(sticker):
    """Whether a sticker moves (in the game it then becomes its own animated quads)."""
    a = sticker.get("anim", "none")
    if a == "frames":
        return len(load_frames(sticker["path"], sticker.get("strip", 1))[0]) > 1
    if a == "keys":
        return any(track_cycle(t) for t in sticker.get("tracks", []))
    return a in PERIODS


def period_frames(sticker):
    """One cycle of a moving sticker in game frames (60 per second)."""
    speed = abs(float(sticker.get("speed", 1.0))) or 1.0
    a = sticker.get("anim", "none")
    if a == "frames":
        sec = sum(load_frames(sticker["path"], sticker.get("strip", 1))[1]) / 1000.0
    elif a == "keys":
        return max([c for c in map(track_cycle, sticker.get("tracks", [])) if c] or [FPS]) / speed
    else:
        sec = PERIODS[a]
    return FPS * sec / speed


def loop_length(periods, lo=120, hi=7200, tolerance=0.05):
    """The loop (in frames) of the one animation every moving sticker shares: the shortest that holds a whole number
    of each sticker's cycles, each within `tolerance` of its own speed (else the closest found)."""
    if not periods:
        return 0
    best = None
    for L in range(lo, hi + 1, 30):
        err = max(abs(L / max(1, round(L / p)) - p) / p for p in periods)
        if err <= tolerance:
            return L
        if best is None or err < best[0]:
            best = (err, L)
    return best[1]


def _sine(channel, amp, L, n):
    """A sine of amplitude amp, n whole cycles over L frames: keys every quarter cycle with their exact slopes."""
    p = L / n
    w = 2 * math.pi / p
    frames = [k * p / 4 for k in range(4 * n + 1)]
    values = [amp * math.sin(2 * math.pi * k / 4) for k in range(4 * n + 1)]
    slopes = [amp * w * math.cos(2 * math.pi * k / 4) for k in range(4 * n + 1)]
    return {"channel": channel, "frames": frames, "values": values, "slopes": slopes, "mode": "hermite"}


def motion_curves(sticker, L, tile_size, size=None):
    """The bone keys of a bob / sway / spin / drift / pulse sticker over an L-frame loop (world units, degrees).
    The map lies in x/z seen from above: the picture's down is +z and a turn on screen is a turn about y."""
    a = sticker.get("anim", "none")
    amount = float(sticker.get("amount", 1.0))
    n = max(1, round(L / period_frames(sticker)))
    if a == "bob":
        return [_sine("tz", -0.25 * amount * tile_size, L, n)]
    if a == "drift":
        return [_sine("tx", 1.5 * amount * tile_size, L, n)]
    if a == "sway":
        return [_sine("ry", -6.0 * amount, L, n)]
    if a == "pulse":
        out = [_sine(c, 0.08 * amount, L, n) for c in ("sx", "sz")]
        for c in out:
            c["values"] = [1.0 + v for v in c["values"]]
        return out
    if a == "keys":
        # As Koopatlas draws a doodad (Newer, Kamek/src/koopatlas/map.cpp renderDoodadLayer): Y Scale keeps the
        # bottom edge where it is (effectiveY += height - effectiveHeight), X Scale the left edge (x unchanged), and
        # the turn is about the scaled doodad's centre. So a tree swaying with Y Scale 100-105 stays rooted: only its
        # top moves. The bones scale about the centre, so the edges are held with matching moves.
        w, h = size or (float(sticker.get("w", 3.0)), float(sticker.get("h") or sticker.get("w", 3.0)))
        ts = tile_size / KEY_PX
        speed = abs(float(sticker.get("speed", 1.0))) or 1.0
        tracks = [(tr, L / max(1, round(L / (track_cycle(tr) / speed)))) for tr in sticker.get("tracks", [])
                  if tr[3] in ("X Position", "Y Position", "Angle", "X Scale", "Y Scale") and track_cycle(tr)]
        if not tracks:
            return []
        step = max(2.0, min(per for _t, per in tracks) / 24.0)
        frames = [i * step for i in range(int(L / step))] + [float(L)]
        kinds = {tr[3] for tr, _p in tracks}
        rows = []
        for f in frames:
            v = {"X Position": 0.0, "Y Position": 0.0, "Angle": 0.0, "X Scale": 100.0, "Y Scale": 100.0}
            for tr, per in tracks:
                v[tr[3]] = track_value(tr, f % per if f < L else 0.0, per)
            sx, sz = v["X Scale"] / 100.0, v["Y Scale"] / 100.0
            rows.append({"tx": v["X Position"] * ts + (sx - 1.0) * w / 2.0 * tile_size,
                         "tz": v["Y Position"] * ts + (1.0 - sz) * h / 2.0 * tile_size,
                         "ry": -v["Angle"], "sx": sx, "sz": sz})
        used = set()
        if kinds & {"X Position", "X Scale"}:
            used.add("tx")
        if kinds & {"Y Position", "Y Scale"}:
            used.add("tz")
        if "Angle" in kinds:
            used.add("ry")
        if "X Scale" in kinds:
            used.add("sx")
        if "Y Scale" in kinds:
            used.add("sz")
        return [{"channel": ch, "frames": frames, "mode": "linear", "values": [r[ch] for r in rows]}
                for ch in ("tx", "tz", "ry", "sx", "sz") if ch in used]
    if a == "spin":
        return [{"channel": "ry", "frames": [k * L / n for k in range(n + 1)], "values": [-360.0 * k for k in range(n + 1)],
                 "mode": "linear"}]
    return []


def frame_curves(k, delays, L):
    """Stepped scale keys showing frame k of a GIF / strip (delays in ms) only during its time, over an L-frame loop."""
    total = float(sum(delays))
    n = max(1, round(L / (FPS * total / 1000.0))) if total else 1
    p = L / n
    s, e = p * sum(delays[:k]) / total, p * sum(delays[:k + 1]) / total
    v0 = 1.0 if k == 0 else 0.0
    ev = [(0.0, v0)]
    for c in range(n):
        if c * p + s > 0:
            ev.append((c * p + s, 1.0))
        if c * p + e < L - 1e-6:
            ev.append((c * p + e, 0.0))
    ev.append((float(L), v0))
    frames = [f for f, _ in ev]
    values = [v for _, v in ev]
    return [{"channel": ch, "frames": frames, "values": values, "mode": "step"} for ch in ("sx", "sy", "sz")]


def _cell(img, scale):
    from PyQt5 import QtCore
    return img.scaled(max(1, int(img.width() * scale)), max(1, int(img.height() * scale)),
                      QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)


def sticker_sheet(frames, path, cell=None):
    """Save the frames as one sheet (a grid with a 2 px clear margin round each cell, so filtering never bleeds into a
    neighbour); returns [(u0, v0, u1, v1)] per frame."""
    from PyQt5 import QtCore, QtGui
    fw, fh = frames[0].width(), frames[0].height()
    cols = max(1, math.ceil(math.sqrt(len(frames))))
    rows = math.ceil(len(frames) / cols)
    pad = 2
    scale = min(1.0, (cell or MAX_CELL) / max(fw, fh), (MAX_ATLAS - 2 * pad * cols) / float(cols * fw),
                (MAX_ATLAS - 2 * pad * rows) / float(rows * fh))
    cw, ch = max(1, int(fw * scale)), max(1, int(fh * scale))
    W, H = cols * (cw + 2 * pad), rows * (ch + 2 * pad)
    sheet = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32)
    sheet.fill(0)
    painter = QtGui.QPainter(sheet)
    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    uvs = []
    for i, img in enumerate(frames):
        x, y = (i % cols) * (cw + 2 * pad) + pad, (i // cols) * (ch + 2 * pad) + pad
        painter.drawImage(QtCore.QRectF(x, y, cw, ch), img)
        uvs.append((x / W, y / H, (x + cw) / W, (y + ch) / H))
    painter.end()
    sheet.save(str(path))
    return uvs


LAYER_STEP = 0.5                    # world units between overlapping images (24-bit depth resolves ~0.03 at map distance)


def quad_layers(project):
    """{id(sticker): layer} for the stickers that become quads of their own in game (moving ones and path art). The
    editor draws stickers in list order, later on top; in game every quad is depth-tested, so two overlapping quads at
    the same height flicker (z-fighting: the tree canopies of the Newer port, 2026-11-23). Each quad is lifted one
    LAYER_STEP above the highest earlier quad it overlaps."""
    quads = [s for s in project.stickers if moves(s) or s.get("reveal")]
    boxes, layer = [], {}
    for s in quads:
        b = sticker_box(s)
        k = 0
        for t, c in boxes:
            if b[0] < c[2] and c[0] < b[2] and b[1] < c[3] and c[1] < b[3]:
                k = max(k, layer[id(t)] + 1)
        layer[id(s)] = k
        boxes.append((s, b))
    return layer


def sticker_parts(project, folder):
    """The moving stickers of a project as quads for the game: (objmodel Parts, items, loop length in frames) where
    every item is {"object", "pivot", "curves"} (one per bone; a GIF has one per frame)."""
    import numpy as np
    from .objmodel import Part
    from .islands import _quad
    moving = [s for s in project.stickers if moves(s)]
    layers = quad_layers(project)
    L = loop_length([period_frames(s) for s in moving])
    parts, items = [], []
    ts = project.tile_size
    # one sheet per picture (many Koopatlas doodads share one), as sharp as its largest use on the map needs
    need = {}
    for s in moving:
        k = (s["path"], int(s.get("strip", 1)), s.get("anim") == "frames")
        need[k] = max(need.get(k, 0), max(float(s.get("w", 3.0)), float(s.get("h") or 0)) * PX_PER_TILE)
    sheets = {}
    for si, s in enumerate(moving):
        frames, delays = load_frames(s["path"], s.get("strip", 1))
        if not frames:
            continue
        play = s.get("anim") == "frames"
        key = (s["path"], int(s.get("strip", 1)), play)
        sheet = Path(folder) / ("sticker%02d.png" % len(sheets) if key not in sheets else sheets[key][0])
        if play and len(frames) > MAX_FRAMES:                 # long GIFs: evenly picked frames, same total time
            pick = [round(i * len(frames) / MAX_FRAMES) for i in range(MAX_FRAMES)]
            delays = [sum(delays[a:b]) for a, b in zip(pick, pick[1:] + [len(frames)])]
            frames = [frames[i] for i in pick]
        if key not in sheets:
            cell = int(min(MAX_CELL, max(32, need[key])))
            sheets[key] = (sheet, sticker_sheet(frames if play else frames[:1], sheet, cell))
        sheet, uvs = sheets[key]
        w = float(s.get("w", 3.0))
        h = float(s["h"]) if s.get("h") else w * frames[0].height() / max(1, frames[0].width())
        x0, x1, y0, y1 = s["x"] - w / 2, s["x"] + w / 2, s["y"] - h / 2, s["y"] + h / 2
        top = max(project.height_at(min(max(x, 0.0), project.width - 1e-3), min(max(y, 0.0), project.height - 1e-3))
                  for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1), (s["x"], s["y"]))) + LIFT \
            + layers.get(id(s), 0) * LAYER_STEP
        pivot = [s["x"] * ts, top, s["y"] * ts]
        ca, sa = math.cos(math.radians(s.get("angle", 0.0))), math.sin(math.radians(s.get("angle", 0.0)))
        corners = [((s["x"] + (u - s["x"]) * ca - (v - s["y"]) * sa) * ts, top,
                    (s["y"] + (u - s["x"]) * sa + (v - s["y"]) * ca) * ts)
                   for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        for k, (u0, v0, u1, v1) in enumerate(uvs):
            P, N, U = [], [], []
            _quad(P, N, U, *corners, (0.0, 1.0, 0.0), [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
            obj = "kdxs%02df%02d" % (si, k)
            f = lambda a: np.asarray(a, np.float32)
            part = Part(obj + "/img", f(P), f(N), f(U), str(sheet), (1.0, 1.0, 1.0), "mask", False)
            part.wrap = ("clamp", "clamp")
            parts.append(part)
            curves = frame_curves(k, delays, L) if play else motion_curves(s, L, ts, (w, h))
            items.append({"object": obj, "pivot": pivot, "curves": curves})
    return parts, items, L


# ---- path art: images that appear when a path opens --------------------------------------------------------------------
# A still image with "reveal": "<path>" (a path some point's exit opens, R<stop><stop>) is drawn into a quad whose
# material is that path's route material mt<path>a with a fade-only reveal mask: MapModel::createRouteAnimation_ gives it
# the MaskRouteAnimation, so it stays hidden until the path opens and then fades in (kdx.nsmbw FADE_MASK; F0265). This
# is Koopatlas's associate layers (art that belongs to a path) the Deluxe way.
REVEAL_FADE = ((0.5, 1.0), -3.0)
REVEAL_PX = 24                       # texture pixels per tile for path art
# Sweeping reveal: the mask texture slides along the mask v (texmtx0 translate from -3 to -0.8, F0304), so a corner
# with a higher v shows first. Mask v 3 where the path starts and 1 where it ends (NSMBW's own roads run v 1 -> 3 the
# other way round of the same slide) makes the art appear along the path towards its destination, which is the point
# MaskRouteAnimation reads from the material name. The quad is split into a grid so a bending path sweeps along it.
SWEEP_V = (3.0, 1.0)
SWEEP_GRID = 12
SWEEP_WIDTH = 7.0                    # tiles: the editor preview's band along the path


def path_line(project, pid):
    """[(x, y)] in tiles along a path's walk (R<from><to>), from its first point to its last; [] if it is not one."""
    from . import rules
    nodes = project.nodes
    for p, walk in rules.map_paths(project.point_rows(), project.route_rows()):
        if p == pid:
            pts = [q for q in [pid[1:5]] + list(walk) + [pid[5:9]] if q in nodes]
            out = []
            for q in pts:
                xy = (float(nodes[q]["x"]), float(nodes[q]["y"]))
                if not out or out[-1] != xy:
                    out.append(xy)
            return out
    return []


def line_until(line, a):
    """The start of a polyline up to fraction a (0-1) of its length."""
    lens = [math.hypot(b[0] - p[0], b[1] - p[1]) for p, b in zip(line, line[1:])]
    total = sum(lens)
    if total <= 0 or a >= 1.0:
        return list(line)
    want, out = total * max(0.0, a), [line[0]]
    for (p, b), L in zip(zip(line, line[1:]), lens):
        if want <= L:
            t = want / L if L else 1.0
            out.append((p[0] + (b[0] - p[0]) * t, p[1] + (b[1] - p[1]) * t))
            return out
        want -= L
        out.append(b)
    return out


def line_param(line, x, y):
    """Where (x, y) lies along a polyline: 0 at its start, 1 at its end (the nearest point's share of the length)."""
    lens = [math.hypot(b[0] - p[0], b[1] - p[1]) for p, b in zip(line, line[1:])]
    total = sum(lens)
    if total <= 0:
        return 0.0
    best, at, done = None, 0.0, 0.0
    for (p, b), L in zip(zip(line, line[1:]), lens):
        vx, vy = b[0] - p[0], b[1] - p[1]
        t = max(0.0, min(1.0, ((x - p[0]) * vx + (y - p[1]) * vy) / (L * L))) if L else 0.0
        d = math.hypot(p[0] + vx * t - x, p[1] + vy * t - y)
        if best is None or d < best:
            best, at = d, done + t * L
        done += L
    return at / total


def reveals(project):
    """{path: [still images with that reveal]}."""
    out = {}
    for s in project.stickers:
        if s.get("reveal") and not moves(s):
            out.setdefault(s["reveal"], []).append(s)
    return out


def sticker_box(s):
    """(x0, y0, x1, y1) in tiles that a still sticker covers (its turn included)."""
    frames, _d = load_frames(s["path"], s.get("strip", 1))
    w = float(s.get("w", 3.0))
    h = float(s["h"]) if s.get("h") else (w * frames[0].height() / max(1, frames[0].width()) if frames else w)
    if s.get("angle"):
        r = math.hypot(w, h) / 2
        return s["x"] - r, s["y"] - r, s["x"] + r, s["y"] + r
    return s["x"] - w / 2, s["y"] - h / 2, s["x"] + w / 2, s["y"] + h / 2


def reveal_parts(project, folder, opened=None):
    """The path art of a project as route parts (one quad per path), for the paths in opened (all when None)."""
    import numpy as np
    from PyQt5 import QtCore, QtGui
    from .objmodel import Part
    from .islands import _quad
    parts = []
    ts = project.tile_size
    layers = quad_layers(project)
    for pid, ss in sorted(reveals(project).items()):
        if (opened is not None and pid not in opened) or len(pid) != 9:
            continue
        boxes = [sticker_box(s) for s in ss]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        px = min(float(REVEAL_PX), 2048.0 / max(1e-6, x1 - x0), 2048.0 / max(1e-6, y1 - y0))
        img = QtGui.QImage(max(1, int((x1 - x0) * px)), max(1, int((y1 - y0) * px)), QtGui.QImage.Format_ARGB32)
        img.fill(0)
        pt = QtGui.QPainter(img)
        pt.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        pt.translate(-x0 * px, -y0 * px)
        for s in ss:
            draw(pt, s, px, 0.0)
        pt.end()
        f = Path(folder) / ("reveal_%s.png" % pid)
        img.save(str(f))
        cx = lambda v: min(max(v, 0.0), project.width - 1e-3)
        cy = lambda v: min(max(v, 0.0), project.height - 1e-3)
        top = max(project.height_at(cx(x), cy(y)) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1),
                                                               ((x0 + x1) / 2, (y0 + y1) / 2))) + LIFT * 0.5 \
            + max(layers.get(id(s), 0) for s in ss) * LAYER_STEP
        P, N, U = [], [], []
        line = path_line(project, pid) if getattr(project, "reveal_style", "fade") == "sweep" else []
        g = SWEEP_GRID if len(line) >= 2 else 1
        for j in range(g):
            for i in range(g):
                u0, u1, v0, v1 = i / g, (i + 1) / g, j / g, (j + 1) / g
                X = lambda u: (x0 + (x1 - x0) * u) * ts
                Z = lambda v: (y0 + (y1 - y0) * v) * ts
                _quad(P, N, U, (X(u0), top, Z(v0)), (X(u1), top, Z(v0)), (X(u1), top, Z(v1)), (X(u0), top, Z(v1)),
                      (0.0, 1.0, 0.0), [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
        f32 = lambda a: np.asarray(a, np.float32)
        part = Part("reveal/mt%sa" % pid, f32(P), f32(N), f32(U), str(f), (1.0, 1.0, 1.0), "translucent", False)
        part.wrap = ("clamp", "clamp")
        if g > 1:
            sv = [SWEEP_V[0] + (SWEEP_V[1] - SWEEP_V[0]) * line_param(line, q[0] / ts, q[2] / ts) for q in P]
            part.uvs2 = np.asarray([(0.5, v) for v in sv], np.float32)
        else:
            part.uvs2 = np.tile(np.asarray(REVEAL_FADE[0], np.float32), (len(P), 1))
        part.mask_offset = REVEAL_FADE[1]
        parts.append(part)
    return parts


def draw(painter, sticker, tile_px, t_ms=0.0, base=None):
    """Draw a sticker with a QPainter at its place (tiles -> pixels with tile_px), posed at t_ms."""
    from PyQt5 import QtCore
    frames, delays = load_frames(sticker["path"], sticker.get("strip", 1))
    img, dx, dy, ang, sc = pose(sticker, frames, delays, t_ms)
    if img is None:
        return
    w = float(sticker.get("w", 3.0)) * tile_px
    h = float(sticker["h"]) * tile_px if sticker.get("h") else w * img.height() / max(1, img.width())
    cx, cy = (sticker["x"] + dx) * tile_px, (sticker["y"] + dy) * tile_px
    if sticker.get("anim") == "keys" and isinstance(sc, tuple):   # Koopatlas: X Scale holds the left edge, Y the bottom
        cx += (sc[0] - 1.0) * w / 2.0
        cy += (1.0 - sc[1]) * h / 2.0
    painter.save()
    painter.translate(cx, cy)
    if sticker.get("anim") == "keys":
        painter.setOpacity(keys_pose(sticker, t_ms * FPS / 1000.0)[4])
    ang += float(sticker.get("angle", 0.0))
    if ang:
        painter.rotate(ang)
    sx, sy = sc if isinstance(sc, tuple) else (sc, sc)
    if (sx, sy) != (1.0, 1.0):
        painter.scale(sx, sy)
    painter.drawImage(QtCore.QRectF(-w / 2, -h / 2, w, h), img)
    painter.restore()

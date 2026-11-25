"""
kdx/koopatlas.py - import a Koopatlas world map (.kpmap) into the World Maker.

Koopatlas (Treeki; RoadrunnerWMC's Koopatlas-Updated) is the world map editor of New Super Mario Bros. Wii mods such as
Newer. Its maps are drawn, not modelled: tile layers (24 px tiles from Koopatlas tilesets, U8 .arc), doodad layers
(PNG pictures placed, turned, scaled and animated by keyframes), one path layer (nodes and the paths between them) and
"associate" layers (tiles and doodads that belong to a path or node and appear when it opens). This module reads that
data only; nothing of the Wii mod is used in the game. It becomes a Deluxe world the World Maker way:

* the layers are drawn into one picture (the map's ground picture);
* doodads that keep moving (Loop / Reversible Loop keyframes) become moving images with the same keyframes
  (kdx.pictures, "keys"), which the export turns into CS_W<n>_Synchro bone animation;
* nodes become points (levels by course number, other stops as path stops, walk-through nodes as bends), paths become
  routes with the matching action (Walk, Ladder, Jump...), unlock conditions become the levels that open each path;
* the world's music track becomes the map's Music choice.

    data = load(path)
    project, report = import_map(path, out_dir, world=1, tilesets_dir=None)
"""
from __future__ import annotations

import base64
import json
import math
import re
import struct
from pathlib import Path

TILE_PX = 24
# Koopatlas path animations, by index (Newer's dKPPath_s::Animation, koopatlas/mapdata.h: WALK 0 ... DOOR 15, then
# TJUMPED, ENTER_CAVE_UP, RESERVED_18, INVISIBLE). Index 7 is JUMP_WATER in Newer's engine; Koopatlas-Updated's editor
# lists it as LaunchStarRight (Starry Skies uses it once). Newer refuses to walk back along jumps, pipes, doors, caves
# and invisible paths (pathmanager.cpp, "forbidden"); Deluxe routes always go both ways.
PATH_ANIMS = ["Walk", "WalkSand", "WalkSnow", "WalkWater", "Jump", "JumpSand", "JumpSnow", "LaunchStarRight",
              "Ladder", "LadderLeft", "LadderRight", "Fall", "Swim", "Run", "Pipe", "Door",
              "TJumped", "EnterCave", "Reserved18", "Invisible"]
# name -> (Deluxe route type, hidden walk, what Deluxe does / what needs code). The Deluxe types are the game's own 23
# route actions (kdx.routeactions, F0312): every walking surface Newer has has a match; swimming walks with water steps
# (水, AnimType 25, splashes); falls and launch stars become the jump arc. Deluxe has no action that hides Mario or warps
# him inside one map: its pipe states (CourseSelectPlayer InDokan / OutDokan, setDokanInOut_ 0x601C983C) only serve the
# warp-pipe network to other worlds (F0317, F0338). So pipes, doors, caves and invisible paths walk as plain road with
# no road art (the tunnel trick); hiding Mario on them needs a framework hook (ReadAction_ 0x60180FE8 + setAction_
# 0x601B83F8, see Next Update/Newer port report.md).
KP_PATHS = {
    "Walk": ("道", False, "road"),
    "WalkSand": ("砂", False, "sand (sand steps)"),
    "WalkSnow": ("雪", False, "snow (snow steps)"),
    "WalkWater": ("水", False, "water (splashing steps)"),
    "Jump": ("ジャンプ", False, "jump arc"),
    "JumpSand": ("ジャンプ", False, "jump arc (sand landing has no own sound in Deluxe)"),
    "JumpSnow": ("ジャンプ", False, "jump arc (snow landing has no own sound in Deluxe)"),
    "LaunchStarRight": ("ジャンプ", False, "jump arc (Deluxe has no launch star; a framework action could add one)"),
    "Ladder": ("はしご", False, "ladder climb"),
    "LadderLeft": ("はしご左", False, "ladder climb facing left"),
    "LadderRight": ("はしご右", False, "ladder climb facing right"),
    "Fall": ("ジャンプ", False, "jump arc down (Deluxe has no fall)"),
    "Swim": ("水", False, "water steps (Deluxe has no swim on the map; a framework action could add one)"),
    "Run": ("道", False, "road (Deluxe already runs on roads: anim 2)"),
    "Pipe": ("道", True, "pipe: road under the map, Mario stays visible (hiding him needs the framework)"),
    "Door": ("道", True, "door: road, Mario stays visible (hiding him needs the framework)"),
    "TJumped": ("ジャンプ", False, "jump arc"),
    "EnterCave": ("道", True, "cave: road, Mario stays visible (hiding him needs the framework)"),
    "Reserved18": ("道", True, "hidden road (Newer's reserved type)"),
    "Invisible": ("道", True, "invisible road (tunnel / hidden road): no road art, Mario stays visible"),
}
ROUTE_OF = {k: v[0] for k, v in KP_PATHS.items()}
ONE_WAY = ("Jump", "JumpSand", "JumpSnow", "LaunchStarRight", "Pipe", "Door", "EnterCave", "Reserved18", "Invisible")


def path_kind(anim):
    """The Koopatlas name of a path animation index (unknown indexes walk)."""
    return PATH_ANIMS[anim] if isinstance(anim, int) and 0 <= anim < len(PATH_ANIMS) else "Walk"


def path_note(route):
    """A sentence for a route imported from a Koopatlas path: its Newer kind and what Deluxe does with it."""
    kp = route.get("kp") or "Walk"
    t = KP_PATHS.get(kp, KP_PATHS["Walk"])
    return "Koopatlas %s -> %s%s." % (kp, t[2], "; one-way in Newer, both ways in Deluxe" if kp in ONE_WAY else "")


MAX_PICTURE = 8192          # px: the ground picture's longer side (the export scales it to the texture size anyway)


class KoopatlasError(ValueError):
    pass


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise KoopatlasError("Not a Koopatlas map: %s" % e)
    if not isinstance(data, dict) or data.get("_t") != "map_root":
        raise KoopatlasError("Not a Koopatlas map (no map_root).")
    return data


def path_layer(data):
    return next((l for l in data["layers"] if l.get("_t") == "path_layer"), {"nodes": [], "paths": []})


# ---- doodads -----------------------------------------------------------------------------------------------------------
def doodad_images(data):
    """[(name, QImage)] of the doodad definitions (PNG kept in the map file)."""
    from PyQt5 import QtGui
    out = []
    for name, d in data.get("doodadDefinitions", []):
        img = QtGui.QImage()
        try:
            img.loadFromData(base64.b64decode(d.get("png", "")), "PNG")
        except (ValueError, TypeError):
            pass
        out.append((name, img))
    return out


def track(anim):
    """A keyframe track as [loop, curve, frames, type, start, end, delay, delay offset]."""
    a = list(anim) + [0, 0]
    return [a[0], a[1], float(a[2]), a[3], float(a[4]), float(a[5]), float(a[6] or 0), float(a[7] or 0)]


def moves(doodad):
    return any(track(a)[0] in ("Loop", "Reversible Loop") for a in doodad.get("animations", []))


def final_state(doodad):
    """{type: value} a doodad ends at after its play-once (Contiguous) keyframes."""
    out = {}
    for a in doodad.get("animations", []):
        t = track(a)
        if t[0] == "Contiguous":
            out[t[3]] = t[5]
    return out


# ---- tilesets ----------------------------------------------------------------------------------------------------------
def _decode_rgb4a3(raw, w=896, h=448):
    import numpy as np
    v = np.frombuffer(raw[:w * h * 2], ">u2").reshape(h // 4, w // 4, 4, 4).transpose(0, 2, 1, 3).reshape(h, w)
    b1, b2 = (v >> 8).astype(np.uint32), (v & 0xFF).astype(np.uint32)
    a = ((b1 & 0x70) >> 4) * 255 // 7
    r, g, b = (b1 & 0xF) * 17, (b2 >> 4) * 17, (b2 & 0xF) * 17
    return np.dstack([r, g, b, a]).astype(np.uint8)


def _decode_rgba8(raw, w=896, h=448):
    import numpy as np
    blk = np.frombuffer(raw[:w * h * 4], np.uint8).reshape(h // 4, w // 4, 2, 16, 2)
    ar, gb = blk[:, :, 0], blk[:, :, 1]                          # (by, bx, 16, 2)
    px = np.stack([ar[..., 1], gb[..., 0], gb[..., 1], ar[..., 0]], -1)   # r g b a
    return px.reshape(h // 4, w // 4, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(h, w, 4)


def _qimage(rgba):
    from PyQt5 import QtGui
    h, w = rgba.shape[:2]
    img = QtGui.QImage(rgba.tobytes(), w, h, w * 4, QtGui.QImage.Format_RGBA8888)
    return img.copy()


def load_tileset(path):
    """(tiles: [512 QImage 24x24], objects: [(rows of tile indexes, width, height, wrap)]) of a Koopatlas tileset."""
    from . import brres
    files = {k.lstrip("/").lower(): v for k, v in brres.u8_files(Path(path).read_bytes()).items()}
    tex = next((v for k, v in files.items() if k.endswith("texture.bin")), None)
    obj = next((v for k, v in files.items() if k.endswith("objects.bin")), None)
    meta = next((v for k, v in files.items() if k.endswith("objectsmeta.bin")), None)
    if tex is None or obj is None or meta is None:
        raise KoopatlasError("%s is not a Koopatlas tileset (texture.bin, objects.bin, objectsMeta.bin)." % Path(path).name)
    sheet = _qimage(_decode_rgba8(tex) if len(tex) >= 896 * 448 * 4 else _decode_rgb4a3(tex))
    tiles, x, y = [], 2, 2
    for _ in range(512):
        tiles.append(sheet.copy(x, y, 24, 24))
        x += 28
        if x >= 896:
            x, y = 2, y + 28
    objects = []
    for i in range(len(meta) // 5):
        off, w, h, wrap = struct.unpack_from(">H3B", meta, i * 5)
        rows = [[struct.unpack_from(">h", obj, off + 2 * (r * w + c))[0] for c in range(w)] for r in range(h)]
        objects.append((rows, w, h, wrap))
    return tiles, objects


def object_tiles(obj, size):
    """Tile indexes of a tileset object drawn at size (w, h): the middle rows / columns repeat; wrap modes 1, 3, 5 keep
    the first row, 1, 3, 4 the last one; 1, 2, 6 the first column, 1, 2, 7 the last one (Koopatlas KPTileObject)."""
    rows, _w, _h, wrap = obj
    W, H = size

    def span(items, n, first, last):
        head = items[:1] if first and len(items) > 1 else []
        tail = items[-1:] if last and len(items) > 1 + len(head) else []
        mid = items[len(head):len(items) - len(tail)] or items
        out = []
        for i in range(n):
            if i < len(head):
                out.append(head[i])
            elif i >= n - len(tail):
                out.append(tail[i - (n - len(tail))])
            else:
                out.append(mid[(i - len(head)) % len(mid)])
        return out
    vr = span(rows, H, wrap in (1, 3, 5), wrap in (1, 3, 4))
    return [span(r, W, wrap in (1, 2, 6), wrap in (1, 2, 7)) for r in vr]


# ---- drawing -------------------------------------------------------------------------------------------------------------
def bounds(data, margin=48):
    """(x0, y0, x1, y1) in map pixels of everything on the map."""
    if data.get("_crop"):
        x0, y0, x1, y1 = data["_crop"]
        return (math.floor(x0 / TILE_PX) * TILE_PX, math.floor(y0 / TILE_PX) * TILE_PX,
                math.ceil(x1 / TILE_PX) * TILE_PX, math.ceil(y1 / TILE_PX) * TILE_PX)
    xs, ys = [], []
    for n in path_layer(data)["nodes"]:
        xs.append(n["position"][0]); ys.append(n["position"][1])

    def add_layer(l):
        for o in l.get("objects", []) + l.get("doodads", []):
            if o.get("_t") == "object":
                x, y = o["position"]; w, h = o["size"]
                xs.extend([x * TILE_PX, (x + w) * TILE_PX]); ys.extend([y * TILE_PX, (y + h) * TILE_PX])
            elif o.get("_t") == "doodad":
                (x, y), (w, h) = o["position"], o["size"]
                r = math.hypot(w, h) / 2 if o.get("angle") else 0
                cx, cy = x + w / 2, y + h / 2
                xs.extend([min(x, cx - r), max(x + w, cx + r)]); ys.extend([min(y, cy - r), max(y + h, cy + r)])
    for l in data["layers"]:
        add_layer(l)
    for l in data.get("associateLayers", []):
        add_layer(l)
    if not xs:
        raise KoopatlasError("The map is empty.")
    nodes = path_layer(data)["nodes"]
    if nodes:                                    # around the points and the ground: sky and sea doodads reach far beyond
        fx = [q["position"][0] for q in nodes]; fy = [q["position"][1] for q in nodes]
        tiles = [o for l in data["layers"] if l.get("_t") == "tile_layer" for o in l.get("objects", [])]
        for o in tiles:
            fx += [o["position"][0] * TILE_PX, (o["position"][0] + o["size"][0]) * TILE_PX]
            fy += [o["position"][1] * TILE_PX, (o["position"][1] + o["size"][1]) * TILE_PX]
        m = 4 * TILE_PX if tiles else max(14 * TILE_PX, 0.25 * max(max(fx) - min(fx), max(fy) - min(fy)))
        xs = [max(min(xs), min(fx) - m), min(max(xs), max(fx) + m)]
        ys = [max(min(ys), min(fy) - m), min(max(ys), max(fy) + m)]
    return (math.floor((min(xs) - margin) / TILE_PX) * TILE_PX, math.floor((min(ys) - margin) / TILE_PX) * TILE_PX,
            math.ceil((max(xs) + margin) / TILE_PX) * TILE_PX, math.ceil((max(ys) + margin) / TILE_PX) * TILE_PX)


# ---- Newer's game files: the exported map (.kpbin) and the tileset textures -------------------------------------------
# A Riivolution folder such as NewerSMBW/ has Maps/<map>.kpbin.LZ (Koopatlas's export: every tile layer cut into 16x16
# sectors of tile numbers, already through the tileset's optimised mapping) and Maps/Texture/<tileset>.bin.LZ (the
# tiles as a GX texture 896 px wide: 32 tiles of 24 px per row in 28 px cells). With them the map is drawn exactly as
# the game draws it, without Koopatlas's tileset archives.
# ---- one world out of a map that holds several (Newer's Fullmap: Rubble Ruins, Mushroom Peaks, Pumpkin Boneyard...) --
def world_regions(data):
    """{label: set of node indexes}: every node joins the world of the nearest level node along the paths (Newer's
    level world number; worlds A-E share 10). A map like Fullmap (307 nodes, 290 paths) is over Deluxe's 256 points /
    routes, so each world is imported on its own."""
    from collections import deque
    pl = path_layer(data)
    nodes, paths = pl["nodes"], pl["paths"]
    adj = {i: [] for i in range(len(nodes))}
    for p in paths:
        a, b = p["startNodeLink"], p["endNodeLink"]
        if a in adj and b in adj:
            adj[a].append(b); adj[b].append(a)
    label, dist, q = {}, {}, deque()
    for i, n in enumerate(nodes):
        if n.get("level") and n["level"][0] < 80:
            label[i], dist[i] = n["level"][0], 0
            q.append(i)
    while q:
        i = q.popleft()
        for j in adj[i]:
            if j not in dist:
                dist[j], label[j] = dist[i] + 1, label[i]
                q.append(j)
    out = {}
    for i in range(len(nodes)):
        out.setdefault(label.get(i, 0), set()).add(i)
    return out


def region_world(data, keep, label=None):
    """The world definition (data["worlds"] entry) of a region: the one whose worldID is its label (Newer worlds 1-9;
    10 = the map's lettered world A-E), else the one most of its world-change nodes name, else the first."""
    worlds = data.get("worlds") or [{}]
    if label is not None:
        want = [w for w in worlds if str(w.get("worldID")) == str(label)] if 1 <= label <= 9 else             [w for w in worlds if str(w.get("worldID", "")).isalpha()]
        if want:
            return want[0]
    nodes = path_layer(data)["nodes"]
    ids = [nodes[i].get("worldDefID") for i in keep if nodes[i].get("worldDefID") is not None]
    if ids:
        best = max(set(ids), key=ids.count)
        if 0 <= best < len(worlds):
            return worlds[best]
    return worlds[0]


def subset(data, keep, margin=8):
    """A copy of a map with only the nodes in keep (and the paths and associate layers between them), cropped to
    them (data["_crop"], map pixels) plus margin tiles."""
    import copy
    d = dict(data)                                       # layers stay the same objects (the .kpbin matches them by id)
    d["_full"] = data
    d["layers"] = [copy.deepcopy(l) if l.get("_t") == "path_layer" else l for l in data["layers"]]
    keep = sorted(keep)
    remap = {old: new for new, old in enumerate(keep)}
    pl = path_layer(d)
    nodes, paths = pl["nodes"], pl["paths"]
    pmap, new_paths = {}, []
    for k, p in enumerate(paths):
        if p["startNodeLink"] in remap and p["endNodeLink"] in remap:
            pmap[k] = len(new_paths)
            p = dict(p, startNodeLink=remap[p["startNodeLink"]], endNodeLink=remap[p["endNodeLink"]])
            new_paths.append(p)
    pl["nodes"] = [nodes[i] for i in keep]
    pl["paths"] = new_paths
    al = []
    for a in d.get("associateLayers", []):
        ref = a.get("associatedRef") or [None, None]
        if ref[0] == "path" and ref[1] in pmap:
            al.append(dict(a, associatedRef=["path", pmap[ref[1]]], _src_id=id(a)))
        elif ref[0] == "node" and ref[1] in remap:
            al.append(dict(a, associatedRef=["node", remap[ref[1]]], _src_id=id(a)))
    d["associateLayers"] = al
    xs = [nodes[i]["position"][0] for i in keep]
    ys = [nodes[i]["position"][1] for i in keep]
    m = margin * TILE_PX
    d["_crop"] = (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
    return d



def lz11(data):
    """Nintendo LZ77 (types 0x10 and 0x11) as used for the .LZ files."""
    if data[0] not in (0x10, 0x11):
        return bytes(data)
    size = int.from_bytes(data[1:4], "little")
    i = 4
    if size == 0:
        size, i = int.from_bytes(data[4:8], "little"), 8
    out = bytearray()
    lz11_mode = data[0] == 0x11
    while len(out) < size and i < len(data):
        flags = data[i]
        i += 1
        for bit in range(8):
            if len(out) >= size or i >= len(data):
                break
            if not flags & (0x80 >> bit):
                out.append(data[i])
                i += 1
                continue
            b0 = data[i]
            if not lz11_mode:
                n, disp = (b0 >> 4) + 3, ((b0 & 0xF) << 8 | data[i + 1]) + 1
                i += 2
            else:
                ind = b0 >> 4
                if ind == 0:
                    n = ((b0 & 0xF) << 4 | data[i + 1] >> 4) + 0x11
                    disp = ((data[i + 1] & 0xF) << 8 | data[i + 2]) + 1
                    i += 3
                elif ind == 1:
                    n = ((b0 & 0xF) << 12 | data[i + 1] << 4 | data[i + 2] >> 4) + 0x111
                    disp = ((data[i + 2] & 0xF) << 8 | data[i + 3]) + 1
                    i += 4
                else:
                    n, disp = ind + 1, ((b0 & 0xF) << 8 | data[i + 1]) + 1
                    i += 2
            start = len(out) - disp
            for k in range(n):
                out.append(out[start + k])
    return bytes(out[:size])


def _gx_rgb5a3(raw, w):
    import numpy as np
    h = len(raw) // (w * 2)
    h -= h % 4
    v = np.frombuffer(raw[:w * h * 2], ">u2").reshape(h // 4, w // 4, 4, 4).transpose(0, 2, 1, 3).reshape(h, w).astype(np.uint32)
    opaque = (v & 0x8000) != 0
    r = np.where(opaque, ((v >> 10) & 0x1F) * 255 // 31, ((v >> 8) & 0xF) * 17)
    g = np.where(opaque, ((v >> 5) & 0x1F) * 255 // 31, ((v >> 4) & 0xF) * 17)
    b = np.where(opaque, (v & 0x1F) * 255 // 31, (v & 0xF) * 17)
    a = np.where(opaque, 255, ((v >> 12) & 0x7) * 255 // 7)
    return np.dstack([r, g, b, a]).astype(np.uint8)


def _gx_rgba8(raw, w):
    import numpy as np
    h = len(raw) // (w * 4)
    h -= h % 4
    blk = np.frombuffer(raw[:w * h * 4], np.uint8).reshape(h // 4, w // 4, 2, 16, 2)
    ar, gb = blk[:, :, 0], blk[:, :, 1]
    px = np.stack([ar[..., 1], gb[..., 0], gb[..., 1], ar[..., 0]], -1)
    return px.reshape(h // 4, w // 4, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(h, w, 4)


def game_maps_dir(game_dir):
    """The Maps folder of a Newer-style Riivolution folder (or the folder itself), or None."""
    if not game_dir:
        return None
    g = Path(game_dir)
    for c in (g / "Maps", g):
        if (c / "Texture").is_dir():
            return c
    return None


def load_game_texture(maps_dir, name):
    """The tileset texture Maps/Texture/<name>.bin(.LZ) as a QImage, or None."""
    for f in (Path(maps_dir) / "Texture" / (name + ".bin.LZ"), Path(maps_dir) / "Texture" / (name + ".bin")):
        if f.exists():
            raw = lz11(f.read_bytes())
            rgba8 = "RGBA8" in name or len(raw) % (896 * 28 * 2) != 0
            return _qimage(_gx_rgba8(raw, 896) if rgba8 else _gx_rgb5a3(raw, 896))
    return None


def read_kpbin(path):
    """{"layers": [{"type": 0 tile / 1 doodad / 2 path, "bounds", "map"}], "sectors": [[16 rows of 16]]} of a .kpbin."""
    raw = Path(path).read_bytes()
    d = lz11(raw)
    if d[:4] != b"KP_m":
        raise KoopatlasError("%s is not a Koopatlas map export." % Path(path).name)
    n_layers, ptrs = struct.unpack_from(">II", d, 8)
    sec_off = struct.unpack_from(">I", d, 0x1C)[0]
    sectors_end = ptrs
    sectors = []
    for o in range(sec_off, sectors_end, 512):
        sectors.append([list(struct.unpack_from(">16h", d, o + 32 * r)) for r in range(16)])
    layers = []
    for k in range(n_layers):
        lo = struct.unpack_from(">I", d, ptrs + 4 * k)[0]
        t = struct.unpack_from(">I", d, lo)[0]
        L = {"type": t}
        if t == 0:
            sb = struct.unpack_from(">4I", d, lo + 12)
            n = (sb[2] - sb[0] + 1) * (sb[3] - sb[1] + 1)
            L["bounds"] = sb
            L["map"] = list(struct.unpack_from(">%dH" % n, d, lo + 44))
        layers.append(L)
    return {"layers": layers, "sectors": sectors}


def find_kpbin(maps_dir, kpmap_path):
    if not maps_dir:
        return None
    stem = Path(kpmap_path).stem
    for f in (Path(maps_dir) / (stem + ".kpbin.LZ"), Path(maps_dir) / (stem + ".kpbin")):
        if f.exists():
            return f
    return None


def export_order(data):
    """The layers in the order Koopatlas exports them: [(kind, layer, associate layer or None)] (tile and doodad layers
    with something on them, the path layer, then after it each associate layer's tiles and doodads)."""
    out = []
    for l in data["layers"]:
        t = l.get("_t")
        if t in ("tile_layer", "doodad_layer") and l.get("objects"):
            out.append(("tile" if t == "tile_layer" else "doodad", l, None))
        elif t == "path_layer":
            out.append(("path", l, None))
            for a in data.get("associateLayers", []):
                if a.get("objects"):
                    out.append(("tile", a, a))
                if a.get("doodads"):
                    out.append(("doodad", a, a))
    return out


class Painter:
    """Draws Koopatlas layers: tiles from the game's export and textures when given, else from Koopatlas tilesets
    (.arc), else a stand-in (kdx.koopatlas STANDIN_*), and doodads with their final play-once state."""

    def __init__(self, data, tilesets_dir=None, game_dir=None, kpmap_path=None):
        self.data, self.tilesets_dir = data, tilesets_dir
        self.defs = doodad_images(data)
        self.maps_dir = game_maps_dir(game_dir)
        self.kpbin = None
        f = find_kpbin(self.maps_dir, kpmap_path) if kpmap_path else None
        if f is not None:
            try:
                kb = read_kpbin(f)
                order = export_order(data.get("_full", data))       # a world cut out of a map: the whole map's order
                types = [{"tile": 0, "doodad": 1, "path": 2}[k] for k, _l, _a in order]
                if types == [L["type"] for L in kb["layers"]]:
                    self.kpbin = (kb, {id(l): L for (k, l, a), L in zip(order, kb["layers"]) if k == "tile"})
            except Exception:
                self.kpbin = None
        self.textures, self.sets, self.missing, self.standin = {}, {}, set(), set()

    def source(self):
        if self.kpbin:
            return "the game's own map export and textures"
        return "Koopatlas tilesets" if self.tilesets_dir else "stand-in ground"

    def _texture(self, name):
        if name not in self.textures:
            self.textures[name] = load_game_texture(self.maps_dir, name) if self.maps_dir else None
        return self.textures[name]

    def _tileset(self, name):
        if name in self.sets:
            return self.sets[name]
        self.sets[name] = None
        if self.tilesets_dir:
            cands = [p for p in Path(self.tilesets_dir).glob("*.arc") if p.stem.lower() == name.lower()]
            if cands:
                try:
                    self.sets[name] = load_tileset(cands[0])
                except Exception as e:
                    self.missing.add("%s (%s)" % (name, e))
        return self.sets[name]

    def tiles(self, pt, layer):
        name = layer.get("tileset")
        L = self.kpbin[1].get(layer.get("_src_id", id(layer))) if self.kpbin else None
        tex = self._texture(name) if L is not None else None
        if L is not None and tex is not None:
            from PyQt5 import QtCore
            sectors = self.kpbin[0]["sectors"]
            x0, y0, x1, y1 = L["bounds"]
            w = x1 - x0 + 1
            for k, si in enumerate(L["map"]):
                if si == 0xFFFF or si >= len(sectors):
                    continue
                sx, sy = x0 + k % w, y0 + k // w
                for r, row in enumerate(sectors[si]):
                    for c, t in enumerate(row):
                        if t >= 0:
                            pt.drawImage(QtCore.QPointF(((sx * 16 + c) * TILE_PX), ((sy * 16 + r) * TILE_PX)), tex,
                                         QtCore.QRectF((t % 32) * 28 + 2, (t // 32) * 28 + 2, 24, 24))
            return
        ts = self._tileset(name)
        if ts is not None:
            tiles, objs = ts
            for o in layer.get("objects", []):
                if o.get("kind", -1) >= len(objs):
                    continue
                ox, oy = o["position"]
                for r, row in enumerate(object_tiles(objs[o["kind"]], o["size"])):
                    for c, t in enumerate(row):
                        if 0 <= t < len(tiles):
                            pt.drawImage((ox + c) * TILE_PX, (oy + r) * TILE_PX, tiles[t])
            return
        self.standin.add(name)
        standin_layer(pt, layer)

    def doodads(self, pt, doodads, animated_out=None):
        from PyQt5 import QtCore
        for d in doodads:
            ref = d.get("sourceRef", -1)
            if not (0 <= ref < len(self.defs)) or self.defs[ref][1].isNull():
                continue
            if moves(d) and animated_out is not None:
                animated_out.append(d)
                continue
            st = final_state(d)
            op = st.get("Opacity", 100.0) / 100.0
            if op <= 0.01:
                continue
            (x, y), (w, h) = d["position"], d["size"]
            pt.save()
            pt.setOpacity(max(0.0, min(1.0, op)))
            pt.translate(x + w / 2.0 + st.get("X Position", 0.0), y + h / 2.0 + st.get("Y Position", 0.0))
            pt.rotate(float(d.get("angle", 0)) + st.get("Angle", 0.0))
            pt.scale(st.get("X Scale", 100.0) / 100.0, st.get("Y Scale", 100.0) / 100.0)
            pt.drawImage(QtCore.QRectF(-w / 2.0, -h / 2.0, w, h), self.defs[ref][1])
            pt.restore()


# ---- stand-in ground (no tileset): drawn from the tile objects' shapes and the layers' names ----------------------------
STANDIN_THEMES = {   # tileset name keywords -> (top colour, top rim, cliff colour)
    "desert": ("#f0d08a", "#c9a356", "#c98a4b"), "sand": ("#f0d08a", "#c9a356", "#c98a4b"),
    "snow": ("#f4f8ff", "#b8c8e0", "#8aa0c0"), "frz": ("#f4f8ff", "#b8c8e0", "#8aa0c0"),
    "cav": ("#6b6f80", "#3f4352", "#4a4e5e"), "crystal": ("#7a6ea8", "#463c70", "#4e4478"),
    "lava": ("#5a3a32", "#2e1a16", "#7a2a18"), "koopa": ("#6a5a50", "#3a2e28", "#5a3a2a"),
    "pumpkin": ("#5f7a3a", "#3a4a22", "#6a4a3a"), "ghost": ("#5f7a3a", "#3a4a22", "#6a4a3a"),
    "star": ("#c8b8f0", "#8a78c8", "#6a5aa8"), "mush": ("#a8c070", "#6a8040", "#a07850"),
}
STANDIN_DEFAULT = ("#62c24a", "#2f8a2a", "#b5763c")       # grass, its dark rim, earth cliffs (Newer's Yoshi's Island)
ROAD = ("#e8a94a", "#b87422", "#b87422")


def _role(layer):
    """What a tile layer is, from its name and tileset: 'road', 'shore', 'cliff', 'shadow', 'top'."""
    n, ts = layer.get("name", "").lower(), (layer.get("tileset") or "").lower()
    if "path" in ts or ts.endswith("paths"):
        return "road"
    if "beach" in ts or "edge" in ts or "water" in n:
        return "shore"
    if "shadow" in n:
        return "shadow"
    if any(k in n for k in ("ground", "rock", "edges", "sides", "walls", "jaw")) and "grass" not in n:
        return "cliff"
    return "top"


def _theme(layer):
    ts = ((layer.get("tileset") or "") + " " + layer.get("name", "")).lower()
    for k, v in STANDIN_THEMES.items():
        if k in ts:
            return v
    return STANDIN_DEFAULT


def _mask(objects, pad):
    """(alpha mask uint8 at 6 px per tile, x0 tile, y0 tile) of tile objects, rounded like hand-drawn tiles."""
    import numpy as np
    xs = [o["position"][0] for o in objects] + [o["position"][0] + o["size"][0] for o in objects]
    ys = [o["position"][1] for o in objects] + [o["position"][1] + o["size"][1] for o in objects]
    x0, y0 = min(xs) - pad, min(ys) - pad
    W, H = (max(xs) + pad - x0) * 6, (max(ys) + pad - y0) * 6
    m = np.zeros((H, W), np.float32)
    for o in objects:
        (x, y), (w, h) = o["position"], o["size"]
        m[(y - y0) * 6:(y - y0 + h) * 6, (x - x0) * 6:(x - x0 + w) * 6] = 1.0
    for _ in range(2):                                         # box blur -> threshold: rounded corners
        k = 4
        c = np.cumsum(np.pad(m, ((0, 0), (k + 1, k))), 1); m = (c[:, 2 * k + 1:] - c[:, :-2 * k - 1]) / (2 * k + 1)
        c = np.cumsum(np.pad(m, ((k + 1, k), (0, 0))), 0); m = (c[2 * k + 1:] - c[:-2 * k - 1]) / (2 * k + 1)
    return m, x0, y0


def standin_layer(pt, layer):
    """Draw a tile layer without its tileset: its objects' outline, coloured for what the layer is."""
    import numpy as np
    from PyQt5 import QtCore, QtGui
    objs = layer.get("objects", [])
    if not objs:
        return
    role = _role(layer)
    top, rim, cliff = ROAD if role == "road" else _theme(layer)
    m, x0, y0 = _mask(objs, 2)
    inside = m > 0.5
    H, W = m.shape
    yy, xx = np.mgrid[0:H, 0:W]
    col = np.zeros((H, W, 4), np.float32)

    def rgb(h):
        q = QtGui.QColor(h)
        return np.array([q.red(), q.green(), q.blue()], np.float32)
    edge = inside & (m < 0.85)
    if role == "cliff":
        base = rgb(cliff) * (0.85 + 0.15 * (np.sin(xx * 0.9) > 0))[..., None]   # vertical strata
        base *= (0.75 + 0.25 * np.clip(1 - yy / max(1, H), 0, 1))[..., None]
        col[..., :3] = base
    elif role == "shore":
        col[..., :3] = rgb("#f2dc9a") * (0.95 + 0.05 * np.sin((xx + yy) * 0.5))[..., None]
        col[edge, :3] = rgb("#ffffff")
    elif role == "shadow":
        col[..., :3] = 0
    else:
        col[..., :3] = rgb(top) * (0.94 + 0.06 * np.sin(xx * 0.7) * np.sin(yy * 0.7))[..., None]
        col[edge, :3] = rgb(rim)
    col[..., 3] = np.where(inside, 90 if role == "shadow" else 255, 0)
    img = _qimage(np.clip(col, 0, 255).astype(np.uint8))
    pt.save()
    pt.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    pt.drawImage(QtCore.QRectF(x0 * TILE_PX, y0 * TILE_PX, W / 6.0 * TILE_PX, H / 6.0 * TILE_PX), img)
    pt.restore()


def render(data, tilesets_dir=None, scale=1.0, animated_out=None, report=None, game_dir=None, kpmap_path=None,
           associates=None, painter_out=None):
    """The map as one QImage (scale x map pixels) and its origin (map pixels). Moving doodads are left out and listed in
    animated_out (as (doodad, layer name)) when it is given. associates: a dict that, when given, gets each associate
    layer drawn on its own instead ({index: (QImage, (x0, y0) map px)}), for paths that show their art when they open.
    Missing tilesets are named in report (a list)."""
    from PyQt5 import QtCore, QtGui
    x0, y0, x1, y1 = bounds(data)
    W, H = max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale))
    img = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtGui.QColor(*BACKDROPS.get(Path(data.get("bgName", "")).stem.lower(), (0, 0, 0, 0))))
    pt = QtGui.QPainter(img)
    pt.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    pt.scale(scale, scale)
    pt.translate(-x0, -y0)
    P = Painter(data, tilesets_dir, game_dir, kpmap_path)
    if painter_out is not None:
        painter_out.append(P)
    moving = []

    def assoc_layer(i, a):
        if associates is None:
            if a.get("objects"):
                P.tiles(pt, a)
            P.doodads(pt, a.get("doodads", []), moving)
            return
        n0 = len(moving)
        bb = associate_bounds(a)
        if not bb:
            return
        ax0, ay0, ax1, ay1 = bb
        sub = QtGui.QImage(max(1, int((ax1 - ax0) * scale)), max(1, int((ay1 - ay0) * scale)),
                           QtGui.QImage.Format_ARGB32_Premultiplied)
        sub.fill(0)
        sp = QtGui.QPainter(sub)
        sp.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        sp.scale(scale, scale)
        sp.translate(-ax0, -ay0)
        if a.get("objects"):
            P.tiles(sp, a)
        P.doodads(sp, a.get("doodads", []), moving)
        for d in moving[n0:]:                            # moving path art: appears with its path (kdx: "reveal")
            d["_assoc"] = i
        sp.end()
        associates[i] = (sub, (ax0, ay0))

    for layer in reversed(data["layers"]):                    # the first layer in the list is the top one
        t = layer.get("_t")
        if t == "tile_layer":
            P.tiles(pt, layer)
        elif t == "doodad_layer":
            P.doodads(pt, layer.get("objects", []), moving)
        elif t == "path_layer":                               # what paths and nodes bring (shown once they open)
            for i, a in enumerate(data.get("associateLayers", [])):
                assoc_layer(i, a)
    pt.end()
    if animated_out is not None:
        animated_out.extend((d, "") for d in moving)
    if report is not None:
        report.extend(sorted(P.missing))
    return img, (x0, y0)


BACKDROPS = {"water": (58, 132, 214, 255), "lava": (90, 20, 10, 255)}     # bgName -> sea colour behind the map


def associate_bounds(a):
    xs, ys = [], []
    for o in a.get("objects", []):
        (x, y), (w, h) = o["position"], o["size"]
        xs += [x * TILE_PX, (x + w) * TILE_PX]; ys += [y * TILE_PX, (y + h) * TILE_PX]
    for d in a.get("doodads", []):
        (x, y), (w, h) = d["position"], d["size"]
        r = math.hypot(w, h) / 2
        xs += [x + w / 2 - r, x + w / 2 + r]; ys += [y + h / 2 - r, y + h / 2 + r]
    if not xs:
        return None
    return math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))


# ---- points and paths ----------------------------------------------------------------------------------------------------
def course_names(world):
    """{course number: Deluxe point name} for a world (W101 -> 1, W1T0 -> 21, W1C0 -> 23...), from kdx.rules; only names
    the game accepts at the end of a path (last character a digit)."""
    from . import rules
    out = {}
    cands = ["W%d0%s" % (world, i) for i in "123456789abcdef"]                  # levels, then the specials' first
    cands += ["W%d%s0" % (world, l) for l in "TCGAKSWXMJ"]                     # names (W1T0 tower, W1C0 castle...)
    cands += ["W%d%s%s" % (world, l, i) for l in "TCGAKSWXMJ" for i in "123456789abcdef"]
    for name in cands:
        if name[-1].isdigit():                    # a path end must end in a digit (0x60181FC0): W10a-W10f cannot
            ci = rules.course_index(name)
            if ci >= 0 and rules.level_file(name):
                out.setdefault(ci + 1, name)
    return out


def _spec_terms(spec, names, warn):
    """(point, 'normal'|'secret') terms of a Koopatlas unlock spec (None: always open)."""
    if not spec:
        return []
    kind = spec[0]
    if kind == "level":
        w, l, secret = spec[1], spec[2], bool(spec[3])
        nm = names.get((w, l), names.get(l))
        if nm:
            return [(nm, "secret" if secret else "normal")]
        warn("level %d-%d has no Deluxe point; its unlock was dropped" % (spec[1], l))
        return []
    if kind in ("and", "or"):
        if kind == "and":
            warn("an AND unlock became OR (the game opens a path when any one of its levels is beaten)")
        out = []
        for sub in spec[1]:
            for t in _spec_terms(sub, names, warn):
                if t not in out:
                    out.append(t)
        return out
    if kind == "star_coins":
        warn("a star-coin unlock (%s %s %d) has no Deluxe equivalent: that path is open from the start" %
             (spec[3], spec[1], spec[2]))
        return []
    return []


# ---- Newer's level list (NewerRes/LevelInfo.bin) ---------------------------------------------------------------------
# Newer numbers its levels by file (Stages/01-15.arc) and shows another number on the map: LevelInfo.bin holds per
# world section the file's (world, level) slot, the shown (world, level), flags and the name (bytes - 0x30). Shown
# numbers above 19 are kinds: 20 ghost house, 21/22 tower, 23 castle, 24 fortress, 25 Bowser's keep, 26 railroad,
# 27 airship, 29 Yoshi house, 36 music house, 37 shop, 38 challenge house, 39-42 switch palaces, 100+ world names.
# Flags: 0x02 has a goal, 0x10 a level, 0x20 a secret exit, 0x400 the world's second map (Sewers, Koopa Core...).
NEWER_KINDS = {20: "ghost house", 21: "tower", 22: "tower", 23: "castle", 24: "fortress", 25: "Bowser's keep",
               26: "level", 27: "airship", 29: "Yoshi house", 36: "music house", 37: "shop", 38: "challenge house",
               39: "switch palace", 40: "switch palace", 41: "switch palace", 42: "switch palace"}
# Without LevelInfo.bin: Newer's usual file numbers -> shown kind (from Newer 1.30's own list).
NEWER_DEFAULT = {15: 21, 16: 22, 24: 25, 25: 23, 31: 41, 33: 38, 34: 36, 35: 36, 36: 36, 37: 36, 38: 27, 41: 29,
                 99: 37}


def newer_levels(game_dir):
    """{(world, level): {"world", "level" (shown), "flags", "name"}} from <game_dir>/NewerRes/LevelInfo.bin, or {}."""
    if not game_dir:
        return {}
    p = Path(game_dir) / "NewerRes" / "LevelInfo.bin"
    if not p.is_file():
        return {}
    d = p.read_bytes()
    if d[:4] != b"NWRp":
        return {}
    out = {}
    try:
        for s_ in range(struct.unpack_from(">I", d, 4)[0]):
            off = struct.unpack_from(">I", d, 8 + 4 * s_)[0]
            for i in range(struct.unpack_from(">I", d, off)[0]):
                ws, ls, dw, dl, nl, _r, fl, no = struct.unpack_from(">6BHI", d, off + 4 + 12 * i)
                if dl >= 100:
                    continue
                name = bytes((b + 0x30) & 0xFF for b in d[no:no + nl]).decode("latin1")
                out[(ws + 1, ls + 1)] = {"world": dw, "level": dl, "flags": fl, "name": name}
    except struct.error:
        pass
    return out


def shown_level(kp_world, kp_level, info):
    """The number (1-19) or kind (20+) Newer shows for a level file."""
    e = info.get((kp_world, kp_level))
    if e:
        return e["level"]
    return NEWER_DEFAULT.get(kp_level, kp_level)


def point_candidates(world, shown):
    """Deluxe point names for a Newer level, best first: its own number, the matching special, then any level. Only
    names ending in a digit: the game registers a path only between such names (0x60181FC0), so W10a-W10f can't be
    on a path; a world's tenth level takes a free special (ghost house, tower, airship) instead."""
    w = world
    lv = ["W%d0%x" % (w, i) for i in range(1, 10)] + ["W%d%s" % (w, k) for k in ("G0", "T1", "T0", "A0")]
    if 1 <= shown <= 9:
        return ["W%d0%x" % (w, shown)] + lv
    if shown == 20:
        return ["W%dG0" % w] + lv
    if shown in (21, 22, 24):
        t = ["W%dT0" % w, "W%dT1" % w]
        return (t if shown == 21 else t[::-1]) + lv
    if shown == 23:
        return (["W8B0", "W8B1"] if w == 8 else []) + ["W%dC0" % w] + lv
    if shown == 25:
        return (["W8B1", "W8B0"] if w == 8 else []) + ["W%dC0" % w] + lv
    if shown == 27:
        return ["W%dA0" % w] + lv
    if shown in (29, 36, 37, 38):
        return ["W%dK%d" % (w, i) for i in range(1, 8)]
    return lv


def stand_in(name, exists):
    """A Deluxe course file to play for a point whose own level file is not in the game: the same world's levels in
    turn for numbered levels, else the same kind of course from the nearest world. None if there is none."""
    from . import rules
    lf = rules.level_file(name)
    if lf is None or exists is None or exists(lf):
        return None
    w, ci = int(name[1]), rules.course_index(name)
    if name[2] == "0":
        pool = [f for f in ("Course/%d-%d.sarc" % (w, i) for i in range(1, 13)) if exists(f)]
        if pool:
            return pool[ci % len(pool)]
    for w2 in sorted(range(1, 10), key=lambda x: (abs(x - w), x)):
        f = "Course/%d-%d.sarc" % (w2, ci + 1)
        if exists(f):
            return f
    f = "Course/%d-1.sarc" % w
    return f if exists(f) else None



DEPTH_STEP = 0.8          # tiles of height per Koopatlas ground level (its painted cliff faces are about that tall)
_LEVEL_WORDS = re.compile(r"\s*\b(ground|grass|sides?|walls?|edges?|rock|top|tops)\b\s*", re.I)


def layer_heights(data, width, height, tx0, ty0, step):
    """Corner heights ((width+1)*(height+1) world units, or None) from a Koopatlas map's own ground levels.

    Koopatlas maps stack their ground in named levels, drawn bottom to top: Yoshi's Island has NewBeach, then Forest
    Lowest / Low / Medium, each a Grass layer (the plateau top) over a Ground layer (the cliff face painted below it).
    Every level above the beach becomes a plateau `step` higher than the one under it, and the tiles where only its
    Ground shows (the painted face) become a slope from the top down to the level below, so the art's own cliffs stand
    up as walls in 3D. tx0, ty0: the project's top-left tile on the map."""
    import numpy as np
    order, parts = [], {}                             # ground groups bottom to top: key -> [(role, layer)]
    for layer in reversed(data.get("layers", [])):
        if layer.get("_t") != "tile_layer" or not layer.get("tileset") or not layer.get("_visible", True):
            continue
        role = _role(layer)
        if role not in ("top", "cliff") or not _LEVEL_WORDS.search(layer.get("name", "")):
            continue                                  # cover-up and doodad tile layers are not ground levels
        key = _LEVEL_WORDS.sub(" ", layer.get("name", "")).strip().lower()
        if key not in parts:
            order.append(key)
            parts[key] = []
        parts[key].append((role, layer))
    if not order:
        return None

    def cover(layer):
        g = np.zeros((height, width), bool)
        for o in layer.get("objects", []):
            if o.get("_t") != "object":
                continue
            (x, y), (w, h) = o["position"], o["size"]
            x0, y0 = max(0, x - tx0), max(0, y - ty0)
            x1, y1 = min(width, x - tx0 + w), min(height, y - ty0 + h)
            if x0 < x1 and y0 < y1:
                g[y0:y1, x0:x1] = True
        return g

    def grow(g, n=2):
        g = g.copy()
        for _ in range(n):
            g[1:] |= g[:-1].copy(); g[:-1] |= g[1:].copy(); g[:, 1:] |= g[:, :-1].copy(); g[:, :-1] |= g[:, 1:].copy()
        return g
    # a group stands one level above the highest earlier group it touches (so the far-apart regions of Fullmap,
    # each with its own Low/Medium layers, are not stacked on one another)
    level, foot = {}, {}
    top = np.zeros((height, width), np.int16)        # highest plateau top on each tile
    face = np.zeros((height, width), np.int16)       # highest level whose cliff face (Ground) is on each tile
    for key in order:
        grids = [(role, cover(layer)) for role, layer in parts[key]]
        fp = np.zeros((height, width), bool)
        for _r, g in grids:
            fp |= g
        near = grow(fp)
        level[key] = 1 + max([level[k] for k in foot if (foot[k] & near).any()] or [0])
        foot[key] = fp
        for role, g in grids:
            grid = top if role == "top" else face
            grid[g] = np.maximum(grid[g], level[key])
    if not top.any():
        return None
    tile = top.astype(np.float32) * step
    corner = np.zeros((height + 1, width + 1), np.float32)
    for dy in (0, 1):                                 # a corner is as high as the highest plateau top touching it
        for dx in (0, 1):
            np.maximum(corner[dy:dy + height, dx:dx + width], tile, out=corner[dy:dy + height, dx:dx + width])
    # painted faces: runs of face tiles straight below a higher top slope down to the ground under them
    for x in range(width):
        y = 0
        while y < height:
            k = face[y, x]
            if k > top[y, x] and y > 0 and top[y - 1, x] >= k:
                y1 = y
                while y1 < height and face[y1, x] >= k and top[y1, x] < k:
                    y1 += 1
                hi = top[y - 1, x] * step
                lo = (top[y1, x] if y1 < height else top[y, x]) * step
                n = y1 - y
                for r in range(n + 1):
                    hv = hi + (lo - hi) * r / n
                    for cx in (x, x + 1):
                        corner[y + r, cx] = hv if r == 0 else max(min(corner[y + r, cx], hi), hv)
                y = y1
            else:
                y += 1
    return [float(v) for v in corner.reshape(-1)]


def import_map(path, out_dir, world=1, tilesets_dir=None, scale=1.0, fit_slots=True, course_exists=None,
               game_dir=None, reveal=True, region=None, name=None, depth=False, stretch=2.0):
    """A World Maker project from a .kpmap: picture, moving images, points, routes, unlocks and music. Pictures go into
    out_dir. fit_slots: levels whose course the game keeps no clear for in this world take the names of free ones
    that it does (W101-W105 in World 1), so their exits open paths. Returns (project2d.Project, report:
    {"warnings": [...], "summary": [...]}). course_exists(level file) -> bool: levels without a course file in the mod
    or the game become path stops (entering a missing course crashes the game). game_dir: the mod's Riivolution
    folder (with Maps/<map>.kpbin.LZ and Maps/Texture): the tiles are drawn from the game's own export. reveal: the
    art Koopatlas shows when a path opens (associate layers) becomes path art that fades in when that path opens."""
    from . import pictures, project2d, rules, unlocks
    path, out_dir = Path(path), Path(out_dir)
    data = load(path)
    region_def = None
    if region is not None:                               # one world of a map that holds several (world_regions)
        label = None
        if not isinstance(region, (set, list, tuple, frozenset)):
            label, region = region, world_regions(data).get(region, set())
        if not region:
            raise KoopatlasError("That world has no nodes in %s." % path.name)
        region_def = region_world(data, region, label)
        data = subset(data, region)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"warnings": [], "summary": []}
    warn_seen = set()

    def warn(t):
        if t not in warn_seen:
            warn_seen.add(t)
            report["warnings"].append(t)
    missing, animated, painters, assoc = [], [], [], {}
    img, (ox, oy) = render(data, tilesets_dir, 1.0, animated, missing, game_dir=game_dir, kpmap_path=path,
                           associates=assoc if reveal else None, painter_out=painters)
    report["summary"].append("Ground drawn from %s." % painters[0].source())
    if painters[0].standin:
        warn("no tiles for %s: drawn as a stand-in from the tile shapes (pick the mod's Riivolution folder, which has "
             "Maps/Texture, for the real tiles)" % ", ".join(sorted(painters[0].standin)))
    big = max(img.width(), img.height())
    pic_scale = min(1.0, MAX_PICTURE / float(big)) * scale
    if pic_scale < 1.0:
        from PyQt5 import QtCore
        img = img.scaled(int(img.width() * pic_scale), int(img.height() * pic_scale), QtCore.Qt.KeepAspectRatio,
                         QtCore.Qt.SmoothTransformation)
    stem = re.sub(r"[^\w\-]+", "_", name or path.stem)
    pic_path = out_dir / ("%s_ground.png" % stem)
    for m in missing:
        warn("tileset %s not found: its tiles are missing from the picture (choose your Koopatlas Tilesets folder)" % m)
    x0, y0, x1, y1 = bounds(data)
    W, H = int(round((x1 - x0) / TILE_PX)), int(round((y1 - y0) / TILE_PX))
    wdef = region_def or (data.get("worlds") or [{}])[0]
    p = project2d.Project(width=W, height=H, world=world, name=name or wdef.get("name") or path.stem)
    p.picture = {"path": str(pic_path.resolve())}
    p.reveal_style = "sweep"
    p.depth_stretch = float(stretch)                     # drawn for Koopatlas's straight-down camera (see Project.stretched)
    if depth:                                            # the map's ground levels as real heights (3D view, game)
        hs = layer_heights(data, W, H, int(round(x0 / TILE_PX)), int(round(y0 / TILE_PX)), DEPTH_STEP * p.tile_size)
        if hs is not None and any(hs):
            p.heights = hs
            report["summary"].append("Depth: the map's ground levels are raised %.1f tiles each, its painted cliff "
                                     "faces stand up as slopes." % DEPTH_STEP)
    T = lambda px, o: (px - o) / float(TILE_PX)

    # moving doodads -> moving images with the same keyframes
    defs = doodad_images(data)
    adir = out_dir / ("%s_images" % stem)
    saved = {}
    for d, lname in animated:
        ref = d["sourceRef"]
        dname = defs[ref][0]
        if ref not in saved:
            adir.mkdir(exist_ok=True)
            f = adir / ("%s.png" % re.sub(r"[^\w\-]+", "_", dname))
            defs[ref][1].save(str(f))
            saved[ref] = str(f.resolve())
        (x, y), (w, h) = d["position"], d["size"]
        if not (-w <= x - ox <= W * TILE_PX and -h <= y - oy <= H * TILE_PX):
            continue                                     # outside this world's part of the map
        p.stickers.append({**({"_assoc": d["_assoc"]} if "_assoc" in d else {}),
                           "name": dname, "path": saved[ref], "x": T(x + w / 2.0, ox), "y": T(y + h / 2.0, oy),
                           "w": w / float(TILE_PX), "h": h / float(TILE_PX), "angle": float(d.get("angle", 0)),
                           "anim": "keys", "tracks": [track(a) for a in d.get("animations", [])],
                           "strip": 1, "speed": 1.0, "amount": 1.0})

    # nodes -> points
    pl = path_layer(data)
    nodes, kpaths = pl["nodes"], pl["paths"]
    info = newer_levels(game_dir)
    hook = fit_slots == "hook"
    p.slot_hook = hook
    used, kp_name = set(), {}
    exits = {i: [] for i in range(len(nodes))}
    for k, pth in enumerate(kpaths):
        exits.setdefault(pth["startNodeLink"], []).append(k)
        exits.setdefault(pth["endNodeLink"], []).append(k)
    start_done = False
    assign, standins, shown_of = {}, {}, {}
    for i, n in enumerate(nodes):
        if n.get("level"):
            kw, kl = n["level"][0], n["level"][1]
            if (kw, kl) in info or kl in NEWER_DEFAULT or 1 <= kl <= 19:
                shown_of[i] = shown_level(kw, kl, info)
            else:
                warn("level %d-%d is not in Newer's level list: it became a path stop" % (kw, kl))
    # own numbers and specials first, then the rest (switch palaces, levels shown above 15...)
    lv = sorted(shown_of, key=lambda i: (not (1 <= shown_of[i] <= 15 or shown_of[i] in (20, 21, 22, 23, 24, 25, 27)),
                                         shown_of[i], i))
    slot_ok = lambda nm: rules.has_slot(world, rules.course_index(nm))
    taken, moved, kinds = set(), [], {}
    for i in lv:
        cands = [c for c in point_candidates(world, shown_of[i]) if c not in taken and rules.course_index(c) >= 0]
        if fit_slots and not hook and cands and not slot_ok(cands[0]) and cands[0][2] == "0":
            cands = [c for c in cands if slot_ok(c) and c[2] == "0"] + cands
        if not cands:
            continue
        nm = cands[0]
        assign[i] = nm
        taken.add(nm)
        kw, kl = nodes[i]["level"][0], nodes[i]["level"][1]
        e = info.get((kw, kl))
        title = "%d-%d" % (kw, kl) + (" %s" % e["name"] if e else "")
        kind = NEWER_KINDS.get(shown_of[i])
        if kind and kind != "level":
            kinds.setdefault(kind, []).append(nm)
        if 1 <= shown_of[i] <= 15 and nm != "W%d0%x" % (world, shown_of[i]):
            moved.append("%s as %s" % (title, nm))
        si = stand_in(nm, course_exists)
        if si:
            standins[nm] = si
        elif course_exists and rules.level_file(nm) and not course_exists(rules.level_file(nm)):
            del assign[i]
            taken.discard(nm)
            warn("level %s has no Deluxe course to play (%s): it became a path stop" % (title, rules.level_file(nm)))
    if moved:
        report["summary"].append("Levels given a course the game tracks in World %d: %s." % (world, ", ".join(moved)))
    if kinds:
        report["summary"].append("Newer's kinds: " + "; ".join("%s %s" % (k, ", ".join(v)) for k, v in sorted(kinds.items())) + ".")
    if standins:
        report["summary"].append("%d levels play a Deluxe stand-in course until you put the level in the mod (Newer's "
                                 "levels are Wii levels): %s." % (len(standins), ", ".join(
                                     "%s %s" % (k, v[len("Course/"):-len(".sarc")]) for k, v in sorted(standins.items()))))
    order = sorted(range(len(nodes)), key=lambda i: (nodes[i].get("level") is None, i))   # levels claim names first
    start_node = 0 if nodes and not nodes[0].get("level") and not nodes[0].get("mapChange") else None
    for i in order:
        n = nodes[i]
        x, y = T(n["position"][0], ox), T(n["position"][1], oy)
        lvl = n.get("level")
        stop = bool(lvl or n.get("mapChange") or n.get("worldDefID") is not None or len(exits[i]) != 2)
        pid, kind = None, None
        if i == start_node and not lvl and not start_done:   # a new game starts on the map's first node (Newer)
            pid, kind, start_done = "W%dM0" % world, "start", True
        if lvl:
            nm = assign.get(i)
            if nm and nm not in used:
                pid, kind = nm, _kind_of(nm)
            elif i in shown_of:
                warn("level %d-%d has no free Deluxe point in World %d: it became a path stop" % (lvl[0], lvl[1], world))
        if pid is None and stop and not start_done and not lvl:
            pid, kind = "W%dM0" % world, "start"
            start_done = True
        if pid is None:
            kind = "stop" if stop else "bend"
        try:
            q = p.add_node(kind or _kind_of(pid), x, y, pid)
        except project2d.ProjectError as e:
            warn("node %d: %s" % (i, e))
            continue
        used.add(q)
        kp_name[i] = q
        if lvl and q == assign.get(i):
            e = info.get((lvl[0], lvl[1]))
            p.nodes[q]["note"] = "Newer %d-%d%s" % (lvl[0], lvl[1], (": " + e["name"]) if e else "")
            if q in standins:
                p.nodes[q]["course_from"] = standins[q]
        if n.get("mapChange"):
            p.nodes[q]["note"] = "Koopatlas: to %s" % n["mapChange"]
    if not start_done:
        warn("no stop without a level for the start: add a start point (Add needed points)")
    # path stops from F100 on: the vanilla worlds' own walks on to the next world use low F names (W2a: F007), which
    # the export copies next to the castle (export2d.world_link)
    for q in [q for q in list(p.nodes) if len(q) == 4 and q[0] == "F" and q[1:].isalnum()]:
        try:
            n = int(q[1:], 16)
        except ValueError:
            continue
        if n < 0x100 and "F%03x" % (n + 0x100) not in p.nodes:
            new = "F%03x" % (n + 0x100)
            p.rename_node(q, new)
            kp_name.update({k: new for k, v in kp_name.items() if v == q})

    # paths -> routes
    kinds_used = {}
    for pth in kpaths:
        a, b = kp_name.get(pth["startNodeLink"]), kp_name.get(pth["endNodeLink"])
        if not a or not b or a == b:
            continue
        if any(r["id"] in ("R%s%s" % (a, b), "R%s%s" % (b, a)) for r in p.routes):
            continue
        kp = path_kind(pth.get("animation", 0))
        rid = p.add_route(a, b, ROUTE_OF.get(kp, "道"))
        if kp != "Walk":
            r = next(x for x in p.routes if x["id"] == rid)
            r["kp"] = kp
            if KP_PATHS[kp][1]:
                r["hidden"] = True
            kinds_used[kp] = kinds_used.get(kp, 0) + 1

    if kinds_used:
        report["summary"].append("Path kinds: " + "; ".join("%d %s -> %s" % (n, k, KP_PATHS[k][2]) for k, n in
                                                            sorted(kinds_used.items())) + ".")
    fixed = fix_directions(p)
    if fixed:
        report["summary"].append("%d roads got a short bend so no point has two roads leaving the same way (the game "
                                 "keeps one per direction)." % fixed)

    # unlocks: every Deluxe path (stop to stop) opens when the levels of its first Koopatlas path's spec are beaten
    seg_spec = {}
    for pth in kpaths:
        a, b = kp_name.get(pth["startNodeLink"]), kp_name.get(pth["endNodeLink"])
        if a and b:
            seg_spec[(a, b)] = seg_spec[(b, a)] = pth.get("unlockSpec")
    rows = p.point_rows()
    point_ids = [r[1] for r in rows]
    level_names = {tuple(nodes[i]["level"][:2]): kp_name[i] for i in assign if kp_name.get(i) == assign[i]}
    opened = 0
    for pid_path, walk in rules.map_paths(rows, p.route_rows()):
        spec = seg_spec.get((walk[0], walk[1])) if len(walk) > 1 else None
        terms = [t for t in _spec_terms(spec, level_names, warn) if t[0] in point_ids]
        if not terms:
            continue
        try:
            rows = unlocks.apply(rows, pid_path, terms)
            opened += 1
        except unlocks.UnlockError as e:
            warn("%s: %s" % (pid_path, e))
    for r in rows:
        if r[1] in p.nodes:
            p.nodes[r[1]]["row"] = [p.nodes[r[1]]["row"][0]] + list(r[1:9])

    # path art: an associate layer shows when its path (or the path into its node) opens; on a path nothing opens it
    # would never show, so it is painted into the picture instead
    rows_now = p.point_rows()
    open_set = {q for r in rows_now for c in (4, 7) for q in rules.split(r[c])}
    walks = rules.map_paths(rows_now, p.route_rows())
    alayers = data.get("associateLayers", [])
    revealed, baked = 0, 0
    pdir = out_dir / ("%s_paths" % stem)
    from PyQt5 import QtCore, QtGui
    bake_pt = QtGui.QPainter(img)
    bake_pt.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    def assoc_target(i):
        ref = (alayers[i].get("associatedRef") or [None, None]) if i < len(alayers) else [None, None]
        if ref[0] == "path" and 0 <= ref[1] < len(kpaths):
            a, b = kp_name.get(kpaths[ref[1]]["startNodeLink"]), kp_name.get(kpaths[ref[1]]["endNodeLink"])
            return next((pid for pid, wk in walks if a in wk and b in wk and pid in open_set), None)
        if ref[0] == "node" and ref[1] in kp_name:
            q = kp_name[ref[1]]
            return next((pid for pid, wk in walks if wk[-1] == q and pid in open_set), None) or \
                next((pid for pid, wk in walks if wk[0] == q and pid in open_set), None)
        return None
    moving_art = 0
    for st in p.stickers:
        k = st.pop("_assoc", None)
        tgt = assoc_target(k) if k is not None else None
        if tgt:
            st["reveal"] = tgt
            moving_art += 1
    if moving_art:
        report["summary"].append("%d moving images belong to a path: the editor shows them when it opens; in game "
                                 "they move from the start (the world's Synchro model cannot wait for a path)." %
                                 moving_art)
    for i, (aimg, (ax, ay)) in sorted(assoc.items()):
        target = assoc_target(i)
        if target is None:
            bake_pt.drawImage(QtCore.QRectF((ax - ox) * pic_scale, (ay - oy) * pic_scale,
                                            aimg.width() * pic_scale, aimg.height() * pic_scale), aimg)
            baked += 1
            continue
        pdir.mkdir(exist_ok=True)
        f = pdir / ("art%03d_%s.png" % (i, target))
        aimg.save(str(f))
        p.stickers.insert(0, {"name": "path art %s" % target, "path": str(f.resolve()),
                              "x": T(ax + aimg.width() / 2.0, ox), "y": T(ay + aimg.height() / 2.0, oy),
                              "w": aimg.width() / float(TILE_PX), "h": aimg.height() / float(TILE_PX),
                              "anim": "none", "strip": 1, "speed": 1.0, "amount": 1.0, "reveal": target})
        revealed += 1
    bake_pt.end()
    img.save(str(pic_path))
    if revealed or baked:
        report["summary"].append("Path art: %d pieces appear when their path opens%s." % (
            revealed, (", %d on paths open from the start are part of the picture" % baked) if baked else ""))

    # map changes (Koopatlas jumps to another map) -> link points, named after where they lead
    links = []
    for i, n in enumerate(nodes):
        dest = n.get("mapChange")
        q = kp_name.get(i)
        if not dest or not q:
            continue
        dname = Path(dest).stem
        other = path.parent / (dname + ".kpmap")
        title = dname
        if other.exists():
            try:
                od = load(other)
                w0 = (od.get("worlds") or [{}])[0]
                title = "%s (%s, Newer world %s)" % (dname, w0.get("name", "?"), w0.get("worldID", "?"))
            except KoopatlasError:
                pass
        p.nodes[q]["note"] = "Koopatlas: to %s" % title
        links.append("%s -> %s" % (q, title))
    if links:
        report["summary"].append("Map changes: %s. In Deluxe a world leads on to the next from its castle (Map > Castle "
                                 "leads on to the next world); these points keep the name of where they went." %
                                 "; ".join(links))

    # music: Koopatlas worlds name a track; Deluxe plays one world map track per world (kdx: map music)
    if hook:
        marks = sorted(q for q in p.slot_points() if q.startswith("Fq"))
        if marks:
            plan = rules.slot_plan(world, list(p.game_nodes()[0]), rules.secret_used_of(p.point_rows()))
            short = sorted({c for (c, sc), v in plan.items() if v is None})
            report["summary"].append("Framework slot hook: %d levels past the tracked ones keep their clears in free "
                                     "entries of World %d (hidden points %s)%s." % (
                                         len(marks), world, ", ".join(marks),
                                         ("; no entry left for course %s" % ", ".join(map(str, short))) if short else ""))
    mt = int(wdef.get("musicTrackID") or 0)
    if 1 <= mt <= 9:
        p.music = mt
    elif mt:
        warn("music track %d is a Newer track: pick a Deluxe one in Map > Music" % mt)
    levels = sum(1 for q in p.nodes if rules.level_file(q))
    report["summary"] += [
        "%s: %d x %d tiles, %d points (%d levels, %d bends), %d routes, %d paths with unlocks, %d moving images." % (
            p.name, W, H, len(p.nodes), levels, sum(n["kind"] == "bend" for n in p.nodes.values()), len(p.routes),
            opened, len(p.stickers))]
    if pictures and p.stickers:
        report["summary"].append("Moving images keep their Koopatlas keyframes (position, angle, scale; opacity in "
                                 "the editor only).")
    return p, report


def _kind_of(pid):
    """The World Maker kind of a Deluxe point name."""
    from . import project2d
    if len(pid) == 4 and pid[0] == "W" and pid[2] == "S":            # the cannon (W1S0): a point with a course
        return "level"
    for kind, (pat, _d) in project2d.NODE_KINDS.items():
        pre = pat.split("{")[0]
        if kind == "level":
            if len(pid) == 4 and pid[0] == "W" and pid[2] == "0":
                return kind
            continue
        letter = pat.replace("{w}", "")[1:2] if pat.startswith("W") else None
        if pat.startswith("W") and letter and len(pid) == 4 and pid[2] == letter:
            return kind
        if not pat.startswith("W") and pid.startswith(pre):
            return kind
    return "stop"


def fix_directions(p, step=0.8, rounds=200):
    """Give one of every two roads that leave a point the same compass way (north / south / east / west) a bend a
    little to a free side, so the game keeps both (one link per direction, 0x60178558). Returns the bends added."""
    from . import rules
    ts = p.tile_size
    vec = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
    added = 0
    for _ in range(rounds):
        pos = {q: (n["x"] * ts, 0.0, n["y"] * ts) for q, n in p.nodes.items()}
        clashes = rules.heading_clashes([r["id"] for r in p.routes], pos)
        if not clashes:
            break
        (pt, h), rids = sorted(clashes.items())[0]
        used = {rules.heading(pos[pt], pos[o]) for r in p.routes for o in [x for x in rules.route_ends(r["id"]) if x != pt]
                if pt in rules.route_ends(r["id"])}
        free = [d for d in ("east", "west", "north", "south") if d not in used]
        if not free:
            break
        rid = rids[-1]
        a, b = rules.route_ends(rid)
        other = b if a == pt else a
        # the free side nearest the road's own direction
        ox, oy = p.nodes[other]["x"] - p.nodes[pt]["x"], p.nodes[other]["y"] - p.nodes[pt]["y"]
        d = max(free, key=lambda k: vec[k][0] * ox + vec[k][1] * oy)
        kx = p.nodes[pt]["x"] + vec[d][0] * step + ox * 0.15
        ky = p.nodes[pt]["y"] + vec[d][1] * step + oy * 0.15
        if rules.heading(pos[pt], (kx * ts, 0, ky * ts)) != d:   # keep the bend's own heading on the free side
            kx, ky = p.nodes[pt]["x"] + vec[d][0] * step, p.nodes[pt]["y"] + vec[d][1] * step
        r = next(x for x in p.routes if x["id"] == rid)
        k = p.add_node("bend", kx, ky)
        p.routes = [x for x in p.routes if x["id"] != rid]
        extra = {x: r[x] for x in ("kp", "hidden") if x in r}
        made = [p.add_route(pt, k, r["type"]), p.add_route(k, other, r["type"])] if a == pt else             [p.add_route(other, k, r["type"]), p.add_route(k, pt, r["type"])]
        for x in p.routes:
            if x["id"] in made:
                x.update(extra)
        added += 1
    return added

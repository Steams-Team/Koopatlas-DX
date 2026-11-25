"""
kdx/project2d.py - a 2D world made from tiles (Koopatlas DX's World Maker).

A project is a grid of tiles in layers, plus the world's points (nodes) and routes. Nodes carry the same data as a
point-table row (flags, nodes/paths/appear animations for both exits) and a camera offset, so a project can be checked
with the game's rules (kdx.rules) exactly like a real world. Saved as JSON (*.kdxmap).

Coordinates: tile (column, row); world units = tile * tile_size. One tile defaults to 30 units, about half of Mario
(an estimate from the map's own markers until Mario's world-map model is measured).
"""
from __future__ import annotations

import json
import math
import os
from collections import deque
from pathlib import Path

from . import rules

FORMAT = "koopatlasdx-map"
VERSION = 1
DEFAULT_TILE_SIZE = 30.0
DEFAULT_TIER_HEIGHT = 60.0   # one island tier: two tiles at the default tile size, like the reference maps' cliffs
MAX_TIER = 3
HIDDEN_DEPTH = 1000.0     # placeholders sit this far under the start point: there for the game's lookups, never seen

NODE_KINDS = {            # kind -> (ID pattern, description)
    "start": ("W{w}M0", "Start point (one per world)"),
    "level": ("W{w}0{n}", "Level 1-15"),
    "tower": ("W{w}T{n}", "Tower"),
    "castle": ("W{w}C{n}", "Castle"),
    "ghost": ("W{w}G{n}", "Ghost house"),
    "airship": ("W{w}A{n}", "Airship"),
    "toad": ("W{w}K{n}", "Toad house"),
    "link": ("W{w}J{n}", "Link to another world"),
    "stop": ("F{n3}", "Path stop"),
    "pipe": ("Fd{n2}", "Map pipe end"),
    "bend": ("K{n3}", "Bend in a route (no stop)"),
}


MUSIC_NAMES = {0: "The game's own"}
MUSIC_NAMES.update({n: "World %d map music" % n for n in range(1, 10)})


MUSIC_CUSTOM = 100           # music 100-199: custom song CUSTOM_BGM_<music - 100> (nsmbudx-music), hidden point Fn<nn>


def music_label(choice):
    """'The game's own', 'World 3 map music' or 'Custom song CUSTOM_BGM_07'."""
    choice = int(choice or 0)
    if choice >= MUSIC_CUSTOM:
        return "Custom song CUSTOM_BGM_%02d" % (choice - MUSIC_CUSTOM)
    return MUSIC_NAMES.get(choice, "World %d map music" % choice)


def music_point(choice):
    """The hidden point that tells the framework's map music hook what to play: Fm01-Fm09, a world's map track, or
    Fn00-Fn99, a custom song CUSTOM_BGM_<nn> added to Sound.bfsar with nsmbudx-music (nsmbud-reverse
    source/code/framework/map_music.cpp; SndBgmMgr's map music starter 0x6080D540)."""
    choice = int(choice)
    if choice >= MUSIC_CUSTOM:
        return "Fn%02d" % (choice - MUSIC_CUSTOM)
    return "Fm%02x" % choice


def custom_songs(bfsar):
    """The custom song numbers (CUSTOM_BGM_<nn>, added by nsmbudx-music) in a Sound.bfsar, or [] (no such file)."""
    import mmap
    import re as _re
    try:
        with open(bfsar, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            return sorted({int(x[11:13]) for x in _re.findall(rb"CUSTOM_BGM_\d\d", m)})
    except (OSError, ValueError):
        return []


def find_sound_archive(mod_root=None, romfs_root=None):
    """The Sound.bfsar a mod plays: <mod>/atmosphere/contents/<title>/romfs/Sound, <mod>/romfs/Sound or <mod>/Sound,
    else the game's own. None if there is none."""
    cands = []
    if mod_root:
        m = Path(mod_root)
        cands += [m / "atmosphere" / "contents" / "0100EA80032EA000" / "romfs" / "Sound" / "Sound.bfsar",
                  m / "romfs" / "Sound" / "Sound.bfsar", m / "Sound" / "Sound.bfsar", m / "Sound.bfsar"]
    if romfs_root:
        cands.append(Path(romfs_root) / "Sound" / "Sound.bfsar")
    return next((c for c in cands if c.is_file()), None)


def ask_custom_song(parent, current=0, mod_root=None, romfs_root=None):
    """Ask which custom song plays: a list of the CUSTOM_BGM songs in the mod's Sound.bfsar when it has some, else a
    number. Returns the number or None."""
    from PyQt5 import QtWidgets
    arc = find_sound_archive(mod_root, romfs_root)
    songs = custom_songs(arc) if arc else []
    if songs:
        items = ["CUSTOM_BGM_%02d" % n for n in songs]
        pick = "CUSTOM_BGM_%02d" % current
        item, ok = QtWidgets.QInputDialog.getItem(
            parent, "Custom map music", "Songs added with nsmbudx-music in %s:" % arc, items,
            items.index(pick) if pick in items else 0, False)
        return int(item[11:13]) if ok else None
    n, ok = QtWidgets.QInputDialog.getInt(
        parent, "Custom map music", "Song number (CUSTOM_BGM_<nn>, added to Sound.bfsar with nsmbudx-music; no "
        "Sound.bfsar with custom songs was found in the mod folder):", current, 0, 99)
    return n if ok else None


def music_of(names):
    """The music a map's point names ask for (0: the game's own)."""
    for n in names:
        if len(n) == 4 and n[:2] == "Fn" and n[2:].isdigit():
            return MUSIC_CUSTOM + int(n[2:])
    for n in names:
        if len(n) == 4 and n[:2] == "Fm":
            try:
                v = int(n[2:], 16)
            except ValueError:
                continue
            if 1 <= v <= 9:
                return v
    return 0


class ProjectError(ValueError):
    pass


class Layer:
    def __init__(self, name, width, height, data=None, visible=True, locked=False):
        self.name = name
        self.visible = visible
        self.locked = locked
        self.data = list(data) if data is not None else [-1] * (width * height)

    def to_json(self):
        return {"name": self.name, "visible": self.visible, "locked": self.locked, "data": self.data}


class Project:
    def __init__(self, width=96, height=64, tile_size=DEFAULT_TILE_SIZE, world=1, tileset="starter", name="New world"):
        if not (8 <= width <= 512 and 8 <= height <= 512):
            raise ProjectError("Maps are 8 to 512 tiles on each side.")
        self.name = name
        self.width, self.height = int(width), int(height)
        self.tile_size = float(tile_size)
        self.world = int(world)
        self.variant = ""        # "", "a" or "b": with the world number, the archive replaced (see slot)
        self.link_next = True    # the export adds the game's way on to the next world (export2d.world_link)
        self.tileset = tileset
        self.layers = [Layer(n, self.width, self.height) for n in ("Ground", "Paths", "Decoration")]
        self.heights = None      # ground height in world units at each tile corner, (width+1)*(height+1), or None: flat
        self.tiers = None        # island tier per tile (0-3, Koopatlas-style raised islands, kdx.islands), or None
        self.tier_height = DEFAULT_TIER_HEIGHT
        self.theme = "grass"     # cliff look of the islands (kdx.islands.THEMES)
        self.island_round = False  # rounded island corners (kdx.islands.tile_outline) and their shadows on the sea
        self.sea_colour = ""     # what surrounds the map ("" = the editor's dark blue); a theme preset sets it
        self.road_colour = ""    # painted roads along the routes ("" = none): Map > Paint roads, or a theme preset
        self.picture = None      # your own drawn/painted map as the ground: {"path"} (kdx.pictures), or None: tiles only
        self.music = 0           # the map's music: 0 the game's own, 1-9 that world's map music (hidden point Fm0n)
        self.slot_hook = False   # more levels than the save tracks: the framework's slot hook (hidden points Fq/Fr, rules)
        self.partner_names = []  # point names of the world's other map (W5a/W5b...): the slot hook plans both
        self.link_add = []       # junction roads in the next world the castle opens too
        self.link_drop = []      # names the export leaves out of the vanilla way on (not in the next world's map)
        self.depth_stretch = 1.0  # export only: the map is stretched this much front to back (z), so a picture drawn
                                  # for a straight-down view (Koopatlas) keeps its shapes under the game's tilted camera
        self.reveal_style = "fade"  # path art: "fade" in at once, or "sweep" along its path (kdx.pictures.reveal_parts)
        self.stickers = []       # images on the map: {"name", "path", "x", "y", "w", "anim", "strip", "speed", "amount"}
        self.model3d = None      # the 3D ground model: a kdx.modelref reference with "offset" (OBJ, DAE, FBX, Wii)
        self._model = None       # the loaded, placed model (not saved)
        self.objects = []        # scenery: kdx.modelref references with "name" and "position" (castles, houses...)
        self.nodes = {}          # id -> {"kind", "x", "y", "row": [9 cells], "camera": [x, y, z]}
        self.routes = []         # [{"id", "type"}]
        self.path = None

    # ---- tiles ------------------------------------------------------------------------------------------------------
    def inside(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, layer, x, y):
        return self.layers[layer].data[y * self.width + x] if self.inside(x, y) else -1

    def set_many(self, layer, cells):
        """cells: {(x, y): tile}. Returns {(x, y): (old, new)} for the cells that changed (for undo)."""
        lay = self.layers[layer]
        changes = {}
        for (x, y), t in cells.items():
            if not self.inside(x, y):
                continue
            i = y * self.width + x
            if lay.data[i] != t:
                changes[(x, y)] = (lay.data[i], t)
                lay.data[i] = t
        return changes

    def apply(self, layer, changes, undo=False):
        lay = self.layers[layer]
        for (x, y), (old, new) in changes.items():
            lay.data[y * self.width + x] = old if undo else new

    def stamp_cells(self, x, y, pattern):
        """pattern: list of rows of tile ids (a palette selection), placed with its top-left at (x, y)."""
        return {(x + dx, y + dy): t for dy, row in enumerate(pattern) for dx, t in enumerate(row)}

    def rect_cells(self, x0, y0, x1, y1, pattern):
        """Fill a rectangle by repeating the pattern."""
        xa, xb = sorted((x0, x1)); ya, yb = sorted((y0, y1))
        ph, pw = len(pattern), len(pattern[0])
        return {(x, y): pattern[(y - ya) % ph][(x - xa) % pw] for y in range(ya, yb + 1) for x in range(xa, xb + 1)}

    def fill_cells(self, layer, x, y, tile, limit=262144):
        """Flood fill (4-way) of the region of equal tiles that contains (x, y)."""
        if not self.inside(x, y):
            return {}
        target = self.get(layer, x, y)
        if target == tile:
            return {}
        cells, seen, q = {}, {(x, y)}, deque([(x, y)])
        while q and len(cells) < limit:
            cx, cy = q.popleft()
            cells[(cx, cy)] = tile
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if (nx, ny) not in seen and self.inside(nx, ny) and self.get(layer, nx, ny) == target:
                    seen.add((nx, ny))
                    q.append((nx, ny))
        return cells

    def resize(self, width, height):
        if not (8 <= width <= 512 and 8 <= height <= 512):
            raise ProjectError("Maps are 8 to 512 tiles on each side.")
        for lay in self.layers:
            new = [-1] * (width * height)
            for y in range(min(height, self.height)):
                for x in range(min(width, self.width)):
                    new[y * width + x] = lay.data[y * self.width + x]
            lay.data = new
        if self.heights is not None:
            old_w = self.width + 1
            self.heights = [self.heights[j * old_w + i] if i < old_w and j <= self.height else 0.0
                            for j in range(height + 1) for i in range(width + 1)]
        if self.tiers is not None:
            self.tiers = [self.tiers[y * self.width + x] if x < self.width and y < self.height else 0
                          for y in range(height) for x in range(width)]
        self.width, self.height = width, height
        for n in self.nodes.values():
            n["x"] = min(n["x"], width - 0.5)
            n["y"] = min(n["y"], height - 0.5)

    # ---- layers -----------------------------------------------------------------------------------------------------
    def add_layer(self, name, index=None):
        lay = Layer(name, self.width, self.height)
        self.layers.insert(len(self.layers) if index is None else index, lay)
        return lay

    def remove_layer(self, index):
        if len(self.layers) <= 1:
            raise ProjectError("A map needs at least one layer.")
        return self.layers.pop(index)

    # ---- nodes and routes ------------------------------------------------------------------------------------------
    def next_id(self, kind):
        pattern = NODE_KINDS[kind][0]
        for n in range(0, 4096):
            if "{n}" in pattern and n > 15:
                break
            cand = pattern.format(w=self.world, n=("%x" % n) if "{n}" in pattern else "", n2="%02x" % n, n3="%03x" % n)
            if kind == "level" and n == 0:
                continue                                         # levels are W?01 … W?0f
            if kind in ("stop", "pipe") and not cand[-1:].isdigit():
                continue                                         # a path end's name must end in a digit (rules.path_bone_ok)
            if cand not in self.nodes and len(cand) == 4:
                return cand
        raise ProjectError("No free ID left for a %s." % kind)

    def add_node(self, kind, x, y, pid=None):
        pid = pid or self.next_id(kind)
        if pid in self.nodes:
            raise ProjectError("%s already exists." % pid)
        if len(pid) != 4:
            raise ProjectError("Point names are 4 characters.")
        self.nodes[pid] = {"kind": kind, "x": float(x), "y": float(y), "row": ["", pid, "", "", "", "", "", "", ""],
                           "camera": [0.0, 0.0, 0.0]}
        return pid

    def remove_node(self, pid):
        node = self.nodes.pop(pid)
        removed = [r for r in self.routes if pid in rules.route_ends(r["id"])]
        self.routes = [r for r in self.routes if pid not in rules.route_ends(r["id"])]
        return node, removed

    def rename_node(self, old, new):
        if new in self.nodes or len(new) != 4:
            raise ProjectError("%s is taken or not 4 characters." % new)
        node = self.nodes.pop(old)
        node["row"][1] = new
        self.nodes[new] = node
        for r in self.routes:
            a, b = rules.route_ends(r["id"])
            if old in (a, b):
                r["id"] = "R%s%s" % (new if a == old else a, new if b == old else b)
        for n in self.nodes.values():
            for col in range(3, 9):
                cell = n["row"][col]
                if old in cell:
                    n["row"][col] = cell.replace(old, new)

    def add_route(self, a, b, rtype="道"):
        rid = "R%s%s" % (a, b)
        if a not in self.nodes or b not in self.nodes:
            raise ProjectError("Both ends of a route must be nodes.")
        if a == b or any(r["id"] == rid for r in self.routes):
            raise ProjectError("%s already exists or joins a node to itself." % rid)
        self.routes.append({"id": rid, "type": rtype})
        return rid

    def world_position(self, pid, height=None):
        n = self.nodes[pid]
        if "pos" in n:                                           # a point copied from a template at its own place
            return tuple(n["pos"])
        return (n["x"] * self.tile_size, self.height_at(n["x"], n["y"]) if height is None else height, n["y"] * self.tile_size)

    # ---- terrain height ------------------------------------------------------------------------------------------------
    def ground_corner(self, i, j):
        """The hills alone at a tile corner (what the tiers stand on)."""
        return self.corner(i, j)

    def tier(self, x, y):
        if not self.tiers or not (0 <= x < self.width and 0 <= y < self.height):
            return 0
        return self.tiers[int(y) * self.width + int(x)]

    def set_tiers(self, cells, level):
        """Raise/lower tiles to an island tier. Returns {(x, y): (old, new)} for undo."""
        level = max(0, min(MAX_TIER, int(level)))
        if self.tiers is None:
            self.tiers = [0] * (self.width * self.height)
        out = {}
        for (x, y) in cells:
            if self.inside(x, y):
                k = y * self.width + x
                if self.tiers[k] != level:
                    out[(x, y)] = (self.tiers[k], level)
                    self.tiers[k] = level
        return out

    def apply_tiers(self, changes, undo=False):
        if self.tiers is None:
            self.tiers = [0] * (self.width * self.height)
        for (x, y), (old, new) in changes.items():
            self.tiers[y * self.width + x] = old if undo else new

    def corner(self, i, j):
        if self.heights is None:
            return 0.0
        i, j = max(0, min(self.width, i)), max(0, min(self.height, j))
        return self.heights[j * (self.width + 1) + i]

    def set_model3d(self, path, z_up=False, scale=None, offset=None, keep_edits=None):
        """Use a 3D model (OBJ, DAE, FBX or a Wii .brres/.arc) as the world's ground. By default it is fitted into the
        map: its x/z extent inside the map, its corner at the map's corner, its lowest point at height 0. keep_edits:
        a previous reference whose rotation, hidden materials and material edits carry over."""
        from . import modelref, objmodel
        path = Path(path)
        if not path.is_absolute() and self.path:
            path = Path(self.path).parent / path
        try:
            model = modelref.load_raw(path, z_up)
        except (objmodel.ObjError, OSError) as e:
            raise ProjectError(str(e))
        if scale is None or offset is None:
            scale, offset = model.fit(self.width * self.tile_size, self.height * self.tile_size)
        ref = {"path": str(path.resolve()), "scale": float(scale) if isinstance(scale, (int, float)) else [float(c) for c in scale],
               "offset": [float(c) for c in offset], "z_up": bool(z_up)}
        for k in ("rotation", "hidden", "edits"):
            if keep_edits and k in keep_edits:
                ref[k] = keep_edits[k]
        self.model3d = ref
        self._model = modelref.build(ref)
        return self._model

    def terrain_model(self):
        """The placed 3D ground model, or None (loaded on first use)."""
        if self.model3d is None:
            return None
        if self._model is None:
            m = self.model3d
            self.set_model3d(m["path"], m.get("z_up", False), m["scale"], m["offset"], keep_edits=m)
        return self._model

    def update_model3d(self, ref):
        """Replace the ground reference (an edited copy of model3d) and rebuild the placed model."""
        from . import modelref
        self.model3d = ref
        self._model = None if ref is None else modelref.build(ref, Path(self.path).parent if self.path else None)

    # ---- scenery objects ---------------------------------------------------------------------------------------------
    def add_object(self, path, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), scale=1.0, z_up=False, name=None):
        """Place a model as a scenery object (it is loaded to check it). Returns its index."""
        from . import modelref, objmodel
        path = Path(path)
        try:
            modelref.load_raw(path, z_up)
        except (objmodel.ObjError, OSError) as e:
            raise ProjectError(str(e))
        base = name or path.stem
        names = {o["name"] for o in self.objects}
        nm, k = base, 2
        while nm in names:
            nm, k = "%s %d" % (base, k), k + 1
        self.objects.append({"name": nm, "path": str(path.resolve()), "z_up": bool(z_up),
                             "position": [float(c) for c in position], "rotation": [float(c) for c in rotation],
                             "scale": float(scale) if isinstance(scale, (int, float)) else [float(c) for c in scale]})
        return len(self.objects) - 1

    def object_model(self, i):
        """Object i placed in the world (ObjModel), or None if its file cannot be read."""
        from . import modelref, objmodel
        try:
            return modelref.build(self.objects[i], Path(self.path).parent if self.path else None)
        except (objmodel.ObjError, OSError):
            return None

    def scene_model(self, map_png=None):
        """Ground model and every scenery object as one ObjModel (what a 3D world exports), or None for a painted map
        without objects. Object parts are named '<object>/<material>'. map_png: the painted map as an image file, for
        the tops of the raised islands (kdx.islands), which are added when the map has tiers."""
        from .objmodel import ObjModel
        parts = []
        if map_png and self.tiers and any(self.tiers) and not self.model3d:
            from . import islands
            parts += islands.island_parts(self, map_png)
        try:
            ground = self.terrain_model() if self.model3d else None
        except ProjectError:
            ground = None
        if ground is not None:
            parts += list(ground.parts)
        for i, o in enumerate(self.objects):
            m = self.object_model(i)
            if m is None:
                continue
            for p in m.parts:
                q = p.copy()
                q.material = "%s/%s" % (o["name"], p.material)
                parts.append(q)
        return ObjModel(parts) if parts else None

    def stretched(self, k=None):
        """A copy stretched k times front to back (rows, points, images, heights): what the export builds from when
        depth_stretch is set. Newer's Koopatlas draws its maps for a straight-down orthographic camera
        (Kamek/src/koopatlas/camera.cpp); Deluxe's map camera looks down at a slant, which squashes everything to about
        half its height (circles, fences and tree tops in the first in-game test). k=2 brings the shapes back."""
        import copy as _copy
        from . import pictures as _pictures
        k = float(self.depth_stretch if k is None else k)
        if abs(k - 1.0) < 1e-6:
            return self
        q = _copy.deepcopy(self)
        q._model = None
        H = max(8, min(512, int(round(self.height * k))))
        k = H / float(self.height)
        rows = [min(self.height - 1, int(j / k)) for j in range(H)]
        for lay in q.layers:
            lay.data = [lay.data[r * self.width + x] for r in rows for x in range(self.width)]
        if self.tiers:
            q.tiers = [self.tiers[r * self.width + x] for r in rows for x in range(self.width)]
        if self.heights is not None:
            W1 = self.width + 1
            q.heights = [self.corner(i, min(self.height, int(round(j / k)))) for j in range(H + 1) for i in range(W1)]
        q.height = H
        for n in q.nodes.values():
            n["y"] = n["y"] * k
        for s in q.stickers:
            if not s.get("h"):
                frames, _d = _pictures.load_frames(s["path"], s.get("strip", 1))
                w = float(s.get("w", 3.0))
                s["h"] = w * frames[0].height() / max(1, frames[0].width()) if frames else w
            s["y"] = s["y"] * k
            s["h"] = float(s["h"]) * k
            for tr in s.get("tracks", []):
                if len(tr) > 5 and tr[3] == "Y Position":
                    tr[4], tr[5] = float(tr[4]) * k, float(tr[5]) * k
        for o in q.objects:
            if "position" in o and len(o["position"]) == 3:
                o["position"] = [o["position"][0], o["position"][1], o["position"][2] * k]
        q.depth_stretch = 1.0
        return q

    def height_at(self, x, y):
        """Ground height at a point given in tiles: the 3D model's surface there if the map has one, else bilinear
        between the tile's corner heights."""
        try:
            model = self.terrain_model() if self.model3d else None
        except ProjectError:                                     # model file gone: flat until it is found again
            model = None
        if model is not None:
            h = model.height_at(x * self.tile_size, y * self.tile_size)
            return 0.0 if h is None else h
        lift = 0.0
        if self.tiers:
            cx, cy = min(max(int(x), 0), self.width - 1), min(max(int(y), 0), self.height - 1)
            lift = self.tiers[cy * self.width + cx] * self.tier_height
        return lift + self.ground_height_at(x, y)

    def ground_height_at(self, x, y):
        """The hills alone (no island tiers): the painted ground's own surface."""
        if self.heights is None:
            return 0.0
        x, y = max(0.0, min(float(self.width), x)), max(0.0, min(float(self.height), y))
        i, j = min(int(x), self.width - 1), min(int(y), self.height - 1)
        fx, fy = x - i, y - j
        top = self.corner(i, j) * (1 - fx) + self.corner(i + 1, j) * fx
        bottom = self.corner(i, j + 1) * (1 - fx) + self.corner(i + 1, j + 1) * fx
        return top * (1 - fy) + bottom * fy

    def height_dab(self, x, y, radius, amount=0.0, flatten_to=None):
        """Raise (amount > 0), lower (< 0) or flatten (flatten_to) the corners within radius tiles of (x, y), with a
        smooth falloff. Returns {(i, j): (old, new)} for the corners that changed (for undo)."""
        if self.heights is None:
            self.heights = [0.0] * ((self.width + 1) * (self.height + 1))
        changes = {}
        r = max(0.5, float(radius))
        for j in range(max(0, int(y - r)), min(self.height, int(y + r) + 1) + 1):
            for i in range(max(0, int(x - r)), min(self.width, int(x + r) + 1) + 1):
                d = math.hypot(i - x, j - y)
                if d > r:
                    continue
                w = 0.5 + 0.5 * math.cos(math.pi * d / r)        # 1 in the middle, 0 at the edge
                k = j * (self.width + 1) + i
                old = self.heights[k]
                new = old + (flatten_to - old) * w if flatten_to is not None else old + amount * w
                if abs(new - old) > 1e-6:
                    self.heights[k] = new
                    changes[(i, j)] = (old, new)
        return changes

    def apply_heights(self, changes, undo=False):
        if self.heights is None:
            self.heights = [0.0] * ((self.width + 1) * (self.height + 1))
        for (i, j), (old, new) in changes.items():
            self.heights[j * (self.width + 1) + i] = old if undo else new

    # ---- the game's tables -----------------------------------------------------------------------------------------
    @property
    def slot(self):
        """The archive this map replaces: W1, W2a, … (rules.SLOTS)."""
        return rules.slot_of(self.world, self.variant)

    def start_node(self):
        return next((pid for pid, n in self.nodes.items() if n["kind"] == "start"), None)

    def needed_points(self, slot=None):
        """[(name, kind, flags, why)] of the points this map still lacks that the game needs: the entry point, the
        castle when it leads on to the next world, and every name / flag the game's code looks up without checking
        (the export would add those hidden; placed with add_needed_points they are yours to move and connect)."""
        slot = slot or self.slot
        need_points, need_flags = rules.required_names(slot)

        out = []
        if self.start_node() is None:
            out.append((None, "start", "", "the map's entry point: Mario starts here"))
        if self.link_next and not any(n["kind"] == "castle" for n in self.nodes.values()):
            out.append((None, "castle", "", "the castle: beating it leads on to the next world"))
        for pid, why in need_points.items():
            if pid not in self.nodes:
                out.append((pid, "stop" if pid[0] == "F" else "level", "", why))
        carried = {f for n in self.nodes.values() for f in rules.split(n["row"][2] if len(n["row"]) > 2 else "")}
        for flag, why in need_flags.items():
            if flag not in carried:
                out.append((None, "stop", flag, why))
        return out

    def add_needed_points(self, slot=None, at=None):
        """Place every point needed_points lists, in a column (2.5 tiles apart) from `at` (tiles; default: right of the
        start point, else the middle of the map). Returns [(name, why)]."""
        need = self.needed_points(slot)
        start = self.start_node()
        if at is None:
            at = (self.nodes[start]["x"] + 2.0, self.nodes[start]["y"]) if start else (self.width / 2.0, self.height / 2.0)
        x0, y0 = at
        added = []
        for i, (pid, kind, flags, why) in enumerate(need):
            x = min(self.width - 0.5, max(0.5, x0 + 12.0 * (i // 8)))
            y = min(self.height - 0.5, max(0.5, y0 + 2.5 * (i % 8)))
            q = self.add_node(kind, x, y, pid)
            if flags:
                self.nodes[q]["row"][2] = flags
            added.append((q, why))
        return added

    def game_nodes(self, slot=None):
        """The nodes the game gets: this map's, plus a hidden placeholder for every name the game's code looks up
        without checking (rules.REQUIRED_POINTS, rules.ENEMY_FLAGS, rules.COMPANION_FLAGS) that the map lacks. Placeholders sit under the start
        point with no route to them. Returns (nodes, {placeholder id: why})."""
        slot = slot or self.slot
        need_points, need_flags = rules.required_names(slot)
        nodes, hidden = dict(self.nodes), {}
        start = self.start_node()
        sx, sy = (self.nodes[start]["x"], self.nodes[start]["y"]) if start else (0.5, 0.5)

        def add(pid, kind, flags, why):
            nodes[pid] = {"kind": kind, "x": sx, "y": sy, "row": ["", pid, flags, "", "", "", "", "", ""],
                          "camera": [0.0, 0.0, 0.0], "hidden": True}
            hidden[pid] = why
        for pid, why in need_points.items():
            if pid not in nodes:
                add(pid, "stop" if pid[0] == "F" else "level", "", why)
        carried = {f for n in self.nodes.values() for f in rules.split(n["row"][2] if len(n["row"]) > 2 else "")}
        free = (pid for pid in ("Fz%02x" % i for i in range(256)) if pid not in nodes)
        for flag, why in need_flags.items():
            if flag not in carried:
                add(next(free), "stop", flag, why)
        if self.music:                                           # read by the framework's map music hook
            add(music_point(self.music), "stop", "", "the map's music (%s)" % music_label(self.music))
        if self.slot_hook:                                       # read by the framework's slot hook (course_slots.cpp)
            for pid, why in self.slot_points(slot, nodes, hidden).items():
                add(pid, "stop", "", why)
        return nodes, hidden

    def slot_points(self, slot=None, nodes=None, hidden=None):
        """{hidden point: why} the framework slot hook reads: Fq<course> for every level whose course the save does
        not track, Fr<course> for placeholders the player never reaches (their entries may be lent)."""
        slot = slot or self.slot
        if nodes is None:
            nodes, hidden = self.game_nodes(slot)
        out = {}
        real = [q for q in self.nodes if self.nodes[q]["kind"] != "bend"]
        # only levels whose exits open something need a clear (the free entries are few: World 1 has four)
        openers = [q for q in real if any(self.nodes[q]["row"][k] for k in (3, 4, 6, 7) if len(self.nodes[q]["row"]) > k)]
        for c in rules.untracked_levels(self.world, openers):
            if not (23 <= c <= 30 or 46 <= c <= 53):             # Toad houses need no clear
                out[rules.slot_marker(c)] = "keeps course %d's clear (framework slot hook)" % c
        for pid in hidden or {}:
            c = rules.course_index(pid)
            if pid[:1] == "W" and c >= 0 and c != 37:
                out[rules.lend_marker(c)] = "%s is a placeholder: its clear entry may be lent (slot hook)" % pid
        enemy_flags = set(rules.ENEMY_FLAGS.get(slot, ()))
        carried = {f_ for q in real for f_ in rules.split(self.nodes[q]["row"][2] if len(self.nodes[q]["row"]) > 2 else "")}
        if enemy_flags and not (enemy_flags & carried):          # the map enemies stand on hidden points only
            for c in (31, 32, 33, 54, 55, 56):
                if rules.has_slot(self.world, c):
                    out[rules.lend_marker(c)] = "the map enemies are placeholders: enemy course %d may be lent" % c
        return out

    def bone_layout(self, slot=None):
        """The map-point model's bones in the order the game reads paths from (rules.map_paths): F and K points on no
        path's walk, then each path bone R<from><to> followed by its walk, then the W points and the placeholders.
        Returns (bone names in order, path list, nodes, hidden) with nodes/hidden as in game_nodes."""
        nodes, hidden = self.game_nodes(slot)
        path_list = rules.map_paths(self.point_rows(nodes), self.route_rows())
        on_walk = {p for _, walk in path_list for p in walk}
        order = [p for p in nodes if p[0] in "FK" and p not in on_walk and p not in hidden]
        for path, walk in path_list:
            order.append(path)
            order += [p for p in walk if p not in order]
        # W points close the last walk (an F or K right after it would join it); placeholders come last
        rest = [p for p in nodes if p not in order]
        order += sorted(rest, key=lambda p: (p in hidden, p[0] != "W"))
        return order, path_list, nodes, hidden

    def walk_plan(self, slot=None):
        """The paths in the order a play-through opens them, for the World Maker's walk test: [(label, [points])].
        Paths open from the start come first (nearest first), then each course's unlock paths (normal exit, then
        secret) in the order the courses are reached. Points are this map's own; walks onto another map are left out."""
        order, path_list, nodes, hidden = self.bone_layout(slot)
        walks = {p: [p[1:5]] + w + [p[5:9]] for p, w in path_list}
        owner = {}
        for pid, n in self.nodes.items():
            for col, exit_ in ((4, "normal"), (7, "secret")):
                for p in rules.paths(n["row"][col] if len(n["row"]) > col else ""):
                    owner.setdefault(p, (pid, exit_))
        plan, done, reached = [], set(), []
        start = self.start_node()
        frontier = [start] if start else []
        while frontier:
            here = frontier.pop(0)
            if here in reached:
                continue
            reached.append(here)
            ready = [p for p, w in walks.items() if p not in done and here in (w[0], w[-1]) and p not in owner]
            ready += [p for p, (o, _) in owner.items() if o == here and p in walks and p not in done]
            for p in ready:
                done.add(p)
                w = walks[p] if walks[p][0] == here or walks[p][-1] != here else walks[p][::-1]
                o = owner.get(p)
                plan.append(("%s (%s)" % (p, "opened by %s, %s exit" % o if o else "open from the start"),
                             [q for q in w if q in self.nodes]))
                frontier += [q for q in (w[0], w[-1]) if q in self.nodes and q not in reached]
        return plan

    def point_rows(self, nodes=None):
        rows = []
        for pid, n in (self.nodes if nodes is None else nodes).items():
            if n["kind"] == "bend":
                continue                                         # bends exist only as bones and in route ids
            r = list(n["row"]) + [""] * (9 - len(n["row"]))
            r[0], r[1] = str(len(rows)), pid
            rows.append(r[:9])
        return rows

    def route_rows(self):
        return [[r["id"], r["type"], ""] for r in self.routes]

    def check(self, slot=None):
        """The game's rules (kdx.rules) applied to the tables an export would write (placeholders included)."""
        import csv as _csv
        import io as _io

        def size(rows):
            b = _io.StringIO(); _csv.writer(b, lineterminator="\r\n").writerows(rows)
            return len(b.getvalue().encode("utf-8"))
        slot = slot or self.slot
        order, path_list, nodes, hidden = self.bone_layout(slot)
        prow, rrow = self.point_rows(nodes), self.route_rows()
        bones = set(order) | rules.connect_bones(self.world)     # the export copies the template's world-link bones
        positions = {pid: tuple(n["pos"]) if "pos" in n else
                     (n["x"] * self.tile_size, self.height_at(n["x"], n["y"]), n["y"] * self.tile_size) for pid, n in nodes.items()}
        rep = rules.check(prow, rrow, size(prow), size(rrow), bones, self.world, slot[2:], hidden=set(hidden),
                          bone_order=order, positions=positions, partner_names=self.partner_names)
        rep.errors += rules.path_problems(path_list)
        if path_list:
            rep.summary.append("%d paths: %s." % (len(path_list), ", ".join(p for p, _ in path_list)))
        for pid, why in hidden.items():
            rep.summary.append("Added on export, hidden: %s (%s)." % (pid, why))
        return rep

    # ---- save / load -----------------------------------------------------------------------------------------------
    def to_json(self):
        return {"format": FORMAT, "version": VERSION, "name": self.name, "width": self.width, "height": self.height,
                "tile_size": self.tile_size, "world": self.world, "variant": self.variant, "link_next": self.link_next, "tileset": self.tileset,
                "layers": [l.to_json() for l in self.layers], "nodes": self.nodes, "routes": self.routes,
                **({"heights": self.heights} if self.heights is not None and any(self.heights) else {}),
                **({"tiers": self.tiers, "tier_height": self.tier_height} if self.tiers and any(self.tiers) else {}),
                **({"theme": self.theme} if self.theme != "grass" else {}),
                **({"island_round": True} if self.island_round else {}),
                **({"sea_colour": self.sea_colour} if self.sea_colour else {}),
                **({"road_colour": self.road_colour} if self.road_colour else {}),
                **({"picture": self._relative(self.picture)} if self.picture else {}),
                **({"stickers": [self._relative(s) for s in self.stickers]} if self.stickers else {}),
                **({"music": self.music} if self.music else {}),
                **({"slot_hook": True} if self.slot_hook else {}),
                **({"depth_stretch": self.depth_stretch} if self.depth_stretch != 1.0 else {}),
                **({"partner_names": self.partner_names} if self.partner_names else {}),
                **({"link_drop": self.link_drop} if self.link_drop else {}),
                **({"link_add": self.link_add} if self.link_add else {}),
                **({"reveal_style": self.reveal_style} if self.reveal_style != "fade" else {}),
                **({"model3d": self._model3d_for_saving()} if self.model3d else {}),
                **({"objects": [self._relative(o) for o in self.objects]} if self.objects else {})}

    def _relative(self, ref):
        """A model reference with its path relative to the map file when it can be (so both can move together)."""
        m = dict(ref)
        if self.path:
            try:
                m["path"] = os.path.relpath(m["path"], Path(self.path).resolve().parent)
            except ValueError:                                   # another drive: keep the full path
                pass
        return m

    def _model3d_for_saving(self):
        return self._relative(self.model3d)

    def save(self, path):
        path = Path(path)
        self.path = path                                         # first: the 3D model's path is saved relative to it
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise ProjectError("%s is not a Koopatlas DX map." % Path(path).name)
        if data.get("version", 0) > VERSION:
            raise ProjectError("This map was made with a newer Koopatlas DX.")
        p = cls(data["width"], data["height"], data["tile_size"], data["world"], data.get("tileset", "starter"), data.get("name", "World"))
        p.variant = data.get("variant", "")
        p.link_next = data.get("link_next", True)
        p.layers = []
        for l in data["layers"]:
            if len(l["data"]) != p.width * p.height:
                raise ProjectError("Layer %r has the wrong size." % l["name"])
            p.layers.append(Layer(l["name"], p.width, p.height, l["data"], l.get("visible", True), l.get("locked", False)))
        p.nodes = data["nodes"]
        p.routes = data["routes"]
        p.model3d = data.get("model3d")
        p.objects = []
        for o in data.get("objects", []):
            o = dict(o)
            if not Path(o["path"]).is_absolute():
                o["path"] = str((Path(path).resolve().parent / o["path"]).resolve())
            p.objects.append(o)
        t = data.get("tiers")
        if t is not None:
            if len(t) != p.width * p.height:
                raise ProjectError("The island tier map has the wrong size.")
            p.tiers = [int(v) for v in t]
            p.tier_height = float(data.get("tier_height", DEFAULT_TIER_HEIGHT))
        p.theme = data.get("theme", "grass")
        p.island_round = bool(data.get("island_round", False))
        p.sea_colour = data.get("sea_colour", "") or ""
        p.road_colour = data.get("road_colour", "") or ""

        def absolute(ref):
            ref = dict(ref)
            if not Path(ref["path"]).is_absolute():
                ref["path"] = str((Path(path).resolve().parent / ref["path"]).resolve())
            return ref
        p.picture = absolute(data["picture"]) if data.get("picture") else None
        p.stickers = [absolute(s) for s in data.get("stickers", [])]
        p.music = int(data.get("music", 0) or 0)
        p.slot_hook = bool(data.get("slot_hook", False))
        p.depth_stretch = float(data.get("depth_stretch", 1.0) or 1.0)
        p.partner_names = list(data.get("partner_names", []))
        p.link_drop = list(data.get("link_drop", []))
        p.link_add = list(data.get("link_add", []))
        p.reveal_style = data.get("reveal_style", "fade") if data.get("reveal_style") in ("fade", "sweep") else "fade"
        h = data.get("heights")
        if h is not None:
            if len(h) != (p.width + 1) * (p.height + 1):
                raise ProjectError("The height map has the wrong size.")
            p.heights = [float(v) for v in h]
        p.path = Path(path)
        return p

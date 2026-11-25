"""
kdx/world.py - one world-map archive (CS_W<n>[a|b].Nin_NX_NVN.szs) as an editable world.

What it knows (all verified against the game's files):
  - pointW*.csv: 9 columns per row (index, ID, flags, unlock targets, unlock routes, -, unlock targets 2, routes 2, -)
  - routeW*.csv: route ID ("R" + start + end, 9 characters), type, empty column
  - the map-point model (CS_W<n>[a|b]) has one bone per point and per path waypoint (K###); its world position is
    where the point is
  - MarioOffsetDataW<n>.bin: one record per point, in point-table order (slots 0-127 first archive, 128-255 "b")

Edits are kept in memory and only written by export(), never to the files that were opened.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import struct
from pathlib import Path

from . import archive, bfres, offsets, rules

TITLE_ID = "0100EA80032EA000"
WORLD_RE = re.compile(r"CS_(W(\d)([ab]?))[._]", re.IGNORECASE)
POINT_COLUMNS = [rules.COLUMNS[i] for i in range(9)]
ROUTE_TYPES_KNOWN = rules.ROUTE_TYPES


class WorldError(ValueError):
    pass


# --------------------------------------------------------------------------------------------- CSV helpers
def csv_style(blob):
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            text = blob.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-8-sig" and not blob.startswith(b"\xef\xbb\xbf"):
            enc = "utf-8"
        return {"encoding": enc, "newline": "\r\n" if "\r\n" in text else "\n"}
    return {"encoding": "utf-8", "newline": "\r\n"}


def csv_decode(blob):
    st = csv_style(blob)
    return [list(r) for r in csv.reader(blob.decode(st["encoding"]).splitlines())]


def csv_encode(rows, style):
    buf = io.StringIO()
    csv.writer(buf, lineterminator=style["newline"]).writerows(rows)
    try:
        return buf.getvalue().encode(style["encoding"])
    except UnicodeEncodeError:
        raise WorldError("The text contains characters the original file's encoding (%s) cannot store." % style["encoding"])


def point_csv_encode(rows, style):
    """A point table quoted the way Nintendo's files are (all twelve vanilla tables come back byte-identical): flags
    quoted when they hold a comma, the node list quoted when it or the path list is set, the path lists quoted when
    set, and the secret-exit node list always quoted."""
    lines = []
    for r in rows:
        c = list(r) + [""] * (9 - len(r))
        if "," in c[2]:
            c[2] = '"%s"' % c[2]
        if c[3] or c[4]:
            c[3] = '"%s"' % c[3]
        for i in (4, 7):
            if c[i]:
                c[i] = '"%s"' % c[i]
        c[6] = '"%s"' % c[6]
        lines.append(",".join(c[:9]))
    text = "".join(line + style["newline"] for line in lines)
    try:
        return text.encode(style["encoding"])
    except UnicodeEncodeError:
        raise WorldError("The text contains characters the original file's encoding (%s) cannot store." % style["encoding"])


def mod_model_dir(mod_root):
    """The romfs/Model folder of a mod folder given at any level: the SD card or staging root, its atmosphere, contents,
    title or romfs folder (any romfs, e.g. Ryujinx's mods/contents/<title>/<mod>/romfs), or Model itself. Picking a
    deeper folder used to nest a second atmosphere/... inside it, where the game never looks."""
    p = Path(mod_root)
    below = ["atmosphere", "contents", TITLE_ID, "romfs", "Model"]
    last = p.name.lower()
    if last == "model" and p.parent.name.lower() == "romfs":
        return p
    for i, name in enumerate(below[:-1]):
        if last == name.lower():
            return p.joinpath(*below[i + 1:])
    return p.joinpath(*below)


def route_ends(route_id):
    if len(route_id) == 9 and route_id[0] == "R":
        return route_id[1:5], route_id[5:9]
    return None, None


def point_kind(pid):
    """A readable category from the ID (based on the naming seen in the game's files)."""
    if len(pid) == 4 and pid[0] == "W" and pid[1].isdigit():
        if pid[2:].isdigit():
            return "Level"
        return {"T": "Tower", "C": "Castle", "K": "Toad house", "J": "World link", "G": "Ghost house", "A": "Airship",
                "S": "Special", "M": "Special", "B": "Special", "X": "Special"}.get(pid[2], "Special")
    if pid.startswith("Fd"):
        return "Pipe"
    if pid.startswith("F"):
        return "Path stop"
    if pid.startswith("K"):
        return "Waypoint"
    return "Other"


def course_label(pid):
    """The CourseName_ label a point probably uses (inferred from the IDs and the names file, not from game code)."""
    if len(pid) != 4 or pid[0] != "W" or not pid[1].isdigit():
        return None
    n = pid[1]
    if pid[2:].isdigit():
        return "CourseName_%s-%d" % (n, int(pid[2:]))
    k = pid[3]
    num = int(k) + 1 if k.isdigit() else 1
    return {"T": "CourseName_%s-TOWER%d" % (n, num), "C": "CourseName_%s-CASTLE" % n, "G": "CourseName_%s-GHOST" % n,
            "A": "CourseName_%s-AIRSHIP" % n}.get(pid[2])


class World:
    def __init__(self, path):
        self.path = Path(path)
        m = WORLD_RE.search(self.path.name + ".")
        if not m:
            raise WorldError("%s is not a world archive (expected a name like CS_W1.Nin_NX_NVN.szs)." % self.path.name)
        self.id = m.group(1)[0].upper() + m.group(1)[1:].lower()       # W1, W5a, W5b
        self.number = int(m.group(2))
        self.block = offsets.block_of(self.id)
        self.archive_name = "CS_%s.Nin_NX_NVN.szs" % self.id
        entries, info, _ = archive.read_szs(self.path)
        self.entries = list(entries)
        self.info = info
        members = {Path(n).name.lower(): (n, d) for n, d in self.entries}
        pk = next((k for k in members if k.startswith("point") and k.endswith(".csv")), None)
        rk = next((k for k in members if k.startswith("route") and k.endswith(".csv")), None)
        bk = next((k for k in members if k.endswith(".bfres")), None)
        if not (pk and rk and bk):
            raise WorldError("%s does not contain a point table, a route table and a model." % self.path.name)
        self.point_member, self.route_member, self.bfres_member = members[pk][0], members[rk][0], members[bk][0]
        self._point_blob, self._route_blob = members[pk][1], members[rk][1]
        self.point_style, self.route_style = csv_style(self._point_blob), csv_style(self._route_blob)
        self.point_rows = csv_decode(self._point_blob)
        self.route_rows = csv_decode(self._route_blob)
        self._orig_point_rows = [list(r) for r in self.point_rows]
        self._orig_route_rows = [list(r) for r in self.route_rows]
        self._orig_bfres = bytes(members[bk][1])
        self.bfres_bytes = bytearray(members[bk][1])
        self.model = bfres.BfresFile.parse(bytes(self.bfres_bytes))
        wanted = [("cs_" + self.id).lower(), ("cs_w%d" % self.number)]
        self.marker = next((mo for name in wanted for mo in self.model.models if mo.name.lower() == name), self.model.models[0])
        self.mario = None                 # MarioOffsetTable for this world number, if loaded
        self.mario_path = None
        self._orig_mario = None
        self.look = None
        self.look_path = None
        self._orig_look = None
        self.model_edits = {"remove": [], "textures": {}, "add": []}    # kdx.worldedit: applied on export
        self.new_bones = {}               # points and bends added in the editor: name -> world position (built on export)
        self.route_copies = []            # [{source, target, offset}]: appear animations copied to new paths (on export)
        self.route_makes = []             # [{target, source_model, shapes, preset, frames, amount, pivot}]: new ones
        self.course_copies = {}           # {"1-6.sarc": source level file}: levels copied into the mod on export
        self._orig_paths = {p for p, _w in rules.map_paths(self.point_rows, self.route_rows)}
        self._saved = self.snapshot()

    # ---- reading ---------------------------------------------------------------------------------------------------
    @property
    def point_ids(self):
        return [r[1] for r in self.point_rows if len(r) > 1]

    def row_of(self, pid):
        for r in self.point_rows:
            if len(r) > 1 and r[1] == pid:
                return r
        return None

    @property
    def bones(self):
        return {b.name: b for b in self.marker.bones}

    def position(self, name):
        if name in self.new_bones:
            return tuple(self.new_bones[name])
        b = self.bones.get(name)
        return b.world_position if b else None

    def has_bone(self, name):
        return name in self.new_bones or name in self.bones

    @property
    def waypoint_ids(self):
        pts = set(self.point_ids)
        return ([b.name for b in self.marker.bones if len(b.name) == 4 and b.name[0] == "K" and b.name not in pts] +
                [k for k in self.new_bones if k[0] == "K"])

    # ---- new points (the map-point model is rebuilt on export: marker_spec) -------------------------------------------
    def free_name(self, kind):
        """A free point name: 'stop' F###, 'bend' K###, 'level' W<n>0<k>, 'pipe' Fd##, 'toad' W<n>K<k> ..."""
        taken = set(self.point_ids) | set(self.bones) | set(self.new_bones)
        n = self.number
        pats = {"stop": ["F%03x" % i for i in range(0x900, 0x1000)], "bend": ["K%03x" % i for i in range(0x900, 0x1000)],
                "level": ["W%d0%x" % (n, i) for i in range(1, 16)], "pipe": ["Fd%02x" % i for i in range(16, 256)],
                "toad": ["W%dK%x" % (n, i) for i in range(1, 8)], "tower": ["W%dT%x" % (n, i) for i in range(0, 2)],
                "castle": ["W%dC%x" % (n, i) for i in range(0, 2)], "ghost": ["W%dG0" % n],
                "airship": ["W%dA0" % n]}
        if kind == "level":                    # levels the save tracks first (CourseSlot_getIndex, rules.COURSE_SLOTS)
            tracked = rules.tracked_level_names(n)
            pats["level"] = tracked + [c for c in pats["level"] if c not in tracked]
        for cand in pats.get(kind, []):
            if cand not in taken:
                return cand
        raise WorldError("No free name left for a %s." % kind)

    def add_point(self, pid, pos):
        """Add a point (F/W, with a point-table row) or a bend (K, bone only) at world position pos."""
        if len(pid) != 4 or self.has_bone(pid) or pid in self.point_ids:
            raise WorldError("%s is taken or not 4 characters." % pid)
        self.new_bones[pid] = tuple(float(c) for c in pos)
        if pid[0] != "K":
            self.point_rows.append([str(len(self.point_rows)), pid, "", "", "", "", "", "", ""])
            if self.mario:
                self.mario.sync(self.block, self.point_ids)          # offset 0: the camera looks at Mario (vanilla)

    def remove_point(self, pid):
        """Remove a point added in the editor (and its routes). Returns what undo needs."""
        if pid not in self.new_bones:
            raise WorldError("Only points added in the editor can be removed (%s is the game's own)." % pid)
        pos = self.new_bones.pop(pid)
        row_i = next((i for i, r in enumerate(self.point_rows) if len(r) > 1 and r[1] == pid), None)
        row = self.point_rows.pop(row_i) if row_i is not None else None
        routes = [(i, r) for i, r in enumerate(self.route_rows) if r and pid in route_ends(r[0])]
        for i, _r in reversed(routes):
            self.route_rows.pop(i)
        for i, r in enumerate(self.point_rows):
            r[0] = str(i)
        cam = None
        if self.mario and row is not None:                       # its camera record goes too (kept for redo)
            k = self.mario.find(self.block, pid)
            cam = self.mario.records[k].copy() if k is not None else None
            self.mario.sync(self.block, self.point_ids)
        return pos, row_i, row, routes, cam

    def restore_point(self, pid, saved):
        pos, row_i, row, routes, cam = saved
        self.new_bones[pid] = pos
        if row is not None:
            self.point_rows.insert(row_i, row)
            for i, r in enumerate(self.point_rows):
                r[0] = str(i)
            if self.mario:
                self.mario.sync(self.block, self.point_ids)
                if cam is not None:
                    self.mario.set_vector(self.block, pid, cam.vector)
                    self.mario.set_flags(self.block, pid, cam.flags)
        for i, r in routes:
            self.route_rows.insert(i, r)

    def needs_marker_rebuild(self):
        paths = {p for p, _w in rules.map_paths(self.point_rows, self.route_rows)}
        return bool(self.new_bones) or bool(paths - self._orig_paths - set(self.bones))

    def marker_order(self):
        """Bone order for the rebuilt map-point model. The game's own order is kept; only paths the editor added or
        whose walk now passes a new point are rewritten (path bone, then its walk), and new points on no path go first.
        Raises WorldError when a new path would take a point another path's walk already owns."""
        names = [b.name for b in self.marker.bones[1:]]
        table = dict(rules.map_paths(self.point_rows, self.route_rows))
        bone_walks = dict(rules.paths_from_bones([b.name for b in self.marker.bones]))
        new = set(self.new_bones)
        order = list(names)
        owner = {q: pth for pth, walk in bone_walks.items() for q in walk}
        for path, walk in table.items():
            if not ((path not in self.bones and path not in self._orig_paths) or new & set(walk)):
                continue
            for q in walk:
                if q in owner and owner[q] != path and q not in bone_walks.get(path, []):
                    raise WorldError("Point %s is on path %s already; path %s can't use it too." % (q, owner[q], path))
            old = bone_walks.get(path, [])
            at = order.index(path) if path in order else len(order)
            for q in [path] + old:
                if q in order:
                    order.remove(q)
            for q in walk:
                if q in order:
                    order.remove(q)
            at = min(at, len(order))
            order[at:at] = [path] + list(walk)
            for q in walk:
                owner[q] = path
        lone = [q for q in self.new_bones if q not in order]
        # F/K on no walk go first (after a path bone they would join its walk); W points can close the list
        return [q for q in lone if q[0] in "FK"] + order + [q for q in lone if q[0] not in "FK"]

    def marker_spec(self, src, out):
        root = self.marker.bones[0]
        rx, ry, rz = root.world_position
        points = []
        for name in self.marker_order():
            pos = self.position(name)
            if pos is None:                                       # a new path bone: only its name and place count
                pos = (rx, ry, rz)
            kind = None
            if name in self.new_bones:
                kind = "level" if name[0] == "W" else ("stop" if name[0] == "F" else None)
            points.append({"name": name, "position": [pos[0] - rx, pos[1] - ry, pos[2] - rz], "marker": kind})
        from .export2d import marker_shapes
        return {"input": str(src), "output": str(out), "marker_model": self.marker.name, "points": points,
                "marker_shapes": marker_shapes(self)}

    def apply_route_copies(self, data):
        """The bfres with each copied appear animation added (kdx-bfres `routecopy`)."""
        import json
        import tempfile
        from .export2d import run_engine
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / "in.bfres", Path(td) / "out.bfres"
            src.write_bytes(data)
            (Path(td) / "spec.json").write_text(json.dumps({"input": str(src), "output": str(out),
                                                            "copies": self.route_copies}), encoding="utf-8")
            run_engine("routecopy", Path(td) / "spec.json")
            new = out.read_bytes()
        bfres.BfresFile.parse(new)
        return new

    def make_items(self, objects=None):
        """The routemake items of the new appear animations. objects: {added object name: [its shape names]} from the
        model edits (worldedit.apply) - an appear animation of an object added from DAE/FBX takes those shapes."""
        from . import events
        removed = {tuple(r) for r in (self.model_edits or {}).get("remove", [])}
        items = []
        for m in self.route_makes:
            shapes = [s for s in m.get("shapes", []) if (m["source_model"], s) not in removed]
            for o in m.get("objects", []):
                if o not in (objects or {}):
                    raise WorldError("The appear animation of %s needs the added object %r, which is no longer in the "
                                     "world." % (m["target"], o))
                shapes += objects[o]
            if not shapes:
                raise WorldError("The appear animation of %s has no objects left." % m["target"])
            keys = dict(m.get("keys") or events.preset_keys(m["preset"], m["frames"], m["amount"]))
            if m.get("interp"):                                  # keyframe editor: linear / smooth per channel
                keys["interp"] = dict(m["interp"])
            items.append({"target": m["target"], "source_model": m["source_model"], "shapes": shapes, "keys": keys,
                          "frames": int(m["frames"]), "pivot": list(m["pivot"])})
        return items

    def apply_route_makes(self, data, objects=None):
        """The bfres with each new appear animation built (kdx-bfres `routemake`): shapes moved into a model named
        like the path, animated by a preset or the keyframe editor's keys."""
        import json
        import tempfile
        from .export2d import run_engine
        items = self.make_items(objects)
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / "in.bfres", Path(td) / "out.bfres"
            src.write_bytes(data)
            (Path(td) / "spec.json").write_text(json.dumps({"input": str(src), "output": str(out), "items": items}),
                                                encoding="utf-8")
            run_engine("routemake", Path(td) / "spec.json")
            new = out.read_bytes()
        bfres.BfresFile.parse(new)
        return new

    def rebuild_marker(self, data):
        """The bfres with its map-point model rebuilt for new points and paths (kdx-bfres `marker`)."""
        import json
        import tempfile
        from .export2d import run_engine
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / "in.bfres", Path(td) / "out.bfres"
            src.write_bytes(data)
            (Path(td) / "spec.json").write_text(json.dumps(self.marker_spec(src, out)), encoding="utf-8")
            run_engine("marker", Path(td) / "spec.json")
            new = out.read_bytes()
        bfres.BfresFile.parse(new)
        return new

    @property
    def routes(self):
        out = []
        for r in self.route_rows:
            if not r:
                continue
            a, b = route_ends(r[0])
            out.append({"id": r[0], "from": a, "to": b, "type": r[1] if len(r) > 1 else ""})
        return out

    def route_row(self, rid):
        for r in self.route_rows:
            if r and r[0] == rid:
                return r
        return None

    def load_offsets(self, model_dir):
        """Load MarioOffsetData/LookOffsetData for this world number from a romfs/Model folder (if present)."""
        model_dir = Path(model_dir)
        for attr, cls, stem in (("mario", offsets.MarioOffsetTable, "MarioOffsetDataW%d.bin"), ("look", offsets.LookOffsetTable, "LookOffsetDataW%d.bin")):
            p = model_dir / (stem % self.number)
            if p.exists():
                table = cls.load(p)
                setattr(self, attr, table)
                setattr(self, attr + "_path", p)
                setattr(self, "_orig_" + attr, table.to_bytes())
        self._saved = self.snapshot()

    def mario_record(self, pid):
        if not self.mario:
            return None
        i = self.mario.find(self.block, pid)
        return self.mario.records[i] if i is not None else None

    def report(self, course_dir=None, hidden=()):
        """The game's rules (from the reverse engineering in wmtool.py) plus Koopatlas DX's own checks."""
        # Check the map-point model as the export will build it: new points, bends and paths get their bones then
        # (kdx-bfres marker), so they are not missing.
        order = [b.name for b in self.marker.bones]
        positions = {b.name: b.world_position for b in self.marker.bones}
        positions.update({k: tuple(v) for k, v in self.new_bones.items()})
        extra = []
        if self.needs_marker_rebuild():
            try:
                order = order[:1] + self.marker_order()
            except WorldError as e:
                extra.append(str(e))
        dirs = [course_dir] if course_dir and not isinstance(course_dir, (list, tuple)) else list(course_dir or [])
        if self.course_copies:                       # levels the export copies in (Use a level for…)
            import tempfile
            stage = Path(tempfile.gettempdir()) / ("kdx_courses_W%d" % self.number)
            stage.mkdir(exist_ok=True)
            for name, src in self.course_copies.items():
                f = stage / name
                if Path(src).exists() and (not f.exists() or f.stat().st_size != Path(src).stat().st_size):
                    shutil.copyfile(src, f)
            dirs = [stage] + dirs
        rep = rules.check(self.point_rows, self.route_rows,
                          len(point_csv_encode(self.point_rows, self.point_style)), len(csv_encode(self.route_rows, self.route_style)),
                          set(order), self.number, self.id[2:], dirs or None, hidden=hidden,
                          bone_order=order, positions=positions)
        rep.errors[:0] = extra
        if self.mario:
            rep.errors += ["MarioOffset table: " + p for p in self.mario.check(self.block, self.point_ids)]
        return rep

    def problems(self):
        """Errors that should block an export (warnings are in report())."""
        return self.report().errors

    def _old_problems(self):
        out = []
        names = set(self.bones)
        for pid in self.point_ids:
            if pid not in names:
                out.append("Point %s has no bone in model %s (the game would not know where it is)." % (pid, self.marker.name))
        seen = set()
        for pid in self.point_ids:
            if pid in seen:
                out.append("Point %s appears twice in the point table." % pid)
            seen.add(pid)
            if len(pid) != 4:
                out.append("Point ID %r is not 4 characters." % pid)
        for r in self.routes:
            if r["from"] is None:
                out.append("Route %r is not R + 4 + 4 characters." % r["id"])
        if len(self.point_ids) > offsets.BLOCK:
            out.append("%d points: the MarioOffset table has room for %d per archive." % (len(self.point_ids), offsets.BLOCK))
        if self.mario:
            out += ["MarioOffset: " + p for p in self.mario.check(self.block, self.point_ids)]
        return out

    # ---- editing ---------------------------------------------------------------------------------------------------
    def set_point_cell(self, pid, column, value):
        if column in (0, 1):
            raise WorldError("The index and ID columns are not edited here.")
        row = self.row_of(pid)
        if row is None:
            raise WorldError("No point %s." % pid)
        while len(row) <= column:
            row.append("")
        row[column] = value

    def add_route(self, rid, rtype="道"):
        """Append a route row (R + start + end). Returns its row index."""
        if not rules.route_ends(rid):
            raise WorldError("Route ids are R + 4-character point + 4-character point (%r)." % rid)
        if self.route_row(rid) is not None:
            raise WorldError("Route %s already exists." % rid)
        self.route_rows.append([rid, rtype, ""])
        return len(self.route_rows) - 1

    def remove_route(self, rid):
        """Remove a route row. Returns (index, row) so it can be put back."""
        for i, r in enumerate(self.route_rows):
            if r and r[0] == rid:
                return i, self.route_rows.pop(i)
        raise WorldError("No route %s." % rid)

    def insert_route(self, index, row):
        self.route_rows.insert(index, list(row))

    def path_chain(self, path):
        """Segments an unlock path opens, or None when there is no chain (rules.path_segments)."""
        return rules.path_segments(path, rules.segment_graph(self.point_rows, self.route_rows))

    def set_route_type(self, rid, value):
        row = self.route_row(rid)
        if row is None:
            raise WorldError("No route %s." % rid)
        while len(row) < 2:
            row.append("")
        row[1] = value

    def move_point(self, name, world_xyz):
        """Move a point or waypoint bone so its world position becomes world_xyz (patches the bone in place)."""
        if name in self.new_bones:
            self.new_bones[name] = tuple(float(c) for c in world_xyz)
            return
        bone = self.bones.get(name)
        if bone is None:
            raise WorldError("No bone %s in %s." % (name, self.marker.name))
        parent = self.marker.bones[bone.parent].world if 0 <= bone.parent < len(self.marker.bones) else bfres._IDENT
        local = bfres.transform_point(bfres.invert_affine(parent), world_xyz)
        struct.pack_into("<3f", self.bfres_bytes, bone.offset + 0x54, *local)
        self.model = bfres.BfresFile.parse(bytes(self.bfres_bytes))
        self.marker = next(mo for mo in self.model.models if mo.name == self.marker.name)

    def set_mario(self, pid, vector=None, flags=None):
        if not self.mario:
            raise WorldError("No MarioOffset table loaded for world %d." % self.number)
        if vector is not None:
            self.mario.set_vector(self.block, pid, vector)
        if flags is not None:
            self.mario.set_flags(self.block, pid, flags)

    # ---- state -----------------------------------------------------------------------------------------------------
    def changed_members(self):
        out = []
        if self.point_rows != self._orig_point_rows:
            out.append(self.point_member)
        if self.route_rows != self._orig_route_rows:
            out.append(self.route_member)
        if (bytes(self.bfres_bytes) != self._orig_bfres or self.has_model_edits() or self.needs_marker_rebuild()
                or self.route_copies or self.route_makes):
            out.append(self.bfres_member)
        return out

    def has_model_edits(self):
        from . import worldedit
        return worldedit.has_edits(getattr(self, "model_edits", None))

    def offsets_changed(self):
        out = []
        if self.mario and self.mario.to_bytes() != self._orig_mario:
            out.append(self.mario_path.name)
        for name, src in sorted(self.course_copies.items()):     # levels for new level points
            cdir = model_dir.parent / "Course"
            cdir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, cdir / name)
            written.append(cdir / name)
        if self.look and self.look.to_bytes() != self._orig_look:
            out.append(self.look_path.name)
        return out

    @property
    def dirty(self):
        """True when something differs from the files that were opened (this decides what export writes)."""
        return bool(self.changed_members() or self.offsets_changed())

    def snapshot(self):
        import json
        return ([list(r) for r in self.point_rows], [list(r) for r in self.route_rows], bytes(self.bfres_bytes),
                self.mario.to_bytes() if self.mario else None, self.look.to_bytes() if self.look else None,
                json.dumps(getattr(self, "model_edits", None), sort_keys=True),
                json.dumps(sorted(getattr(self, "new_bones", {}).items())),
                json.dumps(getattr(self, "route_copies", [])), json.dumps(getattr(self, "route_makes", [])),
                json.dumps(sorted(getattr(self, "course_copies", {}).items())))

    @property
    def unsaved(self):
        """True when there are edits made since the last export (or since opening)."""
        return self.snapshot() != self._saved

    def mark_saved(self):
        self._saved = self.snapshot()

    # ---- export ----------------------------------------------------------------------------------------------------
    def build_archive(self):
        changed = self.changed_members()
        entries = []
        for name, data in self.entries:
            if name == self.point_member and name in changed:
                entries.append((name, point_csv_encode(self.point_rows, self.point_style)))
            elif name == self.route_member and name in changed:
                entries.append((name, csv_encode(self.route_rows, self.route_style)))
            elif name == self.bfres_member and name in changed:
                data = self.build_bfres()
                entries.append((name, data))
            else:
                entries.append((name, data))
        return entries

    def build_bfres(self, data=None, marker=True):
        """The world's BFRES with every pending change applied: the map-point model rebuilt (marker), model edits
        (objects added from DAE/FBX, removed and moved shapes), copied and new appear animations - in that order, so a
        new appear animation can take an added object's shapes."""
        data = bytes(self.bfres_bytes) if data is None else data
        if marker and self.needs_marker_rebuild():           # new points / paths: rebuild the map-point model
            data = self.rebuild_marker(data)
        objects = {}
        if self.has_model_edits():                           # model edits through the model writer
            from . import worldedit
            info = {}
            data = worldedit.apply(data, self.model_edits, self, info=info)
            objects = info.get("object_shapes", {})
        if self.route_copies:                                # appear animations copied to new paths
            data = self.apply_route_copies(data)
        if self.route_makes:                                 # new appear animations from the world's shapes
            data = self.apply_route_makes(data, objects)
        return data

    def export(self, mod_root, compressor=None, compress="auto"):
        """Write changed files into <mod_root>/atmosphere/contents/<title>/romfs/Model/. Returns the paths written."""
        model_dir = mod_model_dir(mod_root)
        sources =[self.path.resolve().parent] + [p.resolve().parent for p in (self.mario_path, self.look_path) if p]
        if any(model_dir.resolve() == s for s in sources):
            raise WorldError("The export folder is the folder you opened the files from; choose a separate mod folder.")
        model_dir.mkdir(parents=True, exist_ok=True)
        written = []
        if self.changed_members():
            entries = self.build_archive()
            out = model_dir / self.archive_name
            archive.write_szs(out, entries, self.info, compress=compress, compressor=compressor)
            back = dict(archive.read_szs(out)[0])
            if back != dict(entries):
                raise WorldError("The exported archive did not read back identically.")
            written.append(out)
        if self.mario and self.mario.to_bytes() != self._orig_mario:
            out = model_dir / ("MarioOffsetDataW%d.bin" % self.number)
            out.write_bytes(self.mario.to_bytes())
            written.append(out)
        if self.look and self.look.to_bytes() != self._orig_look:
            out = model_dir / ("LookOffsetDataW%d.bin" % self.number)
            out.write_bytes(self.look.to_bytes())
            written.append(out)
        self.mark_saved()
        return written

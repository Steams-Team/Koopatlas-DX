"""
kdx/nsmbw.py - port a New Super Mario Bros. Wii world map to a Koopatlas DX map for New Super Mario Bros. U Deluxe.

    project, notes = port_world(r"...\\NSMBW", 1, r"...\\ports\\NSMBW World 1")
    project.save(r"...\\ports\\NSMBW World 1\\NSMBW World 1.kdxmap")

What NSMBW gives (all in the game's own data, nothing guessed):
  WorldMap/CS_W<n>.arc        the world model (MDL0). Points, bends and paths are its bones, named exactly like
                              NSMBUDX's: W101, W1T0, W1C0, F/K points, and path bones R<from><to> with the bends of
                              their walk as children. Nintendo kept the scheme for the Wii U.
  WorldMap/RouteInfo.arc      W<n>/point*.csv and route*.csv: the point table (flags, points and paths each exit
                              opens) and the route table (segment, type in Shift-JIS: 道 path, ジャンプ jump...), in the
                              same CSV layout NSMBUDX uses.
  WorldMap/cob*.arc           scenery placed at child bones of the points: cobCastle under W1C0, cobToride under W1T0,
                              cobKinokoRed3 under W1K1 (file cobKinokoRed), cobCannon00, cobPeachCastle...

What changes for NSMBUDX:
  - names: NSMBW's start W<n>S0 becomes W<n>M0 (the NSMBUDX start); in World 1 the cannon W1W0 (a short course that
    warps to World 5) becomes W1S0, NSMBUDX's secret point that warps to World 5 (W1S0 opens W501).
  - flags NSMBUDX does not know (demo1, kuribo2...) are dropped; NSMBW's map Goomba stops carry W1PakkunA (the
    NSMBUDX World 1 map enemy). Missing required names (W1Item, balloon1) are added hidden by the export.
  - World 1's Goomba square (RFb00Fb03, RFb00W106, RFb01W1K3, RFb02W105) is opened by NSMBW's map-enemy code, not by
    the point table; NSMBUDX needs every path in the table, so W104 opens them with RW104Fb00, each cut at its stops
    (NSMBUDX paths run between stops). Its roads are the kuribo bone's geometry.
  - bends lined up in x with a neighbour are moved 4 units so no point loses a link in the game's slot table
    (rules.slot_losses), and NSMBW's sky stands behind the castle as a scenery object, hiding the next world.
  - the models become COLLADA (.dae) files with PNG textures: the ground (sky dome, clouds, shadow decals and the
    NSMBW level-marker placeholders left out: NSMBUDX has its own sky and markers), NSMBW's gold level rim at each level
    point (NSMBUDX's level actor is the disc alone), the Wii's vertex shading baked into texture copies, the mushroom lifts of World 1 raised to the path
    over them (the Wii raises them with an animation), and each cob* model as a scenery object at its bone.
  - positions are moved so the map starts at (0, 0) in the World Maker; heights stay.
"""
from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path

import numpy as np

from . import brres, model3d, project2d, rules

ROUTE_TYPES_SJIS = "cp932"
SKIP_MATERIALS = ("W1_sky", "mtCloud", "mtCloudFar", "mtKage", "mtKageKuribo", "mtCourseOBJ", "lambert1")
# moving parts: airship, clouds, mushroom lifts. Not "kuribo": that bone holds World 1's Goomba-square roads (mtKuribo,
# mtKuriboBrown, mtKuriboGray; its CLR0 only fades them in, alpha 100 -> 255, when 1-4 is cleared); the Goomba itself
# is Object/kuribo.arc, an actor
ACTOR_BONES = ("shipRoute", "loop", "anim")
SHADE_LEVELS = 5                             # the Wii's vertex shading, baked into texture copies (model3d.shade_groups)
# NSMBW's level node, cobCourse: a disc (mtCourse, coloured open/cleared by its CLR0) inside a gold rim (mtCoursePoint).
# NSMBUDX's cobCourse actor is that disc alone (48.2 across, the same mesh); its worlds draw the ring in the world model
# (CS_W1_World pTorus10__mtCourseRing: every W point but the start). So the port stands the Wii's rim at those points.
COURSE_RIM = ("cobCourse.arc", ("mtCourse",))
# Paths stay hidden until they open. NSMBW and NSMBUDX name a path's materials mt<route><a-h> (mtRW101W102a) and reveal
# them by sliding a mask texture (texMaskOpaque / MaskOpaque, the same 8x64 image) along the path's mask UVs; the game
# keeps the hidden offset in the material (NSMBW: the mask layer's SRT, -2.9; NSMBUDX: texmtx0, -3) and raises it to
# -0.8 when the route opens (MaskRouteAnimation, 0x60229EAC). The port keeps the Wii's mask UVs and offset per part.
ROUTE_MATERIAL = re.compile(r"mt(R\w{8})([a-h])$")
KURIBO_ROADS = ("mtKuribo", "mtKuriboBrown", "mtKuriboGray")   # World 1's Goomba square: faded in when W104 opens
FADE_MASK = ((0.5, 1.0), -3.0)          # a mask that only fades: every corner at v 1, hidden at -3 (row 0), shown at -0.8
LIFT_BONES = ("anim",)                       # World 1: mushroom lifts raised by the RW105W1C0 animation
MARGIN = 4                                   # tiles around the ported map


class PortError(ValueError):
    pass


def world_files(root, world):
    """(model archive, point csv text, route csv text, WorldMap folder) for NSMBW world n (point table of the 'a'
    half when a world is split)."""
    root = Path(root)
    wm = root / "WorldMap" if (root / "WorldMap").is_dir() else root
    arc = wm / ("CS_W%d.arc" % world)
    ri = wm / "RouteInfo.arc"
    if not arc.exists() or not ri.exists():
        raise PortError("%s is not an NSMBW dump: WorldMap/CS_W%d.arc and WorldMap/RouteInfo.arc are needed." % (root, world))
    files = brres.u8_files(ri.read_bytes())
    pts = sorted(k for k in files if k.startswith("W%d/point" % world))
    rts = sorted(k for k in files if k.startswith("W%d/route" % world))
    if not pts or not rts:
        raise PortError("RouteInfo.arc has no tables for World %d." % world)
    dec = lambda b: b.decode(ROUTE_TYPES_SJIS, "replace")
    return arc, dec(files[pts[0]]), dec(files[rts[0]]), wm


def read_csv(text):
    return [r for r in csv.reader(io.StringIO(text)) if r]


def renames(world):
    """NSMBW point name -> NSMBUDX point name."""
    w = str(world)
    out = {"W%sS0" % w: "W%sM0" % w}
    if world == 1:
        out["W1W0"] = "W1S0"
    return out


def rename_token(tok, ren):
    """A point name, or a path/route name R<a><b>, with its point names renamed."""
    if tok in ren:
        return ren[tok]
    if len(tok) == 9 and tok[0] == "R":
        a, b = tok[1:5], tok[5:9]
        return "R" + ren.get(a, a) + ren.get(b, b)
    return tok


def _euler_degrees(m):
    """(rx, ry, rz) degrees with R = Rz * Ry * Rx (kdx.modelref.euler_matrix) from a 3x3 (scale removed)."""
    r = np.asarray(m, np.float64)[:3, :3]
    r = r / np.maximum(np.linalg.norm(r, axis=0, keepdims=True), 1e-12)
    ry = math.asin(max(-1.0, min(1.0, -r[2, 0])))
    if abs(r[2, 0]) < 0.9999:
        rx, rz = math.atan2(r[2, 1], r[2, 2]), math.atan2(r[1, 0], r[0, 0])
    else:
        rx, rz = math.atan2(-r[1, 2], r[1, 1]), 0.0
    return [math.degrees(rx), math.degrees(ry), math.degrees(rz)]


def appear_pose(wm, arc):
    """The end pose of a scenery model's appear animation, or {}. Toad houses rest sunk in the ground (kinoko y -45)
    and rise with cobKinokoAppear when they open; cobKinokoStar has the same skeleton but no animation of its own, so
    it borrows cobKinokoRed's."""
    res, _mdl = model3d.brres_model(arc)
    name = next((n for n in res.chr0 if "appear" in n.lower()), None)
    if name:
        return res.end_pose(name)
    bones = {b.name for b in _mdl.bones}
    red = wm / "cobKinokoRed.arc"
    if {"kinoko", "trunk"} <= bones and red.exists() and red != arc:
        return model3d.brres_model(red)[0].end_pose("cobKinokoAppear")
    return {}


def cob_file(wm, bone):
    """The cob*.arc a scenery bone names (cobKinokoRed3 -> cobKinokoRed.arc), or None."""
    name = bone
    while name:
        p = wm / (name + ".arc")
        if p.exists():
            return p
        if not name[-1].isdigit():
            break
        name = name[:-1]
    return None


def port_world(root, world, out_dir, tile_size=project2d.DEFAULT_TILE_SIZE, name=None):
    """Build a World Maker project from NSMBW world `world`, writing its models (DAE + PNG) into out_dir/models.
    Returns (project, notes). The project is not saved."""
    out_dir = Path(out_dir)
    models = out_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    arc, point_text, route_text, wm = world_files(root, world)
    ren = renames(world)
    # NSMBW's bends (K011, K072...) reuse names the NSMBUDX template's walks to other worlds use (W1: K072 on the way
    # to World 2), and the export copies those walks in. Bends are named by nobody else, so they get their own: Kn00...
    bends = sorted({e for r in read_csv(route_text) for e in (rules.route_ends(r[0]) or ()) if e[0] == "K"})
    if len(bends) > 256:
        raise PortError("World %d has %d bends; at most 256 can be renamed." % (world, len(bends)))
    ren.update({k: "Kn%02x" % i for i, k in enumerate(bends)})
    notes = []

    # ---- models ------------------------------------------------------------------------------------------------------
    ground = model3d.load_brres(arc, skip_materials=SKIP_MATERIALS, skip_bones=ACTOR_BONES, out_dir=models / "textures",
                                shade_levels=SHADE_LEVELS)
    bones, mats = ground.bones, ground.bone_matrices
    ground_dae = models / ("CS_W%d.dae" % world)               # written once the paths have their routes' names
    notes.append("Ground: %d parts, %d triangles -> %s (sky, clouds, shadows and Wii level markers left out)." % (
        len(ground.parts), sum(p.triangle_count for p in ground.parts), ground_dae.name))
    objects = []                                   # (name, dae path, bind matrix, raise)
    try:
        lifts = model3d.load_brres(arc, skip_materials=SKIP_MATERIALS, only_bones=LIFT_BONES, out_dir=models / "textures",
                                   shade_levels=SHADE_LEVELS)
    except model3d.ModelError:
        lifts = None
    route_rows_raw = read_csv(route_text)
    if lifts is not None:
        # The lifts are in their lowered bind pose; the path that crosses them (its bends' height) says how high they
        # stand once raised. Lift the model so its top is at the lowest bend that lies over it.
        lo, hi = lifts.bounds()
        over = [p for n, p in bones.items() if n[:1] == "K" and lo[0] - 1 <= p[0] <= hi[0] + 1 and lo[2] - 1 <= p[2] <= hi[2] + 1]
        rise = (min(p[1] for p in over) - float(hi[1])) if over else 0.0
        dae = model3d.save_dae(lifts, models / ("CS_W%d_lifts.dae" % world))
        objects.append(("Mushroom lifts", dae, np.identity(4), rise))
        notes.append("Mushroom lifts raised by %.1f to the path over them." % rise)
    for b, m in mats.items():
        if not b.startswith("cob"):
            continue
        f = cob_file(wm, b)
        if f is None:
            notes.append("No model file for the scenery bone %s." % b)
            continue
        dae = models / (f.stem + ".dae")
        if not any(o[1] == dae for o in objects):              # cobKinokoRed serves two houses: written once
            pose = appear_pose(wm, f)
            if pose:
                notes.append("%s drawn risen (the end of its appear animation), not sunk in the ground." % f.stem)
            model3d.save_dae(model3d.load_brres(f, out_dir=models / "textures", pose=pose, shade_levels=SHADE_LEVELS), dae)
        objects.append((b, dae, m, 0.0))

    # ---- points and routes -------------------------------------------------------------------------------------------
    point_rows = read_csv(point_text)
    known = set(rules.KNOWN_FLAGS)
    route_ids = []
    for r in route_rows_raw:
        rid = rename_token(r[0], ren)
        if rules.route_ends(rid):
            route_ids.append((rid, r[1] if len(r) > 1 and r[1] else "道"))
    route_points = {p for rid, _t in route_ids for p in rules.route_ends(rid)}
    nodes = {}
    kuribo_stops = []                                     # NSMBW World 1: the map Goomba's square (kuribo2)
    dropped_flags = set()
    for r in point_rows:
        r = (r + [""] * 9)[:9]
        pid = rename_token(r[1], ren)
        flags = [f for f in rules.split(r[2])]
        keep = [f for f in flags if f in known]
        dropped_flags |= set(flags) - set(keep)
        if "kuribo2" in flags:
            kuribo_stops.append(pid)
        cells = list(r)
        cells[1], cells[2] = pid, ",".join(keep)
        for col in (3, 4, 6, 7):
            toks = [rename_token(t, ren) for t in rules.split(cells[col])]
            if col in (4, 7):
                toks = [t for t in toks if rules.route_ends(t)]        # 'kuribo' and other animation names go
            cells[col] = ",".join(toks)
        src = next((k for k, v in ren.items() if v == pid), pid)
        nodes[pid] = {"row": cells, "src": src}
    # NSMBW's path bones (R<from><to> with their walk after them) may walk through a stop: World 1's RFb00W106 passes
    # Fb03, RFb00Fb03 goes round the square by Fb01 and Fb02. NSMBUDX paths run between stops (rules.map_paths), so an
    # unlock path through stops becomes its pieces between them.
    stops = {rename_token(r[1], ren) for r in point_rows if len(r) > 1}
    walks = {rename_token(p, ren): [rename_token(q, ren) for q in w] for p, w in rules.paths_from_bones(list(bones))}

    def pieces(path):
        seq = [path[1:5]] + walks.get(path, []) + [path[5:9]]
        cut = [0] + [i for i in range(1, len(seq) - 1) if seq[i] in stops] + [len(seq) - 1]
        return ["R%s%s" % (seq[i], seq[j]) for i, j in zip(cut, cut[1:])]
    for n in nodes.values():
        for col in (4, 7):
            toks = [q for t in rules.split(n["row"][col]) for q in (pieces(t) if t in walks else [t])]
            n["row"][col] = ",".join(dict.fromkeys(toks))
    if world == 1 and "W104" in nodes:                       # the Goomba square, opened by NSMBW's map-enemy code
        extra = [q for p in ("RFb00Fb03", "RFb00W106", "RFb01W1K3", "RFb02W105") if p in bones for q in pieces(p)]
        row = nodes["W104"]["row"]
        have = rules.split(row[4])
        row[4] = ",".join(have + [p for p in dict.fromkeys(extra) if p not in have])
        notes.append("W104 also opens %s (NSMBW opens them from its map-enemy code)." % ", ".join(dict.fromkeys(extra)))
        square = next(iter(dict.fromkeys(extra)), None)
        if square:                                              # its roads fade in with the first of those paths
            k = 0
            for part in ground.parts:
                if part.material in KURIBO_ROADS:
                    part.material = "mt%s%s" % (square, "abcdefgh"[k])
                    part.uvs2 = np.tile(np.asarray(FADE_MASK[0], np.float32), (len(part.positions), 1))
                    part.mask_offset = FADE_MASK[1]
                    k += 1
            notes.append("The Goomba square's roads fade in when W104 opens %s (NSMBW fades them with its kuribo "
                         "colour animation)." % square)
    # NSMBUDX's World 1 map enemy starts on a plain point with two neighbours, on the road (vanilla Fb01, between W105
    # and F008, flagged W1PakkunA only). On a stop with three ways on (NSMBW's Fb00), meeting it left Mario with
    # direction 6 (no single way on) and the walk-end code read a missing neighbour (test mod 3: main+0x1C6C54). So it
    # gets its own point, Fe00, halfway along the square's front road (Fb00 - Fb03, on the way from W104 to W106).
    if world == 1 and kuribo_stops:
        # the square's front road: the stop that also leads to a level (Fb03 -> W106), not a side of the square
        front = sorted(kuribo_stops[1:], key=lambda p: not any(set(rules.route_ends(r)) & {p} and any(
            rules.point_class(e) == 3 for e in rules.route_ends(r)) for r, _t in route_ids))
        a, b = kuribo_stops[0], next((p for p in front if any(
            set(rules.route_ends(rid)) == {kuribo_stops[0], p} for rid, _t in route_ids)), None)
        rid = next((rid for rid, _t in route_ids if b and set(rules.route_ends(rid)) == {a, b}), None)
        if rid and all(p in nodes and nodes[p]["src"] in bones for p in (a, b)):
            i = [r for r, _t in route_ids].index(rid)
            t = route_ids[i][1]
            route_ids[i:i + 1] = [("R%sFe00" % a, t), ("RFe00%s" % b, t)]
            bones["Fe00"] = tuple((np.asarray(bones[nodes[a]["src"]], float) + np.asarray(bones[nodes[b]["src"]], float)) / 2)
            nodes["Fe00"] = {"row": ["", "Fe00", "W1PakkunA", "", "", "", "", "", ""], "src": "Fe00"}
            notes.append("World 1's map enemy starts on Fe00, a plain point halfway from %s to %s (as NSMBUDX's starts "
                         "on a point with two neighbours)." % (a, b))
        else:
            nodes[a]["row"][2] = ",".join(rules.split(nodes[a]["row"][2]) + ["W1PakkunA"])
    for p in route_points:                                    # bends: only in the route table
        if p not in nodes:
            src = next((k for k, v in ren.items() if v == p), p)
            nodes[p] = {"row": ["", p, "", "", "", "", "", "", ""], "src": src}
    missing = [p for p, n in nodes.items() if n["src"] not in bones]
    if missing:
        notes.append("No bone for %s: left out." % ", ".join(sorted(missing)))
        for p in missing:
            del nodes[p]
        route_ids = [(rid, t) for rid, t in route_ids if all(e in nodes for e in rules.route_ends(rid))]
    # W<n>X0 (World 1: Peach's Castle) is only walked by NSMBW's opening scene; as an NSMBUDX point it would be a
    # level icon at the end of a path open from the start. Drop it, and the bends that only lead to it.
    gone = {p for p in nodes if p[2:4] == "X0" and p[0] == "W"}
    while True:
        route_ids = [(rid, t) for rid, t in route_ids if not set(rules.route_ends(rid)) & gone]
        degree = {}
        for rid, _t in route_ids:
            for e in rules.route_ends(rid):
                degree[e] = degree.get(e, 0) + 1
        dead = {p for p in nodes if p[0] == "K" and degree.get(p, 0) <= 1 and p not in gone}
        if not dead:
            break
        gone |= dead
    if gone:
        for p in gone:
            nodes.pop(p, None)
        for n in nodes.values():
            for col in (3, 4, 6, 7):
                n["row"][col] = ",".join(t for t in rules.split(n["row"][col]) if not set(rules.route_ends(t) or (t,)) & gone)
        notes.append("Left out (NSMBW's opening walk only): %s." % ", ".join(sorted(gone)))
    # NSMBUDX rows list the points an exit unlocks next to its paths (W101: W102 / RW101W102); NSMBW leaves most empty
    for pid, n in nodes.items():
        row = n["row"]
        for pcol, ncol in ((4, 3), (7, 6)):
            have = rules.split(row[ncol])
            ends = [e for t in rules.split(row[pcol]) for e in rules.route_ends(t) if e[0] == "W" and e != pid]
            row[ncol] = ",".join(have + [e for e in dict.fromkeys(ends) if e not in have and e in nodes])
    if dropped_flags:
        notes.append("Flags NSMBUDX does not know, dropped: %s." % ", ".join(sorted(dropped_flags)))
    notes.append("Renamed: %s; bends K... -> Kn00-Kn%02x." % (
        ", ".join("%s -> %s" % kv for kv in ren.items() if kv[0] in bones and kv[0][0] != "K"), max(len(bends) - 1, 0)))

    renamed = []
    for part in ground.parts:                                   # NSMBW's path materials, named after the port's routes
        m = ROUTE_MATERIAL.match(part.material)
        if m and part.uvs2 is not None:
            new = "mt%s%s" % (rename_token(m.group(1), ren), m.group(2))
            if new != part.material:
                renamed.append("%s -> %s" % (part.material, new))
                part.material = new
    if renamed:
        notes.append("Path materials renamed with their routes: %s." % ", ".join(renamed))
    model3d.save_dae(ground, ground_dae)

    # ---- place everything on the World Maker's grid -----------------------------------------------------------------
    lo, hi = ground.bounds()
    pts = np.array([bones[n["src"]] for n in nodes.values()])
    lo = np.minimum(lo, pts.min(axis=0)); hi = np.maximum(hi, pts.max(axis=0))
    shift = np.array([-lo[0] + MARGIN * tile_size, 0.0, -lo[2] + MARGIN * tile_size])
    width = int(math.ceil((hi[0] - lo[0]) / tile_size)) + 2 * MARGIN
    height = int(math.ceil((hi[2] - lo[2]) / tile_size)) + 2 * MARGIN
    if width > 512 or height > 512:
        raise PortError("The world is %d x %d tiles at %g units per tile; maps go up to 512." % (width, height, tile_size))
    proj = project2d.Project(width, height, tile_size, world, name=name or "NSMBW World %d" % world)
    # NSMBW's worlds have more levels than Deluxe keeps clears for (World 1: 1-6 is course 5): the framework's slot
    # hook gives them free entries (hidden points Fq<course>; nsmbud-reverse code/framework/course_slots.cpp)
    proj.slot_hook = True
    proj.set_model3d(ground_dae, scale=1.0, offset=[float(c) for c in shift])
    for pid, n in nodes.items():
        x, y, z = (np.asarray(bones[n["src"]]) + shift).tolist()
        kind = kind_of(pid)
        proj.nodes[pid] = {"kind": kind, "x": x / tile_size, "y": z / tile_size, "pos": [x, y, z], "row": n["row"],
                           "camera": [0.0, 350.0, 400.0] if pid[0] == "W" else [0.0, 0.0, 0.0]}
    proj.routes = [{"id": rid, "type": t} for rid, t in route_ids]
    # The castle's way on to the next world (the export copies the template's walk next to it) needs a free direction,
    # and a route lined up with the castle in x takes its up/down pair (rules.heading). World 1's W106 path reaches the
    # castle that way and its west side is taken, so no side was left. A bend lined up with the castle is moved 4 units
    # sideways: it becomes a plain north/south neighbour, the walk can use the up/down pair.
    castle = "W%dC0" % world
    if castle in proj.nodes:
        cx = proj.nodes[castle]["pos"][0]
        for rid, _t in route_ids:
            a, b = rules.route_ends(rid)
            other = b if a == castle else a if b == castle else None
            if other and other[0] == "K" and abs(proj.nodes[other]["pos"][0] - cx) < 3:
                n = proj.nodes[other]
                n["pos"][0] = cx - 4.0
                n["x"] = n["pos"][0] / tile_size
                notes.append("%s moved 4 units west so %s keeps a free side for the way to the next world." % (other, castle))
    # The game keeps one neighbour per slot and puts neighbours lined up in x (|dx| < 3) in the up/down pair by height
    # only (rules.slot_losses). NSMBW roads often run straight north/south, so a point can get two neighbours for one
    # slot and lose one link (port test: W103 lost its Toad house to the cannon road, Fb02 lost Fb03, bend Kn0f lost
    # W105). Move a bend of such a pair 4 units east: the link leaves the up/down pair, the walk stays on the road.
    moved = set()
    for _ in range(32):
        losses = rules.slot_losses([rid for rid, _t in route_ids], {p: n["pos"] for p, n in proj.nodes.items()})
        fix = next((k for p, _s, a, b, _r in losses for k in (b, a, p) if k[0] == "K" and k in proj.nodes), None)
        if fix is None:
            break
        n = proj.nodes[fix]
        n["pos"][0] += 4.0
        n["x"] = n["pos"][0] / tile_size
        moved.add(fix)
    if moved:
        notes.append("Bends moved 4 units east so no point loses a neighbour link: %s." % ", ".join(sorted(moved)))
    left = rules.slot_losses([rid for rid, _t in route_ids], {p: n["pos"] for p, n in proj.nodes.items()})
    if left:
        notes.append("Links the game will still overwrite: %s." % "; ".join("%s slot %d: %s by %s" % l[:4] for l in left))
    sky = _sky_backdrop(arc, models, proj, castle, lo + shift, hi + shift, world)
    if sky:
        dae, centre = sky
        i = proj.add_object(dae, position=centre, name="Wii sky backdrop")
        proj.objects[i]["source_bone"] = "sky"
        notes.append("Wii sky backdrop stands %g units behind %s: it hides the next world, which NSMBUDX draws there "
                     "(move, scale or delete it in the 3D view)." % (SKY_GAP, castle))
    rim = _course_rim(wm, models)
    if rim:
        rims = sorted(p for p in proj.nodes if p[0] == "W" and proj.nodes[p]["kind"] != "start")
        roads = {}                                              # route -> its path materials' letters and offset
        for part in ground.parts:
            m = ROUTE_MATERIAL.match(part.material)
            if m and part.uvs2 is not None:
                roads.setdefault(m.group(1), [set(), part.mask_offset])[0].add(m.group(2))
        for pid in rims:
            path = rim
            # a secret point (W1S0, the cannon) shows nothing until its path opens; its rim fades in with that path
            route = next((r for r in roads if pid[2] == "S" and rules.route_ends(r)[1] == pid), None)
            if route:
                letter = next(c for c in "abcdefgh" if c not in roads[route][0])
                path = _course_rim(wm, models, ("mt%s%s" % (route, letter), roads[route][1]), pid)
                notes.append("%s's rim appears with %s (material mt%s%s)." % (pid, route, route, letter))
            i = proj.add_object(path, position=list(proj.nodes[pid]["pos"]), name="Level rim %s" % pid)
            proj.objects[i]["source_bone"] = "cobCourse"
        notes.append("NSMBW's gold level rim (cobCourse mtCoursePoint) stands at %d points; NSMBUDX draws the disc." % len(rims))
    for oname, dae, m, rise in objects:
        pos = (m[:3, 3] + shift + np.array([0.0, rise, 0.0])).tolist()
        scale = np.linalg.norm(m[:3, :3], axis=0).tolist()
        i = proj.add_object(dae, position=pos, rotation=_euler_degrees(m), scale=scale, name=oname)
        proj.objects[i]["source_bone"] = oname
    notes.append("%d points, %d routes, %d scenery objects; map %d x %d tiles." % (
        len(proj.nodes), len(proj.routes), len(proj.objects), width, height))
    return proj, notes


def _course_rim(wm, models, route=None, pid=None):
    """The gold rim of NSMBW's level node as a DAE (its disc left out: NSMBUDX's cobCourse actor draws it), or None.
    route: (material name, mask offset) to hide it with a path (a fade mask), saved as cobCourse_rim_<pid>.dae."""
    f = wm / COURSE_RIM[0]
    if not f.exists():
        return None
    try:
        rim = model3d.load_brres(f, skip_materials=COURSE_RIM[1], out_dir=models / "textures",
                                 shade_levels=0 if route else SHADE_LEVELS)
    except model3d.ModelError:
        return None
    if not rim.parts:
        return None
    if route:
        for part in rim.parts:
            part.material, part.mask_offset = route
            part.uvs2 = np.tile(np.asarray(FADE_MASK[0], np.float32), (len(part.positions), 1))
        return model3d.save_dae(rim, models / ("cobCourse_rim_%s.dae" % pid))
    return model3d.save_dae(rim, models / "cobCourse_rim.dae")


SKY_MATERIALS = re.compile(r"_sky$|^mtCloudFar$", re.I)
SKY_GAP = 420.0          # behind the castle: World 2's plateau starts ~470 behind the ported World 1 castle
SKY_SPAN_Y = (-150.0, 650.0)


def _sky_backdrop(arc, models, proj, castle, lo, hi, world):
    """NSMBW's sky and far-cloud quads (the Wii's backdrop) stood up as one wall across the map, SKY_GAP behind the
    castle. NSMBUDX's worlds share one 3D space, so the next world's ground shows where the Wii has sky; the wall
    covers it from World 1's side and is culled from the other. Returns (dae path, centre) or None."""
    if castle not in proj.nodes:
        return None
    _res, mdl = model3d.brres_model(arc)
    others = tuple(m.name for m in mdl.materials if not SKY_MATERIALS.search(m.name))
    if len(others) == len(mdl.materials):
        return None
    try:
        sky = model3d.load_brres(arc, skip_materials=others, out_dir=models / "textures")
    except model3d.ModelError:
        return None
    for p in sky.parts:
        p.double_sided = False
    slo, shi = (np.asarray(v, np.float64) for v in sky.bounds())
    size = np.maximum(shi - slo, 1e-3)
    x0, x1 = float(lo[0]) - 200.0, float(hi[0]) + 200.0
    zwall = float(proj.nodes[castle]["pos"][2]) - SKY_GAP
    centre = [(x0 + x1) / 2.0, SKY_SPAN_Y[0], zwall]
    s = np.array([(x1 - x0) / size[0], (SKY_SPAN_Y[1] - SKY_SPAN_Y[0]) / size[1], 0.05])
    m = np.identity(4)
    m[:3, :3] = np.diag(s)
    # quad's lower-left-near corner (lo x, lo y, hi z: the Wii's quads lean back) to the wall's
    m[:3, 3] = np.array([x0, SKY_SPAN_Y[0], zwall]) - s * np.array([slo[0], slo[1], shi[2]]) - np.array(centre)
    dae = model3d.save_dae(sky.transformed(m), models / ("CS_W%d_sky.dae" % world))
    return dae, centre


def kind_of(pid):
    if pid[0] == "K":
        return "bend"
    if pid[0] == "F":
        return "pipe" if pid[1:2] == "d" else "stop"
    c = pid[2:3]
    return {"M": "start", "T": "tower", "C": "castle", "G": "ghost", "A": "airship", "K": "toad", "J": "link",
            "S": "level", "W": "toad", "X": "level"}.get(c, "level")

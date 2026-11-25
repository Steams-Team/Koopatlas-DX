"""
kdx/events.py - a world's animations and events, as the game uses them (nsmbud-reverse F0326).

Path appear animations: a model named exactly like an unlock path (R<from><to>, as written in a point's Opens paths)
holds the scenery that appears when the path opens (a bridge rising, steps dropping in). Its root bone is "r" + the
name without the R. A skeletal animation of the same name is played once when the path opens
(CourseSelectMapMgr::initRouteAnimCore_ 0x601A11E4 sets Skeletal, Color, TexSrt and Mask route animations by the path
name); optional material animations <name>_fts (texture scroll) and <name>_fcl (colour) go with it.
Ambient: looping animations such as CS_W<n>_Synchro (scenery bobbing in time) or CS_W4_Aurora.
Events: other named animations a map event plays (W6 trapA/trapB, W8a dark-cloud demos).

    ev = analyse(world, anims)                    # anims: kdx.anim.Animations (or None: models only)
    ev["paths"] -> [{"path", "opened_by", "model", "skeletal", "frames", "loop", "extras", "issues"}]
"""
from __future__ import annotations

import math

import numpy as np

from . import rules


def openers(world):
    """{path: [(point, 'normal'|'secret')]} from the point table."""
    out = {}
    for r in world.point_rows:
        for col, ex in ((4, "normal"), (7, "secret")):
            for p in rules.paths(r[col] if len(r) > col else ""):
                out.setdefault(p, []).append((r[1], ex))
    return out


def analyse(world, anims=None):
    skel = {a["name"]: a for a in (getattr(anims, "skeletal_anims", None) or [])}
    mats = {a["name"]: a for a in (getattr(anims, "material_anims", None) or [])}
    models = [m.name for m in world.model.models]
    opened = openers(world)
    copies = {c["target"]: c for c in getattr(world, "route_copies", [])}
    makes = {m["target"]: m for m in getattr(world, "route_makes", [])}
    paths = []
    for name in sorted(set(m for m in models if rules.route_ends(m)) | set(copies) | set(makes)):
        if name in makes:
            mk = makes[name]
            paths.append({"path": name, "opened_by": opened.get(name, []), "model": True, "skeletal": True,
                          "frames": mk["frames"], "loop": False, "extras": [], "pending": None,
                          "make": make_label(mk),
                          "issues": [] if name in opened else ["no point of this map opens %s" % name]})
            continue
        a = skel.get(name)
        src = copies.get(name, {}).get("source")
        if src and not a:
            a = skel.get(src)
        extras = [s for s in ("_fts", "_fcl", "_ftp") if (name + s) in mats or (src and (src + s) in mats)]
        issues = []
        if not a:
            issues.append("no skeletal animation named %s: the model doesn't move (it may simply be shown, or use a "
                          "mask/colour animation)" % name)
        if name not in opened:
            issues.append("no point of this map opens %s (another world's point may, e.g. a cannon chain)" % name)
        paths.append({"path": name, "opened_by": opened.get(name, []), "model": name in models or bool(src),
                      "skeletal": bool(a), "frames": a.get("frames") if a else 0, "loop": a.get("loop") if a else None,
                      "extras": extras, "issues": issues, "pending": src, "make": None})
    used = {p["path"] for p in paths}
    ambient, events = [], []
    for n, a in skel.items():
        if n in used:
            continue
        (ambient if a.get("loop") else events).append({"name": n, "frames": a.get("frames"), "loop": a.get("loop"),
                                                       "model": n in models})
    for n, a in mats.items():
        base = n.rsplit("_", 1)[0]
        if base in used:
            continue
        (ambient if a.get("loop") else events).append({"name": n, "frames": a.get("frames"), "loop": a.get("loop"),
                                                       "model": base in models, "material": True})
    candidates = sorted(p for p in opened if p not in used)
    return {"paths": paths, "ambient": sorted(ambient, key=lambda x: x["name"]),
            "events": sorted(events, key=lambda x: x["name"]), "free_paths": candidates}


def make_label(mk):
    n = len(mk.get("shapes", [])) + len(mk.get("objects", []))
    what = PRESETS.get(mk["preset"], "") if mk["preset"] != "custom" else "your own keyframes"
    added = (", added object%s %s" % ("" if len(mk["objects"]) == 1 else "s", ", ".join(mk["objects"]))
             if mk.get("objects") else "")
    return "%s: %s (%d object%s%s)" % (mk["preset"], what, n, "" if n == 1 else "s", added)


def copy_offset(world, source, target):
    """Where a copied appear animation goes: moved by the offset between the two paths' first points."""
    a, b = rules.route_ends(source), rules.route_ends(target)
    pa = world.position(a[0]) if a else None
    pb = world.position(b[0]) if b else None
    if pa is None or pb is None:
        return (0.0, 0.0, 0.0)
    return tuple(float(y - x) for x, y in zip(pa, pb))


# ---- new appear animations (kdx-bfres routemake) --------------------------------------------------------------------
def _ease(t):
    return 1 - (1 - t) ** 3


PRESETS = {
    "rise": "Rises out of the ground",
    "drop": "Drops in from above with a bounce",
    "pop": "Pops up from nothing (grows with an overshoot)",
    "grow": "Grows up from the ground",
}


def preset_keys(preset, frames=90, amount=200.0, steps=8):
    """{scale: [[f, x, y, z]...], translate: [[f, x, y, z]...]} for a preset, frame 0 being the hidden pose."""
    F = float(frames)
    fr = [F * k / steps for k in range(steps + 1)]
    if preset == "rise":
        return {"translate": [[f, 0.0, -amount * (1 - _ease(f / F)), 0.0] for f in fr]}
    if preset == "drop":
        tr = []
        for f in fr:
            t = f / F
            y = amount * (1 - t / 0.7) ** 2 if t < 0.7 else amount * 0.08 * math.sin(math.pi * (t - 0.7) / 0.3)
            tr.append([f, 0.0, y, 0.0])
        return {"translate": tr, "scale": [[0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0], [F, 1.0, 1.0, 1.0]]}
    if preset == "pop":
        sc = []
        for f in fr:
            t = f / F
            s = 1.15 * _ease(t / 0.7) if t < 0.7 else 1.15 - 0.15 * (t - 0.7) / 0.3
            sc.append([f, s, s, s])
        return {"scale": sc}
    if preset == "grow":
        return {"scale": [[f, 1.0, max(0.0, _ease(f / F)), 1.0] for f in fr]}
    raise ValueError("unknown preset %r" % preset)


def terrain_shapes(world):
    """[(shape name, material, (min xyz), (max xyz))] of the terrain model's shapes that can become appear objects
    (unskinned, on the root bone at the origin)."""
    from . import worldedit
    tm = worldedit.terrain_model_name(world)
    model = next(m for m in world.model.models if m.name == tm)
    ok = {s.name for s in model.shapes if s.skin_count == 0 and 0 <= s.bone_index < len(model.bones)
          and model.bones[s.bone_index].parent < 0 and sum(abs(c) for c in model.bones[s.bone_index].world_position) < 1e-4}
    out = []
    for mname, sname, mat, part in worldedit.world_shapes(world, [tm]):
        if sname in ok and len(part.positions):
            P = part.positions
            out.append((sname, mat, tuple(float(v) for v in P.min(0)), tuple(float(v) for v in P.max(0))))
    return tm, out


def added_objects(world):
    """[(name, file, (min xyz), (max xyz))] of the objects added to the world from DAE/FBX/OBJ (model_edits "add"):
    they are merged into the ground model on export, so they can get an appear animation too."""
    from . import modelref
    out = []
    for ref in (getattr(world, "model_edits", None) or {}).get("add", []):
        try:
            m = modelref.build(ref, None)
            P = np.concatenate([p.positions for p in m.parts if len(p.positions)])
        except Exception:
            continue
        out.append((ref.get("name", "object"), ref.get("path", ""), tuple(float(v) for v in P.min(0)),
                    tuple(float(v) for v in P.max(0))))
    return out


def pivot_of(boxes):
    """The bottom centre of the shapes' bounds: what scales and rises stay anchored to."""
    lo = [min(b[0][i] for b in boxes) for i in range(3)]
    hi = [max(b[1][i] for b in boxes) for i in range(3)]
    return [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2], hi[1] - lo[1]


# ---- previewing pending appear animations ---------------------------------------------------------------------------
_PENDING = {}


def pending_key(world):
    import hashlib
    import json
    h = hashlib.sha1(bytes(world.bfres_bytes)).hexdigest()
    return h + json.dumps([world.route_copies, world.route_makes, world.model_edits], sort_keys=True, default=str)


def pending_preview(world):
    """A temp build of the world's BFRES with its pending appear animations (copies, new ones, and the model edits they
    may take objects from), for the 3D view: {"bfres": kdx.bfres.BfresFile, "anims": kdx.anim.Animations,
    "replace": [model names to draw from this build instead of the world's own]}. Cached until something changes."""
    from . import anim, bfres
    key = pending_key(world)
    hit = _PENDING.get(id(world))
    if hit and hit[0] == key:
        return hit[1]
    data = world.build_bfres(marker=False)
    bf = bfres.BfresFile.parse(data)
    own = {m.name for m in world.model.models}
    replace = {m.name for m in bf.models if m.name not in own}
    replace |= {m["source_model"] for m in world.route_makes}
    edits = world.model_edits or {}
    if world.has_model_edits():
        from . import worldedit
        if edits.get("add"):
            replace.add(worldedit.terrain_model_name(world))
        replace |= {r[0] for r in edits.get("remove", [])}
        replace |= {k.split("/", 1)[0] for k in (edits.get("shapes") or {})}
    out = {"bfres": bf, "anims": anim.Animations.load_bytes(data), "replace": sorted(replace)}
    _PENDING.clear()
    _PENDING[id(world)] = (key, out)
    return out

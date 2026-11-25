"""
kdx/worldedit.py - editing the models inside a game world archive (CS_W*.szs), alongside its points and paths.

    shapes = world_shapes(world)                 # [(model, shape, material, ObjModel part)] in world space
    edits = {"remove": [["CS_W1_World", "pCube342_1__mtIshiyama"]],          # shapes taken out
             "textures": {"Grass_alb": "C:/my/grass.png"},                    # textures given new pixels
             "add": [{"name": "Castle", "path": "castle.dae", "position": [x, y, z], "rotation": [...], "scale": 1}],
             "shapes": {"CS_W1_World/pCube12__mtDokan": {"matrix": [16 floats], "pivot": [x, y, z],  # one shape moved,
                                                          "texture": "C:/my/red.png"}}}                # retextured
    new_bfres = apply(world.bfres_bytes, edits)  # through the model writer's "edit" command; everything else stays
    export_dae(world, ["CS_W1_World"], "w1.dae") # any of the world's models as COLLADA, textures as PNG

Added objects are kdx.modelref references (so they keep their material edits), written into the world's terrain
model (CS_W<n>_World) with copies of its own materials, as World Maker exports do. The World object keeps the edits in
world.model_edits and applies them when it builds its archive.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np

from . import bfres, model3d, modelref
from .objmodel import ObjModel, Part

EMPTY = {"remove": [], "textures": {}, "add": [], "shapes": {}}


def empty():
    return copy.deepcopy(EMPTY)


def has_edits(edits):
    return bool(edits and (edits.get("remove") or edits.get("textures") or edits.get("add")
                           or any(shape_edit_active(e) for e in (edits.get("shapes") or {}).values())))


# ------------------------------------------------------------------------------------------------- one shape's edit
# A shape edit holds the 4x4 the 3D view's gizmo works on: "matrix" places the shape's own origin, "pivot" (the centre
# of its bounds in the game's world space) is where that origin was. The shape's move in world space is
# matrix @ translate(-pivot); identity when the matrix is translate(pivot).
def shape_key(model, shape):
    return "%s/%s" % (model, shape)


def split_key(key):
    return tuple(key.split("/", 1))


def translation(v):
    m = np.identity(4)
    m[:3, 3] = v
    return m


def world_move(edit):
    """The shape's move in world space (4x4), or None."""
    if not edit or edit.get("matrix") is None:
        return None
    return np.asarray(edit["matrix"], np.float64).reshape(4, 4) @ translation(-np.asarray(edit.get("pivot", (0, 0, 0)), np.float64))


def shape_edit_active(edit):
    m = world_move(edit)
    return bool(edit and (edit.get("texture") or (m is not None and not np.allclose(m, np.identity(4), atol=1e-6))))


def local_moves(world, model_name, shape_name, move):
    """([row-major 16 floats], per skin slot) the engine applies to the vertices as stored: bone space for a shape on a
    bone (skin count 0), each slot's bone for rigid skinning, model space for smooth skinning (bfres.build_mesh)."""
    model = next(m for m in world.model.models if m.name == model_name)
    shape = next(s for s in model.shapes if s.name == shape_name)
    bones = model.bones

    def local(b):
        bw = np.identity(4)
        if 0 <= b < len(bones):
            bw[:3, :] = np.asarray(bones[b].world, np.float64)[:12].reshape(3, 4)     # row-major 3x4
        return (np.linalg.inv(bw) @ move @ bw).ravel().tolist()
    if shape.skin_count == 0 and 0 <= shape.bone_index < len(bones):
        return [local(shape.bone_index)], False
    if shape.skin_count == 1:
        slots = list(shape.skin_bone_indices) or [shape.bone_index]
        return [local(b) for b in slots], True
    return [move.ravel().tolist()], False


def terrain_model_name(world):
    return next((m.name for m in world.model.models if m.name.endswith("_World")), world.model.models[0].name)


# ------------------------------------------------------------------------------------------------- display / export
def _texture_png(world, name, cache, textures):
    """PNG path of one of the world's textures (decoded once into the cache folder), or None."""
    if not name:
        return None
    out = cache / (re.sub(r"[^\w.-]", "_", name) + ".png")
    if not out.exists():
        tex = textures.get(name)
        if tex is None:
            return None
        try:
            model3d.write_png(out, tex.rgba())
        except Exception:
            return None
    return str(out)


def world_textures(world):
    from . import texture
    try:
        return texture.read_bntx(world.model.external_bytes("textures.bntx"))
    except Exception:
        return {}


def cache_for(world):
    return model3d.cache_dir("world-%s-%s" % (world.id, hashlib.sha1(bytes(world.bfres_bytes[:65536])).hexdigest()[:12]))


def world_shapes(world, models=None):
    """[(model name, shape name, material name, Part)] for the world's shapes, in world space (bind pose). The Part's
    texture is its material's albedo, decoded to PNG; its mode and culling follow the material."""
    cache = cache_for(world)
    textures = world_textures(world)
    out = []
    for model in world.model.models:
        if models is not None and model.name not in models:
            continue
        for shape in model.shapes:
            try:
                m = bfres.build_mesh(model, {shape.name})
            except Exception:
                continue
            if not m.faces:
                continue
            V = np.asarray(m.vertices, np.float32)
            F = np.asarray([f[:3] for f in m.faces], np.int64)
            UV = np.asarray(m.uvs, np.float32) if len(m.uvs) == len(m.vertices) else np.zeros((len(V), 2), np.float32)
            have = np.array([n is not None for n in m.normals]) if len(m.normals) == len(m.vertices) else np.zeros(len(V), bool)
            N = np.zeros((len(V), 3), np.float32)
            if have.any():
                N[have] = np.asarray([n for n in m.normals if n is not None], np.float32)
            tri = V[F]
            fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
            nrm = np.where(have[F][..., None], N[F], fn[:, None, :])
            mat = model.materials[shape.material_index] if shape.material_index < len(model.materials) else None
            tex = _texture_png(world, mat.albedo if mat else None, cache, textures)
            from .scene import material_color
            part = Part(shape.name, tri.reshape(-1, 3), nrm.reshape(-1, 3).astype(np.float32), UV[F].reshape(-1, 2), tex,
                        material_color(model.name, mat.name if mat else ""), mat.blend_mode if mat else "opaque",
                        (mat.cull == "none") if mat else False)
            out.append((model.name, shape.name, mat.name if mat else "", part))
    return out


def export_dae(world, models, path, shapes=None):
    """Write some of the world's models (or all, models=None) as one file, a material per shape: COLLADA, or binary
    FBX when path ends in .fbx. shapes: only these (model, shape) pairs."""
    parts = []
    for mname, sname, _mat, p in world_shapes(world, set(models) if models else None):
        if shapes is not None and (mname, sname) not in shapes:
            continue
        q = p.copy()
        q.material = "%s__%s" % (mname, sname) if len(models or ()) != 1 else sname
        parts.append(q)
    if not parts:
        raise ValueError("Nothing to export: the chosen models have no triangles.")
    return model3d.save_model(ObjModel(parts), path)


export_model = export_dae


# ------------------------------------------------------------------------------------------------------- applying
def apply(bfres_bytes, edits, world, max_texture=2048, keep_temp=False, info=None):
    """New BFRES bytes with the edits applied by the model writer (kdx-bfres edit). info (a dict) receives
    "object_shapes": {added object name: [shape names in the terrain model]} for the objects that have their own appear
    animation (world.route_makes "objects"): their parts are kept apart from the other objects'."""
    from . import export2d
    from .texture import encode_bc1
    from PyQt5 import QtGui
    ok, msg = export2d.engine_status()
    if not ok:
        raise export2d.ExportError(msg)
    tmp = Path(tempfile.mkdtemp(prefix="kdx_worldedit_"))
    try:
        (tmp / "in.bfres").write_bytes(bytes(bfres_bytes))
        spec = {"input": str(tmp / "in.bfres"), "output": str(tmp / "out.bfres"),
                "remove_shapes": [{"model": m, "shape": s} for m, s in edits.get("remove", [])],
                "replace_textures": [], "textures": [], "add": None}
        for i, (name, png) in enumerate(sorted(edits.get("textures", {}).items())):
            img = QtGui.QImage(png)
            if img.isNull():
                raise export2d.ExportError("The texture %s cannot be read." % png)
            rgba, tw, th = export2d.image_rgba(png, max_texture, (1, 1, 1))
            px = np.frombuffer(rgba, np.uint8).reshape(th, tw, 4)
            f = tmp / ("rep%02d" % i)
            if px[..., 3].min() < 250:                           # keeps its alpha: RGBA8
                f.write_bytes(rgba)
                spec["replace_textures"].append({"name": name, "width": tw, "height": th, "rgba": str(f)})
            else:
                f.write_bytes(encode_bc1(px))
                spec["replace_textures"].append({"name": name, "width": tw, "height": th, "bc1": str(f)})
        removed = {tuple(x) for x in edits.get("remove", [])}
        albedo = {(mo.name, sh.name): mo.materials[sh.material_index].albedo for mo in world.model.models
                  for sh in mo.shapes if sh.material_index < len(mo.materials)}
        spec["shape_edits"] = []
        for i, (key, e) in enumerate(sorted((edits.get("shapes") or {}).items())):
            mname, sname = split_key(key)
            if (mname, sname) in removed or not shape_edit_active(e):
                continue
            item = {"model": mname, "shape": sname}
            move = world_move(e)
            if move is not None and not np.allclose(move, np.identity(4), atol=1e-6):
                item["matrices"], item["per_slot"] = local_moves(world, mname, sname, move)
            if e.get("texture"):
                rgba, tw, th = export2d.image_rgba(e["texture"], max_texture, (1, 1, 1))
                px = np.frombuffer(rgba, np.uint8).reshape(th, tw, 4)
                f = tmp / ("shp%02d" % i)
                tex = {"name": "kdxshp_%02d" % i, "width": tw, "height": th}
                if px[..., 3].min() < 250:
                    f.write_bytes(rgba); tex["rgba"] = str(f)
                else:
                    f.write_bytes(encode_bc1(px)); tex["bc1"] = str(f)
                spec["textures"].append(tex)
                item["texture"], item["replace"] = tex["name"], albedo.get((mname, sname))
            spec["shape_edits"].append(item)
        adds = edits.get("add", [])
        if adds:
            parts = []
            base = None
            for ref in adds:
                m = modelref.build(ref, base)
                for p in m.parts:
                    q = p.copy()
                    q.material = "%s/%s" % (ref.get("name", "object"), p.material)
                    parts.append(q)
            animated = {o for m in getattr(world, "route_makes", []) for o in m.get("objects", [])}
            mtex, mparts = export2d.model_parts(ObjModel(parts), tmp, max_texture, prefix="kdxadd", separate=animated)
            if info is not None:
                info["object_shapes"] = {}
                for p in mparts:
                    if p.get("object"):
                        info["object_shapes"].setdefault(p.pop("object"), []).append(p["name"])
            helpers = {"specular": "kdxadd_black", "bake0": "kdxadd_bake0", "bake1": "kdxadd_bake1"}
            for p in mparts:
                p["textures"] = dict(p["textures"], **helpers)
            (tmp / "black.rgba").write_bytes(b"\x00\x00\x00\xff" * 256)
            (tmp / "bake0.rgba").write_bytes(export2d.BAKE0)
            spec["textures"] += mtex + [
                {"name": "kdxadd_black", "width": 16, "height": 16, "rgba": str(tmp / "black.rgba")},
                {"name": "kdxadd_bake0", "width": 16, "height": 16, "rgba": str(tmp / "bake0.rgba")},
                {"name": "kdxadd_bake1", "width": 16, "height": 16, "neutral_light": export2d.NEUTRAL_LIGHT}]
            spec["add"] = {"model": terrain_model_name(world), "parts": mparts}
        (tmp / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        export2d.run_engine("edit", tmp / "spec.json")
        out = (tmp / "out.bfres").read_bytes()
        bfres.BfresFile.parse(out)                                # our own reader must read what the engine wrote
        return out
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)

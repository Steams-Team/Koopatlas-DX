"""
kdx/export2d.py - turn a World Maker project into a world archive the game can load.

    written, report = export_world(project, image, template_archive, mod_root, model_dir)

The vanilla archive of the same world is the template: its shader archive is kept, its model is rebuilt by the
kdx-bfres engine (BfresLibrary) with
  - the terrain: one flat mesh covering the map, using a copy of a vanilla ground material whose texture is the painted
    map (an RGBA8 texture added to the model's texture archive);
  - the map-point model: one bone per point and bend at its position, with a copy of a vanilla marker (level icon for
    W points, path dot for F points, none for K bends), plus the template's world-link bones (connectW<k>,
    GrandCentral) where they were, so the game can still place the world among the others;
  - a path bone R<from><to> before the walk of every path, in the order the game reads paths from (rules.map_paths;
    without them the map loads but the player cannot move);
  - hidden placeholders for the points and enemy flags the game's code needs (Project.game_nodes): without them the
    game reads a null pointer and crashes (a W1 map without W1S0 crashed when the map music was chosen);
and the point/route tables are written in the game's CSV format (same encoding and line endings as the template).
The MarioOffset (camera) table is updated in the confirmed layout: this archive's block, in point-table order.
"""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import re
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import archive, bfres, offsets, pictures, rules
from .project2d import HIDDEN_DEPTH
from .world import World, csv_encode, mod_model_dir, point_csv_encode

ENGINE_DIR = Path(__file__).resolve().parent / "engine"
ENGINE_DLL = ENGINE_DIR / "kdx-bfres.dll"
DOTNET_URL = "https://dotnet.microsoft.com/download/dotnet/8.0"


class ExportError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------- the engine
def find_dotnet():
    exe = shutil.which("dotnet")
    if exe:
        return exe
    for cand in (r"C:\Program Files\dotnet\dotnet.exe", r"C:\Program Files (x86)\dotnet\dotnet.exe",
                 "/usr/share/dotnet/dotnet", "/usr/lib/dotnet/dotnet", "/usr/local/share/dotnet/dotnet"):
        if os.path.exists(cand):
            return cand
    return None


def engine_status():
    """(ok, message) - whether the model writer can run on this computer."""
    if not ENGINE_DLL.exists():
        return False, "The model writer (kdx/engine/kdx-bfres.dll) is missing from this copy of Koopatlas DX."
    if not find_dotnet():
        return False, ("The model writer needs the free .NET runtime (version 8 or newer). Install the \".NET Runtime\" "
                       "from %s and restart Koopatlas DX." % DOTNET_URL)
    return True, "Model writer ready (BfresLibrary through .NET)."


def run_engine(*args, timeout=600):
    dotnet = find_dotnet()
    if not dotnet:
        raise ExportError(engine_status()[1])
    flags = 0x08000000 if os.name == "nt" else 0                 # CREATE_NO_WINDOW: no console window flashes up
    p = subprocess.run([dotnet, str(ENGINE_DLL)] + [str(a) for a in args], capture_output=True, text=True,
                       timeout=timeout, creationflags=flags)
    if p.returncode != 0:
        raise ExportError("The model writer failed:\n" + (p.stderr or p.stdout).strip()[-1500:])
    return p.stdout


# --------------------------------------------------------------------------------------------- pieces
def pow2_size(w, h, max_px):
    """The power-of-two texture size nearest the map's own (in log scale, so 1848 px -> 2048, not 1024: rounding
    each side down squashed the Newer Yoshi's Island 4464x1848 into 2048x512), at most max_px a side."""
    def p2(n):
        return 1 << max(0, int(round(math.log2(max(1.0, n)))))
    return max(16, min(max_px, p2(w))), max(16, min(max_px, p2(h)))


def bc1_mips(rgba, tw, th):
    """RGBA8 bytes (th x tw) -> (BC1 blocks of every mip level down to 1x1, one after the other; level count). Without
    mips the game samples the full-size ground at every distance and the far side shimmers."""
    from PIL import Image
    from .texture import encode_bc1
    base = Image.fromarray(np.frombuffer(rgba, np.uint8).reshape(th, tw, 4), "RGBA")
    out, level = [], 0
    while True:
        lw, lh = max(1, tw >> level), max(1, th >> level)
        a = np.asarray(base if level == 0 else base.resize((lw, lh), Image.BOX))
        pw, ph = (-lw) % 4, (-lh) % 4                       # BC1 works on 4x4 blocks: pad the last levels
        if pw or ph:
            a = np.pad(a, ((0, ph), (0, pw), (0, 0)), mode="edge")
        out.append(encode_bc1(a))
        if lw == 1 and lh == 1:
            return b"".join(out), level + 1
        level += 1


def bc1_bytes(tw, th):
    """Bytes a BC1 texture with all its mips takes."""
    n, level = 0, 0
    while True:
        lw, lh = max(1, tw >> level), max(1, th >> level)
        n += ((lw + 3) // 4) * ((lh + 3) // 4) * 8
        if lw == 1 and lh == 1:
            return n
        level += 1


def bake_texture(image, max_px=2048, size=None):
    """QImage of the painted map -> (RGBA8 bytes, width, height), scaled to power-of-two sides (or to size)."""
    from PyQt5 import QtCore, QtGui
    tw, th = size or pow2_size(image.width(), image.height(), max_px)
    img = image.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation).convertToFormat(QtGui.QImage.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.byteCount())
    raw = bytes(ptr)
    stride = img.bytesPerLine()
    if stride != tw * 4:
        raw = b"".join(raw[y * stride:y * stride + tw * 4] for y in range(th))
    return raw, tw, th


MAX_TERRAIN_CELLS = 128      # per side when the map is bigger than MAX_TERRAIN_VERTS allows at one cell per tile
MAX_TERRAIN_VERTS = 65535    # 16-bit indices


def terrain_mesh(project, grid=16):
    """A grid over the whole map: (vertex floats, vertex count, uint16 indices). Flat maps use a grid x grid mesh; maps
    with hills one cell per tile (at most MAX_TERRAIN_CELLS per side), heights from Project.height_at, normals from the
    slope.

    Each vertex: position xyz, normal xyz, uv, lightmap uv, colour rgba (14 floats)."""
    W, H = project.width * project.tile_size, project.height * project.tile_size
    hilly = project.heights is not None and any(project.heights)
    gx = project.width if hilly else grid
    gz = project.height if hilly else grid
    if hilly and (gx + 1) * (gz + 1) > MAX_TERRAIN_VERTS:          # one cell per tile when it fits (cliffs stay sharp)
        gx, gz = min(gx, MAX_TERRAIN_CELLS), min(gz, MAX_TERRAIN_CELLS)
    verts = []
    for j in range(gz + 1):
        for i in range(gx + 1):
            u, v = i / gx, j / gz
            tx, ty = u * project.width, v * project.height
            y = project.ground_height_at(tx, ty)                # the islands' tiers stand on this (kdx.islands)
            if hilly:
                e = 0.5                                          # tiles either side for the slope
                dx = (project.ground_height_at(tx + e, ty) - project.ground_height_at(tx - e, ty)) / (2 * e * project.tile_size)
                dz = (project.ground_height_at(tx, ty + e) - project.ground_height_at(tx, ty - e)) / (2 * e * project.tile_size)
                n = math.sqrt(dx * dx + 1 + dz * dz)
                nx, ny, nz = -dx / n, 1 / n, -dz / n
            else:
                nx, ny, nz = 0.0, 1.0, 0.0
            verts += [u * W, y, v * H, nx, ny, nz, u, v, u, v, 1.0, 1.0, 1.0, 1.0]
    idx = []
    for j in range(gz):
        for i in range(gx):
            a = j * (gx + 1) + i
            b, c, d = a + 1, a + gx + 1, a + gx + 2
            idx += [a, c, b, b, c, d]
    return verts, (gx + 1) * (gz + 1), idx


def image_rgba(path, max_px=1024, color=(0.8, 0.8, 0.8)):
    """(RGBA8 bytes, width, height) of a texture file, scaled to power-of-two sides; a 16x16 swatch of `color` when
    there is no file or it cannot be read."""
    from PyQt5 import QtCore, QtGui
    img = QtGui.QImage(str(path)) if path else QtGui.QImage()
    if img.isNull():
        px = bytes(int(max(0.0, min(1.0, c)) * 255) for c in color) + bytes([255])
        return px * 256, 16, 16
    tw, th = pow2_size(img.width(), img.height(), max_px)
    img = img.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation).convertToFormat(QtGui.QImage.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.byteCount())
    raw = bytes(ptr)
    stride = img.bytesPerLine()
    if stride != tw * 4:
        raw = b"".join(raw[y * stride:y * stride + tw * 4] for y in range(th))
    return raw, tw, th


ROUTE_PART = re.compile(r"mt(R\w{8})([a-h])$")
SHOWN_MASK = -0.8                     # MaskRouteAnimation's open offset (0x6022A1D0): the whole path shown
FLAT_NORMAL = bytes((0, 0, 255, 255))  # a normal map pixel the route shader reads as (0, 0): the surface normal as is


def tangent_frame(pos, nrm, uv):
    """Per-corner tangent (x, y, z, handedness) and binormal (x, y, z, 1) from each triangle's uvs, at right angles to
    the normal (the route shader reads _t0 and _b0)."""
    p = np.asarray(pos, np.float64).reshape(-1, 3, 3)
    t = np.asarray(uv, np.float64).reshape(-1, 3, 2)
    n = np.asarray(nrm, np.float64)
    e1, e2, d1, d2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1.0, r)[:, None]
    tan = np.repeat((e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r, 3, axis=0)
    bit = np.repeat((e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r, 3, axis=0)
    tan -= n * (n * tan).sum(axis=1, keepdims=True)
    bad = np.linalg.norm(tan, axis=1) < 1e-9
    tan[bad] = np.cross(n[bad], [0.0, 0.0, 1.0]) + np.cross(n[bad], [1.0, 0.0, 0.0]) * 1e-3
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-12)
    w = np.where((np.cross(n, tan) * bit).sum(axis=1) < 0, -1.0, 1.0)
    b = np.cross(n, tan) * w[:, None]
    return np.c_[tan, w], np.c_[b, np.ones(len(b))]


def open_routes(project):
    """Every path some point's exit opens (point table columns 4 and 7): the paths the game hides until then."""
    return {t for n in project.nodes.values() for c in (4, 7) for t in rules.split((list(n.get("row") or []) + [""] * 9)[c])}


def drawn_alike(parts, separate=()):
    """The parts with those drawn alike (same texture or colour, mode and wrap) joined into one: every scenery object
    brings its own parts, and each part costs the world archive a shape and a material (Yoshi's Island's 416 came to
    3.5 MB). Route parts (their own material and reveal mask each) stay as they are. Parts of the objects named in
    `separate` (material "<object>/<material>": objects with their own appear animation) are only joined with parts of
    the same object, and the joined part says which object it is (kdx_object)."""
    from .objmodel import Part
    groups, out = {}, []
    for p in parts:
        if ROUTE_PART.match(p.material.split("/")[-1]) and getattr(p, "uvs2", None) is not None:
            out.append(p)
            continue
        obj = p.material.split("/")[0] if "/" in p.material else None
        obj = obj if obj in separate else None
        key = (p.texture, tuple(p.color) if not p.texture else None, getattr(p, "mode", "opaque"),
               tuple(getattr(p, "wrap", ("repeat", "repeat"))), obj)
        if key not in groups:
            groups[key] = []
            out.append(groups[key])
        groups[key].append(p)
    merged = []
    for item in out:
        if not isinstance(item, list):
            merged.append(item)
        elif len(item) == 1:
            merged.append(item[0])
        else:
            first = item[0]
            q = Part(first.material, np.concatenate([p.positions for p in item]),
                     np.concatenate([p.normals for p in item]), np.concatenate([p.uvs for p in item]), first.texture,
                     first.color, getattr(first, "mode", "opaque"), first.double_sided)
            q.wrap = getattr(first, "wrap", ("repeat", "repeat"))
            merged.append(q)
    for p in merged:
        obj = p.material.split("/")[0] if "/" in p.material else None
        if obj in separate:
            p.kdx_object = obj
    return merged


def model_parts(model, tmp, max_texture=2048, prefix="kdx", routes=None, separate=()):
    """Terrain parts and their textures for the model writer, from a placed kdx.objmodel model: one part per material,
    every triangle corner its own vertex (14 floats: position, normal, uv, lightmap uv, white colour). Names start with
    prefix (kdx_m00, mtKdx00, kdx_model_00), so parts added to a native world (prefix kdxadd) cannot clash.

    A part named mt<route><a-h> with reveal-mask uvs (Part.uvs2: NSMBW's paths) becomes a route part: a copy of the
    template's path material, 24 floats per corner (+ tangent, binormal, mask uv), an edge mask from its texture's alpha
    and its hidden offset, so the game keeps it hidden until the route opens. routes: the paths the map opens (None:
    any); a route nobody opens is exported shown (it would stay hidden for good)."""
    from .texture import encode_bc1
    textures, parts = [], []
    done = {}                                    # (texture, colour, alpha kept) -> texture name: shared textures once
    edges = {}                                   # albedo texture name -> edge mask texture name
    used = set()                                 # route material names given out
    for i, part in enumerate(drawn_alike(model.parts, separate)):
        alpha = getattr(part, "mode", "opaque") != "opaque"
        key = (part.texture, tuple(part.color) if not part.texture else None, alpha)
        tex = done.get(key)
        if tex is None:
            tex = done[key] = "%s_m%02d" % (prefix, len(done))
            rgba, tw, th = image_rgba(part.texture, max_texture, part.color)
            if alpha:
                # Cut-out and blended parts keep their alpha in RGBA8 (this BC1 encoder has no alpha). Wii-sized
                # textures (up to 256 px) stay small, so only these parts pay for it.
                (tmp / (tex + ".rgba")).write_bytes(rgba)
                textures.append({"name": tex, "width": tw, "height": th, "rgba": str(tmp / (tex + ".rgba"))})
            else:
                # BC1 like the game's own ground textures: a 3D world's textures in RGBA8 made test mod 11's model
                # 22.5 MB, past the largest vanilla world (W5a, 15.9 MB); in BC1 they take an eighth of that.
                (tmp / (tex + ".bc1")).write_bytes(encode_bc1(np.frombuffer(rgba, np.uint8).reshape(th, tw, 4)))
                textures.append({"name": tex, "width": tw, "height": th, "bc1": str(tmp / (tex + ".bc1"))})
        tex_file = "%s_v%02d" % (prefix, i)
        n = len(part.positions)
        m = ROUTE_PART.match(part.material.split("/")[-1])
        route = m is not None and getattr(part, "uvs2", None) is not None and len(part.uvs2) == n
        v = np.empty((n, 24 if route else 14), np.float32)
        v[:, 0:3], v[:, 3:6], v[:, 6:8], v[:, 8:10], v[:, 10:14] = part.positions, part.normals, part.uvs, part.uvs, 1.0
        if route:
            v[:, 14:18], v[:, 18:22] = tangent_frame(part.positions, part.normals, part.uvs)
            v[:, 22:24] = part.uvs2
            if tex not in edges:                 # the route shader's edge mask (_o1): white off the road, from its alpha
                edges[tex] = tex + "_e"
                src = tmp / (tex + ".rgba")                  # opaque albedos (BC1) have no alpha: road everywhere
                tw_, th_ = next((t["width"], t["height"]) for t in textures if t["name"] == tex) if src.exists() else (16, 16)
                a = np.frombuffer(src.read_bytes(), np.uint8).reshape(-1, 4)[:, 3] if src.exists() \
                    else np.full(16 * 16, 255, np.uint8)
                e = np.repeat((255 - a)[:, None], 4, axis=1)
                e[:, 3] = 255                    # opaque: BC1, an eighth of RGBA8
                (tmp / (edges[tex] + ".bc1")).write_bytes(encode_bc1(e.reshape(th_, tw_, 4)))
                textures.append({"name": edges[tex], "width": tw_, "height": th_, "bc1": str(tmp / (edges[tex] + ".bc1"))})
            rname, letter = m.group(1), m.group(2)
            while "mt%s%s" % (rname, letter) in used and letter < "h":
                letter = chr(ord(letter) + 1)
            mname = "mt%s%s" % (rname, letter)
            used.add(mname)
            off = part.mask_offset if part.mask_offset is not None else SHOWN_MASK
            if routes is not None and rname not in routes:
                off = max(off, SHOWN_MASK)
        # Corners with identical data share one vertex: the game keeps the whole world archive in a fixed heap, and
        # unshared corners made Yoshi's Island 28 MB, so the next world's archive no longer fit (crash at boot).
        v, idx = np.unique(v, axis=0, return_inverse=True)
        idx = idx.reshape(-1)
        n = len(v)
        (tmp / (tex_file + ".vtx")).write_bytes(np.ascontiguousarray(v).tobytes())
        wide = n > 0xFFFF
        (tmp / (tex_file + ".idx")).write_bytes(idx.astype(np.uint32 if wide else np.uint16).tobytes())
        parts.append({"name": "%s_model_%02d" % (prefix, i), "material_name": "mt%s%02d" % (prefix.capitalize(), i),
                      "_src": str(part.texture) if part.texture else None,
                      "vertices": str(tmp / (tex_file + ".vtx")),
                      "vertex_count": n, "indices": str(tmp / (tex_file + ".idx")), "index_format": "u32" if wide else "u16",
                      "mode": getattr(part, "mode", "opaque"), "wrap": list(getattr(part, "wrap", ("repeat", "repeat"))),
                      "textures": dict(LIGHTING, albedo=tex)})
        if route:
            parts[-1].update(material_name=mname, route=True, mask_offset=float(off), mode="translucent")
            # The Wii's paths have no normal map. The route shader reads its normal map (RouteW1_nml, BC5 SNORM) as
            # signed x/y as stored, so "flat" is 0, not 128: grey 128 read as +0.5 tilted every road normal about 45
            # degrees along its tangent, and road pieces facing one way came out dark (test mod 9).
            parts[-1]["textures"].update(edge_mask=edges[tex], normal="kdx_flatnml")
            if not any(t["name"] == "kdx_flatnml" for t in textures):
                (tmp / "kdx_flatnml.rgba").write_bytes(FLAT_NORMAL * 256)
                textures.append({"name": "kdx_flatnml", "width": 16, "height": 16, "rgba": str(tmp / "kdx_flatnml.rgba")})
        if getattr(part, "kdx_object", None):
            parts[-1]["object"] = part.kdx_object
    return textures, parts


# The lighting textures of a copied ground material. The template's own lightmaps (lmDif/lmSpc: 16x16 light spheres,
# about 0.4 bright) stay; replacing them with white made test mod 10 2.5 times too bright, washed out, its sand white.
# The baked textures belong to the template's own geometry, so they are replaced by what vanilla has on lit open
# grass (World 1 mtGrass, sampled where its bake maps fall on flat ground): occlusion ~240 (Bake_*_b00, one channel),
# baked light (Bake_*_b01) with alpha ~6 and, once scaled by the material's gsys_bake_light_scale, about 0.71 bright.
# The engine picks the light's colour so that the scaled light is grey (NEUTRAL_LIGHT), whatever the template's tint.
LIGHTING = {"specular": "kdx_black", "bake0": "kdx_bake0", "bake1": "kdx_bake1"}
BAKE0 = bytes((240, 240, 240, 255)) * 256
NEUTRAL_LIGHT = [0.71, 6]


def template_has_route_material(template_world, terrain_model):
    """Does the template's ground model have a road material (mt<route><a-h>) the model writer can copy?"""
    need = {"_p0", "_n0", "_t0", "_b0", "_u0", "_u1", "_u2"}   # the engine's RouteNeeded (RouteDonor)
    for m in template_world.model.models:
        if m.name != terrain_model:
            continue
        for sh in m.shapes:
            mat = m.materials[sh.material_index] if sh.material_index < len(m.materials) else None
            vb = m.vertex_buffers[sh.vertex_buffer_index] if sh.vertex_buffer_index < len(m.vertex_buffers) else None
            if mat is not None and vb is not None and ROUTE_PART.match(mat.name or "") and set(vb.attributes) == need:
                return True
    return False


def marker_shapes(template_world):
    """Names of a vanilla level-icon marker and a vanilla path-dot marker in the template's map-point model."""
    shapes = [s.name for s in template_world.marker.shapes]
    level = next((s for s in shapes if len(s) > 4 and s[0] == "W" and s[2] == "0" and s.endswith("__mtCourseOBJ")), None)
    stop = next((s for s in shapes if s[:1] == "F" and s.endswith("__mtCourseOBJ")), None)
    # W8a and W9a have no numbered level or no F stop marker: any point marker of the model will do (they are all the
    # same editor placeholder quad, mtCourseOBJ)
    level = level or next((s for s in shapes if s[:1] == "W" and s.endswith("__mtCourseOBJ")), None)
    stop = stop or next((s for s in shapes if s[:1] in "FK" and s.endswith("__mtCourseOBJ")), None) or level
    level = level or stop                                    # W9a: two F stops and the star gate only
    if not level or not stop:
        raise ExportError("The template world has no level/path markers to copy.")
    return {"level": level, "stop": stop}


# --------------------------------------------------------------------------------------------- on to the next world
def link_shift(template, project, pid, here, walks):
    """How far to move the template's walk so it starts next to the map's point pid (at `here`).

    By default the template's point lands exactly on the map's. But a point keeps one neighbour per direction
    (rules.heading), so when the walk would leave pid the same way as one of the map's own routes, the walk is placed on
    another side of pid instead (same distance, turned by 90, 180 or 270 degrees), keeping its own shape."""
    there = template.position(pid)
    d = [a - b for a, b in zip(here, there)]
    walk = next((w for w in walks if w), None)
    if not walk:
        return d
    first = template.position(walk[0])
    second = template.position(walk[1]) if len(walk) > 1 else None
    pos = {p: project.world_position(p) for p in project.nodes}
    used = set()
    for r in project.routes:
        a, b = rules.route_ends(r["id"])
        other = b if a == pid else a if b == pid else None
        if other in pos:
            used.add(rules.heading(here, pos[other]))
    vx, vy, vz = (a - b for a, b in zip(first, there))
    onward = rules.heading(first, second) if second else None        # the walk's own next step, unchanged by the move
    spots = [(here[0] + ox, here[1] + vy, here[2] + oz) for ox, oz in ((vx, vz), (-vz, vx), (-vx, -vz), (vz, -vx))]
    ok = [s for s in spots if rules.heading(here, s) not in used and (rules.heading(s, here) != onward or onward is None)]
    for spot in sorted(ok, key=lambda s: rules.heading(here, s) is None):   # a compass direction before the up/down pair
        return [a - b for a, b in zip(spot, first)]
    raise ExportError("Every side of %s already has a route, so the way on to the next world has no free direction to "
                      "leave it. Free one side of %s, or untick 'Castle leads on to the next world'." % (pid, pid))


def world_link(template, project):
    """The vanilla way on to the next worlds, moved to where the map has the same exit point.

    In the template, a point whose exit opens paths ending on another world's map owns the walk there as a path bone and
    route segments (W1C0 opens RW1C0W2J0: F191 … K19e, then W2J0, the junction in World 2's map). When the map has a
    point of that name, its exit gets the template's nodes and paths, and the walk is copied, shifted by (map point -
    template point). The world-link bones get the same shift (see export_world), so the next world still lines up with
    the far end of the walk (0x601A02F8): in the shared 3D space the map's castle sits where the vanilla castle was.
    Returns (copy of the project with the link added, shift for the link bones or None, notes)."""
    tnames = {r[1] for r in template.point_rows}
    trows = {r[1]: r for r in template.point_rows}
    tpaths = dict(rules.paths_from_bones([b.name for b in template.marker.bones]))
    troutes = {frozenset(rules.route_ends(r[0])): r for r in template.route_rows if r and rules.route_ends(r[0])}
    proj = copy.deepcopy(project)
    shift, notes = None, []

    def foreign(name):
        # another world's point (W2J0, W501). Not just "missing from the template's table": World 1's has no W1K3, so
        # a map's path RW104W1K3 would otherwise pass for a way to another world and drag W104's template exits in.
        return name not in tnames and name[:1] == "W" and name[1:2] != str(template.number)
    for trow in template.point_rows:
        pid = trow[1]
        there = template.position(pid)
        if pid not in project.nodes or there is None:
            continue
        for ncol, pcol in ((3, 4), (6, 7)):
            tp = rules.paths(trow[pcol])
            if not any(foreign(e) for p in tp for e in (rules.route_ends(p) or ())):
                continue
            here = project.world_position(pid)
            d = link_shift(template, project, pid, here, [tpaths[p] for p in tp if p in tpaths and p[1:5] == pid])
            row = proj.nodes[pid]["row"]
            drop = set(getattr(project, "link_drop", ()) or ())   # not in the next world's (ported) map
            extra = list(getattr(project, "link_add", ()) or ())   # junction roads added to the next world's map
            for col in (ncol, pcol):
                have = rules.split(row[col])
                more = [x for x in rules.split(trow[col]) if x not in have and x not in drop]
                if col == pcol:
                    more += [x for x in extra if x not in have and x not in more]
                row[col] = ",".join(have + more)
            for path in tp:
                if path not in tpaths:
                    continue                                      # walked on the other map
                seq = [path[1:5]] + tpaths[path] + [path[5:9]]
                for q in seq:
                    if q == pid or foreign(q) or proj.nodes.get(q, {}).get("link"):
                        continue
                    if q in proj.nodes:
                        raise ExportError("The way on to the next world goes through the template's %s, but your map "
                                          "has a point of that name: rename it, or untick 'Castle leads on'." % q)
                    pos = [a + b for a, b in zip(template.position(q), d)]
                    flags = trows[q][2] if q in trows else ""
                    proj.nodes[q] = {"kind": "bend" if q[0] == "K" else "stop", "x": pos[0] / project.tile_size,
                                     "y": pos[2] / project.tile_size, "pos": pos, "camera": [0.0, 0.0, 0.0],
                                     "row": ["", q, flags, "", "", "", "", "", ""], "link": True}
                for a, b in zip(seq, seq[1:]):
                    r = troutes.get(frozenset((a, b)))
                    if r and not any(x["id"] == r[0] for x in proj.routes):
                        proj.routes.append({"id": r[0], "type": r[1] if len(r) > 1 else "道"})
            if shift is None or pid[2:3] == "C":
                shift = d
            notes.append("%s %s exit leads on as in the game: opens %s." % (
                pid, "normal" if ncol == 3 else "secret", trow[pcol]))
    return proj, shift, notes


# --------------------------------------------------------------------------------------------- export
# The game keeps every world's archive in one fixed heap at boot: a world unpacked to 28.6 MB (Yoshi's Island, test mod
# 12) left no room for World 5's archive and crashed (invalid read at 0x0 after "archive 'CS_W5a' is not loaded"). The
# NSMBW World 1 port boots at 13.6 MB; vanilla World 1 is 11.0 MB, the largest vanilla world (W5a) 16.2 MB.
WORLD_SIZE_SAFE = 13_650_000          # unpacked bytes known to boot alongside the vanilla worlds
WORLD_SIZE_VANILLA_MAX = 16_246_992   # CS_W5a, the largest vanilla world archive, unpacked


def unpacked_size(szs_path):
    """The unpacked size of a world archive (its Yaz0 header), or its file size when it is not compressed."""
    with open(szs_path, "rb") as f:
        head = f.read(8)
    if head[:4] == b"Yaz0":
        return int.from_bytes(head[4:8], "big")
    return Path(szs_path).stat().st_size


def size_verdict(unpacked):
    """('ok' | 'tight' | 'over', a sentence) for a world archive's unpacked size."""
    mb = unpacked / 1e6
    if unpacked <= WORLD_SIZE_SAFE:
        return "ok", "World archive: %.1f MB unpacked (fits: worlds up to %.1f MB are known to boot)." % (
            mb, WORLD_SIZE_SAFE / 1e6)
    if unpacked <= WORLD_SIZE_VANILLA_MAX:
        return "tight", ("World archive: %.1f MB unpacked, over the %.1f MB known to boot (the largest vanilla world is "
                         "%.1f MB). It may leave no room for another world's archive: test it, and make it smaller if "
                         "the game crashes at boot." % (mb, WORLD_SIZE_SAFE / 1e6, WORLD_SIZE_VANILLA_MAX / 1e6))
    return "over", ("World archive: %.1f MB unpacked, larger than any vanilla world (%.1f MB). The game will most likely "
                    "crash at boot (another world's archive no longer fits): use fewer or smaller textures and models."
                    % (mb, WORLD_SIZE_VANILLA_MAX / 1e6))


def synchro_name(template):
    """The decoration model every world builds and animates in a loop (MapModel::createDecorateAnimModel: "%s_%s"
    with "Synchro"; nsmbud-reverse F0335): the template's own, else CS_<world>_Synchro."""
    own = [m.name for m in template.model.models if m.name.endswith("_Synchro")]
    return own[0] if own else "CS_%s_Synchro" % template.id


# Decoration models the game builds only if they are there (createDecorateAnimModelCore_ 0x602276B8 returns 0 for a
# missing one; F0335): the export may leave them out when a world would be over the size that boots. CloudBG stays
# (W8a's dark-cloud demo animates it), and so do the actor models (CS_W6a_Trap, CS_W8a_DarkCloud*).
OPTIONAL_DECORATIONS = re.compile(r"^CS_W\d[ab]?_(Quicksand|Ocean|Aurora|Doku|Castle|Wall|Magma|Universe|Fog|Synchro\d?)$")


CLIMB_TYPES = ("\u306f\u3057\u3054", "\u306f\u3057\u3054\u5de6", "\u306f\u3057\u3054\u53f3", "\u306f\u3057\u3054\u5ca9",
               "\u306f\u3057\u3054\u5ca9\u5de6", "\u306f\u3057\u3054\u5ca9\u53f3", "\u306f\u3057\u3054\u7e04", "\u30c4\u30bf")
CLIMB_RISE = 0.58     # height per unit of map depth: a climb straight up looks as long on screen as the drawn ladder
                      # (the map camera looks down at about 30 degrees: depth shows at sin, height at cos)


def climbable_ladders(project):
    """(project copy, notes): ladders and vines of a flat map made climbable. Deluxe climbs only a route whose ends are
    less than 3 units apart in x and z (the up/down direction slots, nsmbud-reverse F0322/F0323): a ladder drawn on a
    flat map runs north-south and Mario just walks it. The upper end (the one further up the picture) is put straight
    above the lower one, raised CLIMB_RISE per unit of depth, when it is a point Mario passes through (a bend, or a stop
    with two routes and no flags, F0325); the routes on from it are unchanged."""
    import copy as _copy
    ladders = [r for r in project.routes if r.get("type") in CLIMB_TYPES]
    if not ladders:
        return project, []
    q = _copy.deepcopy(project)
    q._model = None
    notes, done = [], []
    for r in ladders:
        ends = rules.route_ends(r["id"])
        if not ends or not all(e in q.nodes for e in ends):
            continue
        a, b = ends
        top, bottom = (a, b) if q.nodes[a]["y"] <= q.nodes[b]["y"] else (b, a)
        nt, nb = q.nodes[top], q.nodes[bottom]
        degree = sum(1 for x in q.routes if top in (rules.route_ends(x["id"]) or ()))
        flags = nt.get("row", [""] * 3)[2] if len(nt.get("row", [])) > 2 else ""
        passing = nt["kind"] == "bend" or (top[0] == "F" and degree == 2 and not flags)
        bx, by, bz = q.world_position(bottom)
        _tx, ty, tz = q.world_position(top)
        if not passing or "pos" in nt:
            notes.append("Ladder %s: its top %s is not a point Mario passes through, so it stays a walk." % (r["id"], top))
            continue
        nt["pos"] = [bx, max(ty, by) + CLIMB_RISE * abs(bz - tz), bz]
        done.append(r["id"])
    if done:
        notes.append("Ladders made climbable (top end straight above the bottom): %s." % ", ".join(done))
    return q, notes


def export_world(project, image, template_archive, mod_root, model_dir=None, compressor=None, max_texture=4096, keep_temp=False,
                 drop_decorations=False, texture_size=None):
    """Write the world archive (and its MarioOffset table) into <mod_root>/atmosphere/contents/<title>/romfs/Model.

    project: kdx.project2d.Project. image: QImage of the painted map (no point markers). template_archive: the vanilla
    CS_W<n>[a|b] archive of the world being replaced. model_dir: romfs/Model (for MarioOffsetData). Returns
    (written paths, rules report of the exported world)."""
    ok, msg = engine_status()
    if not ok:
        raise ExportError(msg)
    template = World(template_archive)
    if template.number != project.world:
        raise ExportError("The map is world %d but the template archive is %s. Point names start with the world number "
                          "(it picks the level files), so export a world-%d map over CS_W%d." % (
                              project.world, template.id, template.number, template.number))
    slot = template.id
    orig_project, orig_image = project, image
    if abs(getattr(project, "depth_stretch", 1.0) - 1.0) > 1e-6:     # a straight-down picture under a slanted camera
        from PyQt5 import QtCore
        project = project.stretched()
        from . import koopatlas as _kp
        _kp.fix_directions(project)                 # depth x2 can turn an east/south pair into two souths
        image = image.scaled(image.width(), int(round(image.height() * project.height / float(orig_project.height))),
                             QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    link_shift, link_notes = None, []
    project, ladder_notes = climbable_ladders(project)
    link_notes += ladder_notes
    if getattr(project, "link_next", False):
        project, link_shift, link_notes = world_link(template, project)
    rep = project.check(slot)
    if rep.errors:
        raise ExportError("The map breaks the game's rules:\n- " + "\n- ".join(rep.errors[:10]))
    tmp = Path(tempfile.mkdtemp(prefix="kdx_export_"))
    try:
        (tmp / "template.bfres").write_bytes(bytes(template.bfres_bytes))
        rgba, tw, th = bake_texture(image, max_texture, texture_size)
        # the painted map is opaque: BC1 like the game's ground (RGBA8 made a small 2D map 14.5 MB unpacked), with mips
        blocks, nmips = bc1_mips(rgba, tw, th)
        (tmp / "terrain.bc1").write_bytes(blocks)
        (tmp / "black.rgba").write_bytes(b"\x00\x00\x00\xff" * (16 * 16))
        (tmp / "bake0.rgba").write_bytes(BAKE0)
        verts, nverts, idx = terrain_mesh(project)
        (tmp / "terrain.vtx").write_bytes(struct.pack("<%df" % len(verts), *verts))
        (tmp / "terrain.idx").write_bytes(struct.pack("<%dH" % len(idx), *idx))
        terrain_model = next(m.name for m in template.model.models if m.name.endswith("_World"))
        # Point bones are children of the model's root bone. Vanilla roots sit at a tiny offset (W1: -0.007, 0, 0.485)
        # that the vanilla point bones compensate for, so the same is done here.
        root = template.marker.bones[0]
        if any(abs(c) > 1e-5 for c in root.rotation[:3]) or any(abs(c - 1) > 1e-5 for c in root.scale):
            raise ExportError("The template's map-point model has a rotated or scaled root bone; this exporter does not handle that.")
        rx, ry, rz = root.world_position
        order, path_list, nodes, hidden = project.bone_layout(slot)
        points = []
        for pid in order:
            if pid not in nodes:                                  # a path bone: only its name and place in the order count
                points.append({"name": pid, "position": [-rx, -ry, -rz], "marker": None})
                continue
            n = nodes[pid]
            kind = None if pid in hidden else ("level" if pid[0] == "W" else ("stop" if pid[0] == "F" else None))
            x, y, z = n["pos"] if "pos" in n else project.world_position(pid) if pid in project.nodes else (
                n["x"] * project.tile_size, 0.0, n["y"] * project.tile_size)
            if pid in hidden:
                y = min(project.heights or [0.0]) - HIDDEN_DEPTH
            points.append({"name": pid, "position": [x - rx, y - ry, z - rz], "marker": kind})
        for b in template.marker.bones[1:]:
            if b.name.startswith(rules.LINK_BONE_PREFIXES) and b.name not in nodes:
                x, y, z = [c + s for c, s in zip(b.world_position, link_shift or (0, 0, 0))]
                points.append({"name": b.name, "position": [x - rx, y - ry, z - rz], "marker": None})
        spec = {
            "template": str(tmp / "template.bfres"), "output": str(tmp / "output.bfres"),
            "marker_model": template.marker.name, "terrain_model": terrain_model,
            "keep_models": [template.marker.name, terrain_model],
            # the template's other models and their animations stay, hidden: the game's actors build from them (W8a's
            # dark cloud, W6a's traps, the decoration models); a world without them crashed at the map's start when
            # every world was replaced (Newer port, 2026-11-23)
            "keep_extras": True,
            "drop_models": [m.name for m in template.model.models if OPTIONAL_DECORATIONS.match(m.name)]
            if drop_decorations else [],
            "textures": [{"name": "kdx_terrain", "width": tw, "height": th, "bc1": str(tmp / "terrain.bc1"), "mips": nmips},
                         {"name": "kdx_black", "width": 16, "height": 16, "rgba": str(tmp / "black.rgba")},
                         {"name": "kdx_bake0", "width": 16, "height": 16, "rgba": str(tmp / "bake0.rgba")},
                         {"name": "kdx_bake1", "width": 16, "height": 16, "neutral_light": NEUTRAL_LIGHT}],
            "terrain": {"vertices": str(tmp / "terrain.vtx"), "vertex_count": nverts, "indices": str(tmp / "terrain.idx"),
                        "material_name": "mtKdxTerrain", "textures": dict(LIGHTING, albedo="kdx_terrain")},
            "points": points,
            "marker_shapes": marker_shapes(template),
        }
        map_png = tmp / "painted.png"
        image.save(str(map_png))
        model = project.scene_model(map_png)
        # moving images: quads of their own, moved into CS_W<n>_Synchro after the build (kdx.pictures)
        sparts, sitems, loop = pictures.sticker_parts(project, tmp)
        rparts = pictures.reveal_parts(project, tmp, open_routes(project))   # path art: appears on opening
        if rparts and not template_has_route_material(template, terrain_model):
            for rp in rparts:                    # W5b's ground model has no road material to copy: art always shown
                rp.material, rp.uvs2, rp.mask_offset = rp.material.replace("reveal/", "revealshown/"), None, None
            link_notes.append("%s's ground model has no road material to copy, so the %d pieces of path art are "
                              "always shown." % (template.id, len(rparts)))
        sparts = sparts + rparts
        if sparts:
            from .objmodel import ObjModel
            model = ObjModel((list(model.parts) if model is not None else []) + sparts)
        if model is not None:                                     # a 3D model (and scenery objects) replace the painted ground
            mtex, mparts = model_parts(model, tmp, routes=open_routes(project), separate={i["object"] for i in sitems})
            if project.model3d is None:                           # island tops share the painted map's texture
                own = {p["textures"]["albedo"] for p in mparts if p["name"] and p.get("_src") == str(map_png)}
                for p in mparts:
                    if p.pop("_src", None) == str(map_png):
                        p["textures"]["albedo"] = "kdx_terrain"
                mtex = [t for t in mtex if t["name"] not in own or any(q["textures"]["albedo"] == t["name"] for q in mparts)]
            if project.model3d is None:                           # objects on a painted map: the map stays as the ground
                painted = dict(spec["terrain"], name="kdx_terrain", vertex_count=nverts, index_format="u16")
                spec["textures"] += mtex
                spec["terrain"] = {"parts": [painted] + mparts}
            else:
                spec["textures"] = spec["textures"][1:] + mtex
                spec["terrain"] = {"parts": mparts}
        (tmp / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        run_engine("build", tmp / "spec.json")
        if sitems:
            shapes = {}
            for p in spec["terrain"]["parts"]:
                if p.get("object"):
                    shapes.setdefault(p["object"], []).append(p["name"])
            sspec = {"input": str(tmp / "output.bfres"), "output": str(tmp / "output.bfres"), "source_model": terrain_model,
                     "model": synchro_name(template), "frames": loop, "flags_from": str(tmp / "template.bfres"),
                     "items": [dict(i, shapes=shapes[i["object"]]) for i in sitems if i["object"] in shapes]}
            (tmp / "synchro.json").write_text(json.dumps(sspec, indent=1), encoding="utf-8")
            try:
                run_engine("synchro", tmp / "synchro.json")
            except ExportError as e:                 # W2a, W5b, W8a: the ground model is not on a root bone at the origin
                if "root bone" not in str(e):
                    raise
                link_notes.append("%s's ground model does not sit on a root bone at the origin, so the %d moving "
                                  "images stay still where they start (the synchro command needs that)." % (
                                      template.id, len(sitems)))
        new_bfres = (tmp / "output.bfres").read_bytes()
        bfres.BfresFile.parse(new_bfres)                          # our own reader must read what the engine wrote

        point_rows, route_rows = project.point_rows(nodes), project.route_rows()
        entries = []
        for name, data in template.entries:
            if name == template.point_member:
                entries.append((name, point_csv_encode(point_rows, template.point_style)))
            elif name == template.route_member:
                entries.append((name, csv_encode(route_rows, template.route_style)))
            elif name == template.bfres_member:
                entries.append((name, new_bfres))
            else:
                entries.append((name, data))                      # the shader archive and anything else stay as they are
        out_dir = mod_model_dir(mod_root)
        if out_dir.resolve() == Path(template_archive).resolve().parent:
            raise ExportError("The export folder is the folder the template came from; choose a separate mod folder.")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / template.archive_name
        archive.write_szs(out, entries, template.info, compress="auto", compressor=compressor)
        written = [out]

        mario_src = Path(model_dir) / ("MarioOffsetDataW%d.bin" % project.world) if model_dir else None
        if mario_src and mario_src.exists():
            table = offsets.MarioOffsetTable.load(mario_src)
            ids = [r[1] for r in point_rows]
            table.sync(template.block, ids)
            for pid in ids:
                if not nodes[pid].get("link"):                    # the walk on to the next world keeps the game's camera
                    table.set_vector(template.block, pid, nodes[pid].get("camera", [0, 0, 0]))
            mario_out = out_dir / mario_src.name
            mario_out.write_bytes(table.to_bytes())
            written.append(mario_out)

        # levels played by a stand-in course (Koopatlas import: Newer's own levels are Wii levels): copied into the mod

        romfs = Path(model_dir).parent if model_dir else None
        for pid, n in project.nodes.items():
            src, lf = n.get("course_from"), rules.level_file(pid)
            if not src or not lf:
                continue
            srcp = Path(src) if Path(src).is_absolute() else (romfs / src if romfs else None)
            dst = out_dir.parent / lf
            if srcp is not None and srcp.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(srcp, dst)
                written.append(dst)
        exported = World(out)                                     # read it back the way the editor reads worlds
        if mario_src and mario_src.exists():
            exported.load_offsets(out_dir)
        # levels: the mod's own Course folder first, then the game's (next to its Model folder)
        courses = [d for d in (out_dir.parent / "Course", Path(model_dir).parent / "Course" if model_dir else None)
                   if d is not None and d.is_dir()]
        final = exported.report(courses or None, hidden=set(hidden))
        final.summary += ["Added, hidden: %s (%s)." % (pid, why) for pid, why in hidden.items()] + link_notes
        missing = [p for p in exported.point_ids if p not in exported.bones]
        if missing:
            raise ExportError("Exported world check failed: points without a bone: " + ", ".join(missing))
        if final.errors:
            raise ExportError("Exported world check failed:\n- " + "\n- ".join(final.errors[:10]))
        final.unpacked_size = unpacked_size(out)
        if final.unpacked_size > WORLD_SIZE_SAFE and texture_size is None:
            # too big: the largest ground texture that fits (halving its longer side), before dropping decorations
            room = WORLD_SIZE_SAFE * 0.98 - (final.unpacked_size - bc1_bytes(tw, th))
            sw, sh = tw, th
            while bc1_bytes(sw, sh) > room and max(sw, sh) > 1024:
                sw, sh = (sw // 2, sh) if sw / image.width() >= sh / image.height() else (sw, sh // 2)
            if (sw, sh) != (tw, th) and bc1_bytes(sw, sh) <= room:
                written, final = export_world(orig_project, orig_image, template_archive, mod_root, model_dir, compressor,
                                              max_texture, keep_temp, drop_decorations, texture_size=(sw, sh))
                final.summary.append("Ground texture %dx%d (not %dx%d) to stay under %.1f MB." % (
                    sw, sh, tw, th, WORLD_SIZE_SAFE / 1e6))
                return written, final
        optional = [m.name for m in template.model.models if OPTIONAL_DECORATIONS.match(m.name)]
        if final.unpacked_size > WORLD_SIZE_SAFE and optional and not drop_decorations:
            written, final = export_world(orig_project, orig_image, template_archive, mod_root, model_dir, compressor,
                                          max_texture, keep_temp, drop_decorations=True, texture_size=texture_size)
            final.summary.append("Left out the template's decoration models %s to stay under %.1f MB." % (
                ", ".join(optional), WORLD_SIZE_SAFE / 1e6))
            return written, final
        verdict, sentence = size_verdict(final.unpacked_size)
        (final.summary if verdict == "ok" else final.warnings).insert(0, sentence)
        return written, final
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)

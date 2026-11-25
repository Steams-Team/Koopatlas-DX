"""
kdx/model3d.py - 3D model files for custom worlds: reading OBJ, COLLADA (.dae), FBX (binary and ASCII) and Wii models
(.brres, or a U8 .arc holding one), and writing COLLADA and OBJ.

    model = model3d.load("island.fbx")          # a kdx.objmodel.ObjModel: one Part per material, Y up
    model3d.save_dae(model, "island.dae")       # with its textures copied next to it as PNG

Every reader gives the same thing the OBJ reader always has: per-corner triangles in Y-up model space, UVs with the
game's top-left origin, faces turned to point outwards (ObjModel._face_outwards), one Part per material with its
albedo texture path or colour. Readers also fill Part.mode (opaque / mask / translucent), Part.double_sided and vertex
colours where the file has them.

COLLADA and FBX state their own up axis (asset/up_axis, GlobalSettings UpAxis...), which is applied; node transforms
(matrix, translate/rotate/scale; FBX Lcl transforms with pre/post rotation, pivots and geometric transforms) are baked
into the triangles. FBX textures may be embedded (Video Content); they are written to a cache folder.

Wii models (New Super Mario Bros. Wii and other NW4R games) are read with kdx.brres. Layouts follow BrawlCrate
(soopercool101/BrawlCrate, BrawlLib: MDL0 materials, TEV colour registers, texture SRTs). Greyscale textures that the
Wii coloured in its TEV (water, sky) are coloured with the material's two TEV colour registers, so they keep their look
in a format that has no TEV.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import struct
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np

from .objmodel import ObjError, ObjModel, Part

FORMATS = "3D models (*.obj *.dae *.fbx *.brres *.arc);;Wavefront OBJ (*.obj);;COLLADA (*.dae);;FBX (*.fbx);;" \
          "Wii model (*.brres *.arc)"
EXTENSIONS = (".obj", ".dae", ".fbx", ".brres", ".arc")


class ModelError(ObjError):
    pass


def cache_dir(tag):
    """A folder for files made from a model (decoded or embedded textures): %LOCALAPPDATA%/KoopatlasDX/cache/<tag>."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "KoopatlasDX" / "cache" / tag
    base.mkdir(parents=True, exist_ok=True)
    return base


def load(path, z_up=False, **kw):
    """Any supported model file as a kdx.objmodel.ObjModel. z_up turns a Z-up OBJ to Y up; COLLADA and FBX say
    their own up axis (z_up=True still turns them, for files that state it wrongly)."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".obj":
        return ObjModel.load(path, z_up=z_up)
    if ext == ".dae":
        model = load_dae(path)
    elif ext == ".fbx":
        model = load_fbx(path)
    elif ext in (".brres", ".arc"):
        model = load_brres(path, **kw)
    else:
        raise ModelError("%s: unsupported model format (use %s)." % (path.name, ", ".join(EXTENSIONS)))
    if z_up and ext in (".dae", ".fbx"):
        model = model.transformed(Z_UP_TO_Y_UP)
    return model


Z_UP_TO_Y_UP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], np.float64)   # (x, y, z) -> (x, z, -y)


def _finish(parts, path):
    """An ObjModel from parts whose normals may be missing (zero rows; _given marks the ones from the file)."""
    parts = [p for p in parts if len(p.positions)]
    if not parts:
        raise ModelError("%s has no triangles." % Path(path).name)
    model = ObjModel(parts, Path(path))
    model.flipped = model._face_outwards()
    return model


def _part(name, pos, nrm, uv, colors=None, texture=None, color=(0.8, 0.8, 0.8), mode="opaque", double_sided=False):
    pos = np.asarray(pos, np.float32).reshape(-1, 3)
    n = len(pos)
    given = np.zeros(n, bool)
    normals = np.zeros((n, 3), np.float32)
    if nrm is not None:
        nrm = np.asarray(nrm, np.float32).reshape(-1, 3)
        length = np.linalg.norm(nrm, axis=1)
        given = length > 1e-8
        normals[given] = nrm[given] / length[given, None]
    uv = np.zeros((n, 2), np.float32) if uv is None else np.asarray(uv, np.float32).reshape(-1, 2)
    p = Part(name, pos, normals, uv, texture, tuple(float(c) for c in color[:3]), mode, double_sided,
             None if colors is None else np.asarray(colors, np.float32).reshape(-1, 4))
    p._given = given
    return p


def _image_has_alpha(path):
    try:
        from PyQt5 import QtGui
        img = QtGui.QImage(str(path))
        return not img.isNull() and img.hasAlphaChannel() and _alpha_min(img) < 250
    except Exception:
        return False


def _alpha_min(img):
    from PyQt5 import QtGui
    img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.byteCount())
    a = np.frombuffer(bytes(ptr), np.uint8).reshape(img.height(), img.bytesPerLine())[:, 3:img.width() * 4:4]
    return int(a.min()) if a.size else 255


def _resolve(base, ref):
    """A texture reference from a model file as an existing path when it can be found (as given, relative to the
    model, or by its file name next to the model), else the best guess."""
    if not ref:
        return None
    ref = urllib.parse.unquote(ref.strip())
    if ref.startswith("file://"):
        ref = ref[7:]
        if re.match(r"^/[A-Za-z]:", ref):
            ref = ref[1:]
    ref = ref.replace("\\", "/")
    cands = [Path(ref), Path(base) / ref, Path(base) / Path(ref).name, Path(base) / "textures" / Path(ref).name]
    for c in cands:
        try:
            if c.is_file():
                return str(c.resolve())
        except OSError:
            pass
    return str(Path(base) / Path(ref).name)


# ------------------------------------------------------------------------------------------------------------ PNG
def write_png(path, rgba):
    """Save an (h, w, 4) uint8 array as PNG (no Qt needed)."""
    rgba = np.ascontiguousarray(rgba, np.uint8)
    h, w = rgba.shape[:2]
    raw = b"".join(b"\0" + rgba[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------------------------------------- COLLADA
def _tag(e):
    return e.tag.rsplit("}", 1)[-1]


def _kids(e, name):
    return [c for c in e if _tag(c) == name]


def _kid(e, name):
    for c in e:
        if _tag(c) == name:
            return c
    return None


def _find(e, *names):
    for name in names:
        if e is None:
            return None
        e = _kid(e, name)
    return e


def _floats(text):
    return np.array(text.split(), np.float64) if text and text.strip() else np.zeros(0)


def _ints(text):
    return np.array(text.split(), np.int64) if text and text.strip() else np.zeros(0, np.int64)


def _dae_matrix(node):
    """A node's local 4x4 from its transform elements, in document order."""
    m = np.identity(4)
    for c in node:
        t = _tag(c)
        v = _floats(c.text)
        if t == "matrix" and len(v) == 16:
            m = m @ v.reshape(4, 4)
        elif t == "translate" and len(v) == 3:
            k = np.identity(4); k[:3, 3] = v; m = m @ k
        elif t == "rotate" and len(v) == 4:
            m = m @ _axis_angle(v[:3], v[3])
        elif t == "scale" and len(v) == 3:
            m = m @ np.diag([v[0], v[1], v[2], 1.0])
    return m


def _axis_angle(axis, deg):
    a = np.asarray(axis, np.float64)
    n = np.linalg.norm(a)
    if n < 1e-12:
        return np.identity(4)
    x, y, z = a / n
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    C = 1 - c
    m = np.identity(4)
    m[:3, :3] = [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                 [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                 [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]
    return m


def load_dae(path):
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        raise ModelError("%s cannot be read as COLLADA: %s" % (path.name, e))
    base = path.parent
    ids = {}
    for e in root.iter():
        i = e.get("id")
        if i:
            ids[i] = e

    def ref(url):
        return ids.get((url or "").lstrip("#"))

    up = (_find(root, "asset", "up_axis").text or "Y_UP").strip() if _find(root, "asset", "up_axis") is not None else "Y_UP"
    # images
    images = {}
    for img in root.iter():
        if _tag(img) != "image":
            continue
        init = _kid(img, "init_from")
        if init is not None:
            refe = _kid(init, "ref")
            images[img.get("id")] = _resolve(base, (refe.text if refe is not None else init.text) or "")

    def effect_info(eff):
        """(texture path or None, colour, mode, double sided, wrap (u, v)) of an effect."""
        tex, col, mode, double = None, (0.8, 0.8, 0.8), "opaque", False
        if eff is None:
            return tex, col, mode, double, ("repeat", "repeat")
        wrap = []
        for tag in ("wrap_s", "wrap_t"):
            w = next((e for e in eff.iter() if _tag(e) == tag), None)
            wrap.append({"CLAMP": "clamp", "BORDER": "clamp", "MIRROR": "mirror"}.get((w.text or "").strip().upper(), "repeat")
                        if w is not None else "repeat")
        params = {}
        for np_ in eff.iter():
            if _tag(np_) == "newparam":
                params[np_.get("sid")] = np_
        tech = None
        for e in eff.iter():
            if _tag(e) in ("phong", "lambert", "blinn", "constant"):
                tech = e
                break
        if tech is not None:
            diff = _kid(tech, "diffuse")
            if diff is None:
                diff = _kid(tech, "emission")
            if diff is not None:
                t = _kid(diff, "texture")
                c = _kid(diff, "color")
                if t is not None:
                    key = t.get("texture")
                    seen = 0
                    while key in params and seen < 4:                  # sampler -> surface -> image
                        p = params[key]
                        s = _find(p, "sampler2D", "source")
                        inst = _find(p, "sampler2D", "instance_image")
                        surf = _find(p, "surface", "init_from")
                        if inst is not None:
                            key = inst.get("url", "").lstrip("#"); break
                        key = (s.text if s is not None else surf.text if surf is not None else key).strip()
                        seen += 1
                    tex = images.get(key)
                elif c is not None:
                    v = _floats(c.text)
                    if len(v) >= 3:
                        col = tuple(v[:3])
            tr = _find(tech, "transparency", "float")
            if _kid(tech, "transparent") is not None and tr is not None:
                v = _floats(tr.text)
                if len(v) and v[0] < 0.99 and _kid(tech, "transparent").get("opaque", "A_ONE") == "A_ONE":
                    mode = "translucent"
        for e in eff.iter():
            if _tag(e) == "double_sided" and (e.text or "").strip() in ("1", "true"):
                double = True
        if mode == "opaque" and tex and _image_has_alpha(tex):
            mode = "mask"
        return tex, col, mode, double, tuple(wrap)

    materials = {}
    mask_offsets = {}                                     # material id -> NSMBW reveal-mask offset (KDX extra)
    for mat in root.iter():
        if _tag(mat) == "material":
            ie = _kid(mat, "instance_effect")
            materials[mat.get("id")] = (mat.get("name") or mat.get("id"), effect_info(ref(ie.get("url")) if ie is not None else None))
            mo = next((e for e in mat.iter() if _tag(e) == "mask_offset"), None)
            if mo is not None and _floats(mo.text).size:
                mask_offsets[mat.get("id")] = float(_floats(mo.text)[0])

    sources_cache = {}

    def source(sid):
        if sid in sources_cache:
            return sources_cache[sid]
        s = ids.get(sid)
        arr = None
        if s is not None:
            fa = _kid(s, "float_array")
            acc = _find(s, "technique_common", "accessor")
            if fa is not None:
                data = _floats(fa.text)
                stride = int(acc.get("stride", "1")) if acc is not None else 1
                count = int(acc.get("count", len(data) // max(stride, 1))) if acc is not None else len(data) // stride
                offset = int(acc.get("offset", "0")) if acc is not None else 0
                arr = data[offset:offset + count * stride].reshape(-1, stride)
        sources_cache[sid] = arr
        return arr

    def geometry_prims(geo):
        """[(material symbol, positions, normals or None, uvs or None, colours or None)] per primitive (corners)."""
        mesh = _kid(geo, "mesh")
        if mesh is None:
            return []
        verts = {}
        for v in _kids(mesh, "vertices"):
            verts[v.get("id")] = {i.get("semantic"): i.get("source", "").lstrip("#") for i in _kids(v, "input")}
        out = []
        for prim in mesh:
            kind = _tag(prim)
            if kind not in ("triangles", "polylist", "polygons", "trifans", "tristrips"):
                continue
            inputs = _kids(prim, "input")
            if not inputs:
                continue
            stride = max(int(i.get("offset", "0")) for i in inputs) + 1
            chans = {}
            for i in inputs:
                sem, off, src = i.get("semantic"), int(i.get("offset", "0")), i.get("source", "").lstrip("#")
                st = int(i.get("set", "0"))
                if sem == "VERTEX":
                    for vs, vsrc in verts.get(src, {}).items():
                        chans.setdefault(vs, (off, vsrc, 0))
                elif sem == "TEXCOORD":
                    if "TEXCOORD" not in chans or st < chans["TEXCOORD"][2]:
                        chans["TEXCOORD"] = (off, src, st)
                    if st == 1:
                        chans["TEXCOORD1"] = (off, src, st)
                elif sem in ("NORMAL", "COLOR"):
                    chans.setdefault(sem, (off, src, st))
            if "POSITION" not in chans:
                continue
            polys = []                                     # lists of corner index rows
            if kind == "triangles":
                p = _ints((_kid(prim, "p").text if _kid(prim, "p") is not None else ""))
                rows = p.reshape(-1, stride)
                polys = [rows]
            elif kind == "polylist":
                vc = _ints(_kid(prim, "vcount").text if _kid(prim, "vcount") is not None else "")
                p = _ints(_kid(prim, "p").text if _kid(prim, "p") is not None else "").reshape(-1, stride)
                at, tri = 0, []
                for n in vc:
                    poly = p[at:at + n]; at += n
                    for k in range(1, n - 1):
                        tri += [poly[0], poly[k], poly[k + 1]]
                polys = [np.asarray(tri).reshape(-1, stride)] if tri else []
            else:
                tri = []
                for pe in _kids(prim, "p"):
                    poly = _ints(pe.text).reshape(-1, stride)
                    n = len(poly)
                    if kind == "tristrips":
                        for k in range(n - 2):
                            tri += [poly[k], poly[k + 1], poly[k + 2]] if k % 2 == 0 else [poly[k + 1], poly[k], poly[k + 2]]
                    else:
                        for k in range(1, n - 1):
                            tri += [poly[0], poly[k], poly[k + 1]]
                polys = [np.asarray(tri).reshape(-1, stride)] if tri else []
            for rows in polys:
                got = {}
                for sem, (off, src, _st) in chans.items():
                    arr = source(src)
                    if arr is None:
                        continue
                    idx = np.clip(rows[:, off], 0, len(arr) - 1)
                    got[sem] = arr[idx]
                pos = got["POSITION"][:, :3]
                uv = got.get("TEXCOORD")
                if uv is not None:
                    uv = np.c_[uv[:, 0], 1.0 - uv[:, 1]] if uv.shape[1] >= 2 else np.c_[uv[:, 0], np.zeros(len(uv))]
                uv2 = got.get("TEXCOORD1") if "TEXCOORD1" in chans and chans["TEXCOORD1"][1] != chans.get("TEXCOORD", (0, None, 0))[1] else None
                uv2 = np.c_[uv2[:, 0], 1.0 - uv2[:, 1]] if uv2 is not None and uv2.shape[1] >= 2 else None
                col = got.get("COLOR")
                if col is not None:
                    col = np.c_[col[:, :3], col[:, 3] if col.shape[1] > 3 else np.ones(len(col))]
                nrm = got.get("NORMAL")
                out.append((prim.get("material", ""), pos, None if nrm is None else nrm[:, :3], uv, col, uv2))
        return out

    acc = {}                                              # material key -> list of corner arrays

    def add(geo, matrix, binding, ctrl_bind=None):
        m = matrix if ctrl_bind is None else matrix @ ctrl_bind
        lin, t = m[:3, :3], m[:3, 3]
        nm = np.linalg.inv(lin).T if abs(np.linalg.det(lin)) > 1e-12 else lin
        mirror = np.linalg.det(lin) < 0
        for sym, pos, nrm, uv, col, uv2 in geometry_prims(geo):
            target = binding.get(sym, sym)
            p = pos @ lin.T + t
            n = None if nrm is None else nrm @ nm.T
            if mirror:
                order = np.arange(len(p)).reshape(-1, 3)[:, [0, 2, 1]].ravel()
                p = p[order]; n = None if n is None else n[order]
                uv = None if uv is None else uv[order]; col = None if col is None else col[order]
                uv2 = None if uv2 is None else uv2[order]
            acc.setdefault(target, []).append((p, n, uv, col, uv2))

    def visit(node, parent):
        m = parent @ _dae_matrix(node)
        for c in node:
            t = _tag(c)
            if t in ("instance_geometry", "instance_controller"):
                binding = {}
                for im in c.iter():
                    if _tag(im) == "instance_material":
                        binding[im.get("symbol")] = im.get("target", "").lstrip("#")
                target = ref(c.get("url"))
                if target is None:
                    continue
                if t == "instance_controller":
                    skin = _kid(target, "skin")
                    if skin is None:
                        skin = _kid(target, "morph")
                    if skin is None:
                        continue
                    bsm = _kid(skin, "bind_shape_matrix")
                    bind = _floats(bsm.text).reshape(4, 4) if bsm is not None and len(_floats(bsm.text)) == 16 else None
                    geo = ref(skin.get("source"))
                    if geo is not None:
                        add(geo, m, binding, bind)
                else:
                    add(target, m, binding)
            elif t == "node":
                visit(c, m)
            elif t == "instance_node":
                n = ref(c.get("url"))
                if n is not None:
                    visit(n, m)

    scene = None
    inst = _find(root, "scene", "instance_visual_scene")
    if inst is not None:
        scene = ref(inst.get("url"))
    if scene is None:
        scene = next((e for e in root.iter() if _tag(e) == "visual_scene"), None)
    if scene is not None:
        for node in _kids(scene, "node"):
            visit(node, np.identity(4))
    if not acc:                                              # no scene: every geometry as it is
        for geo in root.iter():
            if _tag(geo) == "geometry":
                add(geo, np.identity(4), {})
    convert = {"Z_UP": Z_UP_TO_Y_UP, "X_UP": np.array([[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], np.float64)}.get(up)
    parts = []
    for key, chunks in acc.items():
        name, (tex, col, mode, double, wrap) = materials.get(
            key, (key or "default", (None, (0.8, 0.8, 0.8), "opaque", False, ("repeat", "repeat"))))
        pos = np.concatenate([c[0] for c in chunks])
        nrm = np.concatenate([c[1] if c[1] is not None else np.zeros_like(c[0]) for c in chunks])
        uv = np.concatenate([c[2] if c[2] is not None else np.zeros((len(c[0]), 2)) for c in chunks])
        cols = np.concatenate([c[3] if c[3] is not None else np.ones((len(c[0]), 4)) for c in chunks]) \
            if any(c[3] is not None for c in chunks) else None
        if convert is not None:
            pos = pos @ convert[:3, :3].T
            nrm = nrm @ convert[:3, :3].T
        parts.append(_part(name, pos, nrm, uv, cols, tex, col, mode, double))
        parts[-1].wrap = wrap
        if all(c[4] is not None for c in chunks):
            parts[-1].uvs2 = np.concatenate([c[4] for c in chunks]).astype(np.float32)
            parts[-1].mask_offset = mask_offsets.get(key)
    return _finish(parts, path)


def save_dae(model, path, copy_textures=True):
    """Write a model as COLLADA 1.4.1 (Y up), one geometry and material per part. Textures are copied next to the
    file as PNG/their own format (copy_textures) and referenced by file name."""
    path = Path(path)
    out = path.parent
    out.mkdir(parents=True, exist_ok=True)
    texmap = _copy_textures(model, out) if copy_textures else {p.texture: p.texture for p in model.parts if p.texture}
    E = ET.Element
    root = E("COLLADA", xmlns="http://www.collada.org/2005/11/COLLADASchema", version="1.4.1")
    asset = ET.SubElement(root, "asset")
    ET.SubElement(ET.SubElement(asset, "contributor"), "authoring_tool").text = "Koopatlas DX"
    ET.SubElement(asset, "unit", name="meter", meter="1")
    ET.SubElement(asset, "up_axis").text = "Y_UP"
    limg, leff, lmat = (ET.SubElement(root, n) for n in ("library_images", "library_effects", "library_materials"))
    lgeo = ET.SubElement(root, "library_geometries")
    lvs = ET.SubElement(root, "library_visual_scenes")
    vs = ET.SubElement(lvs, "visual_scene", id="Scene", name="Scene")
    used = set()
    for i, p in enumerate(model.parts):
        base = re.sub(r"[^A-Za-z0-9_]", "_", p.material) or "mat"
        name = base
        k = 1
        while name in used:
            name = "%s_%d" % (base, k); k += 1
        used.add(name)
        eff = ET.SubElement(leff, "effect", id=name + "-effect")
        prof = ET.SubElement(eff, "profile_COMMON")
        tex = texmap.get(p.texture) if p.texture else None
        if tex:
            ET.SubElement(ET.SubElement(limg, "image", id=name + "-image", name=name), "init_from").text = \
                urllib.parse.quote(Path(tex).name if copy_textures else Path(tex).as_posix())
            surf = ET.SubElement(prof, "newparam", sid=name + "-surface")
            s = ET.SubElement(surf, "surface", type="2D")
            ET.SubElement(s, "init_from").text = name + "-image"
            samp = ET.SubElement(prof, "newparam", sid=name + "-sampler")
            s2d = ET.SubElement(samp, "sampler2D")
            ET.SubElement(s2d, "source").text = name + "-surface"
            for tag, w in zip(("wrap_s", "wrap_t"), getattr(p, "wrap", ("repeat", "repeat"))):
                ET.SubElement(s2d, tag).text = {"clamp": "CLAMP", "mirror": "MIRROR"}.get(w, "WRAP")
        tech = ET.SubElement(ET.SubElement(prof, "technique", sid="common"), "lambert")
        diff = ET.SubElement(tech, "diffuse")
        if tex:
            ET.SubElement(diff, "texture", texture=name + "-sampler", texcoord="UVMap")
        else:
            ET.SubElement(diff, "color", sid="diffuse").text = "%g %g %g 1" % tuple(p.color[:3])
        if p.mode == "translucent":
            ET.SubElement(ET.SubElement(tech, "transparent", opaque="A_ONE"), "texture" if tex else "color",
                          **({"texture": name + "-sampler", "texcoord": "UVMap"} if tex else {})).text = None if tex else "1 1 1 1"
        if p.double_sided:
            extra = ET.SubElement(ET.SubElement(prof, "extra"), "technique", profile="GOOGLEEARTH")
            ET.SubElement(extra, "double_sided").text = "1"
        mat = ET.SubElement(lmat, "material", id=name + "-material", name=p.material)
        ET.SubElement(mat, "instance_effect", url="#" + name + "-effect")
        if getattr(p, "uvs2", None) is not None and getattr(p, "mask_offset", None) is not None:
            ET.SubElement(ET.SubElement(ET.SubElement(mat, "extra"), "technique", profile="KDX"),
                          "mask_offset").text = "%.6g" % p.mask_offset
        geo = ET.SubElement(lgeo, "geometry", id=name + "-mesh", name=name)
        mesh = ET.SubElement(geo, "mesh")
        n = len(p.positions)
        uv = np.c_[p.uvs[:, 0], 1.0 - p.uvs[:, 1]]
        chans = [("positions", p.positions, ("X", "Y", "Z")), ("normals", p.normals, ("X", "Y", "Z")), ("map", uv, ("S", "T"))]
        if p.colors is not None:
            chans.append(("colors", p.colors, ("R", "G", "B", "A")))
        if getattr(p, "uvs2", None) is not None:                 # NSMBW's path-reveal mask UVs
            chans.append(("map2", np.c_[p.uvs2[:, 0], 1.0 - p.uvs2[:, 1]], ("S", "T")))
        for sid, arr, comps in chans:
            src = ET.SubElement(mesh, "source", id="%s-%s" % (name, sid))
            fa = ET.SubElement(src, "float_array", id="%s-%s-array" % (name, sid), count=str(arr.size))
            fa.text = " ".join("%.6g" % v for v in np.asarray(arr, np.float64).ravel())
            acc_ = ET.SubElement(ET.SubElement(src, "technique_common"), "accessor", source="#%s-%s-array" % (name, sid),
                                 count=str(len(arr)), stride=str(len(comps)))
            for c in comps:
                ET.SubElement(acc_, "param", name=c, type="float")
        verts = ET.SubElement(mesh, "vertices", id=name + "-vertices")
        ET.SubElement(verts, "input", semantic="POSITION", source="#%s-positions" % name)
        tris = ET.SubElement(mesh, "triangles", material=name + "-material", count=str(n // 3))
        ET.SubElement(tris, "input", semantic="VERTEX", source="#%s-vertices" % name, offset="0")
        ET.SubElement(tris, "input", semantic="NORMAL", source="#%s-normals" % name, offset="0")
        ET.SubElement(tris, "input", semantic="TEXCOORD", source="#%s-map" % name, offset="0", set="0")
        if p.colors is not None:
            ET.SubElement(tris, "input", semantic="COLOR", source="#%s-colors" % name, offset="0", set="0")
        if getattr(p, "uvs2", None) is not None:
            ET.SubElement(tris, "input", semantic="TEXCOORD", source="#%s-map2" % name, offset="0", set="1")
        ET.SubElement(tris, "p").text = " ".join(map(str, range(n)))
        node = ET.SubElement(vs, "node", id=name + "-node", name=name, type="NODE")
        ig = ET.SubElement(node, "instance_geometry", url="#%s-mesh" % name, name=name)
        bm = ET.SubElement(ET.SubElement(ig, "bind_material"), "technique_common")
        im = ET.SubElement(bm, "instance_material", symbol=name + "-material", target="#%s-material" % name)
        ET.SubElement(im, "bind_vertex_input", semantic="UVMap", input_semantic="TEXCOORD", input_set="0")
    ET.SubElement(ET.SubElement(root, "scene"), "instance_visual_scene", url="#Scene")
    ET.indent(root) if hasattr(ET, "indent") else None
    path.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="utf-8"))
    return path


def save_fbx(model, path, copy_textures=True):
    """Write a model as binary FBX 7.4 (Y up): kdx.fbxwrite."""
    from .fbxwrite import save_fbx as _save
    return _save(model, path, copy_textures)


SAVE_FORMATS = "COLLADA (*.dae);;FBX (*.fbx);;Wavefront OBJ (*.obj)"


def save_model(model, path, copy_textures=True):
    """Write a model in the format its extension names (.dae, .fbx, .obj)."""
    ext = Path(path).suffix.lower()
    fn = {".fbx": save_fbx, ".obj": save_obj, ".dae": save_dae}.get(ext)
    if fn is None:
        raise ModelError("Save as .dae, .fbx or .obj (not %s)." % (ext or "no extension"))
    return fn(model, path, copy_textures)


def save_obj(model, path, copy_textures=True):
    """Write a model as Wavefront OBJ + MTL (Y up)."""
    path = Path(path)
    out = path.parent
    out.mkdir(parents=True, exist_ok=True)
    texmap = _copy_textures(model, out) if copy_textures else {p.texture: p.texture for p in model.parts if p.texture}
    mtl = path.with_suffix(".mtl")
    lines, mlines = ["# Koopatlas DX", "mtllib %s" % mtl.name], ["# Koopatlas DX"]
    v = 1
    names = set()
    for i, p in enumerate(model.parts):
        name = re.sub(r"\s", "_", p.material) or "mat%d" % i
        while name in names:
            name += "_"
        names.add(name)
        mlines += ["newmtl %s" % name, "Kd %g %g %g" % tuple(p.color[:3])]
        if p.texture:
            mlines.append("map_Kd %s" % (Path(texmap.get(p.texture, p.texture)).name if copy_textures else p.texture))
        lines.append("o %s" % name)
        lines += ["v %.6g %.6g %.6g" % tuple(q) for q in p.positions]
        lines += ["vt %.6g %.6g" % (q[0], 1.0 - q[1]) for q in p.uvs]
        lines += ["vn %.6g %.6g %.6g" % tuple(q) for q in p.normals]
        lines.append("usemtl %s" % name)
        for k in range(0, len(p.positions), 3):
            a, b, c = v + k, v + k + 1, v + k + 2
            lines.append("f %d/%d/%d %d/%d/%d %d/%d/%d" % (a, a, a, b, b, b, c, c, c))
        v += len(p.positions)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtl.write_text("\n".join(mlines) + "\n", encoding="utf-8")
    return path


def _copy_textures(model, out):
    """Copy each part's texture into out (unique names); {source path: copied path}."""
    done, names = {}, set()
    for p in model.parts:
        src = p.texture
        if not src or src in done:
            continue
        s = Path(src)
        name = s.name
        stem, ext = s.stem, s.suffix
        k = 1
        while name.lower() in names:
            name = "%s_%d%s" % (stem, k, ext); k += 1
        names.add(name.lower())
        dst = out / name
        try:
            if s.resolve() != dst.resolve():
                shutil.copyfile(s, dst)
        except OSError:
            continue
        done[src] = str(dst)
    return done


# ------------------------------------------------------------------------------------------------------------- FBX
class FbxNode:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name, self.props, self.children = name, props, children

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def all(self, name):
        return [c for c in self.children if c.name == name]

    def value(self, name, default=None):
        c = self.find(name)
        return c.props[0] if c is not None and c.props else default


def parse_fbx(data):
    """The top-level FbxNodes of an FBX file (binary or ASCII)."""
    if data[:18] == b"Kaydara FBX Binary":
        return _fbx_binary(data)
    return _fbx_ascii(data.decode("utf-8", "replace"))


def _fbx_binary(data):
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    head = "<QQQB" if wide else "<IIIB"
    hsize = struct.calcsize(head)

    def prop(p):
        t = chr(data[p]); p += 1
        if t == "Y":
            return struct.unpack_from("<h", data, p)[0], p + 2
        if t == "C":
            return bool(data[p]), p + 1
        if t == "I":
            return struct.unpack_from("<i", data, p)[0], p + 4
        if t == "F":
            return struct.unpack_from("<f", data, p)[0], p + 4
        if t == "D":
            return struct.unpack_from("<d", data, p)[0], p + 8
        if t == "L":
            return struct.unpack_from("<q", data, p)[0], p + 8
        if t in "fdlib":
            n, enc, clen = struct.unpack_from("<III", data, p); p += 12
            raw = data[p:p + clen]; p += clen
            if enc == 1:
                raw = zlib.decompress(raw)
            dt = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "u1"}[t]
            return np.frombuffer(raw, dt, n), p
        if t in "SR":
            n = struct.unpack_from("<I", data, p)[0]; p += 4
            raw = data[p:p + n]
            return (raw.decode("utf-8", "replace") if t == "S" else bytes(raw)), p + n
        raise ModelError("FBX: unknown property type %r" % t)

    def node(p):
        end, nprops, _plen, nlen = struct.unpack_from(head, data, p)
        if end == 0:
            return None, p + hsize
        name = data[p + hsize:p + hsize + nlen].decode("ascii", "replace")
        q = p + hsize + nlen
        props = []
        for _ in range(nprops):
            v, q = prop(q)
            props.append(v)
        kids = []
        while q < end:
            k, q = node(q)
            if k is None:
                break
            kids.append(k)
        return FbxNode(name, props, kids), end

    out, p = [], 27
    while p < len(data) - hsize:
        n, p = node(p)
        if n is None:
            break
        out.append(n)
    return out


_TOKEN = re.compile(r'\s*(?:(;[^\n]*)|("(?:[^"\\]|\\.)*")|([A-Za-z_][\w|\-. ]*?):|(\*\d+)|([{},])|([^\s,{}";]+))', re.S)


def _fbx_ascii(text):
    toks = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            pos += 1
            continue
        pos = m.end()
        if m.group(1):
            continue
        if m.group(2) is not None:
            toks.append(("s", m.group(2)[1:-1]))
        elif m.group(3) is not None:
            toks.append(("k", m.group(3).strip()))
        elif m.group(4):
            toks.append(("n", m.group(4)))
        elif m.group(5):
            toks.append(("p", m.group(5)))
        else:
            toks.append(("v", m.group(6)))
    i = 0

    def val(t):
        kind, s = t
        if kind == "s":
            return s
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return s

    def nodes(until_brace):
        nonlocal i
        out = []
        while i < len(toks):
            kind, s = toks[i]
            if kind == "p" and s == "}":
                i += 1
                return out
            if kind != "k":
                i += 1
                continue
            name = s
            i += 1
            props = []
            while i < len(toks) and not (toks[i][0] == "k") and not (toks[i][0] == "p" and toks[i][1] in "{}"):
                if toks[i][0] == "p":                              # comma
                    i += 1
                    continue
                if toks[i][0] != "n":
                    props.append(val(toks[i]))
                i += 1
            kids = []
            if i < len(toks) and toks[i] == ("p", "{"):
                i += 1
                kids = nodes(True)
            if len(kids) == 1 and kids[0].name == "a" and not props:  # "Name: *N { a: ... }" arrays
                props, kids = [np.asarray(kids[0].props)], []
            out.append(FbxNode(name, props, kids))
        return out
    return nodes(False)


def _fbx_props70(n):
    """{name: [values]} of a node's Properties70 (or Properties60)."""
    out = {}
    pr = n.find("Properties70") or n.find("Properties60")
    if pr is not None:
        for p in pr.children:
            if p.props:
                out[str(p.props[0])] = p.props[4:] if pr.name == "Properties70" else p.props[3:]
    return out


def _euler(r, order=0):
    """4x4 from Euler degrees in an FBX rotation order (0 XYZ ... 5 ZYX); FBX XYZ applies X first."""
    rx, ry, rz = (math.radians(float(a)) for a in r)
    X = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Y = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Z = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    seq = {0: "XYZ", 1: "XZY", 2: "YZX", 3: "YXZ", 4: "ZXY", 5: "ZYX"}.get(int(order), "XYZ")
    m = np.identity(3)
    for a in seq:                                            # first axis applied first: rightmost in the product
        m = {"X": X, "Y": Y, "Z": Z}[a] @ m
    out = np.identity(4)
    out[:3, :3] = m
    return out


def _tm(v):
    m = np.identity(4)
    m[:3, 3] = [float(c) for c in v[:3]]
    return m


def _fbx_model_matrix(p):
    g = lambda k, d: p.get(k, d)
    order = g("RotationOrder", [0])[0] if g("RotationOrder", None) else 0
    T, R = _tm(g("Lcl Translation", [0, 0, 0])), _euler(g("Lcl Rotation", [0, 0, 0]), order)
    S = np.diag([float(c) for c in g("Lcl Scaling", [1, 1, 1])[:3]] + [1.0])
    Rpre, Rpost = _euler(g("PreRotation", [0, 0, 0])), _euler(g("PostRotation", [0, 0, 0]))
    Roff, Rp = _tm(g("RotationOffset", [0, 0, 0])), _tm(g("RotationPivot", [0, 0, 0]))
    Soff, Sp = _tm(g("ScalingOffset", [0, 0, 0])), _tm(g("ScalingPivot", [0, 0, 0]))
    return T @ Roff @ Rp @ Rpre @ R @ np.linalg.inv(Rpost) @ np.linalg.inv(Rp) @ Soff @ Sp @ S @ np.linalg.inv(Sp)


def _fbx_geometric(p):
    T = _tm(p.get("GeometricTranslation", [0, 0, 0]))
    R = _euler(p.get("GeometricRotation", [0, 0, 0]))
    S = np.diag([float(c) for c in p.get("GeometricScaling", [1, 1, 1])[:3]] + [1.0])
    return T @ R @ S


def _layer(geo, name, key, index_key):
    """Per polygon-corner values of a layer element (normals, UVs...), or None."""
    el = geo.find(name)
    if el is None:
        return None, None
    data = el.value(key)
    if data is None:
        return None, None
    data = np.asarray(data, np.float64)
    mapping = str(el.value("MappingInformationType", "ByPolygonVertex"))
    ref = str(el.value("ReferenceInformationType", "Direct"))
    idx = el.value(index_key)
    return (data, mapping, ref, None if idx is None else np.asarray(idx, np.int64)), el


def load_fbx(path):
    path = Path(path)
    try:
        top = parse_fbx(path.read_bytes())
    except (OSError, struct.error, zlib.error, ValueError) as e:
        raise ModelError("%s cannot be read as FBX: %s" % (path.name, e))
    by = {n.name: n for n in top}
    objects = by.get("Objects")
    if objects is None:
        raise ModelError("%s: no Objects in the FBX file." % path.name)
    if objects.children and objects.children[0].props and isinstance(objects.children[0].props[0], str) and \
            not str(objects.children[0].props[0]).lstrip("-").isdigit():
        raise ModelError("%s is an FBX 6 file; export FBX 7 (2011 or newer) instead." % path.name)
    objs = {}
    for o in objects.children:
        if o.props:
            objs[int(o.props[0])] = o
    parents, children, op = {}, {}, {}
    conns = by.get("Connections")
    for c in (conns.children if conns else []):
        if len(c.props) < 3:
            continue
        kind, a, b = c.props[0], int(c.props[1]), int(c.props[2])
        children.setdefault(b, []).append(a)
        if kind == "OO":
            parents.setdefault(a, []).append(b)
        else:
            op.setdefault(b, []).append((a, str(c.props[3]) if len(c.props) > 3 else ""))
            parents.setdefault(a, []).append(b)
    gs = _fbx_props70(by["GlobalSettings"]) if "GlobalSettings" in by else {}

    def axis(k, d):
        return int(gs.get(k, [d])[0]) if gs.get(k) else d
    up, ups = axis("UpAxis", 1), axis("UpAxisSign", 1)
    front, fs = axis("FrontAxis", 2), axis("FrontAxisSign", 1)
    coord, cs = axis("CoordAxis", 0), axis("CoordAxisSign", 1)
    conv = np.zeros((3, 3))
    conv[0, coord], conv[1, up], conv[2, front] = cs, ups, fs
    if abs(np.linalg.det(conv)) < 0.5:                     # broken axis settings: keep the file's axes
        conv = np.identity(3)
    # Units: UnitScaleFactor is centimetres per file unit, and sizes come out in metres (x UnitScaleFactor / 100), as
    # Unity and Blender read FBX. Blender writes its objects at scale 100 with UnitScaleFactor 1 (centimetres), so a
    # Blender model comes back at the size it had in Blender.
    usf = float(gs.get("UnitScaleFactor", [1.0])[0]) if gs.get("UnitScaleFactor") else 1.0
    if usf > 0:
        conv = conv * (usf / 100.0)
    tcache = {}

    def texture_path(tex_id):
        if tex_id in tcache:
            return tcache[tex_id]
        t = objs.get(tex_id)
        out = None
        if t is not None:
            for vid in children.get(tex_id, []):                 # embedded image (Video Content)
                v = objs.get(vid)
                if v is not None and v.name == "Video":
                    content = v.value("Content")
                    fname = str(v.value("RelativeFilename") or v.value("Filename") or "tex%d.png" % vid)
                    if isinstance(content, (bytes, bytearray)) and len(content) > 16:
                        d = cache_dir("fbx-" + hashlib.sha1(bytes(content[:4096]) + fname.encode()).hexdigest()[:16])
                        dst = d / (Path(fname.replace("\\", "/")).name or "tex.png")
                        if not dst.exists():
                            dst.write_bytes(bytes(content))
                        out = str(dst)
            if out is None:
                rel, absf = t.value("RelativeFilename"), t.value("FileName")
                for cand in (rel, absf):
                    if cand:
                        out = _resolve(path.parent, str(cand))
                        if Path(out).exists():
                            break
        tcache[tex_id] = out
        return out

    def material_info(mid):
        m = objs.get(mid)
        name = str(m.props[1]).split("\x00")[0] if m is not None and len(m.props) > 1 else "default"
        name = name.replace("Material::", "")
        p = _fbx_props70(m) if m is not None else {}
        col = p.get("DiffuseColor") or p.get("Diffuse") or [0.8, 0.8, 0.8]
        tex = None
        for tid, prop in op.get(mid, []):
            if objs.get(tid) is not None and objs[tid].name == "Texture" and \
                    any(k in prop.lower() for k in ("diffuse", "basecolor", "base_color", "base color")):
                tex = texture_path(tid)
        if tex is None:                                          # any texture linked to it
            for tid, prop in op.get(mid, []):
                if objs.get(tid) is not None and objs[tid].name == "Texture":
                    tex = texture_path(tid)
                    break
        opacity = 1.0
        if p.get("Opacity"):
            opacity = float(p["Opacity"][0])
        elif p.get("TransparencyFactor"):
            opacity = 1.0 - float(p["TransparencyFactor"][0])
        mode = "translucent" if opacity < 0.99 else ("mask" if tex and _image_has_alpha(tex) else "opaque")
        return name, tex, tuple(float(c) for c in col[:3]), mode

    def world(mid, seen=()):
        m = objs.get(mid)
        if m is None or m.name != "Model" or mid in seen:
            return np.identity(4)
        local = _fbx_model_matrix(_fbx_props70(m))
        parent = next((pid for pid in parents.get(mid, []) if objs.get(pid) is not None and objs[pid].name == "Model"), None)
        return (world(parent, seen + (mid,)) if parent is not None else np.identity(4)) @ local

    acc = {}
    minfo = {}
    for gid, geo in objs.items():
        if geo.name != "Geometry" or str(geo.props[2] if len(geo.props) > 2 else "Mesh") != "Mesh":
            continue
        V = geo.value("Vertices")
        PVI = geo.value("PolygonVertexIndex")
        if V is None or PVI is None:
            continue
        V = np.asarray(V, np.float64).reshape(-1, 3)
        PVI = np.asarray(PVI, np.int64)
        ends = PVI < 0
        vidx = np.where(ends, ~PVI, PVI)
        poly_id = np.concatenate([[0], np.cumsum(ends)[:-1]])
        corner = np.arange(len(PVI))
        normals, _ = _layer(geo, "LayerElementNormal", "Normals", "NormalsIndex")
        uvs, _ = _layer(geo, "LayerElementUV", "UV", "UVIndex")
        colors, _ = _layer(geo, "LayerElementColor", "Colors", "ColorIndex")
        mat_el = geo.find("LayerElementMaterial")
        mats = np.asarray(mat_el.value("Materials", [0]), np.int64) if mat_el is not None else np.zeros(1, np.int64)
        mat_all = mat_el is None or str(mat_el.value("MappingInformationType", "AllSame")) == "AllSame"

        def per_corner(layer, comps):
            if layer is None:
                return None
            data, mapping, ref, idx = layer
            data = data.reshape(-1, comps)
            key = corner if "PolygonVertex" in mapping else vidx if mapping in ("ByVertice", "ByVertex", "ByControlPoint") \
                else poly_id if mapping == "ByPolygon" else np.zeros_like(corner)
            if ref.startswith("IndexToDirect") or ref == "Index":
                if idx is None:
                    return None
                key = idx[np.clip(key, 0, len(idx) - 1)]
            return data[np.clip(key, 0, len(data) - 1)]
        N = per_corner(normals, 3)
        UV = per_corner(uvs, 2)
        C = per_corner(colors, 4)
        # fan triangulation per polygon
        starts = np.concatenate([[0], np.nonzero(ends)[0][:-1] + 1])
        stops = np.nonzero(ends)[0] + 1
        tri = []
        tri_poly = []
        for pi, (a, b) in enumerate(zip(starts, stops)):
            for k in range(a + 1, b - 1):
                tri += [a, k, k + 1]
                tri_poly.append(pi)
        if not tri:
            continue
        tri = np.asarray(tri, np.int64)
        tri_poly = np.repeat(np.asarray(tri_poly), 3)
        models = [mid for mid in parents.get(gid, []) if objs.get(mid) is not None and objs[mid].name == "Model"] or [None]
        for mid in models:
            mm = (world(mid) @ _fbx_geometric(_fbx_props70(objs[mid]))) if mid is not None else np.identity(4)
            lin, t = mm[:3, :3], mm[:3, 3]
            nm = np.linalg.inv(lin).T if abs(np.linalg.det(lin)) > 1e-12 else lin
            mlist = [c for c in children.get(mid, []) if objs.get(c) is not None and objs[c].name == "Material"] \
                if mid is not None else []
            mslot = np.zeros(len(tri), np.int64) if mat_all else mats[np.clip(tri_poly, 0, len(mats) - 1)]
            if mat_all and len(mats):
                mslot[:] = mats[0]
            for slot in np.unique(mslot):
                sel = tri[mslot == slot]
                mat_id = mlist[slot] if 0 <= slot < len(mlist) else None
                if mat_id not in minfo:
                    minfo[mat_id] = material_info(mat_id) if mat_id is not None else ("default", None, (0.8, 0.8, 0.8), "opaque")
                pos = V[vidx[sel]] @ lin.T + t
                nrm = None if N is None else N[sel] @ nm.T
                uv = None if UV is None else np.c_[UV[sel][:, 0], 1.0 - UV[sel][:, 1]]
                col = None if C is None else C[sel]
                if np.linalg.det(lin) < 0:
                    order = np.arange(len(pos)).reshape(-1, 3)[:, [0, 2, 1]].ravel()
                    pos = pos[order]; nrm = None if nrm is None else nrm[order]
                    uv = None if uv is None else uv[order]; col = None if col is None else col[order]
                acc.setdefault(mat_id, []).append((pos, nrm, uv, col))
    parts = []
    for mat_id, chunks in acc.items():
        name, tex, col, mode = minfo[mat_id]
        pos = np.concatenate([c[0] for c in chunks]) @ conv.T
        nrm = np.concatenate([c[1] if c[1] is not None else np.zeros_like(c[0]) for c in chunks]) @ conv.T
        uv = np.concatenate([c[2] if c[2] is not None else np.zeros((len(c[0]), 2)) for c in chunks])
        cols = np.concatenate([c[3] if c[3] is not None else np.ones((len(c[0]), 4)) for c in chunks]) \
            if any(c[3] is not None for c in chunks) else None
        if np.linalg.det(conv) < 0:                              # a handedness change turns faces inside out
            order = np.arange(len(pos)).reshape(-1, 3)[:, [0, 2, 1]].ravel()
            pos, nrm, uv = pos[order], nrm[order], uv[order]
            cols = None if cols is None else cols[order]
        parts.append(_part(name, pos, nrm, uv, cols, tex, col, mode))
    return _finish(parts, path)


# -------------------------------------------------------------------------------------------------------- Wii BRRES
GREY_FORMATS = (0, 1, 2, 3)                   # I4, I8, IA4, IA8: coloured by the TEV on the Wii


def albedo_layer(material):
    """Index of the material's colour texture: the first layer that is not a reveal mask, lightmap or environment
    map (NSMBW path materials put texMask/texMaskOpaque first and the road second)."""
    for i, layer in enumerate(material.layers):
        low = layer.texture.lower()
        if not (low.startswith(("texmask", "lm_")) or low.endswith("_env")):
            return i
    return None


def mask_layer(material):
    """Index of an NSMBW path material's reveal mask (texMask / texMaskOpaque), or None."""
    return next((i for i, layer in enumerate(material.layers) if layer.texture.lower().startswith("texmask")), None)


def brres_model(path, model_name=None):
    """(kdx.brres.Brres, kdx.brres.Model) of a .brres or of the first .brres inside a U8 .arc."""
    from . import brres
    data = Path(path).read_bytes()
    if data[:4] == b"\x55\xaa\x38\x2d":
        files = brres.u8_files(data)
        name = next((k for k in files if k.lower().endswith(".brres")), None)
        if name is None:
            raise ModelError("%s holds no .brres model." % Path(path).name)
        data = files[name]
    try:
        res = brres.Brres(data)
    except brres.BrresError as e:
        raise ModelError("%s: %s" % (Path(path).name, e))
    if not res.models:
        raise ModelError("%s has no MDL0 model." % Path(path).name)
    mdl = res.models.get(model_name) if model_name else None
    return res, mdl or next(iter(res.models.values()))


SHADE_STEP = 0.04            # vertex shading that varies less than this (relative to its mean) is not split


def _pieces(positions):
    """Connected piece of each triangle (triangles sharing a corner position are one piece): an int array."""
    key = np.round(np.asarray(positions, np.float64), 3)
    _u, corner = np.unique(key, axis=0, return_inverse=True)
    corner = corner.reshape(-1, 3)
    parent = list(range(int(corner.max()) + 1))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b, c in corner.tolist():
        ra, rb, rc = root(a), root(b), root(c)
        parent[rb] = ra
        parent[root(rc)] = ra
    return np.array([root(a) for a in corner[:, 0].tolist()])


def shade_groups(colors, levels, positions):
    """Triangles of one material grouped by the Wii's vertex shading (the grey vertex colour its TEV multiplies in),
    one shade per connected piece (a leaf, a flower, a blade clump), area-weighted: [(factor, triangle indices)], the
    factor relative to the material's mean shade so the material keeps its average brightness. One large mesh (the
    ground) is one piece and keeps its look; flat shading per triangle would facet the Wii's smooth gradients.
    [] when the pieces hardly differ or levels < 2."""
    if levels < 2 or colors is None or not len(colors):
        return []
    pos = np.asarray(positions, np.float64).reshape(-1, 3, 3)
    g = np.asarray(colors, np.float64)[:, :3].mean(axis=1).reshape(-1, 3).mean(axis=1)
    area = 0.5 * np.linalg.norm(np.cross(pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0]), axis=1) + 1e-9
    piece = _pieces(positions)
    _ids, inv = np.unique(piece, return_inverse=True)
    parea = np.bincount(inv, area)
    pshade = np.bincount(inv, area * g) / parea
    mean = float((pshade * parea).sum() / parea.sum())
    if mean <= 1e-6:
        return []
    rel = pshade / mean
    if np.sqrt(((rel - 1.0) ** 2 * parea).sum() / parea.sum()) < SHADE_STEP:
        return []
    edges = np.linspace(rel.min(), rel.max() + 1e-9, levels + 1)
    level = np.clip(np.searchsorted(edges, rel, side="right") - 1, 0, levels - 1)
    out = []
    for k in range(levels):
        sel = level == k
        if sel.any():
            out.append((float((rel[sel] * parea[sel]).sum() / parea[sel].sum()), np.nonzero(sel[inv])[0]))
    return out


def load_brres(path, model_name=None, skip_materials=(), skip_bones=(), out_dir=None, only_bones=None, pose=None,
               shade_levels=0):
    """A Wii model as an ObjModel: one part per material (with its decoded texture saved as PNG in out_dir or a cache
    folder). Polygons drawn by a material named in skip_materials, or bound to a bone under one named in skip_bones,
    are left out (only_bones: keep only polygons under these bones). model.bones gets {bone name: (x, y, z) bind
    position}, model.bone_matrices {bone name: 4x4 bind matrix} and model.brres_name the MDL0's name. pose: a skeleton
    pose to draw in instead of the bind pose (kdx.brres.chr0_end_pose: a Toad house risen, lifts raised).

    shade_levels: the Wii multiplies each vertex's grey colour in (NSMBW: darker inner leaves of a bush, shaded corners);
    NSMBUDX's world shader does not (its _c0 is a mask: vanilla World 1 grass averages 0.11). With shade_levels >= 2, a
    textured material whose vertex shading varies is split into up to that many parts, each with its texture darkened or
    lightened by its triangles' shade (shade_groups). The part nearest the mean keeps the material's name."""
    path = Path(path)
    res, mdl = brres_model(path, model_name)
    mdl.apply_pose(pose)
    if out_dir is None:
        out_dir = cache_dir("wii-%s-%s" % (re.sub(r"\W", "_", path.stem), hashlib.sha1(res.data[:65536]).hexdigest()[:12]))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    def under(b, names):
        while b is not None:
            if b.name in names:
                return True
            b = b.parent
        return False
    skip_set = {b.index for b in mdl.bones if under(b, set(skip_bones))}
    if only_bones is not None:
        skip_set |= {b.index for b in mdl.bones if not under(b, set(only_bones))}
    textures = {}

    def texture_for(mat, shade=1.0, opaque=False):
        """((png path or None, lowest alpha), albedo layer index or None). shade: the colour scaled (shade_groups);
        opaque: alpha set to 255 (the Wii draws the material opaque; readers take a texture with alpha for a cut-out)."""
        li = albedo_layer(mat)
        if li is None:
            return (None, 255), None
        tname = mat.layers[li].texture
        tex = res.textures.get(tname)
        if tex is None:
            return (None, 255), li
        key = (tname, tuple(mat.tev_colors[:2]) if tex.format in GREY_FORMATS else (), round(shade, 2), opaque)
        if key not in textures and (round(shade, 2) != 1.0 or opaque):
            (base, amin), _li = texture_for(mat)
            rgba = pixels[base].copy()
            stem = Path(base).stem
            if round(shade, 2) != 1.0:
                rgba[..., :3] = np.clip(rgba[..., :3].astype(np.float32) * shade + 0.5, 0, 255).astype(np.uint8)
                stem += "__shade%03d" % int(round(shade * 100))
            if opaque:
                rgba[..., 3] = 255
                stem += "__opaque"
            dst = Path(base).with_name(stem + ".png")
            write_png(dst, rgba)
            textures[key] = (str(dst), 255 if opaque else amin)
        if key not in textures:
            rgba = tex.rgba().copy()
            fname = tname
            if tex.format in GREY_FORMATS and len(mat.tev_colors) >= 2 and mat.tev_colors[0][:3] != mat.tev_colors[1][:3] \
                    and any(sum(c[:3]) > 0 for c in mat.tev_colors[:2]):
                c0, c1 = (np.asarray(c[:3], np.float32) for c in mat.tev_colors[:2])
                i = rgba[..., :1].astype(np.float32) / 255.0
                rgba[..., :3] = np.clip((c0 + (c1 - c0) * i) * 255.0 + 0.5, 0, 255).astype(np.uint8)
                fname = "%s__%s" % (tname, re.sub(r"\W", "", mat.name))
            # named after the model too: NSMBW's CS_W1 and cobKinokoRed both have a texKinokoRed (a 64x128 cap and
            # the Toad house's 128x128 atlas); in one folder the second overwrote the first (in game: every World 1
            # mushroom wore the Toad house's wood rings)
            dst = out_dir / ("%s__%s.png" % (re.sub(r"\W", "_", mdl.name), fname))
            write_png(dst, rgba)
            textures[key] = (str(dst), int(rgba[..., 3].min()))
            pixels[str(dst)] = rgba
        return textures[key], li
    pixels = {}
    groups = {}
    for mat_i, poly_i, bone_i, _xlu in mdl.draws:
        if mat_i >= len(mdl.materials) or poly_i >= len(mdl.polygons):
            continue
        mat = mdl.materials[mat_i]
        poly = mdl.polygons[poly_i]
        owner = mdl.matrix_bone.get(poly.bone_id) if poly.bone_id >= 0 else None    # node id: a matrix id
        if mat.name in skip_materials or bone_i in skip_set or (owner is not None and owner.index in skip_set):
            continue
        li = albedo_layer(mat)
        uv_set = mat.layers[li].uv_set if li is not None and mat.layers[li].uv_set >= 0 else 0
        pos, nrm, uv, col = mdl.triangles(poly, bone_i, uv_set)
        if not len(pos):
            continue
        mi = mask_layer(mat)
        muv = None
        if mi is not None and mat.layers[mi].uv_set >= 0:
            muv = mdl.triangles(poly, bone_i, mat.layers[mi].uv_set)[2]
            muv = muv if muv is not None and len(muv) == len(pos) else None
        groups.setdefault(mat_i, []).append((pos, nrm, uv, col, muv))
    parts = []
    for mat_i, chunks in groups.items():
        mat = mdl.materials[mat_i]
        (tex, amin), li = texture_for(mat)
        pos = np.concatenate([c[0] for c in chunks])
        nrm = np.concatenate([c[1] if c[1] is not None else np.zeros_like(c[0]) for c in chunks])
        uv = np.concatenate([c[2] for c in chunks]).astype(np.float64)
        srt = mat.uv_srts[li] if li is not None and li < len(mat.uv_srts) else None
        if srt is not None and all(math.isfinite(v) for v in srt) and (srt[0], srt[1]) != (0.0, 0.0):
            sx, sy, rot, tx, ty = srt
            if abs(rot) > 1e-6:
                c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                uv = np.c_[uv[:, 0] * c - uv[:, 1] * s, uv[:, 0] * s + uv[:, 1] * c]
            uv = np.c_[uv[:, 0] * sx + tx, uv[:, 1] * sy + ty]
        col = np.concatenate([c[3] for c in chunks])
        # cut-out where the Wii tests alpha, or blends without the XLU flag (roads); a texture's alpha alone is not
        # enough: NSMBW's fruit keeps its green calyx in texels of alpha 0 and draws them opaque (cut out, the fruit
        # showed a dark band). Unknown blending (no pixel display list) keeps the old guess.
        # Seen in game: the Wii also cuts out the transparent texels of a CMPR texture (its 1-bit alpha) in a material
        # that neither tests nor blends (CS_common_takamura_mtFlowerA, the flat flowers), which no pixel-engine setting
        # read here explains; the fruit's RGB5A3 alpha-0 texels are drawn.
        blends = getattr(mat, "blend", None)
        cmpr = li is not None and getattr(res.textures.get(mat.layers[li].texture), "format", None) == 14
        mode = "translucent" if mat.translucent else (
            "mask" if mat.alpha_test or (tex and amin < 250 and (blends is not False or cmpr)) else "opaque")
        color = (0.8, 0.8, 0.8)
        if tex is None:
            # no colour texture: the TEV's first colour register (castle doors: an environment map tinted grey or
            # brown), lightened a little for the missing shine; else the vertex colours
            c1 = mat.tev_colors[0][:3] if mat.tev_colors else (0.0, 0.0, 0.0)
            color = tuple(0.35 + 0.65 * float(v) for v in c1) if any(v > 0.01 for v in c1) else \
                tuple(float(v) for v in col[:, :3].mean(axis=0))
        wrap = mat.layers[li].wrap if li is not None else ("repeat", "repeat")
        opaque = bool(tex) and mode == "opaque" and amin < 250
        if opaque:
            (tex, _a), _li = texture_for(mat, opaque=True)
        muvs = [c[4] for c in chunks]
        mask = np.concatenate(muvs).astype(np.float32) if all(m is not None for m in muvs) else None
        split = shade_groups(col, shade_levels, pos) if tex and mask is None else []
        if not split:
            parts.append(_part(mat.name, pos, nrm, uv, col, tex, color, mode, mat.cull == 0))
            parts[-1].wrap = wrap
            if mask is not None:
                mi = mask_layer(mat)
                srt = mat.uv_srts[mi] if mi < len(mat.uv_srts) else None
                parts[-1].uvs2 = mask
                parts[-1].mask_offset = float(srt[4]) if srt is not None and math.isfinite(srt[4]) else 0.0
            continue
        main = min(range(len(split)), key=lambda k: abs(split[k][0] - 1.0))
        for k, (shade, tris) in enumerate(split):
            corners = (tris[:, None] * 3 + np.arange(3)).ravel()
            (stex, _a), _li = texture_for(mat, shade, opaque)
            name = mat.name if k == main else "%s__shade%03d" % (mat.name, int(round(shade * 100)))
            parts.append(_part(name, pos[corners], nrm[corners], uv[corners], col[corners], stex, color, mode,
                               mat.cull == 0))
            parts[-1].wrap = wrap
    model = _finish(parts, path)
    model.bones = {b.name: b.position for b in mdl.bones}
    model.bone_matrices = {b.name: b.world for b in mdl.bones}
    model.brres_name = mdl.name
    return model

"""
kdx/fbxwrite.py - binary FBX 7.4 writer for a kdx.objmodel.ObjModel (what Blender, Maya, 3ds Max and Switch Toolbox
read; Blender reads binary FBX only, so the writer is binary).

    save_fbx(model, "island.fbx")       # Y up, 1 unit = 1 game unit (UnitScaleFactor 100: metres, as Blender's own)

One mesh object, geometry and material per part. Corners with the same position share a vertex, so the mesh can be
edited (normals, UVs and colours stay per corner). Textures are copied next to the file and referenced by name.
Read back by kdx.model3d.load_fbx at the same size.
"""
from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import numpy as np

# The fixed FileId / CreationTime / footer id pair FBX readers accept from writers that do not stamp a real time
_FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
_TIME_ID = "1970-01-01 10:00:00:000"
_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOT_END = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"
VERSION = 7400


class Node:
    def __init__(self, name, *props, children=None):
        self.name, self.props, self.children = name, list(props), list(children or [])

    def add(self, name, *props, children=None):
        n = Node(name, *props, children=children)
        self.children.append(n)
        return n


class I32(int):
    pass


class I16(int):
    pass


def _prop(v):
    if isinstance(v, bool):
        return b"C" + struct.pack("<?", v)
    if isinstance(v, I16):
        return b"Y" + struct.pack("<h", v)
    if isinstance(v, I32):
        return b"I" + struct.pack("<i", v)
    if isinstance(v, int):
        return b"L" + struct.pack("<q", v)
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, bytes):
        return b"R" + struct.pack("<I", len(v)) + v
    if isinstance(v, str):
        b = v.encode("utf-8")
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, np.ndarray):
        code, dt = {"f": (b"d", "<f8"), "i": (b"i", "<i4"), "u": (b"i", "<i4"), "b": (b"b", "u1")}[v.dtype.kind]
        if v.dtype.kind == "i" and v.dtype.itemsize == 8:
            code, dt = b"l", "<i8"
        raw = np.ascontiguousarray(v, dt).tobytes()
        enc, data = (1, zlib.compress(raw)) if len(raw) > 256 else (0, raw)
        return code + struct.pack("<III", v.size, enc, len(data)) + data
    raise TypeError("FBX property of type %s" % type(v).__name__)


def _encode(node, offset):
    """The node's bytes, given the file offset it starts at (end offsets are absolute)."""
    props = b"".join(_prop(p) for p in node.props)
    name = node.name.encode("ascii")
    head_len = 13 + len(name)
    body, pos = b"", offset + head_len + len(props)
    for c in node.children:
        b = _encode(c, pos)
        body += b
        pos += len(b)
    if node.children or not node.props:
        body += b"\x00" * 13                                 # the null record closing a nested list
    end = offset + head_len + len(props) + len(body)
    return struct.pack("<IIIB", end, len(node.props), len(props), len(name)) + name + props + body


def _p70(*entries):
    n = Node("Properties70")
    for e in entries:
        n.add("P", *e)
    return n


def _name(s, cls):
    return "%s\x00\x01%s" % (s, cls)


def save_fbx(model, path, copy_textures=True):
    """Write a model as binary FBX 7.4 (Y up). Returns the path."""
    from .model3d import _copy_textures
    path = Path(path)
    out = path.parent
    out.mkdir(parents=True, exist_ok=True)
    texmap = _copy_textures(model, out) if copy_textures else {p.texture: p.texture for p in model.parts if p.texture}
    ids = iter(range(1000000, 10 ** 9, 7))
    objects, conns, used = Node("Objects"), Node("Connections"), set()
    counts = {"Model": 0, "Geometry": 0, "Material": 0, "Texture": 0}
    for p in model.parts:
        if not len(p.positions):
            continue
        base = re.sub(r"[^A-Za-z0-9_.]", "_", p.material) or "mat"
        name, k = base, 1
        while name in used:
            name, k = "%s_%d" % (base, k), k + 1
        used.add(name)
        pos = np.asarray(p.positions, np.float64)
        verts, inv = np.unique(np.round(pos, 5), axis=0, return_inverse=True)
        inv = inv.reshape(-1).astype(np.int32)
        pvi = inv.copy()
        pvi[2::3] = ~pvi[2::3]                              # the last corner of each polygon is stored negated
        gid, mid, matid = next(ids), next(ids), next(ids)
        geo = objects.add("Geometry", gid, _name(name, "Geometry"), "Mesh")
        geo.add("Properties70")
        geo.add("GeometryVersion", I32(124))
        geo.add("Vertices", verts.ravel())
        geo.add("PolygonVertexIndex", pvi)
        ln = geo.add("LayerElementNormal", I32(0))
        ln.add("Version", I32(101)); ln.add("Name", ""); ln.add("MappingInformationType", "ByPolygonVertex")
        ln.add("ReferenceInformationType", "Direct"); ln.add("Normals", np.asarray(p.normals, np.float64).ravel())
        layers = [("LayerElementNormal", 0)]
        if p.colors is not None:
            lc = geo.add("LayerElementColor", I32(0))
            lc.add("Version", I32(101)); lc.add("Name", "Col"); lc.add("MappingInformationType", "ByPolygonVertex")
            lc.add("ReferenceInformationType", "Direct"); lc.add("Colors", np.asarray(p.colors, np.float64).ravel())
            layers.append(("LayerElementColor", 0))
        uv = np.c_[p.uvs[:, 0], 1.0 - p.uvs[:, 1]].astype(np.float64)
        lu = geo.add("LayerElementUV", I32(0))
        lu.add("Version", I32(101)); lu.add("Name", "UVMap"); lu.add("MappingInformationType", "ByPolygonVertex")
        lu.add("ReferenceInformationType", "IndexToDirect"); lu.add("UV", uv.ravel())
        lu.add("UVIndex", np.arange(len(uv), dtype=np.int32))
        layers.append(("LayerElementUV", 0))
        if getattr(p, "uvs2", None) is not None:             # NSMBW's path-reveal mask UVs: a second UV set
            uv2 = np.c_[p.uvs2[:, 0], 1.0 - p.uvs2[:, 1]].astype(np.float64)
            l2 = geo.add("LayerElementUV", I32(1))
            l2.add("Version", I32(101)); l2.add("Name", "Mask"); l2.add("MappingInformationType", "ByPolygonVertex")
            l2.add("ReferenceInformationType", "IndexToDirect"); l2.add("UV", uv2.ravel())
            l2.add("UVIndex", np.arange(len(uv2), dtype=np.int32))
        lm = geo.add("LayerElementMaterial", I32(0))
        lm.add("Version", I32(101)); lm.add("Name", ""); lm.add("MappingInformationType", "AllSame")
        lm.add("ReferenceInformationType", "IndexToDirect"); lm.add("Materials", np.zeros(1, np.int32))
        layers.append(("LayerElementMaterial", 0))
        lay = geo.add("Layer", I32(0))
        lay.add("Version", I32(100))
        for t, i in layers:
            le = lay.add("LayerElement")
            le.add("Type", t); le.add("TypedIndex", I32(i))
        mdl = objects.add("Model", mid, _name(name, "Model"), "Mesh")
        mdl.add("Version", I32(232))
        mdl.children.append(_p70(("DefaultAttributeIndex", "int", "Integer", "", I32(0))))
        mdl.add("Shading", True)
        mdl.add("Culling", "CullingOff" if p.double_sided else "CullingOnCCW")
        col = tuple(float(c) for c in (p.color or (0.8, 0.8, 0.8))[:3])
        mat = objects.add("Material", matid, _name(p.material, "Material"), "")
        mat.add("Version", I32(102)); mat.add("ShadingModel", "lambert"); mat.add("MultiLayer", I32(0))
        mat.children.append(_p70(("DiffuseColor", "Color", "", "A") + col,
                                 ("Diffuse", "Vector3D", "Vector", "") + col,
                                 ("Opacity", "double", "Number", "", 0.5 if p.mode == "translucent" else 1.0)))
        conns.add("C", "OO", mid, 0)
        conns.add("C", "OO", gid, mid)
        conns.add("C", "OO", matid, mid)
        counts["Model"] += 1; counts["Geometry"] += 1; counts["Material"] += 1
        tex = texmap.get(p.texture) if p.texture else None
        if tex:
            tid = next(ids)
            rel = Path(tex).name if copy_textures else str(tex)
            t = objects.add("Texture", tid, _name(name, "Texture"), "")
            t.add("Type", "TextureVideoClip"); t.add("Version", I32(202))
            t.add("TextureName", _name(name, "Texture"))
            t.children.append(_p70(("UseMaterial", "bool", "", "", I32(1))))
            t.add("Media", _name(name, "Video"))
            t.add("FileName", str(Path(tex).resolve()) if copy_textures else str(tex))
            t.add("RelativeFilename", rel)
            conns.add("C", "OP", tid, matid, "DiffuseColor")
            counts["Texture"] += 1
    if not counts["Model"]:
        raise ValueError("Nothing to export: the model has no triangles.")

    head = Node("FBXHeaderExtension")
    head.add("FBXHeaderVersion", I32(1003)); head.add("FBXVersion", I32(VERSION)); head.add("EncryptionType", I32(0))
    ts = head.add("CreationTimeStamp")
    for k, v in (("Version", 1000), ("Year", 1970), ("Month", 1), ("Day", 1), ("Hour", 10), ("Minute", 0),
                 ("Second", 0), ("Millisecond", 0)):
        ts.add(k, I32(v))
    head.add("Creator", "Koopatlas DX")
    gs = Node("GlobalSettings")
    gs.add("Version", I32(1000))
    gs.children.append(_p70(("UpAxis", "int", "Integer", "", I32(1)), ("UpAxisSign", "int", "Integer", "", I32(1)),
                            ("FrontAxis", "int", "Integer", "", I32(2)), ("FrontAxisSign", "int", "Integer", "", I32(1)),
                            ("CoordAxis", "int", "Integer", "", I32(0)), ("CoordAxisSign", "int", "Integer", "", I32(1)),
                            ("OriginalUpAxis", "int", "Integer", "", I32(1)),
                            ("OriginalUpAxisSign", "int", "Integer", "", I32(1)),
                            ("UnitScaleFactor", "double", "Number", "", 100.0),
                            ("OriginalUnitScaleFactor", "double", "Number", "", 100.0)))
    defs = Node("Definitions")
    defs.add("Version", I32(100))
    defs.add("Count", I32(sum(counts.values()) + 1))
    defs.add("ObjectType", "GlobalSettings").add("Count", I32(1))
    for k, v in counts.items():
        if v:
            defs.add("ObjectType", k).add("Count", I32(v))
    top = [head, Node("FileId", _FILE_ID), Node("CreationTime", _TIME_ID), Node("Creator", "Koopatlas DX"), gs,
           Node("Documents", children=[Node("Count", I32(0))]), Node("References"), defs, objects, conns]
    data = bytearray(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", VERSION))
    for n in top:
        data += _encode(n, len(data))
    data += b"\x00" * 13
    data += _FOOT_ID + b"\x00" * 4
    pad = ((len(data) + 15) & ~15) - len(data)
    data += b"\x00" * (pad or 16)
    data += struct.pack("<I", VERSION) + b"\x00" * 120 + _FOOT_END
    path.write_bytes(bytes(data))
    return path

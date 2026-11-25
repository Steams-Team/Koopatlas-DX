#!/usr/bin/env python3
"""
3D model tests: the model readers and writers (FBX binary and ASCII, COLLADA, OBJ, Wii BRRES), model references
(placement and material edits), scenery objects in maps, the NSMBW world port, and the 3D world / model editors.

    python tests/test_models.py [<romfs folder> [<NSMBW folder>]]

Without folders, only the checks on files made here run (synthetic FBX/DAE/OBJ, GX texture blocks, references).
With the NSMBUDX romfs, the ported world is also exported (needs the .NET runtime); with the NSMBW dump (the folder
holding WorldMap), World 1 is read and ported. KDX_ROMFS / KDX_NSMBW work too. The editor checks need OpenGL 3.3.
"""
import json
import math
import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PyQt5 import QtCore, QtGui, QtWidgets

fmt = QtGui.QSurfaceFormat()
fmt.setVersion(3, 3); fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile); fmt.setDepthBufferSize(24)
QtGui.QSurfaceFormat.setDefaultFormat(fmt)
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
import numpy as np
from kdx import archive, bfres, brres, model3d, modelref, objmodel, project2d, romfs, rules

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if (not ok and detail is not None and str(detail)) else ""))


TMP = Path(tempfile.mkdtemp(prefix="kdx_models_")).resolve()

# ---- a small scene written as FBX by an independent writer (binary 7.4 and ASCII) ------------------------------------
# A box 3 x 1 x 2 (file units) in a Z-up file, under a model turned 90 degrees about the file's Y, scaled 2, moved 10
# along x, with an embedded 2x2 texture. Expected in Y-up: x 10..14, y -6..0, z -2..0 (see the comments in load_fbx).
BOX = np.array([[x, y, z] for z in (0, 2) for y in (0, 1) for x in (0, 3)], np.float64)
QUADS = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]   # outward, CCW seen from outside
TEX = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]], [[0, 0, 255, 255], [255, 255, 255, 128]]], np.uint8)
model3d.write_png(TMP / "box.png", TEX)
PNG = (TMP / "box.png").read_bytes()


def box_scene():
    """(name, props, children) tree of the FBX file."""
    pvi, normals, uvs, uvidx = [], [], [], []
    center = BOX.mean(axis=0)
    for q in QUADS:
        a, b, c = BOX[q[0]], BOX[q[1]], BOX[q[2]]
        n = np.cross(b - a, c - a); n /= np.linalg.norm(n)
        assert np.dot(n, BOX[list(q)].mean(axis=0) - center) > 0
        pvi += list(q[:-1]) + [~q[-1]]
        normals += list(n) * 4
        uvidx += [0, 1, 2, 3]
    uvs = [0, 0, 1, 0, 1, 1, 0, 1]
    p70 = lambda *entries: ("Properties70", [], [("P", list(e), []) for e in entries])
    return [
        ("FBXHeaderExtension", [], [("FBXVersion", [7400], [])]),
        ("GlobalSettings", [], [p70(["UpAxis", "int", "Integer", "", 2], ["UpAxisSign", "int", "Integer", "", 1],
                                    ["FrontAxis", "int", "Integer", "", 1], ["FrontAxisSign", "int", "Integer", "", -1],
                                    ["CoordAxis", "int", "Integer", "", 0], ["CoordAxisSign", "int", "Integer", "", 1],
                                    ["UnitScaleFactor", "double", "Number", "", 100.0])]),
        ("Objects", [], [
            ("Geometry", [100, "Box\x00\x01Geometry", "Mesh"], [
                ("Vertices", [BOX.ravel().astype(np.float64)], []),
                ("PolygonVertexIndex", [np.asarray(pvi, np.int32)], []),
                ("LayerElementNormal", [0], [("MappingInformationType", ["ByPolygonVertex"], []),
                                             ("ReferenceInformationType", ["Direct"], []),
                                             ("Normals", [np.asarray(normals, np.float64)], [])]),
                ("LayerElementUV", [0], [("MappingInformationType", ["ByPolygonVertex"], []),
                                         ("ReferenceInformationType", ["IndexToDirect"], []),
                                         ("UV", [np.asarray(uvs, np.float64)], []),
                                         ("UVIndex", [np.asarray(uvidx * 1, np.int32)], [])]),
                ("LayerElementMaterial", [0], [("MappingInformationType", ["AllSame"], []),
                                               ("Materials", [np.asarray([0], np.int32)], [])])]),
            ("Model", [200, "Box\x00\x01Model", "Mesh"], [p70(["Lcl Translation", "Lcl Translation", "", "A", 10.0, 0.0, 0.0],
                                                             ["Lcl Rotation", "Lcl Rotation", "", "A", 0.0, 90.0, 0.0],
                                                             ["Lcl Scaling", "Lcl Scaling", "", "A", 2.0, 2.0, 2.0])]),
            ("Material", [300, "BoxMat\x00\x01Material", ""], [p70(["DiffuseColor", "Color", "", "A", 0.5, 0.25, 0.125])]),
            ("Texture", [400, "BoxTex\x00\x01Texture", ""], [("RelativeFilename", ["missing/box.png"], [])]),
            ("Video", [500, "BoxVid\x00\x01Video", "Clip"], [("RelativeFilename", ["box_embedded.png"], []),
                                                          ("Content", [PNG], [])])]),
        ("Connections", [], [("C", ["OO", 100, 200], []), ("C", ["OO", 200, 0], []), ("C", ["OO", 300, 200], []),
                             ("C", ["OP", 400, 300, "DiffuseColor"], []), ("C", ["OO", 500, 400], [])]),
    ]


def fbx_binary(tree):
    def prop(v):
        if isinstance(v, bytes):
            return b"R" + struct.pack("<I", len(v)) + v
        if isinstance(v, str):
            b = v.encode("utf-8")
            return b"S" + struct.pack("<I", len(b)) + b
        if isinstance(v, float):
            return b"D" + struct.pack("<d", v)
        if isinstance(v, int):
            return b"L" + struct.pack("<q", v) if abs(v) > 2 ** 31 - 1 or v >= 100 else b"I" + struct.pack("<i", v)
        a = np.asarray(v)
        code = {"f8": b"d", "i4": b"i", "i8": b"l", "f4": b"f"}[a.dtype.str[1:]]
        raw = zlib.compress(a.tobytes())
        return code + struct.pack("<III", len(a), 1, len(raw)) + raw

    def node(name, props, kids, start):
        pb = b"".join(prop(p) for p in props)
        head = 13 + len(name)
        body = b""
        pos = start + head + len(pb)
        for k in kids:
            kb = node(*k, pos)
            body += kb; pos += len(kb)
        if kids:
            body += b"\0" * 13
        end = start + head + len(pb) + len(body)
        return struct.pack("<IIIB", end, len(props), len(pb), len(name)) + name.encode() + pb + body
    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400)
    for n in tree:
        out += node(*n, len(out))
    return out + b"\0" * 13


def fbx_ascii(tree, depth=0):
    def val(v):
        if isinstance(v, bytes):
            return '"%s"' % ("".join("%02x" % c for c in v[:4]))      # ASCII files carry no embedded content here
        if isinstance(v, str):
            return '"%s"' % v.replace("\x00\x01", "::")
        if isinstance(v, (int, float)):
            return repr(v)
        return None
    lines = []
    pad = "\t" * depth
    for name, props, kids in tree:
        arrays = [p for p in props if isinstance(p, np.ndarray)]
        if arrays:
            a = arrays[0]
            lines.append("%s%s: *%d {\n%s\ta: %s\n%s}" % (pad, name, len(a), pad, ",".join(repr(float(x)) if a.dtype.kind == "f" else str(int(x)) for x in a), pad))
            continue
        if name == "Content":
            continue
        s = ", ".join(val(p) for p in props)
        lines.append("%s%s: %s%s" % (pad, name, s, " {" if kids else ""))
        if kids:
            lines.append(fbx_ascii(kids, depth + 1))
            lines.append(pad + "}")
    return "\n".join(lines)


EXPECT_LO, EXPECT_HI = np.array([10, -6, -2]), np.array([14, 0, 0])


def faces_out(m):
    """Every triangle's winding agrees with its normal, and points away from the model's centre."""
    allp = np.concatenate([p.positions for p in m.parts])
    c = allp.mean(axis=0)
    for p in m.parts:
        t = p.positions.reshape(-1, 3, 3).astype(np.float64)
        fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        if (np.einsum("ij,ij->i", fn, t.mean(axis=1) - c) <= 0).any():
            return False
        if (np.einsum("ij,ij->i", fn, p.normals.reshape(-1, 3, 3).mean(axis=1)) <= 0).any():
            return False
    return True


(TMP / "box.fbx").write_bytes(fbx_binary(box_scene()))
fb = model3d.load(TMP / "box.fbx")
lo, hi = fb.bounds()
check("FBX binary: node transform (Lcl T/R/S), Z-up axis settings and units give the expected box",
      np.allclose(lo, EXPECT_LO, atol=1e-4) and np.allclose(hi, EXPECT_HI, atol=1e-4), (lo, hi))
check("FBX binary: 6 quads -> 12 triangles, one material with its name and colour",
      len(fb.parts) == 1 and fb.parts[0].triangle_count == 12 and fb.parts[0].material == "BoxMat"
      and np.allclose(fb.parts[0].color, (0.5, 0.25, 0.125)), [(p.material, p.triangle_count, p.color) for p in fb.parts])
check("FBX binary: faces point outwards, nothing needed turning round", faces_out(fb) and fb.flipped == 0, fb.flipped)
tex = fb.parts[0].texture
img = QtGui.QImage(tex) if tex else QtGui.QImage()
check("FBX binary: the embedded texture (Video Content) is written out and reads back pixel for pixel",
      bool(tex) and Path(tex).exists() and img.width() == 2 and QtGui.QColor.fromRgba(img.pixel(1, 1)).alpha() == 128
      and QtGui.QColor.fromRgba(img.pixel(0, 0)).red() == 255, tex)
check("FBX binary: a texture with partial alpha makes the part a cut-out", fb.parts[0].mode == "mask", fb.parts[0].mode)
uvs = {tuple(np.round(u, 4)) for u in fb.parts[0].uvs}
check("FBX binary: UVs (IndexToDirect) arrive with the game's top-left origin", uvs == {(0, 1), (1, 1), (1, 0), (0, 0)}, uvs)

(TMP / "box_ascii.fbx").write_text("; FBX 7.4.0 project file\n" + fbx_ascii(box_scene()), encoding="utf-8")
fa = model3d.load(TMP / "box_ascii.fbx")
lo, hi = fa.bounds()
check("FBX ASCII: the same scene gives the same box", np.allclose(lo, EXPECT_LO, atol=1e-4) and np.allclose(hi, EXPECT_HI, atol=1e-4)
      and fa.parts[0].triangle_count == 12 and faces_out(fa), (lo, hi))

# ---- COLLADA and OBJ writers and readers --------------------------------------------------------------------------
model3d.save_dae(fb, TMP / "dae" / "box.dae")
fd = model3d.load(TMP / "dae" / "box.dae")
same = lambda a, b: np.allclose(np.sort(np.concatenate([p.positions for p in a.parts]), axis=0),
                                np.sort(np.concatenate([p.positions for p in b.parts]), axis=0), atol=1e-4)
check("COLLADA: what Koopatlas DX writes reads back the same (positions, faces out, material, texture copied)",
      same(fb, fd) and faces_out(fd) and fd.parts[0].material == "BoxMat" and fd.parts[0].texture
      and Path(fd.parts[0].texture).resolve().parent == (TMP / "dae").resolve() and fd.parts[0].mode == "mask",
      (same(fb, fd), faces_out(fd), fd.parts[0].material, fd.parts[0].texture, fd.parts[0].mode))
check("COLLADA: UVs survive the round trip (v flipped on writing and back on reading)",
      {tuple(np.round(u, 4)) for u in fd.parts[0].uvs} == uvs)
dae_z = (TMP / "dae" / "box.dae").read_text(encoding="utf-8").replace("<up_axis>Y_UP</up_axis>", "<up_axis>Z_UP</up_axis>")
(TMP / "dae" / "box_z.dae").write_text(dae_z, encoding="utf-8")
fz = model3d.load(TMP / "dae" / "box_z.dae")
lo, hi = fz.bounds()
check("COLLADA: a Z_UP file is turned to Y up ((x, y, z) -> (x, z, -y))",
      np.allclose(lo, [10, -2, 0], atol=1e-4) and np.allclose(hi, [14, 0, 6], atol=1e-4), (lo, hi))
nested = """<?xml version="1.0"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><up_axis>Y_UP</up_axis></asset>
<library_geometries><geometry id="g"><mesh>
<source id="p"><float_array id="pa" count="9">0 0 0 1 0 0 0 0 -1</float_array><technique_common><accessor source="#pa" count="3" stride="3"/></technique_common></source>
<vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
<polylist count="1"><input semantic="VERTEX" source="#v" offset="0"/><vcount>3</vcount><p>0 1 2</p></polylist>
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene id="s"><node><translate>5 0 0</translate><rotate>0 1 0 90</rotate>
<node><scale>2 2 2</scale><instance_geometry url="#g"/></node></node></visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#s"/></scene></COLLADA>"""
(TMP / "nested.dae").write_text(nested, encoding="utf-8")
fn_ = model3d.load(TMP / "nested.dae")
lo, hi = fn_.bounds()
check("COLLADA: nested node transforms (translate, rotate, scale) and a polylist without normals",
      np.allclose(lo, [3, 0, -2], atol=1e-4) and np.allclose(hi, [5, 0, 0], atol=1e-4) and fn_.parts[0].triangle_count == 1, (lo, hi))
model3d.save_obj(fb, TMP / "obj" / "box.obj")
fo = model3d.load(TMP / "obj" / "box.obj")
check("OBJ: what Koopatlas DX writes reads back the same", same(fb, fo) and faces_out(fo) and fo.parts[0].texture)

# ---- GX texture decoding on hand-made blocks ----------------------------------------------------------------------
cmpr = bytes([0xF8, 0x00, 0x00, 0x1F, 0b00011011, 0b00011011, 0b00011011, 0b00011011]) * 4    # red/blue, indices 0,1,2,3
px = brres.decode_texture(cmpr, 14, 8, 8)
check("GX CMPR: big-endian colours, 2-bit indices MSB first, 2x2 sub-blocks",
      tuple(px[0, 0]) == (255, 0, 0, 255) and tuple(px[0, 1]) == (0, 0, 255, 255) and tuple(px[0, 2][:3]) == (170, 0, 85)
      and tuple(px[0, 3][:3]) == (85, 0, 170) and tuple(px[0, 4]) == (255, 0, 0, 255), px[0, :5])
rgb5a3 = struct.pack(">16H", *([0xFC00] + [0x3F00] + [0] * 14))                         # opaque red, then 3-bit-alpha red
px = brres.decode_texture(rgb5a3, 5, 4, 4)
check("GX RGB5A3: opaque 5:5:5 and translucent 3:4:4:4 texels", tuple(px[0, 0]) == (255, 0, 0, 255)
      and tuple(px[0, 1]) == (255, 0, 0, 109), px[0, :2])
i8 = bytes(range(32))
px = brres.decode_texture(i8, 1, 8, 4)
check("GX I8: 8x4 blocks in row order", px[0, 0, 0] == 0 and px[0, 7, 0] == 7 and px[1, 0, 0] == 8 and px[3, 7, 0] == 31)
ia8 = struct.pack(">16H", *([0x80FF] * 16))
px = brres.decode_texture(ia8, 3, 4, 4)
check("GX IA8: alpha high byte, intensity low byte", tuple(px[0, 0]) == (255, 255, 255, 128), px[0, 0])

# ---- model references: placement and edits -------------------------------------------------------------------------
ref = {"path": str(TMP / "dae" / "box.dae"), "position": [100, 5, 0], "rotation": [0, 90, 0], "scale": 0.5}
placed = modelref.build(ref)
lo, hi = placed.bounds()
want = np.array([[x, y, z] for x in (10, 14) for y in (-6, 0) for z in (-2, 0)], np.float64)
w2 = (want * 0.5) @ np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]]).T + [100, 5, 0]           # turn 90 about Y: x' = z, z' = -x
check("reference: position, turn and scale place the model", np.allclose(lo, w2.min(0), atol=1e-4) and np.allclose(hi, w2.max(0), atol=1e-4),
      (lo, hi, w2.min(0), w2.max(0)))
ed = modelref.apply_edits(fb, {"edits": {"BoxMat": {"mode": "opaque", "flip": True, "color": [1, 0, 0], "texture": ""}}})
check("reference: material edits (drawn as, texture removed, colour, faces turned round)",
      ed.parts[0].mode == "opaque" and ed.parts[0].texture is None and ed.parts[0].color == (1.0, 0.0, 0.0)
      and not faces_out(ed) and np.allclose(np.sort(ed.parts[0].positions, axis=0), np.sort(fb.parts[0].positions, axis=0)))
check("reference: hidden materials are left out", not modelref.apply_edits(fb, {"hidden": ["BoxMat"]}).parts)
mir = fb.transformed(np.diag([-1.0, 1, 1, 1]))
check("a mirroring transform keeps the faces pointing outwards", faces_out(mir))

# ---- scenery assets cut out of a world (kdx.assetkit) --------------------------------------------------------------
from kdx import assetkit
two = objmodel.ObjModel([p.copy() for p in fb.parts] + [p.copy() for p in fb.transformed(
    np.array([[1, 0, 0, 500.0], [0, 1, 0, 40.0], [0, 0, 1, 0], [0, 0, 0, 1]])).parts])
blo, bhi = fb.bounds()
bc = (np.asarray(blo) + np.asarray(bhi)) / 2
lid = fb.transformed(np.array([[0.4, 0, 0, 500.0 + 0.6 * bc[0]], [0, 0.2, 0, 40.0 + bhi[1] - 0.2 * blo[1]],
                               [0, 0, 0.4, 0.6 * bc[2]], [0, 0, 0, 1]]))         # a lid on the second box's top
for p in lid.parts:
    p.material = "Lid"
split = assetkit.pieces(list(two.parts) + list(lid.parts))
check("asset kit: a world's shapes split into its objects (two boxes; a small lid standing on one belongs to it)",
      len(split) == 2 and sorted(len(o.parts) for o in split) == [1, 2], [len(o.parts) for o in split])
cm, base = assetkit.centred(split[0])
lo, hi = cm.bounds()
check("asset kit: a cut object stands with its foot's centre at the origin, its old place returned",
      abs(lo[1]) < 1e-4 and abs(lo[0] + hi[0]) < 1e-3 and abs(lo[2] + hi[2]) < 1e-3 and len(base) == 3, (lo, hi, base))
bw = float(bhi[0] - blo[0])
halves = [fb.transformed(np.array([[1, 0, 0, dx], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])) for dx in (0.0, 0.9 * bw)]
far = fb.transformed(np.array([[1, 0, 0, 5 * bw], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))
groups = assetkit.clusters(halves + [far], gap=0.05 * bw)
check("asset kit: clusters joins pieces whose boxes touch (a plant in two halves) and keeps a distant one apart",
      sorted(len(g.parts) for g in groups) == [1, 2], [len(g.parts) for g in groups])
from kdx import export2d
alike = export2d.drawn_alike([p.copy() for g in (halves + [far]) for p in g.parts])
check("export: parts drawn alike (same texture, mode, wrap) leave as one shape, their triangles all kept",
      len(alike) == len(fb.parts) and sum(p.triangle_count for p in alike) == 3 * sum(p.triangle_count for p in fb.parts),
      (len(alike), len(fb.parts)))

# ---- scenery objects in a map --------------------------------------------------------------------------------------
proj = project2d.Project(40, 40, world=1, name="Objects")
i = proj.add_object(TMP / "dae" / "box.dae", position=(300, 0, 300), name="Box")
proj.add_object(TMP / "dae" / "box.dae", position=(600, 0, 300), name="Box")
check("objects get unique names", [o["name"] for o in proj.objects] == ["Box", "Box 2"], [o["name"] for o in proj.objects])
proj.save(TMP / "maps" / "objects.kdxmap") if (TMP / "maps").mkdir() is None else None
saved = (TMP / "maps" / "objects.kdxmap").read_text(encoding="utf-8")
check("objects are saved with paths relative to the map", "../dae/box.dae" in saved.replace("\\\\", "/"), saved[-300:])
back = project2d.Project.load(TMP / "maps" / "objects.kdxmap")
sm = back.scene_model()
check("a painted map with objects: the scene has the objects' parts, named <object>/<material>",
      sm is not None and [p.material for p in sm.parts] == ["Box/BoxMat", "Box 2/BoxMat"], sm and [p.material for p in sm.parts])
back.set_model3d(TMP / "obj" / "box.obj")
back.model3d["hidden"] = []
back.model3d["edits"] = {"BoxMat": {"mode": "translucent"}}
back.update_model3d(back.model3d)
check("the ground reference takes material edits too", back.terrain_model().parts[0].mode == "translucent")

# ---- NSMBW --------------------------------------------------------------------------------------------------------
romfs_root = romfs.normalize(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KDX_ROMFS", ""))
nsmbw_root = Path(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("KDX_NSMBW", "") or "/nonexistent")
port = None
if (nsmbw_root / "WorldMap" / "CS_W1.arc").exists():
    from kdx import nsmbw
    files = brres.u8_files((nsmbw_root / "WorldMap" / "CS_W1.arc").read_bytes())
    res = brres.Brres(files["g3d/model.brres"])
    mdl = res.models["CS_W1"]
    check("NSMBW CS_W1: U8 archive and BRRES read (MDL0 v11: 84 bones, 129 polygons, 57 materials, 52 textures)",
          (mdl.version, len(mdl.bones), len(mdl.polygons), len(mdl.materials), len(res.textures)) == (11, 84, 129, 57, 52))
    bad = [t.name for t in res.textures.values() if t.rgba().shape != (t.height, t.width, 4)]
    check("NSMBW CS_W1: every texture decodes (CMPR, I8, IA8, RGB5A3, RGB565)", not bad, bad)
    water = next(m for m in mdl.materials if m.name == "mtWater")
    check("NSMBW: the water's TEV colour registers are read (BrawlCrate MatTevColorBlock)",
          len(water.tev_colors) >= 2 and water.tev_colors[0][2] > 0.8 and water.tev_colors[0][0] < 0.05, water.tev_colors[:2])
    stem = mdl.polygons[44]
    check("NSMBW: a polygon's node id is a matrix id (polygon44 -> kinoko1Stem, bone index 51)",
          mdl.matrix_bone[stem.bone_id].name == "kinoko1Stem" and mdl.matrix_bone[stem.bone_id].index == 51)
    road = next(m for m in mdl.materials if m.name == "mtRW101W102a")
    check("NSMBW: path materials read the road from UV set 1 (XF texgen at display list + 0xE0), the mask from set 0",
          [l.uv_set for l in road.layers[:2]] == [0, 1] and road.layers[1].texture == "W1_road", [l.uv_set for l in road.layers])
    cap = next(m for m in mdl.materials if m.name == "mtKinokoRed")
    check("NSMBW: texture wrap modes at +0x18/+0x1C (mushroom caps mirror a half-cap texture)",
          cap.layers[0].wrap == ("mirror", "mirror") and road.layers[1].wrap == ("repeat", "repeat"), (cap.layers[0].wrap, road.layers[1].wrap))
    red = brres.Brres(brres.u8_files((nsmbw_root / "WorldMap" / "cobKinokoRed.arc").read_bytes())["g3d/model.brres"])
    pose = red.end_pose("cobKinokoAppear")
    check("NSMBW: CHR0 end pose read (cobKinokoAppear raises the Toad house's kinoko bone from y -45 to +5)",
          abs(pose["kinoko"]["translation"][1] - 5.0) < 0.01 and abs(pose["trunk"]["translation"][1] + 10.345) < 0.01, pose)
    risen = model3d.load_brres(nsmbw_root / "WorldMap" / "cobKinokoRed.arc", out_dir=TMP / "toad", pose=pose)
    check("NSMBW: posed, the Toad house stands on the ground (bottom near 0, top about 97)",
          -15 < risen.bounds()[0][1] < 0 and risen.bounds()[1][1] > 90, risen.bounds())
    out = TMP / "port"
    port, notes = nsmbw.port_world(nsmbw_root, 1, out)
    rep = port.check()
    check("port: NSMBW World 1 passes NSMBUDX's rules (no errors, every stop reachable)", not rep.errors
          and rep.summary[0].startswith("Play-through from W1M0: 18/18"), (rep.errors, rep.summary[:1]))
    check("port: start W1S0 -> W1M0, cannon W1W0 -> W1S0 (NSMBUDX's warp to World 5), opened by W103's secret exit",
          "W1M0" in port.nodes and "W1S0" in port.nodes and "RW103W1S0" in port.nodes["W103"]["row"][7])
    check("port: point tables list the points each exit unlocks (W101 -> W102), as NSMBUDX's do",
          port.nodes["W101"]["row"][3] == "W102")
    check("port: World 1's Goomba square is opened by W104, each path cut at its stops (RFb00W106 -> RFb00Fb03 + "
          "RFb03W106, the square's far side RFb00Fb01 + RFb01Fb02 + RFb02Fb03)",
          set(rules.split(port.nodes["W104"]["row"][4])) >= {"RW104Fb00", "RFb00Fb01", "RFb01Fb02", "RFb02Fb03",
                                                               "RFb00Fb03", "RFb03W106", "RFb01W1K3", "RFb02W105"},
          port.nodes["W104"]["row"][4])
    check("port: no point loses a neighbour link in the game's slot table (0x60178558: W103's Toad house, Fb02's Fb03, "
          "Kn0f's W105 were lost)",
          not rules.slot_losses([r["id"] for r in port.routes], {k: n["pos"] for k, n in port.nodes.items()}),
          rules.slot_losses([r["id"] for r in port.routes], {k: n["pos"] for k, n in port.nodes.items()}))
    enemy = [k for k, n in port.nodes.items() if "W1PakkunA" in rules.split(n["row"][2])]
    links = [r["id"] for r in port.routes if enemy and enemy[0] in rules.route_ends(r["id"])]
    check("port: World 1's map enemy starts on a plain point with two neighbours on the square's front road (Fe00, "
          "as NSMBUDX's Fb01; on a stop it left Mario with direction 6: main+0x1C6C54)",
          enemy == ["Fe00"] and sorted(links) == ["RFb00Fe00", "RFe00Fb03"]
          and "stop" not in rules.split(port.nodes["Fe00"]["row"][2]), (enemy, links))
    check("port: the Wii sky stands behind the castle as a scenery object (it hides World 2)",
          any(o["name"] == "Wii sky backdrop" and o["position"][2] < port.nodes["W1C0"]["pos"][2] - 300
              for o in port.objects), [o["name"] for o in port.objects])
    kept = [p for p in port.nodes if p[0] == "K" and not p.startswith("Kn")]
    check("port: no bend keeps an NSMBW name that NSMBUDX walks use (K072...): they are Kn00...", not kept, kept)
    check("port: castle, tower, Toad houses, cannon and Peach's Castle become scenery objects at their bones",
          {o["name"] for o in port.objects} >= {"cobCastle", "cobToride", "cobKinokoRed3", "cobKinokoStar2",
                                                 "cobKinokoRed1", "cobCannon00", "cobPeachCastle", "Mushroom lifts"})
    ground = port.terrain_model()
    gmats = {p.material for p in ground.parts}
    check("port: sky, clouds, shadows and Wii level markers are left out of the ground",
          not gmats & set(nsmbw.SKIP_MATERIALS) and "mtGrass" in gmats, gmats & set(nsmbw.SKIP_MATERIALS))
    check("port: the Goomba square's roads are in the ground (the kuribo bone's mtKuribo, mtKuriboBrown, mtKuriboGray, "
          "named after the square's first path so they stay hidden until W104 opens it)",
          {"mtRFb00Fb01a", "mtRFb00Fb01b", "mtRFb00Fb01c"} <= gmats, sorted(m for m in gmats if m.startswith("mtRFb")))
    check("port: points stand where NSMBW's bones are (W1C0 on the castle's hill)",
          abs(port.nodes["W1C0"]["pos"][1] - 75.0) < 1e-3)
    cutouts = sorted(p.material for p in ground.parts if p.mode == "mask")
    check("port: cut-out materials (flowers, road edges) keep their alpha mode", "mtRW101W102a" in cutouts and len(cutouts) >= 8, cutouts)
    texs = [Path(p.texture) for i in range(len(port.objects)) for p in port.object_model(i).parts if p.texture] + \
           [Path(p.texture) for p in ground.parts if p.texture]
    clash = {t.stem.split("__")[0] for t in texs if t.stem.split("__")[1:2] == ["texKinokoRed"]}
    check("port: textures are named per model (CS_W1's and the Toad house's texKinokoRed no longer overwrite each other)",
          clash == {"CS_W1", "cobKinokoRed"}, clash)
    modes = {p.material: p.mode for p in ground.parts}
    check("port: the fruit is opaque as on the Wii (no blend, no alpha test: its alpha-0 rows hold the green calyx); "
          "roads, which blend, stay cut-outs", modes.get("mtFruit") == "opaque" and modes.get("mtRW101W102a") == "mask",
          (modes.get("mtFruit"), modes.get("mtRW101W102a")))
    leaves = sorted(p.material for p in ground.parts if p.material.split("__")[0] == "mtKusaFruits")
    check("port: the bush's leaves keep the Wii's vertex shading (texture copies per shade, the ground one piece)",
          len(leaves) >= 3 and "mtKusaFruits" in leaves and not any(p.material.startswith("mtGrass__") for p in ground.parts),
          leaves)
    opened = {t for n in port.nodes.values() for c in (4, 7) for t in rules.split(n["row"][c])}
    paths = {p.material: p.mask_offset for p in ground.parts if p.uvs2 is not None}
    check("port: path materials keep the Wii's reveal mask uvs and hidden offset, named after routes the point table "
          "opens (mtRW103W1W0a -> mtRW103W1S0a; the Goomba square's roads fade in with RFb00Fb01)",
          paths.get("mtRW101W102a") is not None and abs(paths["mtRW101W102a"] + 2.9) < 1e-4
          and "mtRW103W1S0a" in paths and {"mtRFb00Fb01a", "mtRFb00Fb01b", "mtRFb00Fb01c"} <= set(paths)
          and all(nsmbw.ROUTE_MATERIAL.match(m).group(1) in opened for m in paths), paths)
    check("port: the flat flowers stay cut out (CMPR alpha), the fruit opaque",
          modes.get("CS_common_takamura_mtFlowerA") == "mask" and modes.get("mtFruit") == "opaque",
          (modes.get("CS_common_takamura_mtFlowerA"), modes.get("mtFruit")))
    srim = next(port.object_model(i) for i, o in enumerate(port.objects) if o["name"] == "Level rim W1S0")
    check("port: the cannon's rim (a secret point) appears with its path (mtRW103W1S0b, a fade mask)",
          [p.material for p in srim.parts] == ["mtRW103W1S0b"] and srim.parts[0].uvs2 is not None, [p.material for p in srim.parts])
    rims = sorted(o["name"][10:] for o in port.objects if o["name"].startswith("Level rim "))
    check("port: NSMBW's gold rim stands at every level point but the start (NSMBUDX's level actor is the disc alone)",
          rims == sorted(p for p in port.nodes if p[0] == "W" and port.nodes[p]["kind"] != "start") and "W106" in rims
          and "W1M0" not in rims, rims)
    houses = [port.object_model(i) for i, o in enumerate(port.objects) if o["name"].startswith("cobKinoko")]
    check("port: the Toad houses stand above the ground (risen pose), not sunk in it",
          houses and all(h.bounds()[1][1] - port.nodes["W1K1"]["pos"][1] > 80 for h in houses), [h.bounds()[1][1] for h in houses])
    roadp = next(p for p in ground.parts if p.material == "mtRW101W102a")
    check("port: the road keeps its own UVs (the road texture across the path, not the reveal mask's)",
          roadp.uvs[:, 0].max() - roadp.uvs[:, 0].min() > 0.3, (roadp.uvs.min(0), roadp.uvs.max(0)))
    port.save(out / "port.kdxmap")
else:
    print("SKIP NSMBW checks (pass the NSMBW folder as the second argument, or set KDX_NSMBW)")

if port is not None and romfs_root is not None:
    from kdx import export2d, world as W
    ok, msg = export2d.engine_status()
    if ok:
        md = romfs.model_dir(romfs_root)
        image = QtGui.QImage(64, 64, QtGui.QImage.Format_ARGB32); image.fill(0xff40a040)
        try:                                                      # W106 has no Course/1-6 in NSMBUDX: refused
            export2d.export_world(port, image, md / "CS_W1.Nin_NX_NVN.szs", TMP / "mod0", md)
            refused = ""
        except export2d.ExportError as e:
            refused = str(e)
        check("port export: a level point without its level archive is refused (W106 -> Course/1-6.sarc: the game "
              "crashed entering it, main+0x40D33C)", "Course/1-6.sarc does not exist" in refused, refused[:200])
        check("points show the level archive they open (W106 · 1-6, W1T0 · 1-21)",
              rules.point_label("W106") == "W106 · 1-6" and rules.point_label("W1T0") == "W1T0 · 1-21"
              and rules.point_label("Fb00") == "Fb00")
        ts = rules.level_tilesets(Path(romfs_root) / "Course" / "1-1.sarc")
        check("level archives name their tilesets (1-1 area 1: Pa0_jyotyu, Pa1_1-1_1, Pa2_1-1_1 in romfs/Unit)",
              ts.get("course/course1.bin") == ["Pa0_jyotyu", "Pa1_1-1_1", "Pa2_1-1_1"], ts)
        stand_in = TMP / "mod" / "atmosphere" / "contents" / "0100EA80032EA000" / "romfs" / "Course"
        stand_in.mkdir(parents=True, exist_ok=True)
        from kdx import levels, texture as T
        ren = levels.copy_level(Path(romfs_root) / "Course" / "1-1.sarc", stand_in / "1-6.sarc",
                                [Path(romfs_root) / "Unit"], stand_in.parent / "Unit", "1-1", "1-6")
        t6 = levels.tilesets(stand_in / "1-6.sarc")
        check("a level copied as 1-6 gets its own tilesets (Pa1_1-6_1...; Pa0_jyotyu stays shared), renamed inside "
              "too (BG_unt, BG_chk and the BFRES textures)",
              t6.get("course/course1.bin") == ["Pa0_jyotyu", "Pa1_1-6_1", "Pa2_1-6_1"] and len(ren) == 5
              and "BG_unt/Pa1_1-6_1.bin" in dict(archive.read_szs(stand_in.parent / "Unit" / "Pa1_1-6_1.szs")[0])
              and "Pa1_1-6_1_nml" in T.read_bntx(bfres.BfresFile.parse(dict(archive.read_szs(
                  stand_in.parent / "Unit" / "Pa1_1-6_1.szs")[0])["output.bfres"]).external_bytes("textures.bntx")), t6)
        written, erep = export2d.export_world(port, image, md / "CS_W1.Nin_NX_NVN.szs", TMP / "mod", md)
        ew = W.World(written[0])
        g = next(m for m in ew.model.models if m.name.endswith("_World"))
        modes = [m.blend_mode for m in g.materials]
        check("port export: the world exports and reads back with no errors", not erep.errors, erep.errors)
        check("port export: cut-out parts copy a vanilla alpha-test material (W1: mtKinokoRed)", modes.count("mask") >= 3, modes.count("mask"))
        rmats = {m.name for m in g.materials if m.name.startswith("mtR")}
        rshape = next(s for s in g.shapes if g.materials[s.material_index].name == "mtRW101W102a")
        export2d.run_engine("materials", TMP / "port_check.bfres" if (TMP / "port_check.bfres").write_bytes(
            bytes(ew.bfres_bytes)) else TMP / "port_check.bfres", TMP / "port_check.json")
        pm = json.loads((TMP / "port_check.json").read_text())["models"][g.name]
        check("port export: paths are route materials the game hides until they open (mt<route><a-h>, the route "
              "shader, MaskOpaque on the Wii's mask uvs in _u2, texmtx0 at the Wii's hidden offset: RW101W102 -2.9)",
              {"mtRW101W102a", "mtRW103W1S0a", "mtRW103W1S0b", "mtRFb00Fb01a"} <= rmats
              and "_u2" in g.vertex_buffers[rshape.vertex_buffer_index].attributes
              and pm["mtRW101W102a"]["shading_model"] == "nw4f_cs_route_shader"
              and "MaskOpaque" in pm["mtRW101W102a"]["textures"]
              and abs(pm["mtRW101W102a"]["params"]["texmtx0"]["value"]["translate"][1] + 2.9) < 1e-4, sorted(rmats))
        nml = pm["mtRW101W102a"]["textures"][pm["mtRW101W102a"]["samplers"].index("_n0")]
        px = np.asarray(T.read_bntx(ew.model.external_bytes("textures.bntx"))[nml].rgba()).reshape(-1, 4)[0]
        check("port export: the paths' flat normal map reads as (0, 0) (the route shader takes BC5 SNORM x/y as "
              "stored; grey 128 tilted the road normals and darkened pieces facing one way)",
              nml == "kdx_flatnml" and list(px[:2]) == [0, 0], (nml, list(px)))
        check("port export: the castle leads on to World 2 and W1S0 towards World 5, as in NSMBUDX",
              any("W2J0" in s for s in erep.summary) and any("W3J0" in s for s in erep.summary), erep.summary[:2])
    else:
        print("SKIP port export: " + msg)

# ---- editing a game world's own models --------------------------------------------------------------------------------
if romfs_root is not None:
    from kdx import export2d, worldedit, world as W, texture as T
    ok, msg = export2d.engine_status()
    if ok:
        md = romfs.model_dir(romfs_root)
        vw = W.World(md / "CS_W1.Nin_NX_NVN.szs")
        shapes = worldedit.world_shapes(vw)
        check("native world: every shape of every model is listed (World 1: 426 in 7 models)",
              len(shapes) == 426 and len({m for m, _s, _t, _p in shapes}) == 7, len(shapes))
        tname = worldedit.terrain_model_name(vw)
        victim = next(s for m, s, _t, _p in shapes if m == tname and "Ishiyama" in s)
        vw.model_edits = {"remove": [[tname, victim]], "textures": {"Grass_alb": str(TMP / "box.png")},
                          "add": [{"name": "Box", "path": str(TMP / "dae" / "box.dae"), "position": [600.0, 120.0, -600.0],
                                   "rotation": [0, 30, 0], "scale": 10.0}]}
        check("native world: model edits mark the world changed", vw.unsaved and vw.bfres_member in vw.changed_members())
        out = vw.export(TMP / "native_mod")
        back = W.World(out[0])
        bm = {m.name: m for m in back.model.models}
        names = [s.name for s in bm[tname].shapes]
        check("native world: the removed shape is gone, the added object is new shapes with copied materials",
              victim not in names and any(n.startswith("kdxadd_model_") for n in names)
              and any(m.name.startswith("mtKdxadd") for m in bm[tname].materials), names[-3:])
        check("native world: every other model is untouched",
              all(len(bm[m.name].shapes) == len(m.shapes) for m in vw.model.models if m.name != tname))
        bt = T.read_bntx(back.model.external_bytes("textures.bntx"))
        check("native world: the replaced texture has the new pixels' size (2x2 PNG -> 16x16 minimum)",
              (bt["Grass_alb"].width, bt["Grass_alb"].height) == (16, 16), (bt["Grass_alb"].width, bt["Grass_alb"].height))
        check("native world: the edited world still passes the game's rules", not back.report().errors, back.report().errors)
        worldedit.export_dae(vw, [tname], TMP / "native_dae" / "w1_world.dae")
        wd = model3d.load(TMP / "native_dae" / "w1_world.dae")
        check("native world: a model exports as COLLADA with a material per shape and its textures",
              len(wd.parts) == len([1 for m, _s, _t, _p in shapes if m == tname]) and all(p.texture for p in wd.parts[:5]))
    else:
        print("SKIP native world edits: " + msg)

# ---- the 3D world editor and the model editor (OpenGL) ------------------------------------------------------------
from kdx.maker2d import WorldMaker
mk = WorldMaker(); mk.setAttribute(QtCore.Qt.WA_DontShowOnScreen); mk.resize(1500, 900); mk.show()
for _ in range(5):
    app.processEvents()
mk.open(str((TMP / "port" / "port.kdxmap") if port is not None else (TMP / "maps" / "objects.kdxmap")))
mk.views.setCurrentIndex(1)
for _ in range(5):
    app.processEvents()
v = mk.view3d
if not v._ready:
    print("SKIP editor checks: no OpenGL 3.3 context")
else:
    nobj = len(mk.project.objects)
    check("3D world tab: the ground and every object are items", len(v.items) == nobj + 1 and v.items[0].key == "ground",
          [it.key for it in v.items])
    v.frame_all()
    frame = v.grabFramebuffer().convertToFormat(QtGui.QImage.Format_RGBA8888)
    ptr = frame.constBits(); ptr.setsize(frame.byteCount())
    a = np.frombuffer(bytes(ptr), np.uint8).reshape(frame.height(), frame.width(), 4)[..., :3].astype(int)
    drawn = (np.abs(a - [140, 184, 224]).sum(-1) > 30).mean()
    check("3D world tab: the world is drawn with its models (%.0f%% of the frame)" % (100 * drawn), drawn > 0.25, drawn)
    before = [dict(o) for o in mk.project.objects]
    m = v.items[1].matrix.copy(); m[0, 3] += 123.0
    v.objectEdited.emit(v.items[1].key, m)
    moved = mk.project.objects[0]["position"][0] - before[0]["position"][0]
    check("dragging an object moves it in the map (one undo step)", abs(moved - 123.0) < 1e-6, moved)
    mk.undo.undo()
    check("undo puts the object back", mk.project.objects[0]["position"] == before[0]["position"])
    mk.obj_list.setCurrentRow(0)
    mk.duplicate_object()
    check("Copy duplicates the selected object", len(mk.project.objects) == nobj + 1)
    mk.remove_object() if mk.obj_list.currentRow() == nobj else None
    check("Remove takes it out again", len(mk.project.objects) == nobj, len(mk.project.objects))
    pid = next(p for p in mk.project.nodes if p[0] == "W")
    old = mk.project.world_position(pid)
    v.pointDragged.emit(pid, (old[0] + 30.0, old[1], old[2]))
    new = mk.project.world_position(pid)
    check("dragging a point in 3D moves it (its own 3D position too)", abs(new[0] - old[0] - 30.0) < 1e-6, (old, new))
    mk.undo.undo()
    from kdx.modeleditor import ModelEditor
    dlg = ModelEditor(mk.project.objects[0], None, "test", mk, with_position=True)
    dlg.setAttribute(QtCore.Qt.WA_DontShowOnScreen); dlg.show()
    for _ in range(3):
        app.processEvents()
    first = dlg.table.item(0, 1).text()
    dlg.table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
    dlg.table.cellWidget(0, 3).setCurrentIndex(2)
    check("model editor: hiding a material and changing how it is drawn are stored as edits",
          dlg.ref.get("hidden") == [first] and dlg.ref["edits"][first]["mode"] == "translucent", (dlg.ref.get("hidden"), dlg.ref.get("edits")))
    check("model editor: the preview shows the edited model", len(dlg._edited.parts) == len(dlg.raw.parts) - 1)
    dlg.rot[1].setValue(45.0)
    check("model editor: turning the model is stored in the reference", dlg.ref.get("rotation") == [0.0, 45.0, 0.0], dlg.ref.get("rotation"))
    dlg.reject()
    if romfs_root is not None:
        from kdx import world as W
        from kdx.worldmodels import WorldModelsDialog
        nw = W.World(romfs.model_dir(romfs_root) / "CS_W1.Nin_NX_NVN.szs")
        wd = WorldModelsDialog(nw, mk)
        wd.setAttribute(QtCore.Qt.WA_DontShowOnScreen); wd.show()
        for _ in range(5):
            app.processEvents()
        check("world model editor: every shape is its own item, pickable (World 1: 426 in 7 models)",
              len(wd.view.items) == 426 and wd.tree.topLevelItemCount() == 7 and all(it.pickable for it in wd.view.items),
              len(wd.view.items))
        top = wd.tree.topLevelItem(0)
        first = top.child(0)
        first.setCheckState(0, QtCore.Qt.Unchecked)
        fkey = ("shape",) + tuple(first.data(0, QtCore.Qt.UserRole))
        check("world model editor: unticking a shape takes it out (stored as an edit, gone from the view)",
              [list(first.data(0, QtCore.Qt.UserRole))] == wd.edits["remove"] and wd.view.item(fkey) is None
              and len(wd.view.items) == 425, wd.edits["remove"])
        pipe = ("shape", "CS_W1_World", "dokan6__mtDokanRed")
        wd.view.selected = pipe
        wd._picked(pipe)
        check("world model editor: clicking a shape selects it (its row and the shape panel)",
              wd.shape_label.text().startswith("dokan6__mtDokanRed") and wd.shape_fields["position"][0].isEnabled()
              and tuple(wd.tree.currentItem().data(0, QtCore.Qt.UserRole)) == pipe[1:], wd.shape_label.text())
        m0 = wd._shape_matrix(*pipe[1:])
        m1 = m0.copy(); m1[0, 3] += 120.0; m1[:3, :3] *= 1.5
        wd._object_dragged(pipe, m1)
        e = wd.edits["shapes"].get(worldedit.shape_key(*pipe[1:]), {})
        mv = worldedit.world_move(e)
        check("world model editor: dragging a game shape moves, turns or scales it about its centre (stored as an edit)",
              mv is not None and abs(mv[0, 3] - (120.0 - 0.5 * m0[0, 3])) < 1e-6 and abs(mv[0, 0] - 1.5) < 1e-9
              and abs(wd.shape_fields["position"][0].value() - (m0[0, 3] + 120.0)) < 0.06, mv)
        png = TMP / "red.png"
        img = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32); img.fill(0xffff0000); img.save(str(png))
        wd._retexture_shape(str(png))
        check("world model editor: a shape can get its own texture (only that shape changes)",
              e.get("texture") == str(png.resolve()) and wd.view.item(pipe).model.parts[0].texture == str(png.resolve()))
        ok, _msg = export2d.engine_status()
        if ok:
            out = worldedit.apply(nw.bfres_bytes, wd.edits, nw)
            mo = next(x for x in bfres.BfresFile.parse(out).models if x.name == pipe[1])
            src = next(x for x in nw.model.models if x.name == pipe[1])
            a = np.asarray(bfres.build_mesh(mo, {pipe[2]}).vertices)
            b = np.asarray(bfres.build_mesh(src, {pipe[2]}).vertices)
            want = (np.c_[b, np.ones(len(b))] @ mv.T)[:, :3]
            sh = next(x for x in mo.shapes if x.name == pipe[2])
            check("world edit: the moved shape's vertices are moved in the file, it uses its own copy of its material "
                  "with the new texture", float(np.abs(a - want).max()) < 1e-3
                  and mo.materials[sh.material_index].name.startswith("mtDokanRed_kdx")
                  and mo.materials[sh.material_index].albedo.startswith("kdxshp_"), float(np.abs(a - want).max()))
        wd._reset_shape()
        check("world model editor: Reset puts the shape back", worldedit.shape_key(*pipe[1:]) not in wd.edits["shapes"])
        wd.reject()

# ---- 0.9.7: FBX export, the gizmo, the world size meter, the theme ----------------------------------------------------
from kdx import export2d, fbxwrite, theme
from kdx.editor3d import Item, SceneView, decompose
src = model3d.load(TMP / "box.fbx")
out_fbx = model3d.save_model(src, TMP / "fbx_out" / "box_again.fbx")
back = model3d.load(out_fbx)
check("FBX export: binary FBX 7.4 that reads back with the same triangles, positions, UVs and material",
      out_fbx.read_bytes()[:20] == b"Kaydara FBX Binary  " and len(back.parts) == len(src.parts)
      and back.parts[0].triangle_count == src.parts[0].triangle_count and back.parts[0].material == src.parts[0].material
      and np.allclose(np.sort(back.parts[0].positions, 0), np.sort(src.parts[0].positions, 0), atol=1e-4)
      and np.allclose(np.sort(back.parts[0].uvs, 0), np.sort(src.parts[0].uvs, 0), atol=1e-5),
      [(p.material, p.triangle_count) for p in back.parts])
check("FBX export: the texture is copied next to the file and the material points at it",
      back.parts[0].texture is not None and Path(back.parts[0].texture).parent == out_fbx.parent
      and Path(back.parts[0].texture).exists(), back.parts[0].texture)
try:
    model3d.save_model(src, TMP / "box.xyz")
    bad_ext = False
except model3d.ModelError:
    bad_ext = True
check("save_model: picks the format by extension (.dae, .fbx, .obj) and refuses others", bad_ext)

sv = SceneView()
sv.resize(800, 600)
sv.set_items([Item("obj", src, np.identity(4))])
sv.selected, sv.target, sv.distance, sv.yaw, sv.pitch = "obj", np.zeros(3), 400.0, -0.6, 0.5


def gizmo_drag(view, axis, end_world, ctrl=False, start_world=None):
    """Press on a handle of axis (0-2: its tip's screen point; 3: the centre square's corner), drag to the screen point
    of end_world, release. Returns the item's matrix."""
    it = view.item("obj")
    handles = dict(view._gizmo_handles(it))
    start = view.project(start_world if start_world is not None else handles[axis][-1] if axis < 3 else handles[3][1])
    end = view.project(end_world)
    mods = QtCore.Qt.ControlModifier if ctrl else QtCore.Qt.NoModifier
    def ev(kind, p, buttons):
        return QtGui.QMouseEvent(kind, QtCore.QPointF(*p), QtCore.Qt.LeftButton, buttons, mods)
    view.mousePressEvent(ev(QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton))
    view.mouseMoveEvent(ev(QtCore.QEvent.MouseMove, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), QtCore.Qt.LeftButton))
    view.mouseMoveEvent(ev(QtCore.QEvent.MouseMove, end, QtCore.Qt.LeftButton))
    view.mouseReleaseEvent(ev(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.NoButton))
    return view.item("obj").matrix.copy()


edited = []
sv.objectEdited.connect(lambda k, m: edited.append(k))
sv.set_mode("move")
L = sv._gizmo_frame(sv.item("obj"))[2]
m = gizmo_drag(sv, 0, (L + 37.0, 0.0, 0.0))
check("gizmo: dragging the X arrow moves along x only (y and z stay), and reports the edit on release",
      abs(m[0, 3] - 37.0) < 1.0 and abs(m[1, 3]) < 1e-6 and abs(m[2, 3]) < 1e-6 and edited == ["obj"], m[:3, 3])
sv.snapping = True
sv.item("obj").matrix = np.identity(4)
m = gizmo_drag(sv, 0, (L + 37.0, 0.0, 0.0))
check("gizmo: with snapping the move goes in steps of 10 units", abs(m[0, 3] - 40.0) < 1e-6, m[0, 3])
m = (sv.item("obj").__setattr__("matrix", np.identity(4)), gizmo_drag(sv, 0, (L + 37.0, 0.0, 0.0), ctrl=True))[1]
check("gizmo: Ctrl while dragging does the opposite of the snapping setting", abs(m[0, 3] - 37.0) < 1.0, m[0, 3])
sv.snapping = False
sv.item("obj").matrix = np.identity(4)
sv.set_mode("turn")
o, axes, L = sv._gizmo_frame(sv.item("obj"))
r = 0.8 * L                                                       # the Y ring, from 45 to 105 degrees round it


def on_y_ring(deg):
    return r * math.cos(math.radians(deg)) * axes[2] + r * math.sin(math.radians(deg)) * axes[0]
m = gizmo_drag(sv, 1, on_y_ring(105), start_world=on_y_ring(45))
_p, rot, _s = decompose(m)
check("gizmo: dragging the Y ring turns around y only", abs(rot[0]) < 0.5 and abs(rot[2]) < 0.5 and abs(abs(rot[1]) - 60) < 2.0,
      rot)
sv.item("obj").matrix = np.identity(4)
sv.set_mode("scale")
L = sv._gizmo_frame(sv.item("obj"))[2]
m = gizmo_drag(sv, 2, (0.0, 0.0, 2 * L))
_p, _r, sc = decompose(m)
check("gizmo: dragging the Z handle scales along z only", abs(sc[2] - 2.0) < 0.05 and abs(sc[0] - 1) < 1e-9 and
      abs(sc[1] - 1) < 1e-9, sc)
modes = []
sv.modeChanged.connect(modes.append)
for key in (QtCore.Qt.Key_W, QtCore.Qt.Key_E, QtCore.Qt.Key_R):
    sv.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier))
sv.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_X, QtCore.Qt.NoModifier))
check("gizmo: W/E/R pick move/turn/scale and X switches local axes", modes == ["move", "turn", "scale"]
      and sv.space == "local", (modes, sv.space))

v1, _ = export2d.size_verdict(11_019_528)
v2, _ = export2d.size_verdict(15_000_000)
v3, t3 = export2d.size_verdict(28_651_040)
check("world size: vanilla W1 fits, 15 MB is tight, Yoshi's Island at 28.6 MB (crashed at boot) is over",
      (v1, v2, v3) == ("ok", "tight", "over") and "crash" in t3, (v1, v2, v3))
check("theme: dark and light palettes and style sheets build", all(
    theme.palette(n).color(QtGui.QPalette.Window).isValid() and "QToolBar" in theme.style_sheet(n) for n in theme.THEMES))

# ---- 0.9.8: route actions ------------------------------------------------------------------------------------------
from kdx import routeactions as RA, routeui
GAME = {"道": 3, "砂": 20, "木": 33, "ジャンプ": 7, "はしご": 6, "ツタ": 10, "坂": 11, "氷坂": 12, "スイッチブロック": 19,
        "流砂": 21, "雪": 22, "氷": 23, "雲": 24, "水": 25, "はしご右": 27, "はしご左": 26, "はしご岩": 29, "はしご岩右": 31,
        "はしご岩左": 30, "はしご縄": 32, "土": 28, "石橋": 4, "石": 5}           # ReadAction_'s table (0x60AF7B04)
check("route actions: the 23 names and AnimTypes are the game's (ReadAction_ table), the same set rules knows",
      {a.name: a.anim_type for a in RA.ACTIONS} == GAME and set(GAME) == set(rules.ROUTE_TYPES))
NET = {6, 10, 26, 27, 29, 30, 31, 32}
RUN = {3, 4, 5, 20, 21, 22, 23, 24, 28, 33}
SLIP = {11, 12}
check("route actions: climb/walk/slide/jump follow isNetWalk_, isNormalRun_ and isSlip_ (switch block and water walk)",
      all((a.style == "climb") == (a.anim_type in NET) and (a.style == "slide") == (a.anim_type in SLIP) and
          (a.style == "jump") == (a.anim_type == 7) for a in RA.ACTIONS) and
      all(a.style == "walk" for a in RA.ACTIONS if a.anim_type in RUN | {19, 25}))
check("route actions: an unknown name walks as road, as in the game", RA.action("??").name == "道" and not RA.known("??"))
pal = routeui.ActionPalette()
pal.setCurrentIndex(pal.findData("ツタ"))
check("action palette: 23 buttons, combo-box API (findData / setCurrentIndex / currentData)",
      pal.count() == 23 and pal.currentData() == "ツタ" and pal.findData("nope") == -1)
rit = routeui.RouteItem("RW101W102", (0, 0), (200, 0), "はしご")
rsc = QtWidgets.QGraphicsScene()
rsc.addItem(rit)
rimg = QtGui.QImage(240, 60, QtGui.QImage.Format_ARGB32)
rimg.fill(0)
rp = QtGui.QPainter(rimg)
rp.translate(20, 30)
rit.paint(rp, None)
rp.end()
check("route item: draws (ladder) and its tooltip says what the game does",
      rimg.pixel(120, 30) != 0 and "climbs" in rit.toolTip())
rows = routeui.route_lines_3d((0, 0, 0), (100, 0, 0), "ジャンプ")
check("3D routes: a jump is an arc above the ground; a ladder has rungs",
      max(r[1] for r in rows) > 20 and len(routeui.route_lines_3d((0, 0, 0), (100, 0, 0), "はしご")) > 10)
check("3D routes: picking by screen distance", routeui.pick_route_screen([("R1", (0, 0), (100, 0))], 50, 4) == "R1"
      and routeui.pick_route_screen([("R1", (0, 0), (100, 0))], 50, 40) is None)

# ---- 0.9.9: pipes, cannons and links ------------------------------------------------------------------------------
from kdx import pointactions as PA
prow = [["", "W1M0", "", "", ""], ["", "Fd01", "dokan", "", ""], ["", "Fd02", "dokan2", "", ""],
        ["", "Fs01", "stop", "", ""], ["", "W1S0", "", "W301", "RW1S0W3J0,RW3J0W5J0"]]
check("point actions: dokan = warp pipe, dokan2 = island pipe, W<n>S0 = cannon, link/balloon/boarding",
      [a.key for a in PA.actions_of("Fd01", ["dokan"])] == ["warp_pipe"] and
      [a.key for a in PA.actions_of("W1S0", [])] == ["cannon"] and not PA.is_cannon("W1S01") and
      [a.key for a in PA.actions_of("X", ["link2", "balloon1", "station"])] == ["link", "yoshi", "lift"])
check("cannon: shoots Mario along the first path its normal exit opens (W1S0 -> W3J0)",
      PA.cannon_target(prow[4]) == (3, "W3J0") and PA.cannon_target(["", "W2S0", "", "", ""]) == (None, None))
check("cannon: a chain that starts at a junction (W3S0 -> Fj02) has no world number and no crash",
      PA.cannon_target(["", "W3S0", "", "", "RW3S0Fj02,RFj02W6J0"]) == (None, "Fj02"))
check("pipe checks: W1 as in the game has no problems; an empty (hidden) W1S0 is no cannon problem",
      PA.problems(prow, "W1") == [] and PA.problems([["", "W1S0", "", "", ""]], "W1") == [])
bad = [r[:] for r in prow if r[1] != "Fs01"] + [["", "Fd09", "dokan", "", ""], ["", "W2S0", "", "W201", ""]]
bt = " | ".join(PA.problems(bad, "W1"))
check("pipe checks: two warp pipes, island pipe without Fs01, cannon with no path",
      "only the first one" in bt and "Fs01" in bt and "W2S0" in bt, bt)
check("pipe checks: dokan2 outside World 1 is ignored by the game", any("ignores" in t for t in PA.problems(prow, "W3")))
bar = routeui.PointActionBar()
bar.show_actions(["warp_pipe"], "W3")
check("point action bar: shows the point's actions; W1-only island pipe greyed out in W3",
      bar.buttons["warp_pipe"].isChecked() and not bar.buttons["island_pipe"].isEnabled() and
      bar.buttons["cannon"].isEnabled())
ppos = {"Fd01": (0, 0, 0), "Fs01": (500, 0, 0), "W1S0": (0, 0, 300)}
wl = routeui.warp_lines_3d(prow, ppos.get, "W1")
check("3D warp lines: pipe hop to Fs01 and the cannon arc rise above the ground",
      len(wl) > 8 and max(r[1] for r in wl) > 50 and routeui.warp_lines_3d(prow, lambda q: None, "W1") == [])

# ---- climbs, direction slots, flag help (calcConnect_ 0x601A3C64) ------------------------------------------------
from kdx import flaghelp, pointhelp
check("direction slots: up/down only when both x and z are within 3 (z counts, calcConnect_)",
      rules.heading((0, 0, 0), (0, 300, 0)) is None and rules.heading((0, 0, 0), (2, 140, -95)) == "north" and
      rules.heading((0, 0, 0), (100, 0, 10)) == "east")
cpos = {"W105": (-270, 180, -1500), "Fb01": (-268, 320, -1595), "Fc31": (30, 180, -930), "F032": (30, 420, -930),
        "Fz01": (500, 185, -930), "Fz02": (500, 190, -930)}
croutes = [["RW105Fb01", "はしご岩", ""], ["RFc31F032", "はしご", ""], ["RFz01Fz02", "ツタ", ""]]
call = croutes + [["RF000Fc31", "道", ""], ["RF032F001", "道", ""], ["RF002Fz01", "道", ""], ["RFz02F004", "道", ""],
                  ["RFb01F003", "道", ""], ["RW105W106", "道", ""]]
ct = [t for _r, t in rules.climb_problems(croutes, cpos, {"Fb01": ["W1PakkunA"]}, call)]
check("climb checks: a slanted ladder from a level (one-way), onto a map enemy, a vine that does not rise; a ladder "
      "between two pass-through points is fine",
      len(ct) == 4 and "is a stop" in ct[0] and "not vertical" in ct[1] and "W1PakkunA" in ct[2] and
      "rises only" in ct[3], ct)
check("flag help: every flag the game knows has a plain label, group and text",
      all(flaghelp.info(f).label and flaghelp.info(f).group in flaghelp.GROUPS and flaghelp.info(f).text
          for f in rules.KNOWN_FLAGS) and "fix" not in rules.KNOWN_FLAGS and "ignores" in flaghelp.info("fix").text)
check("flag help: tooltip warns when a flag is for another world; unknown names are flagged",
      "Only read in W1" in flaghelp.tooltip("balloon1", "W3") and "ignores" in flaghelp.info("bogus").warn)
check("point help: a level says its level file; a bend says it can't have flags; next free level names",
      "Course/1-3.sarc" in pointhelp.explain("W103", ["restart"], "Yoshi Hill") and
      "can't have flags" in pointhelp.explain("K012") and
      pointhelp.free_level_names({"W101": 1, "W102": 1, "W104": 1}, 1, 3) == ["W103", "W105", "W106"])

# ---- adding points to a vanilla world (map-point model rebuilt on export) --------------------------------------
if romfs_root and (romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs").exists():
    from kdx.world import World as _W
    aw = _W(romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs")
    check("add points: vanilla W1 needs no rebuild; free names W106 (level), F900 (stop), K900 (bend)",
          not aw.needs_marker_rebuild() and aw.free_name("level") == "W106" and aw.free_name("stop") == "F900"
          and aw.free_name("bend") == "K900")
    p5 = aw.position("W105")
    aw.add_point("W106", (p5[0] + 40, p5[1], p5[2] + 300))
    aw.add_route("RW105W106")
    r5 = aw.row_of("W105"); r5[3] += ",W106"; r5[4] += ",RW105W106"
    order = aw.marker_order()
    check("add points: a new level has a row and a position; the bone order keeps the game's and adds the path bone",
          aw.row_of("W106") is not None and aw.position("W106")[1] == p5[1] and aw.needs_marker_rebuild() and
          order.index("RW105W106") >= 0 and order[:3] == [b.name for b in aw.marker.bones[1:4]])
    try:
        nb = aw.rebuild_marker(bytes(aw.bfres_bytes))
        mk = next(m for m in bfres.BfresFile.parse(nb).models if m.name == aw.marker.name)
        nm = [x.name for x in mk.bones]
        check("add points: the engine rebuilds the map-point model: new bone + level marker, path bone, every old "
              "bone and marker kept in place",
              "W106" in nm and "RW105W106" in nm and any(s.name.startswith("W106") for s in mk.shapes) and
              len(mk.shapes) == len(aw.marker.shapes) + 1 and
              all(abs(a - c) < 1e-3 for x in aw.marker.bones for a, c in zip(x.world_position,
                  next(y for y in mk.bones if y.name == x.name).world_position)))
    except Exception as ex:                                       # the engine needs .NET
        check("add points: engine rebuild", False, str(ex))
    saved = aw.remove_point("W106")
    check("add points: removing a new point takes its routes; the game's own points can't be removed",
          aw.route_row("RW105W106") is None and aw.row_of("W106") is None)
    mw = _W(romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs")
    mw.load_offsets(romfs_root / "Model")
    if mw.mario:
        mw.add_point("W106", mw.position("W105"))
        mw.set_mario("W106", (1.0, 2.0, 3.0))
        sv = mw.remove_point("W106")
        ok1 = mw.mario.check(mw.block, mw.point_ids) == []
        mw.restore_point("W106", sv)
        check("add points: undo drops the new point's camera record (no 'Records with no point'), redo restores it",
              ok1 and mw.mario.check(mw.block, mw.point_ids) == [] and
              tuple(mw.mario_record("W106").vector) == (1.0, 2.0, 3.0))

# ---- phase 3: path appear animations (events) -------------------------------------------------------------------
if romfs_root and (romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs").exists():
    from kdx import events as EV, anim as AN, export2d as E2
    ew = _W(romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs")
    if E2.engine_status()[0]:
        an = AN.Animations.load(ew)
        ev = EV.analyse(ew, an)
        pb = {p["path"]: p for p in ev["paths"]}
        check("events: W1's path animations are the models named like paths, with who opens them (RW103W104: W103's "
              "normal exit, 178 frames + fts; RW102W1S0: W102's secret exit); CS_W1_Synchro is ambient",
              pb["RW103W104"]["opened_by"] == [("W103", "normal")] and pb["RW103W104"]["frames"] == 178 and
              "_fts" in pb["RW103W104"]["extras"] and pb["RW102W1S0"]["opened_by"] == [("W102", "secret")] and
              [a["name"] for a in ev["ambient"]] == ["CS_W1_Synchro"], str(pb.keys()))
        ew.add_point("W106", ew.position("W105"))
        ew.add_route("RW105W106")
        r5 = ew.row_of("W105"); r5[3] += ",W106"; r5[4] += ",RW105W106"
        ew.route_copies.append({"source": "RW103W104", "target": "RW105W106",
                                "offset": list(EV.copy_offset(ew, "RW103W104", "RW105W106"))})
        nb = ew.apply_route_copies(bytes(ew.bfres_bytes))
        nf = bfres.BfresFile.parse(nb)
        nm = {m.name: m for m in nf.models}
        cm, om = nm.get("RW105W106"), nm["RW103W104"]
        off = EV.copy_offset(ew, "RW103W104", "RW105W106")
        check("events: copying an appear animation adds the model under the new path's name, root bone renamed "
              "(rW105W106) and moved by the offset between the paths' first points",
              cm is not None and cm.bones[0].name == "rW105W106" and
              all(abs(a - (c + o)) < 1e-3 for a, c, o in zip(cm.bones[1].world_position, om.bones[1].world_position, off)))
        tmpb = Path(tempfile.mkdtemp()) / "rc.bfres"; tmpb.write_bytes(nb)
        E2.run_engine("anims", tmpb, str(tmpb) + ".json")
        aj = json.load(open(str(tmpb) + ".json", encoding="utf-8"))
        check("events: ...with its skeletal and texture animations under the new name",
              "RW105W106" in [a["name"] for a in aj["skeletal_anims"]] and
              "RW105W106_fts" in [a["name"] for a in aj["material_anims"]])

    if E2.engine_status()[0]:
        mw2 = _W(romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs")
        tmn, shp = EV.terrain_shapes(mw2)
        dk = next(x for x in shp if x[0].startswith("dokan5"))
        piv, hgt = EV.pivot_of([dk[2:]])
        mw2.route_makes.append({"target": "RW105W106", "source_model": tmn, "shapes": [dk[0]], "preset": "pop",
                                "frames": 90, "amount": 200.0, "pivot": piv})
        nb2 = mw2.apply_route_makes(bytes(mw2.bfres_bytes))
        nm2 = {m.name: m for m in bfres.BfresFile.parse(nb2).models}
        t2 = Path(tempfile.mkdtemp()) / "mk.bfres"; t2.write_bytes(nb2)
        E2.run_engine("anims", t2, str(t2) + ".json")
        aj2 = json.load(open(str(t2) + ".json", encoding="utf-8"))
        a2 = next((a for a in aj2["skeletal_anims"] if a["name"] == "RW105W106"), None)
        an2 = AN.Animations.__new__(AN.Animations); an2._skel = {"RW105W106": a2}
        m2 = nm2.get("RW105W106")
        s0 = np.array(an2.bone_matrices(m2, 0, a2)[3]).reshape(3, 4) if (m2 and a2) else None
        s9 = np.array(an2.bone_matrices(m2, 90, a2)[3]).reshape(3, 4) if (m2 and a2) else None
        check("new appear animation (pop): the pipe moves into model RW105W106 (bones rW105W106 / pivot / appear / "
              "inner) and out of the ground; its animation is hidden at frame 0 and full size at the end",
              m2 is not None and [x.name for x in m2.bones] == ["rW105W106", "kdx_pivot", "kdx_appear", "kdx_inner"] and
              dk[0] not in [s.name for s in nm2[tmn].shapes] and a2 is not None and not a2["loop"] and
              abs(s0[:, :3]).max() < 1e-6 and np.allclose(s9, np.c_[np.eye(3), np.zeros(3)], atol=1e-4))
        # an object added from DAE gets its own appear animation (model edits run first, its parts kept apart)
        aw3 = _W(romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs")
        aw3.model_edits = {"remove": [], "textures": {}, "add": [
            {"name": "Box", "path": str(TMP / "dae" / "box.dae"), "position": [600.0, 120.0, -600.0], "scale": 10.0},
            {"name": "Box2", "path": str(TMP / "dae" / "box.dae"), "position": [900.0, 120.0, -600.0], "scale": 10.0}]}
        objs = {o[0]: o for o in EV.added_objects(aw3)}
        bp, _bh = EV.pivot_of([objs["Box"][2:]])
        aw3.route_makes.append({"target": "RW105W106", "source_model": tmn, "shapes": [], "objects": ["Box"],
                                "preset": "rise", "frames": 60, "amount": 100.0, "pivot": bp})
        nm3 = {m.name: m for m in bfres.BfresFile.parse(aw3.build_bfres(marker=False)).models}
        bx = nm3.get("RW105W106")
        tnames = [s.name for s in nm3[tmn].shapes]
        check("added object: the dialog lists both boxes; Box's parts move into RW105W106, Box2 (same texture) stays "
              "in the ground model", sorted(objs) == ["Box", "Box2"] and bx is not None and len(bx.shapes) >= 1 and
              all(s.name.startswith("kdxadd_model_") for s in bx.shapes) and
              any(n.startswith("kdxadd_model_") for n in tnames), (list(nm3), tnames[-3:]))
        pvw = EV.pending_preview(aw3)
        check("pending preview: the temp build replaces the ground model and adds RW105W106, with its animation",
              {"RW105W106", tmn} <= set(pvw["replace"]) and pvw["anims"].skeletal_for("RW105W106") is not None)
        aw3.model_edits["add"].pop(0)
        try:
            aw3.make_items({})
            gone = False
        except Exception as ex:
            gone = "Box" in str(ex)
        check("added object: removing the object makes the export say which appear animation needs it", gone)
        # keyframe editor keys: smooth move, a full turn, cubic curves like the game's own
        from kdx import keyedit as KE
        kw = _W(romfs_root / "Model" / "CS_W1.Nin_NX_NVN.szs")
        ck = {"translate": [[0, 0, -150, 0], [40, 0, 30, 0], [60, 0, 0, 0]], "rotate": [[0, 0, 0, 0], [60, 0, 360, 0]],
              "scale": [[0, 0, 0, 0], [1, 0.3, 0.3, 0.3], [60, 1, 1, 1]]}
        kw.route_makes.append({"target": "RW105W106", "source_model": tmn, "shapes": [dk[0]], "preset": "custom",
                               "frames": 60, "amount": 0, "pivot": piv, "keys": ck,
                               "interp": {"translate": "smooth", "rotate": "smooth"}})
        kb = kw.build_bfres(marker=False)
        ka = AN.Animations.load_bytes(kb)
        km = next(m for m in bfres.BfresFile.parse(kb).models if m.name == "RW105W106")
        kan = ka.skeletal_for("RW105W106")
        okk = True
        for fr in (0, 20, 30, 50, 60):
            M = np.array(ka.bone_matrices(km, fr, kan)[2]).reshape(3, 4)
            ty = KE.channel_value(ck["translate"], fr, True)[1]
            yaw = KE.channel_value(ck["rotate"], fr, True)[1]
            got = math.degrees(math.atan2(M[0, 2], M[0, 0])) % 360
            okk &= abs(M[1, 3] - (piv[1] + ty)) < 0.05 and abs((got - yaw % 360 + 180) % 360 - 180) < 0.05
        check("keyframes: the engine writes cubic curves (scale, rotate, translate) that match the editor's own curve "
              "maths at every frame checked (smooth move, a full turn)",
              okk and {c["type"] for b in kan["bones"] for c in b["curves"]} == {"Cubic"} and
              [c["target"] for b in kan["bones"] for c in b["curves"]] == [0x04, 0x08, 0x0C, 0x24, 0x14])
        rk = EV.preset_keys("rise", 60, 150)
        check("presets: rise starts 150 under the ground and ends in place; drop starts hidden above; grow scales y",
              rk["translate"][0][2] == -150 and abs(rk["translate"][-1][2]) < 1e-9 and
              EV.preset_keys("drop", 60, 100)["scale"][0][1] == 0.0 and EV.preset_keys("grow")["scale"][0][2] == 0.0)

passed = sum(ok for _, ok in results)
print("\n%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)

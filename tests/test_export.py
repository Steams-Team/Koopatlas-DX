#!/usr/bin/env python3
"""
World Maker export tests: a .kdxmap becomes a real world archive through the kdx-bfres engine.

    python tests/test_export.py <romfs folder>        (needs the .NET runtime; skipped otherwise)

Optional: KDX_YAZ0MOTO=<Yaz0moto checkout> also decodes the new texture with Yaz0moto's bntxlib (an independent
deswizzler) and compares it pixel for pixel.
"""
import json
import os

import numpy as np
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
from kdx import export2d, project2d, world as W, romfs, rules
from kdx.maker2d import WorldMaker

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if (detail and not ok) else ""))


ok, msg = export2d.engine_status()
if not ok:
    print("SKIP: " + msg)
    sys.exit(0)
root = romfs.normalize(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KDX_ROMFS", ""))
if root is None:
    raise SystemExit("Pass the romfs folder (with Model/CS_W1.Nin_NX_NVN.szs).")
model_dir = romfs.model_dir(root)
template = next(p for wid, p in romfs.worlds(root) if wid == "W1")

mk = WorldMaker()
check("the Starter Island sample opens", mk.open(str(ROOT / "samples" / "Starter Island.kdxmap")))
image = mk.render_image(with_nodes=False)
mod = Path(tempfile.mkdtemp())
written, rep = export2d.export_world(mk.project, image, template, mod, model_dir)
names = sorted(p.name for p in written)
check("export writes the world archive and its camera table into the LayeredFS Model folder",
      names == ["CS_W1.Nin_NX_NVN.szs", "MarioOffsetDataW1.bin"] and all("0100EA80032EA000" in str(p) for p in written), names)
other_map = ("another map", "not a point of this map", "another world")
check("the exported world passes the game's rules (no errors; warnings only for the way on to World 2, as in vanilla)",
      not rep.errors and all(any(k in m for k in other_map) for m in rep.warnings), (rep.errors, rep.warnings))
check("play-through: every point reachable, and the castle leads to World 2",
      any("10/10 points reachable" in x and "21/21 routes walkable" in x for x in rep.summary)
      and "Leads to other maps: W2J0" in rep.summary, rep.summary)
w = W.World(written[0])
w.load_offsets(written[0].parent)
check("the archive keeps the template's member list (tables, shaders, model)", [Path(n).name for n, _ in w.entries] == [Path(n).name for n, _ in W.World(template).entries])
check("the archive asks the game to unpack it 0x2000-aligned, like every vanilla Model archive (GPU resources need it)",
      open(written[0], "rb").read(12)[8:12] == (0x2000).to_bytes(4, "big"))
tw = W.World(template)
tw.load_offsets(model_dir)
linked, shift, _ = export2d.world_link(tw, mk.project)
nodes, hidden = linked.game_nodes("W1")
check("the point table is the map's points, in order, then the walk on to World 2 and the hidden placeholders",
      w.point_ids == [r[1] for r in linked.point_rows(nodes)] and w.point_ids[:5] == [r[1] for r in mk.project.point_rows()], w.point_ids)
check("the castle's exit is the game's own (opens W201 and the paths to World 2)",
      w.row_of("W1C0")[3:5] == tw.row_of("W1C0")[3:5], (w.row_of("W1C0"), tw.row_of("W1C0")))
link_pts = [p for p, n in nodes.items() if n.get("link")]
check("the vanilla walk to World 2 is copied, moved by (our castle - vanilla castle)",
      link_pts and all(all(abs(a - (b + c)) < 1e-2 for a, b, c in zip(w.position(q), tw.position(q), shift)) for q in link_pts), link_pts)
# Regression: the first in-game test crashed at main+0x1E7FB4 (the W1 map music reads point W1S0 without a null check)
check("W1S0 is in the point table and has a bone (map music lookup, 0x601E7F30)", "W1S0" in w.point_ids and "W1S0" in w.bones)
flags = {f for r in w.point_rows for f in rules.split(r[2])}
check("every W1 map-enemy flag is on a point (start-point search 0x6021F6E0)", set(rules.ENEMY_FLAGS["W1"]) <= flags, flags)
# Regression: the third in-game test froze when the player walked back onto the start point: with no point flagged
# balloon1, W1's map Yoshi sat on the player's point and the "Yoshi joins" event (116) waited forever at step 324.
check("W1's map Yoshi has its spot (balloon1), out of reach", "balloon1" in flags and
      all(w.position(r[1])[1] < -500 for r in w.point_rows if "balloon1" in rules.split(r[2])), flags)
check("connectW2 moves with the walk, so World 2 still lines up with its far end",
      "connectW2" in w.bones and all(abs(a - (b + c)) < 1e-2 for a, b, c in zip(w.position("connectW2"), tw.position("connectW2"), shift)))
# Regression: the second in-game test loaded but the player could not move: the game builds its paths from bones named
# R<from><to> followed by their walk in bone order (0x60181FC0), and the export wrote none.
bone_names = [b.name for b in w.marker.bones]
game_paths = rules.paths_from_bones(bone_names)
check("every path has its bone, followed by its walk, as the game reads them",
      sorted(game_paths) == sorted(linked.bone_layout("W1")[1]) and
      dict(game_paths) == {"RW101W102": ["K000"], "RW102W1T0": ["K001"], "RW1T0W1C0": [], "RW1M0W101": [],
                           "RW1C0W2J0": dict(rules.paths_from_bones([b.name for b in tw.marker.bones]))["RW1C0W2J0"]}, game_paths)
check("every route segment lies on a path the game builds", rules.unwalkable(w.route_rows, game_paths) == [],
      rules.unwalkable(w.route_rows, game_paths))
raw_points = dict(w.entries)[w.point_member].decode("utf-8")
check("the point table is quoted the way Nintendo's are", raw_points.splitlines()[1] == '1,W101,,"W102","RW101W102",,"",,',
      raw_points.splitlines()[:2])
check("placeholders are hidden under the start point, with no marker and no route",
      all(w.position(h)[1] < -500 for h in hidden) and not any(h in r[0] for h in hidden for r in w.route_rows))
check("every point and bend is a bone exactly at its map position",
      all(all(abs(a - b) < 1e-3 for a, b in zip(w.position(pid), mk.project.world_position(pid))) for pid in mk.project.nodes))
models = {m.name: m for m in w.model.models}
check("the vanilla road models go; the template's other models stay, every bone hidden (the game's actors use them)",
      sorted(models) == ["CS_W1", "CS_W1_Synchro", "CS_W1_World"] and not any(b.flags & 1 for b in models["CS_W1_Synchro"].bones),
      (sorted(models), [b.flags & 1 for b in models.get("CS_W1_Synchro", models["CS_W1"]).bones][:4]))
check("each W point has a level marker and each F stop a path dot (copies of vanilla's); placeholders none",
      sorted(s.name for s in models["CS_W1"].shapes) == sorted(p + "__mtCourseOBJ" for p in w.point_ids if p[0] in "WF" and p not in hidden))
check("the terrain is one 512-triangle mesh with one material", len(models["CS_W1_World"].shapes) == 1 and models["CS_W1_World"].triangle_total() == 512 and len(models["CS_W1_World"].materials) == 1)
mat = models["CS_W1_World"].materials[0]
check("the terrain material shows the painted map", "kdx_terrain" in mat.textures and not any(t.endswith("_alb") for t in mat.textures), mat.textures)
check("the camera table holds the map's camera offsets, in point order",
      [(r.name, r.vector) for r in w.mario.block_records(0)] == [(r[1], tw.mario_record(r[1]).vector if nodes[r[1]].get("link")
                                                                   else tuple(float(c) for c in nodes[r[1]]["camera"])) for r in linked.point_rows(nodes)])
tmp = Path(tempfile.mkdtemp())
(tmp / "out.bfres").write_bytes(bytes(w.bfres_bytes))
export2d.run_engine("roundtrip", tmp / "out.bfres", tmp / "out2.bfres")
check("BfresLibrary reloads and resaves the exported model", (tmp / "out2.bfres").exists())

yz = os.environ.get("KDX_YAZ0MOTO")
try:
    import pyximport
except ImportError:
    yz = None                                                    # Yaz0moto's bntxlib is built with Cython
if yz and Path(yz).is_dir():
    sys.path.insert(0, yz)
    import pyximport
    pyximport.install()
    from miyamoto import tileset as T
    from bntxlib import swizzle as S
    bntx = T.loadBNTXFromBFRES(bytes(w.bfres_bytes))
    tex = next(t for t in bntx.textures if t.name == "kdx_terrain")
    rgba, tw, th = export2d.bake_texture(image, 2048)
    data = S.deswizzle(tex.width, tex.height, 1, 1, 1, 4, tex.tileMode, tex.blockHeightLog2, tex.data[:tex.imageSize])
    check("the map texture, decoded by Yaz0moto's bntxlib, equals the painted map pixel for pixel", bytes(data[:tw * th * 4]) == rgba)
else:
    print("skip: set KDX_YAZ0MOTO to decode the texture with Yaz0moto's bntxlib")

wrong = project2d.Project(world=3)
try:
    export2d.export_world(wrong, image, template, mod, model_dir); refused = False
except export2d.ExportError as e:
    refused = "world 3" in str(e)
check("a world-3 map cannot be exported over World 1 (point names pick the level files)", refused)
broken = project2d.Project.load(ROOT / "samples" / "Starter Island.kdxmap")
broken.nodes["W101"]["row"][4] = ",".join("RW101W1%02d" % i for i in range(17))
try:
    export2d.export_world(broken, image, template, mod, model_dir); refused = False
except export2d.ExportError as e:
    refused = "rules" in str(e)
check("a map that breaks the game's rules is refused", refused)
try:
    export2d.export_world(mk.project, image, template, template.parent.parent.parent.parent.parent if False else mod, model_dir)
    shutil.rmtree(mod / "atmosphere")
    fake = Path(tempfile.mkdtemp()); md = fake / "atmosphere" / "contents" / "0100EA80032EA000" / "romfs" / "Model"; md.mkdir(parents=True)
    shutil.copy(template, md / template.name)
    export2d.export_world(mk.project, image, md / template.name, fake, model_dir); refused = False
except export2d.ExportError:
    refused = True
check("exporting into the folder the template came from is refused", refused)

hills = project2d.Project.load(ROOT / "samples" / "Starter Island.kdxmap")
hx, hy = hills.nodes["W102"]["x"], hills.nodes["W102"]["y"]
hills.height_dab(hx, hy, 4, 90)
hills.height_dab(hx + 8, hy - 3, 3, -40)
hw, hrep = export2d.export_world(hills, image, template, Path(tempfile.mkdtemp()), model_dir)
hw = W.World(hw[0])
hmodels = {m.name: m for m in hw.model.models}
check("a map with hills exports a terrain mesh of one cell per tile",
      hmodels["CS_W1_World"].triangle_total() == 2 * min(hills.width, 128) * min(hills.height, 128) and not hrep.errors,
      (hmodels["CS_W1_World"].triangle_total(), hrep.errors))
check("points stand on the hills (bone height = ground height under the point)",
      all(abs(hw.position(p)[1] - hills.height_at(n["x"], n["y"])) < 1e-2 for p, n in hills.nodes.items())
      and hw.position("W102")[1] > 60, hw.position("W102"))
check("the walk on to World 2 starts from the castle's new height",
      abs((hw.position("F191")[1] - tw.position("F191")[1]) - (hw.position("W1C0")[1] - tw.position("W1C0")[1])) < 1e-2)

# ---- a custom 3D world: an OBJ model as the ground ----------------------------------------------------------------
from kdx import texture as T
isl = project2d.Project(width=40, height=40, world=1, name="Hill Island")
isl.set_model3d(ROOT / "samples" / "models" / "hill_island.obj")
for kind, x, y in (("start", 12.5, 20.5), ("level", 20.5, 20.5), ("castle", 28.5, 20.5)):
    isl.add_node(kind, x, y)
isl.add_route("W1M0", "W101"); isl.add_route("W101", "W1C0")
isl.nodes["W101"]["row"][4] = "RW101W1C0"
isl.link_next = False
iw, irep = export2d.export_world(isl, image, template, Path(tempfile.mkdtemp()), model_dir)
iw = W.World(iw[0])
imodels = {m.name: m for m in iw.model.models}
ground = imodels["CS_W1_World"]
check("a 3D-model world exports one terrain shape and material per OBJ material",
      sorted(s.name for s in ground.shapes) == ["kdx_model_00", "kdx_model_01"] and len(ground.materials) == 2
      and not irep.errors, ([s.name for s in ground.shapes], irep.errors))
check("points stand on the model's surface",
      all(abs(iw.position(p)[1] - isl.height_at(n["x"], n["y"])) < 1e-2 for p, n in isl.nodes.items())
      and iw.position("W101")[1] > 100, [round(iw.position(p)[1], 1) for p in isl.nodes])
itex = T.read_bntx(iw.model.external_bytes("textures.bntx"))
from PyQt5 import QtGui
src = QtGui.QImage(str(ROOT / "samples" / "models" / "grass.png")).convertToFormat(QtGui.QImage.Format_RGBA8888)
ptr = src.constBits(); ptr.setsize(src.byteCount())
src_px = np.frombuffer(bytes(ptr), np.uint8).reshape(src.height(), src.width(), 4)
blocks = np.frombuffer(T.encode_bc1(src_px), np.uint8).reshape(-1, 8)
want = T.decode_bc1(blocks).reshape(src.height() // 4, src.width() // 4, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(
    src.height(), src.width(), 4)
check("the model's texture is written in BC1 and comes back block for block (engine swizzle, kdx.texture reader)",
      "kdx_m00" in itex and itex["kdx_m00"].format_name == "bc1_unorm"
      and np.array_equal(itex["kdx_m00"].raw_rgba(), want) and "kdx_m01" in itex,
      (sorted(n for n in itex if n.startswith("kdx_")), itex.get("kdx_m00") and itex["kdx_m00"].format_name))
mse = ((want[..., :3].astype(float) - src_px[..., :3]) ** 2).mean()
check("BC1 keeps the picture (PSNR %.1f dB over 32)" % (10 * np.log10(255 ** 2 / max(mse, 1e-9))),
      10 * np.log10(255 ** 2 / max(mse, 1e-9)) > 32)
mats = {m.name: m for m in ground.materials}
check("each part's material shows its own texture (albedo sampler)",
      mats["mtKdx00"].albedo == "kdx_m00" and mats["mtKdx01"].albedo == "kdx_m01", {k: m.albedo for k, m in mats.items()})
(tmp / "isl.bfres").write_bytes(bytes(iw.bfres_bytes))
export2d.run_engine("roundtrip", tmp / "isl.bfres", tmp / "isl2.bfres")
check("BfresLibrary reloads and resaves the 3D-model world", (tmp / "isl2.bfres").exists())


def facing_up(model, shape_name):
    """Share of a shape's triangles whose winding (counter-clockwise = front, as the game draws them) faces up."""
    s = next(x for x in model.shapes if x.name == shape_name)
    P = np.asarray(model.vertex_buffers[s.vertex_buffer_index].read("_p0"))[:, :3]
    t = P[np.asarray(s.meshes[0].indices(), np.int64)].reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    big = np.linalg.norm(n, axis=1) > 1e-6
    return float((n[big, 1] > 0).mean())


# Test mod 9: hill_island.obj winds clockwise, so the game culled the grass and showed the far cliff from inside.
check("an inside-out OBJ is turned round on loading (hill_island.obj: every triangle)",
      isl.terrain_model().flipped == 2400, isl.terrain_model().flipped)
check("the exported grass faces up, so the game draws it (front faces only)", facing_up(ground, "kdx_model_00") > 0.99,
      facing_up(ground, "kdx_model_00"))
export2d.run_engine("materials", tmp / "isl.bfres", tmp / "mats.json")
mats_json = json.loads((tmp / "mats.json").read_text(encoding="utf-8"))
check("the engine lists each material's shader options, parameters and sampler wrap modes",
      mats_json["models"]["CS_W1_World"]["mtKdx00"]["wrap"]["_a0"] == "Wrap/Wrap"
      and "shadow_color" in mats_json["models"]["CS_W1_World"]["mtKdx00"]["params"])

# Test mod 10: white lightmaps and baked light made the world 2.5 times too bright, its sand white.
m00 = mats_json["models"]["CS_W1_World"]["mtKdx00"]
scale = m00["params"]["gsys_bake_light_scale"]["value"]
bake1 = [int(c) for c in np.asarray(itex["kdx_bake1"].raw_rgba()).reshape(-1, 4)[0]]
check("model parts keep the template's lightmaps (lmDif/lmSpc), not white",
      "lmDifCSW1a" in m00["textures"] and "lmSpcCSW1a" in m00["textures"], m00["textures"])
check("the baked light is grey once the material scales it, as bright as vanilla grass (0.71)",
      all(abs(bake1[k] / 255 * scale[k] - 0.71) < 0.02 for k in range(3)) and bake1[3] == 6, (list(bake1), scale))

yi = project2d.Project.load(ROOT / "samples" / "Yoshi Island 3D.kdxmap")
check("the Yoshi Island model is built facing outwards (nothing to turn round)", yi.terrain_model().flipped == 0,
      yi.terrain_model().flipped)
yw, yrep = export2d.export_world(yi, image, template, Path(tempfile.mkdtemp()), model_dir)
yw = W.World(yw[0])
check("the Yoshi Island sample exports with every point reachable", not yrep.errors
      and any("10/10 points reachable" in x for x in yrep.summary), (yrep.errors, yrep.summary))
ypaths = dict(rules.paths_from_bones([b.name for b in yw.marker.bones]))
ym = yi.terrain_model()
check("the walk on to World 2 runs on the island's ridge (ground within 2 units of every bend)",
      all(abs(ym.height_at(yw.position(k)[0], yw.position(k)[2]) - yw.position(k)[1]) < 2 for k in ypaths["RW1C0W2J0"]),
      [(k, round(yw.position(k)[1], 1), round(ym.height_at(yw.position(k)[0], yw.position(k)[2]), 1)) for k in ypaths["RW1C0W2J0"]])

ends = project2d.Project.load(ROOT / "samples" / "Starter Island.kdxmap")
ends.link_next = False
_, rep_end = export2d.export_world(ends, image, template, Path(tempfile.mkdtemp()), model_dir)
check("with 'Castle leads on' unticked the world ends at its castle (no warnings)",
      not rep_end.errors and not rep_end.warnings and any("5/5 points reachable" in x for x in rep_end.summary), (rep_end.warnings, rep_end.summary))

deep = Path(tempfile.mkdtemp()) / "atmosphere" / "contents" / "0100EA80032EA000" / "romfs"
written2, _ = export2d.export_world(mk.project, image, template, deep, model_dir)
check("a mod folder chosen at its romfs level is not nested a second time", written2[0].parent == deep / "Model", written2[0])

# moving images (phase 5): their own quads in CS_W1_Synchro, the decoration model the game animates in a loop
from PyQt5 import QtGui
from kdx import anim as A
_d = Path(tempfile.mkdtemp())
_im = QtGui.QImage(4 * 32, 32, QtGui.QImage.Format_ARGB32); _im.fill(QtGui.QColor(255, 200, 0, 160)); _im.save(str(_d / "s.png"))
pj = mk.project
pj.stickers = [dict(name="star", path=str(_d / "s.png"), x=8.0, y=8.0, w=2.0, anim="frames", strip=4, speed=1.0, amount=1.0),
               dict(name="boat", path=str(_d / "s.png"), x=12.0, y=8.0, w=3.0, anim="drift", strip=1, speed=1.0, amount=1.0)]
written3, rep3 = export2d.export_world(pj, mk.render_image(with_nodes=False, moving=False), template, Path(tempfile.mkdtemp()), model_dir)
w3 = W.World(written3[0])
syn = [m for m in w3.model.models if m.name == "CS_W1_Synchro"]
sk = A.Animations.load_bytes(bytes(w3.bfres_bytes)).skeletal_for("CS_W1_Synchro")
check("moving images: CS_W1_Synchro holds a quad per frame plus the drifting one, on bones of their own",
      syn and len(syn[0].shapes) == 5 and sum(b.name.endswith("_in") for b in syn[0].bones) == 5 and not rep3.errors,
      (syn and len(syn[0].shapes), rep3.errors))
check("...animated by a looping skeletal animation of the same name (as vanilla's swaying palms)",
      sk is not None and sk["loop"] and sk["frames"] > 0, sk and (sk["loop"], sk["frames"]))
check("...and the archive stays well under the game's world heap", written3[0].stat().st_size < 13.6e6, written3[0].stat().st_size)

passed = sum(ok for _, ok in results)
print("\n%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)

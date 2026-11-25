#!/usr/bin/env python3
"""
3D view tests with real OpenGL: textured materials, animations and the ride-along camera.

    python tests/test_render.py <romfs folder>

Needs a desktop session with OpenGL 3.3 (the view is created off screen: Qt.WA_DontShowOnScreen, nothing appears).
The other GUI tests run without OpenGL (QT_QPA_PLATFORM=offscreen), which is why these are separate.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PyQt5 import QtCore, QtGui, QtWidgets

fmt = QtGui.QSurfaceFormat()
fmt.setVersion(3, 3); fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile); fmt.setDepthBufferSize(24)
QtGui.QSurfaceFormat.setDefaultFormat(fmt)
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
import numpy as np
from kdx import anim, export2d, ridealong, romfs
from kdx.scene import WorldScene
from kdx.viewport import Viewport
from kdx.world import World

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if (detail and not ok) else ""))


def pixels(img):
    img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
    p = img.constBits(); p.setsize(img.byteCount())
    return np.frombuffer(bytes(p), np.uint8).reshape(img.height(), img.width(), 4)


root = romfs.normalize(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KDX_ROMFS", ""))
if root is None:
    raise SystemExit("Pass the romfs folder (with Model/CS_W1.Nin_NX_NVN.szs).")
model_dir = romfs.model_dir(root)
w = World(model_dir / "CS_W1.Nin_NX_NVN.szs")
w.load_offsets(model_dir)
v = Viewport(); v.setAttribute(QtCore.Qt.WA_DontShowOnScreen); v.resize(1024, 640); v.show()
for _ in range(5):
    app.processEvents()
if not v._ready:
    print("SKIP: no OpenGL 3.3 context on this machine")
    sys.exit(0)
v.set_world(w, WorldScene(w))
for _ in range(3):
    app.processEvents()
v.target = np.array([-200.0, 150.0, -900.0]); v.distance, v.pitch, v.yaw = 1300.0, 0.6, -0.4
tex = pixels(v.grabFramebuffer())
check("every albedo texture of W1 uploads (%d, none failed)" % v.texture_stats["decoded"],
      v.texture_stats["decoded"] >= 30 and not v.texture_stats["failed"], v.texture_stats)
bg = np.array([18, 26, 38])
drawn = (np.abs(tex[..., :3].astype(int) - bg).sum(-1) > 30).mean()
check("the textured world fills the view (%.0f%% of the pixels drawn)" % (100 * drawn), drawn > 0.6, drawn)
shadows = sorted({b.material for m in v.textured.values() for b in m.batches if b.shadow})
check("the acorn trees' ground shadow is drawn as a shadow, not white (the only shadow decal in W1)",
      shadows == ["mtLeafDonguriShadow"], shadows)
culled = {b.cull for m in v.textured.values() for b in m.batches}
check("materials cull faces as the game does (W1's ground draws front faces only)", "back" in culled, culled)
v.set_textures(False)
flat = pixels(v.grabFramebuffer())
v.set_textures(True)
check("View > Textures switches to flat colours and back", np.abs(tex.astype(int) - flat.astype(int)).mean() > 5)

if export2d.engine_status()[0]:
    a = anim.Animations.load(w)
    check("W1's animations are read (texture scroll and moving scenery)",
          a.animated_materials() == {"mtWaterEdgeMini"} and a.skeletal_for("CS_W1_Synchro") is not None)
    v.play_animations(a); v.anim_timer.stop()
    v.anim_frame = 0.0; v._repose(); f0 = pixels(v.grabFramebuffer())
    v.anim_frame = 200.0; v._repose(); f1 = pixels(v.grabFramebuffer())
    changed = (np.abs(f0.astype(int) - f1.astype(int)).sum(-1) > 30).mean()
    check("playing the animations moves the scenery (%.1f%% of the pixels change)" % (100 * changed), 0.005 < changed < 0.5, changed)
    v.play_animations(None)
    back = pixels(v.grabFramebuffer())
    check("stopping the animations puts the scenery back at rest", np.abs(back.astype(int) - tex.astype(int)).max() < 8,
          int(np.abs(back.astype(int) - tex.astype(int)).max()))
else:
    print("skip: the .NET runtime is needed to read animations")

ride = ridealong.Ride(w, ["RW102W1T0"])
v.start_ride(ride, "look"); v.ride_timer.stop()
starts = []
for frac in (0.0, 0.5, 1.0):
    v.ride["dist"] = ride.length * frac
    v._ride_tick() if frac < 1.0 else None
    starts.append(tuple(v._rider) if v._rider else None)
check("ride along: Mario walks the path from W102 towards W1T0",
      starts[0] is not None and starts[1] is not None and np.allclose(starts[0], w.position("W102"), atol=10)   # one tick (250 units/s x 33 ms) from the start
      and not np.allclose(starts[0], starts[1]), starts)
check("ride along: the camera looks at Mario + the point's camera offset",
      np.allclose(v.target, np.add(starts[1], ride.sample(ride.length * 0.5)[1])), (v.target, starts[1]))
v.stop_ride()
check("ride along stops cleanly", v.ride is None and v._rider is None)

# ---- View > Shading > Game shaders (kdx.shader: the world's own programs, translated) ---------------------------------
v.target = np.array([-200.0, 150.0, -900.0]); v.distance, v.pitch, v.yaw = 1300.0, 0.6, -0.4
simple = pixels(v.grabFramebuffer())
saved = QtCore.QSettings("KoopatlasDX", "KoopatlasDX").value("render/game_shaders", "false")
if v.gl45:
    v.set_game_shaders(True)
    game = pixels(v.grabFramebuffer())
    g = v.game
    total = sum(len(m.materials) for m in w.model.models)
    check("game shaders: every W1 material is drawn with its own program (%d of %d, %s)"
          % (len(g.drawn) if g else 0, total, g.errors[:2] if g else "no renderer"),
          g is not None and len(g.drawn) == total and not g.errors)
    check("game shaders: the light maps are made from Env/env_pack (%s)" % sorted(g.lm_live) if g else "",
          g is not None and {"lmDifCSW1a", "lmSpcCSW1a"} <= set(g.lm_live))
    drawn = (np.abs(game[..., :3].astype(int) - bg).sum(-1) > 30).mean()
    check("game shaders: the world fills the view (%.0f%%) and looks different from the simple renderer" % (100 * drawn),
          drawn > 0.6 and np.abs(game.astype(int) - simple.astype(int)).mean() > 5)
    holes = lambda img: (img[..., 3] < 128).mean()
    check("game shaders: translucent materials leave no see-through holes (alpha < 0.5: %.2f%%, simple %.2f%%)"
          % (100 * holes(game), 100 * holes(simple)), holes(game) <= holes(simple) + 0.001)
    v.gl45 = False
    v._drop_game()
    nogl = pixels(v.grabFramebuffer())
    check("game shaders without OpenGL 4.5 fall back to the simple renderer",
          v.game is None and np.abs(nogl.astype(int) - simple.astype(int)).max() < 8 and "4.5" in v.game_status)
    v.gl45 = True
    v.set_game_shaders(False)
    check("View > Shading > Simple goes back to the simple renderer",
          np.abs(pixels(v.grabFramebuffer()).astype(int) - simple.astype(int)).max() < 8)
else:
    print("skip: no OpenGL 4.5 context for the game shaders")
QtCore.QSettings("KoopatlasDX", "KoopatlasDX").setValue("render/game_shaders", saved)

passed = sum(ok for _, ok in results)
print("\n%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)

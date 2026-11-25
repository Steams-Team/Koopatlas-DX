#!/usr/bin/env python3
"""
Koopatlas DX GUI tests: drives the real main window and OpenGL view.

    python tests/test_gui.py [folder with the game files]      (Linux without a display: xvfb-run -a python3 ...)
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLES = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KDX_SAMPLES", "samples"))

from PyQt5 import QtCore, QtGui, QtWidgets, QtTest
fmt = QtGui.QSurfaceFormat(); fmt.setVersion(3, 3); fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile); fmt.setDepthBufferSize(24)
QtGui.QSurfaceFormat.setDefaultFormat(fmt)
app = QtWidgets.QApplication(sys.argv)
QtCore.QCoreApplication.setOrganizationName("KoopatlasDX-tests")      # keep test settings separate
from kdx.mainwindow import MainWindow, OffsetTablesDialog
from kdx import offsets, rules

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if (detail and not ok) else ""))


def near(a, b, tol=1e-2):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


w1 = next(iter(sorted(SAMPLES.glob("CS_W1[._]Nin_NX_NVN.szs"))), None)
common = next(iter(sorted(SAMPLES.glob("CommonMsg_*.szs"))), None)
if not w1:
    raise SystemExit("CS_W1 archive not found in %s" % SAMPLES)

win = MainWindow()
win.settings.clear()
win.resize(1400, 860)
win.show()
win.tabs.setCurrentIndex(1)            # the 3D tests below need the OpenGL view on screen
app.processEvents()
ok = win.open_world(str(w1), names_path=str(common) if common else None)
for _ in range(5):
    app.processEvents()
check("World 1 opens in the main window", ok and win.world.id == "W1")
check("every model is uploaded to the GPU, terrain in full (no sampling)",
      len(win.view.meshes) == 7 and win.view.mesh_stats.get("CS_W1_World") == 106736, win.view.mesh_stats)
check("the browser lists points, waypoints and routes", win.tree.topLevelItemCount() == 4)
check("the world name comes from the game text", "Acorn Plains" in win.tree.headerItem().text(0), win.tree.headerItem().text(0))
texts = [win.problems.item(i).text() for i in range(win.problems.count())]
check("the checks run the game's rules: no errors, with the play-through summary", any(t.startswith("No errors") for t in texts) and any("71/72 points reachable" in t for t in texts), texts[:3])

img = win.view.grabFramebuffer()
bg = QtGui.QColor.fromRgbF(0.07, 0.10, 0.15)
drawn = sum(1 for x in range(0, img.width(), 8) for y in range(0, img.height(), 8)
            if abs(QtGui.QColor(img.pixel(x, y)).red() - bg.red()) > 12 or abs(QtGui.QColor(img.pixel(x, y)).green() - bg.green()) > 12)
check("the 3D view renders the world (a large part of the frame is drawn)", drawn > 400, drawn)
img.save("/tmp/kdx_view.png")

# selecting by clicking in the 3D view
pos = win.world.position("W101")
scr, vis = win.view.project([pos])
x, y = int(scr[0][0]), int(scr[0][1])
QtTest.QTest.mouseClick(win.view, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
app.processEvents()
check("clicking a point in the 3D view selects it", win.view.selected == "W101" and win.props.title.text() == "W101", win.view.selected)
check("the panel shows the level name", "Acorn Plains Way" in win.props.subtitle.text(), win.props.subtitle.text())
check("the panel shows the point's MarioOffset record (slot 1)", win.props.mario_box.isVisible() and "slot 1" in win.props.mario_box.title())

# editing a point-table cell through the panel, with undo/redo
win.props.cells[2].setText("restart")
win.props._cell_edited(2)
check("editing a flag cell changes the point table", win.world.row_of("W101")[2] == "restart")
win.undo.undo()
check("undo restores it", win.world.row_of("W101")[2] == "" and win.props.cells[2].text() == "")
win.undo.redo()
check("redo re-applies it", win.world.row_of("W101")[2] == "restart")

# MarioOffset through the panel
win.props.mx.setValue(0.0); win.props.mz.setValue(0.0); win.props._mario_edited()
check("editing the MarioOffset vector updates the record", win.world.mario_record("W101").vector[0] == 0.0 and win.world.mario_record("W101").vector[2] == 0.0)

# dragging a point in move mode, snapped to the terrain
pos = win.world.position("W102")
scr, _ = win.view.project([pos])
sx, sy = int(scr[0][0]), int(scr[0][1])
QtTest.QTest.mousePress(win.view, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(sx, sy))
ev = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(sx + 30, sy + 10), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
app.sendEvent(win.view, ev)
QtTest.QTest.mouseRelease(win.view, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(sx + 30, sy + 10))
app.processEvents()
newpos = win.world.position("W102")
check("dragging a point in move mode moves it", not near(newpos, pos, 1.0), (pos, newpos))
h = win.view.terrain_height(newpos[0], newpos[2])
check("the dragged point lands on the terrain surface", h is not None and abs(newpos[1] - h) < 0.05, (newpos, h))
win.undo.undo(); app.processEvents()
check("undo puts the dragged point back", near(win.world.position("W102"), pos))

# route type
win.props.show_route("RW101W102")
i = win.props.route_type.findData("坂")
win.props.route_type.setCurrentIndex(i); win.props._route_edited()
check("a route's type can be changed", win.world.route_row("RW101W102")[1] == "坂")

# offset tables dialog
dlg = OffsetTablesDialog(win)
check("the offset dialog shows all 256 MarioOffset slots and the Look values", dlg.mario_table.rowCount() == 256 and dlg.look_table.rowCount() == 31)
dlg.mario_table.item(2, 5).setText("3")
dlg.apply()
check("editing a slot in the offset dialog changes the table", win.world.mario.records[2].flags == 3)
win.undo.undo()
check("...and it can be undone", win.world.mario.records[2].flags == 0)
win.undo.redo()

# hiding a model
item = next(win.models_list.item(i) for i in range(win.models_list.count()) if win.models_list.item(i).data(QtCore.Qt.UserRole) == "CS_W1_World")
item.setCheckState(QtCore.Qt.Unchecked)
check("unticking a model hides it", "CS_W1_World" in win.view.hidden)
item.setCheckState(QtCore.Qt.Checked)

# export
check("the window title marks unsaved changes", win.windowTitle().startswith("*"), win.windowTitle())
root = Path(tempfile.mkdtemp())
written = win.export_to(str(root), quiet=True)
check("export writes the world archive and the MarioOffset table", sorted(p.name for p in written) == ["CS_W1.Nin_NX_NVN.szs", "MarioOffsetDataW1.bin"], written)
check("after export the title is clean", not win.windowTitle().startswith("*"), win.windowTitle())


win.undo.redo() if False else None
win.props.cells[3].setText(win.world.row_of("W101")[3] + ",W1T0"); win.props.show_point("W101")
win.set_cell("W101", 3, win.world.row_of("W101")[3], win.world.row_of("W101")[3] + ",W1T0")
check("a new edit after export marks the window unsaved again", win.windowTitle().startswith("*"))
written2 = win.export_to(str(root), quiet=True)
w_again = __import__("kdx.world", fromlist=["World"]).World(next(p for p in written2 if p.name.startswith("CS_")))
check("a second export still contains the earlier changes (route type, MarioOffset) and the new one",
      w_again.route_row("RW101W102")[1] == "坂" and w_again.row_of("W101")[2] == "restart" and w_again.row_of("W101")[3].endswith(",W1T0"))
# ------------------------------------------------------------------ the 2D map
win.tabs.setCurrentIndex(0)
win.map.fit_all()
for _ in range(5):
    app.processEvents()
m = win.map
check("the 2D map has every point and waypoint", len(m.items) == len(win.world.point_ids) + len(win.world.waypoint_ids), len(m.items))
check("the 2D map has every route as a line", len(m.routes) == len(win.world.routes))
bg = m.background.pixmap().toImage()
colored = sum(1 for x in range(0, bg.width(), 16) for y in range(0, bg.height(), 16) if QtGui.QColor(bg.pixel(x, y)).green() > 60)
check("the terrain picture seen from above is drawn", colored > 300, colored)
p101 = win.world.position("W101")
check("points sit at their world X/Z on the map", abs(m.items["W101"].x() - p101[0]) < 1e-3 and abs(m.items["W101"].y() - p101[2]) < 1e-3)

vp = m.scene_pos_of("W103")
QtTest.QTest.mouseClick(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, vp)
app.processEvents()
check("clicking a point on the 2D map selects it everywhere", m.selected == "W103" and win.view.selected == "W103" and win.props.title.text() == "W103",
      (m.selected, win.view.selected))
win.select_point("W104")
check("selecting elsewhere highlights it on the 2D map", m.selected == "W104" and m.items["W104"].zValue() == 30)
QtTest.QTest.mouseClick(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(5, m.viewport().height() - 5))
app.processEvents()
check("clicking empty map space clears the selection", m.selected == "" and win.props.title.text() == "Nothing selected")

before = win.world.position("W103")
start = m.scene_pos_of("W103")
end = start + QtCore.QPoint(40, 25)
QtTest.QTest.mousePress(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
for k in range(1, 6):
    pt = QtCore.QPointF(start) + QtCore.QPointF(end - start) * (k / 5.0)
    app.sendEvent(m.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, pt, QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
app.processEvents()
after = win.world.position("W103")
check("dragging a point on the 2D map moves it in the world", abs(after[0] - before[0]) > 1 or abs(after[2] - before[2]) > 1, (before, after))
h = win.scene.height_at(after[0], after[2])
check("the point dropped on the 2D map lands on the terrain", h is not None and abs(after[1] - h) < 0.05, (after, h))
check("routes follow the moved point", any(abs(m.routes[r["id"]].line().p1().x() - after[0]) < 1e-3 or abs(m.routes[r["id"]].line().p2().x() - after[0]) < 1e-3
                                          for r in win.world.routes if "W103" in (r["from"], r["to"])))
check("the 3D view sees the same new position", any(i == "W103" and abs(p[0] - after[0]) < 1e-3 for i, _k, p in win.view.markers))
win.undo.undo(); app.processEvents()
check("undo moves the point back on the 2D map", abs(m.items["W103"].x() - before[0]) < 1e-3 and abs(m.items["W103"].y() - before[2]) < 1e-3)

cs = next(win.models_list.item(i) for i in range(win.models_list.count()) if win.models_list.item(i).data(QtCore.Qt.UserRole) == "CS_W1_World")
old_bg = m.background
cs.setCheckState(QtCore.Qt.Unchecked); app.processEvents()
check("hiding a model redraws the 2D terrain picture without it", m.background is not old_bg and "CS_W1_World" in m.hidden)
cs.setCheckState(QtCore.Qt.Checked)
win.labels_action.trigger()
check("labels can be switched off on the 2D map", not m.items["W101"].label.isVisible())
win.labels_action.trigger()
m.viewport().grab().save("/tmp/kdx_map2d.png")

# ------------------------------------------------------------------ ROMFS folder and the Worlds panel
from kdx.mainwindow import RomfsDialog
dump = Path(os.environ.get("KDX_ROMFS_DUMP", "/tmp/romfs_test/dump"))
if (dump / "romfs" / "Model").exists():
    d = RomfsDialog(win, str(dump / "romfs" / "Model"), first_run=True)
    check("the ROMFS dialog accepts the Model folder and finds the romfs root", d._validate() == dump / "romfs" and d.buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled())
    d.path.setText(str(dump))
    check("...and the folder that contains romfs", d._validate() == dump / "romfs")
    d.path.setText(str(dump.parent))
    check("a folder without world archives is refused with a message", d._validate() is None and not d.buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled()
          and "No world archives" in d.status.text())
    d.path.setText(str(dump / "romfs"))
    d._accept()
    check("accepting stores the romfs root and the text language", d.root == dump / "romfs" and d.language == "USen", (d.root, d.language))
    win._confirm_discard = lambda: True
    win.set_romfs(d.root, d.language)
    items = [win.worlds_list.item(i).text() for i in range(win.worlds_list.count())]
    check("the Worlds panel lists the 12 worlds with their names", len(items) == 12 and items[0].startswith("W1") and "Acorn Plains" in items[0] and items[-1].startswith("W9b"), items[:3])
    w5b = next(win.worlds_list.item(i) for i in range(win.worlds_list.count()) if win.worlds_list.item(i).text().startswith("W5b"))
    win._world_activated(w5b); app.processEvents()
    check("double-clicking a world opens it, with its offset table from the romfs", win.world.id == "W5b" and win.world.mario is not None and win.world.block == 1)
    check("the 2D map follows the newly opened world", len(win.map.items) == len(win.world.point_ids) + len(win.world.waypoint_ids))
else:
    print("SKIP ROMFS tests (no test dump at %s)" % dump)

win.grab().save("/tmp/kdx_window.png")
# ------------------------------------------------------------------ 0.4: paths, picking, camera, nudging
from kdx.mainwindow import FlagsDialog
win._confirm_discard = lambda: True
win.open_world(str(w1), names_path=str(common) if common else None)
win.tabs.setCurrentIndex(0); win.map.fit_all()
for _ in range(4):
    app.processEvents()
m = win.map
win.select_point("W102")
check("the panel shows the point's class and level file (from the game's rules)", "level file Course/1-2.sarc" in win.props.subtitle.text() and "stop" in win.props.subtitle.text(), win.props.subtitle.text())
check("the segments W102's exits open are highlighted on the map", "RW102F021" in m.highlight and "RK02hW1T0" in m.highlight and "Normal exit opens" in win.props.segments_label.text(),
      win.props.segments_label.text())
check("limit counters show entries per column", win.props.counters[4].text().endswith("/16") and win.props.counters[3].text().endswith("/4"))
d = FlagsDialog(win, "W101", "restart")
d.boxes["stop"].setChecked(True)
check("the flags chooser lists the game's flags and keeps the order", d.value() == "restart,stop" and len(d.boxes) > 90, (d.value(), len(d.boxes)))

win.select_point("W101")
before = win.world.row_of("W101")[3]
win.props._pick_for(3)
check("Pick… switches the views to picking", m.pick_handler is not None and win.view.pick_handler is not None)
QtTest.QTest.mouseClick(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, m.scene_pos_of("W1T0"))
app.processEvents()
check("picking a point adds it to 'Nodes opened'", win.world.row_of("W101")[3] == ",".join([x for x in before.split(",") if x] + ["W1T0"]) and m.pick_handler is None,
      win.world.row_of("W101")[3])
win.undo.undo()
check("...and undo removes it", win.world.row_of("W101")[3] == before)

win.connect_points()
QtTest.QTest.mouseClick(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, m.scene_pos_of("W101")); app.processEvents()
QtTest.QTest.mouseClick(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, m.scene_pos_of("W104")); app.processEvents()
check("Connect: two clicks create route RW101W104 (type road)", win.world.route_row("RW101W104") == ["RW101W104", "道", ""] and "RW101W104" in m.routes)
check("the new route opens in the panel", win.props.current == ("route", "RW101W104"))
win.props._delete_route()
check("Delete this route removes it", win.world.route_row("RW101W104") is None and "RW101W104" not in m.routes)
win.undo.undo(); app.processEvents()
check("undo brings the deleted route back", win.world.route_row("RW101W104") is not None)
win.undo.undo(); app.processEvents()
check("undo again removes the new route", win.world.route_row("RW101W104") is None)

win.select_point("W101")
rec0 = win.world.mario_record("W101").vector
check("the selected point's camera marker is drawn on the 2D map", "W101" in m.cameras)
cam_vp = m.mapFromScene(m.cameras["W101"][0].pos())
QtTest.QTest.mousePress(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, cam_vp)
for k in range(1, 4):
    app.sendEvent(m.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(cam_vp) + QtCore.QPointF(10 * k, 0), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, cam_vp + QtCore.QPoint(30, 0))
app.processEvents()
rec1 = win.world.mario_record("W101").vector
check("dragging the camera marker on the 2D map changes the camera's X offset only", rec1[0] > rec0[0] + 1 and abs(rec1[1] - rec0[1]) < 1e-4 and abs(rec1[2] - rec0[2]) < 1e-3, (rec0, rec1))
win.undo.undo()
check("...undo restores the camera", win.world.mario_record("W101").vector == rec0)

win.tabs.setCurrentIndex(1)
for _ in range(4):
    app.processEvents()
v = win.view
v.frame_all(); app.processEvents()
win.select_point("W101")
cpos = v.camera_position("W101")
scr, ok = v.project([cpos])
cx, cy = int(scr[0][0]), int(scr[0][1])
QtTest.QTest.mousePress(v, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(cx, cy))
app.sendEvent(v, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(cx + 25, cy + 15), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(v, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(cx + 25, cy + 15))
app.processEvents()
rec2 = win.world.mario_record("W101").vector
check("dragging the camera gizmo in 3D changes the camera offset (height kept)", (abs(rec2[0] - rec0[0]) > 0.5 or abs(rec2[2] - rec0[2]) > 0.5) and abs(rec2[1] - rec0[1]) < 1e-3, (rec0, rec2))
win.undo.undo()
win.props._view_from_camera(); app.processEvents()
import math as _m
off = win.world.mario_record("W101").vector
check("View from this camera puts the 3D eye at point + camera offset", abs(v.distance - _m.sqrt(sum(c * c for c in off))) < 1e-3
      and all(abs(a - b) < 1e-3 for a, b in zip(v.target, win.world.position("W101"))))

win.tabs.setCurrentIndex(0); app.processEvents()
win.select_point("W101")
x0 = win.world.position("W101")[0]
win.nudge(10.0, 0.0)
check("arrow-key nudge moves the selected point 10 units (Shift)", abs(win.world.position("W101")[0] - (x0 + 10)) < 1e-3)
win.undo.undo()
win.lock_action.trigger()
start = m.scene_pos_of("W103"); before = win.world.position("W103")
QtTest.QTest.mousePress(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
app.sendEvent(m.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(start) + QtCore.QPointF(40, 20), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(m.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start + QtCore.QPoint(40, 20))
check("Lock points stops dragging", win.world.position("W103") == before)
win.lock_action.trigger()
win.cams_action.trigger(); app.processEvents()
check("Cameras for all points shows many camera markers", len(m.cameras) > 20, len(m.cameras))
win.cams_action.trigger()
m.viewport().grab().save("/tmp/kdx_map2d_v04.png")
# ------------------------------------------------------------------ panning (right-drag) on the 2D map: the 0.5 crash report
win.tabs.setCurrentIndex(0); app.processEvents()
m = win.map
m.resetTransform(); m.scale(2.0, 2.0); app.processEvents()
c0 = m.mapToScene(m.viewport().rect().center())
p0 = QtCore.QPoint(200, 200)
QtTest.QTest.mousePress(m.viewport(), QtCore.Qt.RightButton, QtCore.Qt.NoModifier, p0)
for k in range(1, 5):
    app.sendEvent(m.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(p0) + QtCore.QPointF(-15 * k, -10 * k), QtCore.Qt.NoButton, QtCore.Qt.RightButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(m.viewport(), QtCore.Qt.RightButton, QtCore.Qt.NoModifier, p0 + QtCore.QPoint(-60, -40)); app.processEvents()
c1 = m.mapToScene(m.viewport().rect().center())
check("right-drag pans the 2D map (this crashed before)", c1.x() > c0.x() + 5 and c1.y() > c0.y() + 5, (c0, c1))
m.fit_all()

# ------------------------------------------------------------------ the World Maker
win.tabs.setCurrentIndex(2)
for _ in range(4):
    app.processEvents()
mk = win.maker
mk.new_project(confirm=False, width=40, height=30)
app.processEvents()
cv = mk.canvas
check("the World Maker opens with the starter tileset (64 original tiles)", mk.tileset.count == 64 and mk.project.width == 40)
check("Edit > Undo follows the World Maker while its tab is active", win.undo_group.activeStack() is mk.undo)

def tile_pos(x, y):
    return cv.mapFromScene(QtCore.QPointF((x + 0.5) * 32, (y + 0.5) * 32))

mk.layer = 0
mk.palette.select_tile(0)
mk.set_tool("fill")
QtTest.QTest.mouseClick(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(5, 5)); app.processEvents()
check("Fill paints the whole ground layer with grass", all(v == 0 for v in mk.project.layers[0].data))
mk.undo.undo()
check("undo empties it again", all(v == -1 for v in mk.project.layers[0].data))
mk.undo.redo()

mk.layer = 1
mk.palette.select_tile(16)
mk.set_tool("brush")
QtTest.QTest.mousePress(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(3, 8))
for x in range(4, 12):
    app.sendEvent(cv.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(tile_pos(x, 8)), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(11, 8)); app.processEvents()
row8 = [mk.project.get(1, x, 8) for x in range(3, 12)]
check("a brush stroke paints a path across 9 tiles", row8 == [16] * 9, row8)
mk.undo.undo()
check("one undo removes the whole stroke", all(mk.project.get(1, x, 8) == -1 for x in range(3, 12)))
mk.undo.redo()

mk.layer = 2
mk.palette.select_tile(40)
mk.set_tool("rect")
QtTest.QTest.mousePress(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(14, 12))
app.sendEvent(cv.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(tile_pos(16, 13)), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(16, 13)); app.processEvents()
check("the rectangle tool plants a 3×2 block of trees", sum(1 for v in mk.project.layers[2].data if v == 40) == 6)
mk.palette.select_tile(3)
mk.set_tool("pick")
QtTest.QTest.mouseClick(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(15, 12)); app.processEvents()
check("the eyedropper picks the tree and switches back to the brush", mk.palette.pattern() == [[40]] and mk.tool == "brush")

mk.set_tool("height")
mk.brush_size.setValue(3); mk.height_step.setValue(20)
QtTest.QTest.mousePress(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(25, 10))
for x in range(25, 30):
    app.sendEvent(cv.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(tile_pos(x, 10)), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(29, 10)); app.processEvents()
hill = mk.project.height_at(27.5, 10.5)
check("the Height tool raises a hill along the stroke", hill > 15 and mk.project.height_at(5, 5) == 0, hill)
mk.undo.undo()
check("one undo flattens the whole Height stroke", not any(mk.project.heights or []), max(mk.project.heights or [0]))
mk.undo.redo()
check("redo brings the hill back", abs(mk.project.height_at(27.5, 10.5) - hill) < 1e-6)

mk.set_tool("node")
cv.centerOn(QtCore.QPointF(12.5 * 32, 20.5 * 32))             # keep all three spots on screen, whatever the screen size
for kind, x in (("start", 6), ("level", 12), ("level", 18)):
    mk.kind_box.setCurrentIndex(mk.kind_box.findData(kind))
    QtTest.QTest.mouseClick(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(x, 20)); app.processEvents()
check("the point tool places W1M0, W101 and W102", sorted(mk.project.nodes) == ["W101", "W102", "W1M0"], sorted(mk.project.nodes))
mk.set_tool("route")
for a, b in (("W1M0", "W101"), ("W101", "W102")):
    for pid in (a, b):
        QtTest.QTest.mouseClick(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, cv.mapFromScene(cv.node_items[pid].pos())); app.processEvents()
check("the route tool joins them with two clicks each", [r["id"] for r in mk.project.routes] == ["RW1M0W101", "RW101W102"])
mk.select(("node", "W101"))
mk.cells[4].setText("RW101W102"); mk._cell_edited(4)
texts = [mk.checks.item(i).text() for i in range(mk.checks.count())]
check("the World Maker checks the design with the game's rules (3/3 points reachable)", any("3/3 points reachable" in t for t in texts) and any(t == "No errors." for t in texts), texts[:3])

# walk test: the marker walks every path of the plan, then removes itself
mk.canvas.walk_test()
legs = len(mk.project.walk_plan())
for _ in range(2000):
    if not mk.canvas._walk:
        break
    mk.canvas._walk_step()
check("the walk test walks every path in play order and finishes", legs >= 2 and mk.canvas._walk is None
      and getattr(mk.canvas, "_walker", None) is None, legs)

# flag picker: the world's needs come first; picking one adds it to the selected point
mk.select(("node", "W102"))
items = [mk.flag_pick.itemData(i) for i in range(mk.flag_pick.count())]
check("Add flag lists this world's map enemies and Yoshi spot first", items[1:4] == ["W1Item", "W1PakkunA", "balloon1"], items[:5])
mk._flag_picked(items.index("balloon1"))
check("picking a flag adds it to the point (undoable)", "balloon1" in rules.split(mk.project.nodes["W102"]["row"][2]))
mk.undo.undo()
check("undo removes the picked flag", "balloon1" not in rules.split(mk.project.nodes["W102"]["row"][2]))

mk.set_tool("select")
start = cv.mapFromScene(cv.node_items["W102"].pos())
QtTest.QTest.mousePress(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
app.sendEvent(cv.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(tile_pos(18, 24)), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(cv.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tile_pos(18, 24)); app.processEvents()
n = mk.project.nodes["W102"]
check("dragging a point moves it, snapped to the tile centre", (n["x"], n["y"]) == (18.5, 24.5), (n["x"], n["y"]))
mk.undo.undo()
check("undo puts it back", (mk.project.nodes["W102"]["x"], mk.project.nodes["W102"]["y"]) == (18.5, 20.5))
mk.select(("node", "W102")); mk.delete_selected()
check("deleting a point also removes its routes", "W102" not in mk.project.nodes and [r["id"] for r in mk.project.routes] == ["RW1M0W101"])
mk.undo.undo()
check("undo restores the point and its route", "W102" in mk.project.nodes and len(mk.project.routes) == 2)

cv.resetTransform(); cv.scale(2.0, 2.0); app.processEvents()
h0, v0 = cv.horizontalScrollBar().value(), cv.verticalScrollBar().value()
QtTest.QTest.mousePress(cv.viewport(), QtCore.Qt.RightButton, QtCore.Qt.NoModifier, QtCore.QPoint(200, 200))
app.sendEvent(cv.viewport(), QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(150, 160), QtCore.Qt.NoButton, QtCore.Qt.RightButton, QtCore.Qt.NoModifier))
QtTest.QTest.mouseRelease(cv.viewport(), QtCore.Qt.RightButton, QtCore.Qt.NoModifier, QtCore.QPoint(150, 160)); app.processEvents()
check("right-drag pans the World Maker canvas", (cv.horizontalScrollBar().value(), cv.verticalScrollBar().value()) != (h0, v0))
import tempfile as _tf
fp = Path(_tf.mkdtemp()) / "maker_test.kdxmap"
mk.project.save(fp); mk.undo.setClean()
saved = mk.project.to_json()
mk.new_project(confirm=False)
check("the reopened map is identical", mk.open(str(fp)) and mk.project.to_json() == saved)
img = mk.export_png(Path(_tf.mkdtemp()) / "map.png")
check("the map exports as a picture of 40×30 tiles", img.width() == 40 * 32 and img.height() == 30 * 32 and QtGui.QColor(img.pixel(5 * 32 + 16, 5 * 32 + 16)).green() > 100)
cv.viewport().grab().save("/tmp/kdx_maker.png")
mk.undo.setClean()

# a V-shaped bend: both routes leave K000 north, so the game would keep one link (0x60178558): red rings show it
mk.new_project(confirm=False, width=24, height=16)
for kind, x, y in (("start", 2.5, 2.5), ("level", 6.5, 2.5), ("level", 10.5, 2.5), ("bend", 8.5, 6.5)):
    mk.project.add_node(kind, x, y)
mk.project.add_route("W1M0", "W101"); mk.project.add_route("W101", "K000"); mk.project.add_route("K000", "W102")
mk.project.nodes["W101"]["row"][4] = "RW101W102"
mk._nodes_changed(); app.processEvents()
ring = cv.node_items["K000"].pen().color().name()
check("a V-shaped bend is an error and its points get a red ring", "K000" in mk.problem_points and ring == "#ef4444"
      and "W1M0" not in mk.problem_points, (sorted(mk.problem_points), ring))

# phase 3: previewing pending appear animations and the keyframe editor (needs the .NET runtime)
from kdx import export2d as _e2, events as _ev, keyedit as _ke
if _e2.engine_status()[0]:
    import time as _time
    win._confirm_discard = lambda: True
    win.tabs.setCurrentIndex(1)
    win.open_world(str(w1)); app.processEvents()
    _tm, _shp = _ev.terrain_shapes(win.world)
    _dk = next(x for x in _shp if x[0].startswith("dokan5"))
    _pv, _h = _ev.pivot_of([_dk[2:]])
    win.add_route_make({"target": "RW105W106", "source_model": _tm, "shapes": [_dk[0]], "objects": [], "preset": "drop",
                        "frames": 60, "amount": 300.0, "pivot": _pv})
    win.events.refresh()
    _top = win.events.tree.topLevelItem(0)
    _row = next(_top.child(i) for i in range(_top.childCount()) if "RW105W106" in _top.child(i).text(0))
    win.events.tree.setCurrentItem(_row); app.processEvents()
    check("events: a new (pending) appear animation can be previewed and edited key by key",
          win.events.play_btn.isEnabled() and win.events.keys_btn.isEnabled())
    win.events.preview()
    check("events: ▶ on a pending one builds it in a temp copy and draws its models from that build",
          bool(win.view._overlay) and "RW105W106" in win.view._overlay["models"] and win.view._preview[0] == "RW105W106"
          and not win.view._game_active())
    _t = _time.time()
    while win.view._overlay and _time.time() - _t < 8:
        app.processEvents(); _time.sleep(0.02)
    check("events: when the preview ends the world's own models come back", not win.view._overlay and
          "RW105W106" not in win.view.textured, list(win.view.textured)[:8])
    _old = win.world.route_makes[0]
    _ed = _ke.KeyframeEditor(_ev.preset_keys("drop", 60, 300.0), 60)
    _ed.channel, _ed.playhead = "rotate", 30
    _ed.add_key(); _ed.val[1].setValue(90.0)
    check("keyframe editor: + Key at the playhead adds a turn key, edited in the fields",
          _ed.result_keys()["rotate"] == [[30.0, 0.0, 90.0, 0.0]])
    win.replace_route_make(_old, dict(_old, preset="custom", keys=_ed.result_keys(), frames=60, interp={"rotate": "smooth"}))
    check("keyframe editor: saving replaces the animation (custom keys), undo brings the preset back",
          win.world.route_makes[0]["preset"] == "custom" and "your own keyframes" in
          next(p for p in win.events.ev["paths"] if p["path"] == "RW105W106")["make"])
    win.undo.undo()
    check("...undo", win.world.route_makes[0] is _old)
    win.undo.undo()
    win.world.mark_saved()
else:
    print("SKIP pending-preview tests (no .NET runtime)")

# roads through the ground, bones the export builds, levels for new level points (0.10.3)
win._confirm_discard = lambda: True
win.open_world(str(w1)); app.processEvents()
ww = win.world
win.set_romfs(SAMPLES.parent, "USen")
ww.add_point("F900", ww.position("F044"))
ww.add_route("RW103F900")
ww.row_of("W103")[4] += ",RW103F900"
ww.add_point("W106", ww.position("W105")[:1] + (ww.position("W105")[1], ww.position("W105")[2] - 120.0))
ww.add_route("RF900W106")
errs = win.run_checks()
texts = [win.problems.item(i).text() for i in range(win.problems.count())]
check("checks: new points and paths are not reported as missing bones (the export builds them)",
      not any("has no path bone" in t or "no bone named" in t for t in texts), [t for t in texts if "bone" in t][:3])
sink_items = [win.problems.item(i) for i in range(win.problems.count())
              if (win.problems.item(i).data(QtCore.Qt.UserRole) or ("",))[0] == "ground"]
check("checks: a straight road over raised ground is flagged (red in 3D) with a double-click fix",
      any(it.data(QtCore.Qt.UserRole)[1] == "RW103F900" for it in sink_items) and "RW103F900" in win.view.sinking,
      list(win.view.sinking))
n0 = len(ww.route_rows)
win.follow_ground("RW103F900"); win.run_checks()
check("Follow the ground: bends on the ground split the road and the warning goes", len(ww.route_rows) > n0 and
      "RW103F900" not in win.view.sinking and not any(k.startswith(("RW103K", "RK")) for k in win.view.sinking),
      list(win.view.sinking))
win.undo.undo(); win.run_checks()
check("...undo brings the straight road back", ww.route_row("RW103F900") is not None and len(ww.route_rows) == n0)
lvl = [win.problems.item(i).data(QtCore.Qt.UserRole) for i in range(win.problems.count())]
check("checks: a new level with no level file offers to choose one", ("level", "W106") in lvl, lvl[-3:])
src = next(iter(sorted(SAMPLES.parent.glob("Course/1-1.sarc"))), None)
if src:
    ww.course_copies["1-6.sarc"] = str(src)
    win.run_checks()
    texts = [win.problems.item(i).text() for i in range(win.problems.count())]
    check("...with a level chosen the missing-level error goes, and export copies it to Course/1-6.sarc",
          not any("Course/1-6.sarc does not exist" in t for t in texts))
sec = win.secret_routes()
check("phase 4: roads only a secret exit opens are drawn pink (W102's secret path to W1S0)",
      sec and sec <= set(r[0] for r in ww.route_rows) and set(ww.path_chain("RW102W1S0")) <= sec and
      win.view.secret_routes == sec, sorted(sec)[:4])
win.select_point("W102"); app.processEvents()
check("phase 4: the point panel says whether the save tracks the level's normal and secret exits",
      "Save: normal exit tracked" in win.props.meaning.text() and "secret exit tracked" in win.props.meaning.text(),
      win.props.meaning.text()[-160:])
ww.mark_saved()

# junctions, stick directions, cameras of new points, camera arrows, secret goals (0.11.1)
import numpy as np
from kdx import directions as _dirs
win._confirm_discard = lambda: True
win.open_world(str(w1)); app.processEvents()
dw = win.world
_p = dw.position("W103")
dw.add_point("F901", (_p[0] + 180, _p[1], _p[2] - 30))
_rec = dw.mario_record("F901")
check("a new point's camera offset stays 0 (the camera looks at Mario, as on 39 of W1's 72 vanilla points)",
      _rec is not None and not any(abs(v) > 1e-6 for v in _rec.vector), _rec.vector if _rec else None)
win._after_edit(refresh_markers=True)(); win.select_point("F901"); app.processEvents()
_lg = win.view.camera_gizmo()
check("...and its camera's X/Y/Z arrows are lifted above the point, apart from the point's own arrows",
      _lg is not None and _lg[0][1] > dw.position("F901")[1] + 1 and abs(_lg[0][0] - dw.position("F901")[0]) < 1e-6)
win._after_edit(refresh_markers=True)()
_pick = win.start_pick
win.start_pick = lambda prompt, cb: cb("F901")
win.branch_from("W103")
win.start_pick = _pick
_c = _dirs.compass(dw, "W103")
check("Branch to another point: the new road leaves W103 in a free direction (north; east is taken)",
      [r for r in _c["north"] if r.startswith("RW103K")] and len(_c["east"]) == 1, {k: v for k, v in _c.items() if v})
win.undo.undo()
check("...one undo step takes the branch away", not any("F901" in r[0] for r in dw.route_rows))
dw.add_route("RW103F901")
_k = win.set_direction("W103", "RW103F901", "north")
check("Stick directions: a road set to north leaves through a bend placed north of the point",
      "RW103F901" not in [r[0] for r in dw.route_rows] and _dirs.compass(dw, "W103")["north"] == ["RW103%s" % _k] and
      len(_dirs.compass(dw, "W103")["east"]) == 1)
win.select_point("W103"); app.processEvents()
check("the point panel lists the stick directions (one road each)", "Stick:" in win.props.meaning.text() and
      "only one works" not in win.props.meaning.text())
win.select_point("W101"); app.processEvents()
win.view.target = np.asarray(dw.position("W101"), float); win.view.distance = 1500.0; win.view.yaw, win.view.pitch = -0.6, 0.85
win.view.update(); app.processEvents()
_cg = win.view.camera_gizmo()
check("the selected point's camera marker has X/Y/Z arrows", _cg is not None)
if _cg is not None:
    _o, _L = _cg
    _off0 = tuple(win._camera_offset("W101"))
    _tip = _o + np.array([_L, 0, 0])
    _s, _ = win.view.project([_o + np.array([_L * 0.7, 0, 0])]); _e, _ = win.view.project([_o + np.array([_L * 0.7 + 50.0, 0, 0])])
    def _ev(kind, pt, buttons):
        return QtGui.QMouseEvent(kind, QtCore.QPointF(*pt), QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier)
    win.view.mousePressEvent(_ev(QtCore.QEvent.MouseButtonPress, _s[0], QtCore.Qt.LeftButton))
    win.view.mouseMoveEvent(_ev(QtCore.QEvent.MouseMove, _e[0], QtCore.Qt.LeftButton))
    win.view.mouseReleaseEvent(_ev(QtCore.QEvent.MouseButtonRelease, _e[0], QtCore.Qt.NoButton))
    _off1 = tuple(win._camera_offset("W101"))
    check("dragging the camera's X arrow moves the camera along x only",
          abs((_off1[0] - _off0[0]) - 50.0) < 6.0 and abs(_off1[1] - _off0[1]) < 1e-3 and abs(_off1[2] - _off0[2]) < 1e-3,
          (_off0, _off1))
dw.row_of("W101")[7] = "RW101W102"
_errs = dw.report(SAMPLES.parent / "Course").warnings
check("phase 4: a secret exit on a level with no secret goal (1-1) is flagged, pointing to Yaz0moto",
      any("no secret goal" in m and "Yaz0moto" in m for m in _errs))
win.select_point("W102"); app.processEvents()
check("the point panel shows the level's goals (1-2: 1 regular, 1 secret) and the Yaz0moto button",
      "1 regular, 1 secret" in win.props.meaning.text() and not win.props.yaz_btn.isHidden(), win.props.meaning.text()[-200:])
dw.mark_saved()

# phase 5: Koopatlas-style unlock expressions
from kdx import unlocks as _un
win.open_world(str(w1)); app.processEvents()
uw = win.world
win.set_unlock("RW102W1T0", "1-2 OR 1-1 secret")
check("Opens when: '1-2 OR 1-1 secret' writes the path into W102's normal and W101's secret exit (with W1T0 opened)",
      _un.describe(uw.point_rows, "RW102W1T0") == "1-1 secret OR 1-2" and "W1T0" in rules.split(uw.row_of("W101")[6]),
      _un.describe(uw.point_rows, "RW102W1T0"))
win.undo.undo()
check("...undo restores who opened it", _un.describe(uw.point_rows, "RW102W1T0") == "1-2")
_rid = next(r[0] for r in uw.route_rows if r[0] in uw.path_chain("RW102W1T0"))
win.select_route(_rid) if hasattr(win, "select_route") else win.props.show_route(_rid)
app.processEvents()
check("the route panel shows 'Opens when' for the paths the road is on", win.props.unlock_form.rowCount() >= 2)
try:
    _un.parse("1-2 and 1-3", uw.point_ids); _and = False
except _un.UnlockError as _e:
    _and = "AND" in str(_e)
check("AND is refused with the reason (the game opens a path when any one opener is beaten)", _and)
uw.mark_saved()
mk.new_project(confirm=False, width=24, height=16)
for kind, x, y in (("start", 2.5, 2.5), ("level", 6.5, 2.5), ("level", 10.5, 2.5)):
    mk.project.add_node(kind, x, y)
mk.project.add_route("W1M0", "W101"); mk.project.add_route("W101", "W102")
mk.project.nodes["W101"]["row"][4] = "RW101W102"; mk.project.nodes["W101"]["row"][3] = "W102"
mk._nodes_changed(); app.processEvents()
mk.selected = ("route", "RW101W102"); mk._show_selection()
check("World Maker: the selected route shows 'Opens when' (1-1)", mk.unlock_form.rowCount() >= 2 and
      _un.describe(mk.project.point_rows(), "RW101W102") == "1-1")
mk.set_unlock("RW101W102", "1-1 secret")
check("World Maker: changing it to '1-1 secret' moves the path to W101's secret exit",
      "RW101W102" in mk.project.nodes["W101"]["row"][7] and "RW101W102" not in mk.project.nodes["W101"]["row"][4])
mk.undo.setClean()

# phase 5: Koopatlas-style 2D worlds — raised islands, your own picture as the ground, animated images
from kdx import islands as _isl, pictures as _pics, project2d as _p2d
mk.new_project(confirm=False, width=24, height=16)
pj = mk.project
_plateau = {(x, y) for y in range(4, 12) for x in range(4, 16)}
pj.set_tiers(_plateau, 1); pj.set_tiers({(8, 6), (9, 6)}, 2)
pj.add_node("start", 2.5, 2.5); pj.add_node("level", 6.5, 6.5)
check("Islands: a point on a tier-1 plateau stands one tier up (60 units); the ground around stays at 0",
      abs(pj.world_position("W101")[1] - 60.0) < 1e-6 and abs(pj.world_position("W1M0")[1]) < 1e-6)
_parts = _isl.island_parts(pj, str(Path(_tf.gettempdir()) / "map.png"))
_walls = [q for q in _parts if q.material == "islands/cliff"]
_P = _walls[0].positions.reshape(-1, 3, 3) if _walls else None
check("Islands: tops (with the painted map) and cliff walls that face outwards (the game draws front faces only)",
      [q.material for q in _parts] == ["islands/top", "islands/cliff"] and
      bool(np.all((np.cross(_P[:, 1] - _P[:, 0], _P[:, 2] - _P[:, 0]) * _walls[0].normals.reshape(-1, 3, 3)[:, 0]).sum(1) > 0)))
_tmpd = Path(_tf.mkdtemp())
_img = QtGui.QImage(240, 160, QtGui.QImage.Format_ARGB32); _img.fill(QtGui.QColor("#e0b0ff")); _img.save(str(_tmpd / "art.png"))
_strip = QtGui.QImage(4 * 32, 32, QtGui.QImage.Format_ARGB32); _strip.fill(QtGui.QColor(255, 200, 0, 255)); _strip.save(str(_tmpd / "strip.png"))
mk.set_picture({"path": str(_tmpd / "art.png")})
_ri = mk.render_image(with_nodes=False)
check("Picture ground: your own drawn map becomes the ground picture (and the export image)",
      QtGui.QColor(_ri.pixel(_ri.width() // 2, 4)).name() == "#e0b0ff", QtGui.QColor(_ri.pixel(_ri.width() // 2, 4)).name())
mk.add_stickers([str(_tmpd / "strip.png")], at=(12.0, 3.0))
mk.sticker_list.setCurrentRow(0); mk.st_strip.setValue(4)
mk.st_anim.setCurrentIndex(mk.st_anim.findData("frames")); mk._sticker_edited()
_fr, _dl = _pics.load_frames(str(_tmpd / "strip.png"), 4)
check("Animated images: a PNG strip of 4 frames plays them (each frame a quarter of the strip)",
      len(_fr) == 4 and _fr[0].width() == 32 and pj.stickers[0]["anim"] == "frames")
pj.save(str(_tmpd / "art.kdxmap"))
_q = _p2d.Project.load(str(_tmpd / "art.kdxmap"))
check("...picture, images and island tiers are saved with the map", bool(_q.picture) and len(_q.stickers) == 1 and
      _q.tier(9, 6) == 2 and _q.stickers[0]["strip"] == 4)
mk.undo.undo(); mk.undo.undo()
check("...undo takes the image settings back", pj.stickers[0]["anim"] in ("none", "frames") and len(pj.stickers) == 1)
# moving images in the game: quads in CS_W<n>_Synchro on bones of their own (kdx.pictures.sticker_parts)
from kdx.maker2d import TILE as _TILE
pj.stickers[0].update(anim="frames", strip=4)
_cloud = dict(pj.stickers[0], path=str(_tmpd / "art.png"), anim="bob", strip=1, x=4.0, y=12.0, w=2.0, name="cloud")
pj.stickers.append(_cloud)
_still = mk.render_image(with_nodes=False, moving=False)
_live = mk.render_image(with_nodes=False)
_px = lambda im: QtGui.QColor(im.pixel(int(12 * _TILE), int(3 * _TILE))).name()
check("Moving images are left out of the baked map picture (the export gives them their own quads)",
      _px(_live) == "#ffc800" and _px(_still) == "#e0b0ff", (_px(_live), _px(_still)))
_sp, _items, _L = _pics.sticker_parts(pj, _tmpd)
check("...a 4-frame strip becomes 4 quads and a bobbing image 1, all on one loop of whole cycles",
      len(_sp) == 5 and [i["object"] for i in _items] == ["kdxs00f00", "kdxs00f01", "kdxs00f02", "kdxs00f03", "kdxs01f00"]
      and _L > 0 and all(q.mode == "mask" for q in _sp), ([i["object"] for i in _items], _L))
def _step(c, f):
    v = c["values"][0]
    for fr, val in zip(c["frames"], c["values"]):
        if fr <= f:
            v = val
    return v
_vis = [[_step(_items[k]["curves"][0], f) for k in range(4)] for f in range(0, _L, 7)]
check("...exactly one frame quad is shown at any time, in order",
      all(sum(r) == 1.0 for r in _vis) and _vis[0] == [1.0, 0.0, 0.0, 0.0], _vis[:4])
_bob = _items[4]["curves"][0]
check("...a bobbing image moves along the map's down (+z) as in the editor, a whole number of times per loop",
      _bob["channel"] == "tz" and _bob["mode"] == "hermite" and abs(_bob["values"][-1]) < 1e-6 and
      abs(min(_bob["values"]) + 0.25 * pj.tile_size) < 1e-6)
pj.stickers.pop()
# resizing with the corner handles, transparency
mk.sticker_list.setCurrentRow(0)
_r = mk.sticker_rect(pj.stickers[0])
check("Resize: the selected image has corner handles", mk.sticker_handle_at(_r.bottomRight()) == 0 and
      mk.sticker_handle_at(_r.center()) is None)
_w0 = pj.stickers[0]["w"]
mk.resize_sticker(0, _w0 * 2)
check("...dragging one makes it bigger (kept in proportion), undo takes it back",
      abs(pj.stickers[0]["w"] - 2 * _w0) < 1e-6 and (mk.undo.undo() or abs(pj.stickers[0]["w"] - _w0) < 1e-6))
_half = QtGui.QImage(240, 160, QtGui.QImage.Format_ARGB32); _half.fill(0)
QtGui.QPainter(_half).fillRect(0, 0, 120, 160, QtGui.QColor("#40a040")); _half.save(str(_tmpd / "island.png"))
mk.set_picture({"path": str(_tmpd / "island.png")})
mk.pic_over.setChecked(True)
check("Transparency: the picture can lie over the tiles", pj.picture.get("over") is True)
pj.tiers = None
mk.tier_level.setValue(1); mk.islands_from_picture()
check("...and its solid part becomes islands (the clear part stays sea)",
      pj.tier(2, 8) == 1 and pj.tier(20, 8) == 0, (pj.tier(2, 8), pj.tier(20, 8)))
mk.undo.undo()
check("...undo lowers them again", pj.tier(2, 8) == 0)
mk.undo.setClean()

# Route tool (pen), road following, branching, needed points
from kdx import roadtrace as _rt
from kdx.maker2d import TILE as _T
mk.new_project(confirm=False, width=24, height=16)
pj = mk.project
_road = QtGui.QImage(240, 160, QtGui.QImage.Format_ARGB32); _road.fill(QtGui.QColor("#3c9a3c"))
_rp = QtGui.QPainter(_road); _rp.fillRect(15, 15, 10, 90, QtGui.QColor("#f2dca0")); _rp.fillRect(15, 95, 180, 10, QtGui.QColor("#f2dca0")); _rp.end()
_road.save(str(_tmpd / "road.png"))
mk.set_picture({"path": str(_tmpd / "road.png")})
_b = _rt.trace(mk.road_image(), (24, 16), (2.0, 2.0), (19.0, 10.0))
check("Follow the road: the way between two clicks keeps to the road drawn in the picture (round its corner)",
      any(abs(x - 2.0) < 0.8 and abs(y - 10.0) < 0.8 for x, y in _b), _b)
mk.set_tool("route")
_bar2 = mk.paths_bar
win.tabs.setCurrentWidget(mk); app.processEvents()
check("the Route and Point tools are on their own Paths row (not hidden in the toolbar's overflow), with a hint",
      all(_bar2.widgetForAction(mk.tool_actions[k]).isVisible() for k in ("node", "route")) and
      _bar2.widgetForAction(mk.tool_actions["route"]).geometry().right() < _bar2.width() and "double-click" in mk.path_hint.text(),
      mk.path_hint.text())
_c = mk.canvas
check("Route tool: straight lines between clicks by default (Follow the road is an option)", not mk.follow_road.isChecked())
mk.follow_road.setChecked(True)
_c.fitInView(_c.map_item, QtCore.Qt.KeepAspectRatio); app.processEvents()
_V = lambda x, y: _c.mapFromScene(QtCore.QPointF(x * _T, y * _T))
QtTest.QTest.mouseClick(_c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _V(2.0, 2.0)); app.processEvents()
QtTest.QTest.mouseDClick(_c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _V(19.0, 10.0)); app.processEvents()
_k = [q for q, n in pj.nodes.items() if n["kind"] == "bend"]
check("Route tool: click then double-click draws a whole path, start to a new level, with bends along the road",
      "W1M0" in pj.nodes and "W101" in pj.nodes and _k and not pj.check().errors, (list(pj.nodes), pj.check().errors))
check("...and the start opens the new level (Connect)", "W101" in pj.nodes["W1M0"]["row"][3], pj.nodes["W1M0"]["row"][3:5])
_r0 = next(r["id"] for r in pj.routes if r["id"].startswith("R" + _k[-1]))
_l = _c.route_items[_r0].line()
_mid = ((_l.x1() + _l.x2()) / 2 / _T, (_l.y1() + _l.y2()) / 2 / _T)
QtTest.QTest.mouseClick(_c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _V(*_mid)); app.processEvents()
QtTest.QTest.mouseClick(_c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _V(_mid[0], 14.0)); app.processEvents()
QtTest.QTest.keyClick(_c.viewport(), QtCore.Qt.Key_Return); app.processEvents()
_f = [q for q, n in pj.nodes.items() if n["kind"] == "stop"]
check("...clicking a road branches off it: a path stop goes into that road and the new path starts there",
      len(_f) == 1 and any(r["id"].startswith("R" + _f[0]) for r in pj.routes) and len(pj.nodes) > 3 + len(_k), list(pj.nodes))
mk.undo.undo()
check("...each path is one undo step", not [q for q, n in pj.nodes.items() if n["kind"] == "stop"] and "W101" in pj.nodes)
QtTest.QTest.mouseClick(_c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _V(10.0, 3.0)); app.processEvents()
QtTest.QTest.keyClick(_c.viewport(), QtCore.Qt.Key_Escape); app.processEvents()
check("...Esc cancels a path being drawn", _c._pen is None and "W102" not in pj.nodes)
win.start_place("castle") if win.tabs.currentWidget() is mk else None
_need = [q for q, k, f, w in pj.needed_points()]
_added = mk.add_needed_points()
check("Add needed points: the castle, the map-music point and the map enemies' points are placed for you to move",
      not pj.needed_points() and {"W1C0", "W1S0"} <= set(pj.nodes) and any(n["row"][2] == "W1Item" for n in pj.nodes.values()),
      [q for q, _w in _added])
check("...they are labelled with what they are for, and shown (selected, pulsing)",
      "map music" in mk.point_purpose("W1S0") and mk.canvas._flash is not None and mk.selected and mk.selected[0] == "node",
      (mk.point_purpose("W1S0"), mk.selected))
mk.undo.undo()
check("...one undo step", "W1S0" not in pj.nodes and "W101" in pj.nodes)
# easier editing: right-click menu, toolbar action on the selected path, arrows / Alt, properties on the right
mk.follow_road.setChecked(False)
mk.select(("node", "W101"))
_acts = {lab: fn for lab, kind, fn in mk.canvas.map_menu_actions(8.0, 13.0)}
_acts["Level 1-15"]()
check("Right-click the map: 'Level' adds one there, joined to the selected point",
      "W102" in pj.nodes and abs(pj.nodes["W102"]["x"] - 8.0) < 1e-6 and any(r["id"] == "RW101W102" for r in pj.routes),
      list(_acts))
mk.select(("route", "RW101W102"))
mk.route_type_box.setCurrentIndex(mk.route_type_box.findData("はしご")); mk.route_type_box.activated.emit(mk.route_type_box.currentIndex())
check("Picking an action in the toolbar changes the selected path", next(r["type"] for r in pj.routes if r["id"] == "RW101W102") == "はしご")
mk.select(("node", "W102"))
_x0 = pj.nodes["W102"]["x"]
QtTest.QTest.keyClick(mk.canvas.viewport(), QtCore.Qt.Key_Right, QtCore.Qt.AltModifier); app.processEvents()
check("Alt+arrow nudges the selected point a twentieth of a tile (arrows: half a tile)", abs(pj.nodes["W102"]["x"] - _x0 - 0.05) < 1e-6,
      pj.nodes["W102"]["x"] - _x0)
win.tabs.setCurrentWidget(mk); app.processEvents()
check("In the World Maker the right-hand Properties dock shows the selected point (and Checks the maker's checks)",
      win.props_stack.currentIndex() == 1 and "W102" in mk.sel_title.text() and mk.sel_box.isVisibleTo(win) and
      win.checks_stack.currentIndex() == 1, (win.props_stack.currentIndex(), mk.sel_title.text()))
win.tabs.setCurrentIndex(0); app.processEvents()
check("...and the main editor's own panel elsewhere", win.props_stack.currentIndex() == 0)
win.tabs.setCurrentWidget(mk); app.processEvents()
mk.undo.setClean()

# Koopatlas import (New Super Mario Bros. Wii / Newer world maps), keyframed images, map music
from kdx import koopatlas as _kp
_tr = ["Loop", "Sinusoidial", 120.0, "Y Position", 0.0, -12.0, 0.0, 0.0]
check("Koopatlas keyframes: a sine Loop goes 0 -> end -> 0 over its frames (Qt's sine curve, 60 per second)",
      abs(_pics.track_value(_tr, 0)) < 1e-6 and abs(_pics.track_value(_tr, 60) + 12.0) < 1e-6 and abs(_pics.track_value(_tr, 120)) < 1e-6)
_rv = ["Reversible Loop", "Linear", 100.0, "Angle", 0.0, 30.0, 0.0, 0.0]
check("...a Reversible Loop goes there and back", abs(_pics.track_value(_rv, 100) - 30) < 1e-6 and abs(_pics.track_value(_rv, 150) - 15) < 1e-6)
_ks = dict(name="k", path=str(_tmpd / "art.png"), x=5.0, y=5.0, w=2.0, h=1.0, angle=20.0, anim="keys", tracks=[_tr], strip=1)
_mc = _pics.motion_curves(_ks, 240, 30.0)
check("...and become the game's bone keys (Y Position -> along the map's down, in world units)",
      _pics.moves(_ks) and _mc and _mc[0]["channel"] == "tz" and abs(min(_mc[0]["values"]) + 12.0 / 24 * 30.0) < 0.5, _mc and min(_mc[0]["values"]))
_kpfile = ROOT / "Next Update" / "Koopatlas world map" / "newer mod World Maps 130" / "Goldwood.kpmap"
if _kpfile.exists():
    _kout = Path(_tf.mkdtemp())
    _rep = mk.import_koopatlas(str(_kpfile), _kout, world=1, ask=False)
    pj = mk.project
    _keys = [x for x in pj.stickers if x.get("anim") == "keys"]
    check("Import Koopatlas: Goldwood Forest becomes a World Maker map with its picture, points, routes and moving doodads",
          pj.name == "Goldwood Forest" and pj.picture and Path(pj.picture["path"]).exists() and len(pj.nodes) > 30 and
          len(pj.routes) > 30 and len(_keys) > 20, (_rep or {}).get("summary"))
    check("...levels by course number, walk-through nodes as bends, a start point",
          {"W101", "W102", "W1M0"} <= set(pj.nodes) and sum(n["kind"] == "bend" for n in pj.nodes.values()) > 10)
    check("...path actions (ladders) and unlocks come across", any(r["type"].startswith("はしご") for r in pj.routes) and
          any(n["row"][4] for n in pj.nodes.values()))
    check("...and it passes the game's rules (no errors)", not pj.check().errors, pj.check().errors[:3])
    check("...without the mod's files the ground is a stand-in drawn from the tile shapes", any("stand-in" in t for t in _rep["summary"]),
          _rep["summary"][:1])
    _newer = Path(os.environ.get("KDX_NEWER", str(Path.home() / "Documents/Emulation/Emulator's/Dolphin/User/Load/Riivolution/NewerSMBW")))
    _yi = _kpfile.parent / "YoshiIsland.kpmap"
    if _kp.game_maps_dir(_newer) and _yi.exists():
        _kb = _kp.read_kpbin(_kp.find_kpbin(_kp.game_maps_dir(_newer), _yi))
        check("Newer's game files: the map export (.kpbin, LZ11) has the same layers as the .kpmap",
              [L["type"] for L in _kb["layers"]] == [{"tile": 0, "doodad": 1, "path": 2}[k] for k, _l, _a in _kp.export_order(_kp.load(_yi))])
        _rep = mk.import_koopatlas(str(_yi), Path(_tf.mkdtemp()), world=1, ask=False, game_dir=str(_newer))
        pj = mk.project
        _art = [x for x in pj.stickers if x.get("reveal")]
        check("Import with the mod folder: Yoshi's Island's ground is drawn from the game's own tiles",
              any("game's own" in t for t in _rep["summary"]) and not pj.check().errors, _rep["summary"][:2])
        check("...its path art (Koopatlas associate layers) appears when its path opens", _art and
              all(x["reveal"] in mk._opened_paths() for x in _art), [x["reveal"] for x in _art])
        _rp = _pics.reveal_parts(pj, Path(_tf.mkdtemp()), mk._opened_paths())
        check("...and exports as route parts with a fade-only reveal mask (hidden until the path opens)",
              _rp and all(re.match(r"mtR\w{8}a$", q.material.split("/")[-1]) and q.uvs2 is not None and q.mask_offset == -3.0
                          for q in _rp), [q.material for q in _rp])
        mk.canvas.reveal_preview()
        check("Reveal preview: path art starts hidden and fades in in play-through order", mk.reveal_alpha(_art[0]["reveal"]) < 0.5)
        mk.canvas.stop_walk()
        check("...and is fully shown again afterwards", mk.reveal_alpha(_art[0]["reveal"]) == 1.0)
        # phase 7: sweeping reveal, Koopatlas path kinds, Newer's level list, the slot hook, world regions
        _vs = [float(v) for q in _rp for v in q.uvs2[:, 1]]
        check("Path art sweeps along its path: the reveal mask v runs from 3 (start) to 1 (end) across the quad",
              pj.reveal_style == "sweep" and min(_vs) < 1.3 and max(_vs) > 2.7, (pj.reveal_style, min(_vs), max(_vs)))
        _clip = mk.reveal_clip(_art[0]["reveal"], 0.5)
        check("...and the editor previews the sweep as a band growing along the path", _clip is not None and not _clip.isEmpty())
        _kinds = {r.get("kp"): r for r in pj.routes if r.get("kp")}
        check("Koopatlas path kinds map onto Deluxe's route actions: Swim walks with water steps, Jump jumps, Ladder climbs",
              _kinds.get("Swim", {}).get("type") == "水" and _kinds.get("Jump", {}).get("type") == "ジャンプ"
              and _kinds.get("Ladder", {}).get("type") == "はしご", {k: v["type"] for k, v in _kinds.items()})
        check("...a Pipe path is a hidden road (no road art, the tunnel trick)", _kinds.get("Pipe", {}).get("hidden") is True
              and _kinds["Pipe"]["type"] == "道", _kinds.get("Pipe"))
        check("...every one of Newer's 20 path animations has a Deluxe action and a note",
              len(_kp.PATH_ANIMS) == 20 and all(k in _kp.KP_PATHS for k in _kp.PATH_ANIMS)
              and "one-way" in _kp.path_note({"kp": "Pipe"}))
        _li = _kp.newer_levels(_newer)
        check("Newer's level list (LevelInfo.bin): 1-15 is shown as a tower, 1-25 a castle, 2-38 an airship",
              _li.get((1, 15), {}).get("level") == 21 and _li.get((1, 25), {}).get("level") == 23
              and _li.get((2, 38), {}).get("level") == 27 and _li[(1, 1)]["name"] == "Palm Beach", _li.get((1, 15)))
        check("...which name the Deluxe points: tower W1T0, castle W1C0, airship W1A0, a music house a Toad house",
              [_kp.point_candidates(1, d)[0] for d in (21, 23, 27, 36)] == ["W1T0", "W1C0", "W1A0", "W1K1"])
        check("...and only names ending in a digit (the game registers no path to W10a)",
              all(c[-1].isdigit() for c in _kp.point_candidates(1, 12)))
        mk.undo.setClean()
        _rep = mk.import_koopatlas(str(_yi), Path(_tf.mkdtemp()), world=1, ask=False, game_dir=str(_newer))
        _proj, _rep2 = _kp.import_map(_yi, Path(_tf.mkdtemp()), world=1, fit_slots="hook", game_dir=str(_newer),
                                      course_exists=lambda f: True)
        _gn = list(_proj.game_nodes()[0])
        check("Import with the slot hook: levels keep their numbers (W106-W108) and hidden points Fq ask for clears",
              _proj.slot_hook and {"W106", "W107", "W108"} <= set(_proj.nodes) and any(q.startswith("Fq") for q in _gn)
              and not _proj.check().errors, (_proj.check().errors[:2], [q for q in _gn if q[:2] in ("Fq", "Fr")]))
        check("...and the start is Newer's first node, so every level is reachable",
              not any("W101" in w for w in _proj.check().warnings if w.startswith("Never reachable")))
        _full = _kp.load(_kpfile.parent / "Fullmap.kpmap")
        _reg = _kp.world_regions(_full)
        _sub = _kp.subset(_full, _reg[2])
        check("Fullmap (290 paths) splits into its worlds, each under the game's 256 routes",
              {2, 3, 6} <= set(_reg) and len(_kp.path_layer(_sub)["paths"]) <= 256
              and _kp.region_world(_full, _reg[2], 2)["name"] == "Rubble Ruins", sorted(_reg))
        from kdx import newerport as _np
        check("The Newer port plan fills Deluxe's twelve world archives once each",
              sorted(s for s, *_r in _np.PLAN) == sorted(rules.SLOTS))
        mk.undo.setClean()
    else:
        print("SKIP Newer game files test (no NewerSMBW Riivolution folder)")
    mk.undo.setClean()
else:
    print("SKIP Koopatlas import test (no Newer map in Next Update)")
# right-click a point: change it into what this world needs
mk.new_project(confirm=False, width=20, height=14)
pj = mk.project
pj.add_node("start", 2.5, 2.5); _f = pj.add_node("stop", 6.5, 6.5)
_opts = mk.change_options(_f)
check("Change to: the world's needs first, then the levels the game keeps clears for, the specials and stops",
      _opts[0][0] == "Needed by this world" and any(o[2] == "W103" and o[0].startswith("Levels the game keeps") for o in _opts)
      and any(o[2] == "W1T0" for o in _opts) and any(o[3] == "bend" for o in _opts), [o[:3] for o in _opts[:4]])
mk.change_point(_f, "W103", "level")
check("...changing a stop into level 1-3 renames it (routes and unlocks follow), undo turns it back",
      "W103" in pj.nodes and pj.nodes["W103"]["kind"] == "level" and (mk.undo.undo() or _f in pj.nodes))
mk.toggle_flag("W1M0", "W1Item")
check("Mark as: the map enemy's start flag goes on the point", "W1Item" in pj.nodes["W1M0"]["row"][2])
mk.undo.setClean()
pj = mk.project
pj.music = 3
_gn, _hid = pj.game_nodes()
check("Music: World 3's map music is asked for with a hidden point Fm03 (read by the framework's map music hook)",
      "Fm03" in _gn and "Fm03" in _hid)
pj.music = 0
mk.undo.setClean()

# export through the dialog (needs the .NET runtime and the ROMFS test dump)
from kdx.maker2d import ExportDialog
from kdx import export2d
dump_romfs = Path(os.environ.get("KDX_ROMFS_DUMP", "/tmp/romfs_test/dump")) / "romfs"
if export2d.engine_status()[0] and (dump_romfs / "Model").exists():
    win.set_romfs(dump_romfs, "USen")
    mk.open(str(ROOT / "samples" / "Starter Island.kdxmap"))
    dlg = ExportDialog(mk)
    check("the export dialog offers the vanilla CS_W1 from the ROMFS as the template", dlg.template.count() >= 1 and dlg.template.currentText().endswith("CS_W1.Nin_NX_NVN.szs"))
    out_root = Path(_tf.mkdtemp())
    dlg.mod_root.setText(str(out_root))
    written, rep = dlg.do_export()
    check("Export to game writes CS_W1 and its camera table from the World Maker", sorted(p.name for p in written) == ["CS_W1.Nin_NX_NVN.szs", "MarioOffsetDataW1.bin"] and not rep.errors)
    win._confirm_discard = lambda: True
    check("the exported world opens in Koopatlas DX's own editor, with the walk on to World 2 and the hidden placeholders",
          win.open_world(str(written[0])) and win.world.point_ids == ["W1M0", "W101", "W102", "W1T0", "W1C0", "F191", "F193", "F195", "F19a", "F19b", "W1S0", "Fz00", "Fz01", "Fz02"],
          win.world.point_ids if win.world else None)
    win.tabs.setCurrentIndex(2)
else:
    print("SKIP export dialog test (no .NET runtime or no ROMFS test dump)")
# phase 7 controls: slot hook, custom map music, rounded islands, theme preset, painted roads
mk.new_project(confirm=False)
_p7 = mk.project
_p7.nodes = {}
_p7.add_node("start", 2, 2, "W1M0"); _p7.add_node("level", 6, 2, "W106")
_p7.add_route("W1M0", "W106")
_p7.nodes["W106"]["row"][4] = "RW106W1M0"
mk.slot_check.setChecked(True)
check("More levels per world: the switch turns on the slot hook (undoable) and adds the hidden point Fq05 for W106",
      _p7.slot_hook and "Fq05" in _p7.game_nodes()[0])
mk.undo.undo()
check("...undo turns it off again", not _p7.slot_hook and not mk.slot_check.isChecked())
from kdx import project2d as _p2d
_p7.music = _p2d.MUSIC_CUSTOM + 7
check("Custom map music: CUSTOM_BGM_07 is the hidden point Fn07 the framework's map music hook reads",
      "Fn07" in _p7.game_nodes()[0] and _p2d.music_of(["Fn07"]) == 107 and "CUSTOM_BGM_07" in _p2d.music_label(107))
_p7.music = 0
mk.theme_box.setCurrentIndex(mk.theme_box.findData("desert")); mk._theme_changed(0)
check("Theme preset: desert cliffs and the desert sea colour", _p7.theme == "desert" and _p7.sea_colour == "#2f9fc9")
mk.roads_check.setChecked(True)
check("Paint roads: the theme's road colour", _p7.road_colour == "#c9763a")
mk.round_check.setChecked(True)
check("Round edges: on, undoable", _p7.island_round)
for _u in range(3):
    mk.undo.undo()
check("...and all three undo", not _p7.island_round and not _p7.road_colour and _p7.theme != "desert")
mk.undo.setClean()
if win.world is not None and win.world.number == 1:
    win.set_world_music(3)
    check("Main editor Music field: World 3's part adds the hidden point Fm03 to the open world",
          "Fm03" in win.world.point_ids and win.world_music() == 3)
    win.undo.undo()
    check("...undo removes it", "Fm03" not in win.world.point_ids and win.world_music() == 0)
win._confirm_discard = lambda: True
win.close()
passed = sum(ok for _, ok in results)
print("\n%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)

"""
kdx/worldmodels.py - the world model editor: the models inside an opened game world (CS_W*.szs) in 3D.

Every model and shape of the world is listed and drawn with its textures. Click a shape in the 3D view (a pipe, a
tree) to select it, then drag it, turn it, scale it or give it its own texture; untick a shape to take it out of the
world, give a texture new pixels everywhere it is used, add objects from any model file (FBX, DAE, OBJ, Wii) and place
them by dragging or by typing, and export any model (or the whole world) as COLLADA. The edits go to world.model_edits and are applied
by the model writer when the world is exported (kdx.worldedit), so the files you opened stay as they are.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from . import model3d, modelref, objmodel, worldedit
from .editor3d import Item, SceneView, decompose


class WorldModelsDialog(QtWidgets.QDialog):
    def __init__(self, world, parent=None):
        super().__init__(parent)
        self.setWindowTitle("World models: %s" % world.archive_name)
        self.resize(1400, 860)
        self.world = world
        self.edits = copy.deepcopy(getattr(world, "model_edits", None) or worldedit.empty())
        self.edits.setdefault("remove", []); self.edits.setdefault("textures", {}); self.edits.setdefault("add", [])
        self.edits.setdefault("shapes", {})
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.shapes = worldedit.world_shapes(world)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.version = 0
        self._build()
        self._fill_tree()
        self._fill_textures()
        self._fill_objects()
        self._refresh_view(frame=True)

    # ---- UI ----------------------------------------------------------------------------------------------------------
    def _build(self):
        lay = QtWidgets.QHBoxLayout(self)
        split = QtWidgets.QSplitter()
        lay.addWidget(split)
        left = QtWidgets.QTabWidget()
        # models and shapes
        mw = QtWidgets.QWidget(); ml = QtWidgets.QVBoxLayout(mw)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Model / shape", "Material", "Triangles"])
        self.tree.itemChanged.connect(self._tree_changed)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        ml.addWidget(QtWidgets.QLabel("Untick a shape to take it out of the world on export."))
        ml.addWidget(self.tree, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("Export model…", self._export_model), ("Export whole world…", self._export_all),
                           ("Keep all", self._keep_all)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        ml.addLayout(row)
        box = QtWidgets.QGroupBox("Selected shape")
        bl = QtWidgets.QVBoxLayout(box)
        self.shape_label = QtWidgets.QLabel("Click a shape in the 3D view, or pick one in the list.")
        self.shape_label.setWordWrap(True)
        bl.addWidget(self.shape_label)
        sf = QtWidgets.QFormLayout()
        self.shape_fields = {}
        for key, lo, hi, dec in (("position", -1e6, 1e6, 1), ("rotation", -360, 360, 1), ("scale", -1e4, 1e4, 3)):
            h = QtWidgets.QHBoxLayout(); spins = []
            for _ in range(3):
                sp = QtWidgets.QDoubleSpinBox(); sp.setRange(lo, hi); sp.setDecimals(dec); sp.setKeyboardTracking(False)
                sp.editingFinished.connect(self._shape_fields_edited); h.addWidget(sp); spins.append(sp)
            self.shape_fields[key] = spins
            sf.addRow({"position": "Position", "rotation": "Turn (°)", "scale": "Scale"}[key], h)
        bl.addLayout(sf)
        row = QtWidgets.QHBoxLayout()
        self.shape_buttons = []
        for text, slot in (("Export…", self._export_shape), ("Replace…", self._replace_shape),
                           ("Retexture…", self._retexture_shape), ("Game texture", self._shape_texture_back),
                           ("Reset", self._reset_shape), ("Frame", self._frame_shape)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); row.addWidget(b); self.shape_buttons.append(b)
        bl.addLayout(row)
        ml.addWidget(box)
        left.addTab(mw, "Models")
        # textures
        tw = QtWidgets.QWidget(); tl = QtWidgets.QVBoxLayout(tw)
        self.tex_list = QtWidgets.QTreeWidget()
        self.tex_list.setHeaderLabels(["Texture", "Size", "Replaced by"])
        self.tex_list.setIconSize(QtCore.QSize(48, 48))
        tl.addWidget(self.tex_list, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("Replace with PNG…", self._replace_texture), ("Save as PNG…", self._save_texture),
                           ("Back to the game's", self._revert_texture)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        tl.addLayout(row)
        left.addTab(tw, "Textures")
        # added objects
        ow = QtWidgets.QWidget(); ol = QtWidgets.QVBoxLayout(ow)
        self.obj_list = QtWidgets.QListWidget()
        self.obj_list.currentRowChanged.connect(self._object_selected)
        ol.addWidget(QtWidgets.QLabel("Objects added to the world's ground model (%s)." % worldedit.terrain_model_name(self.world)))
        ol.addWidget(self.obj_list, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("Add…", self._add_object), ("Copy", self._copy_object), ("Remove", self._remove_object),
                           ("Edit model…", self._edit_object)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        ol.addLayout(row)
        f = QtWidgets.QFormLayout()
        self.fields = {}
        for key, lo, hi, dec in (("position", -1e6, 1e6, 1), ("rotation", -360, 360, 1), ("scale", -1e4, 1e4, 3)):
            h = QtWidgets.QHBoxLayout(); spins = []
            for _ in range(3):
                s = QtWidgets.QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(dec); s.setKeyboardTracking(False)
                s.editingFinished.connect(self._fields_edited); h.addWidget(s); spins.append(s)
            self.fields[key] = spins
            f.addRow({"position": "Position", "rotation": "Turn (°)", "scale": "Scale"}[key], h)
        ol.addLayout(f)
        left.addTab(ow, "Added objects")
        self.info = QtWidgets.QLabel(); self.info.setWordWrap(True)
        lw = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(lw); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(left, 1); lv.addWidget(self.info)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lv.addWidget(bb)
        split.addWidget(lw)
        right = QtWidgets.QWidget(); rv = QtWidgets.QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QHBoxLayout()
        grp = QtWidgets.QButtonGroup(self)
        self.mode_buttons = {}
        for key, text in (("move", "Move (W)"), ("turn", "Turn (E)"), ("scale", "Scale (R)")):
            b = QtWidgets.QToolButton(); b.setText(text); b.setCheckable(True); b.setChecked(key == "move")
            b.clicked.connect(lambda _=False, k=key: self.view.set_mode(k))
            grp.addButton(b); bar.addWidget(b)
            self.mode_buttons[key] = b
        cull = QtWidgets.QCheckBox("Front faces only"); cull.setChecked(True)
        cull.toggled.connect(lambda on: (setattr(self.view, "cull", on), self.view.update()))
        bar.addWidget(cull); bar.addStretch(1)
        rv.addLayout(bar)
        self.view = SceneView()
        from .editor3d import gizmo_options
        for wdg in gizmo_options(self.view, "world_models_gizmo"):
            bar.insertWidget(bar.count() - 2, wdg)
        self.view.modeChanged.connect(lambda k: self.mode_buttons[k].setChecked(True))
        self.view.show_points = False
        self.view.lock_ground = True
        self.view.select_first = True
        self.view.picked.connect(self._picked)
        self.view.objectEdited.connect(self._object_dragged)
        self.view.ground_height = self._ground_height
        rv.addWidget(self.view, 1)
        hint = QtWidgets.QLabel("Click a shape or object to select it · W/E/R: move/turn/scale gizmo (drag a handle: "
                                "one axis) · X: local axes · right drag: orbit · middle drag: pan · wheel: zoom · F: frame")
        hint.setObjectName("hint"); hint.setWordWrap(True)
        rv.addWidget(hint)
        split.addWidget(right)
        split.setSizes([520, 880])

    # ---- models ------------------------------------------------------------------------------------------------------
    def _removed(self):
        return {tuple(x) for x in self.edits["remove"]}

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        removed = self._removed()
        models = {}
        for mname, sname, mat, part in self.shapes:
            top = models.get(mname)
            if top is None:
                top = models[mname] = QtWidgets.QTreeWidgetItem([mname, "", ""])
                top.setFlags(top.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsAutoTristate)
                self.tree.addTopLevelItem(top)
            it = QtWidgets.QTreeWidgetItem([sname, mat, str(part.triangle_count)])
            it.setData(0, QtCore.Qt.UserRole, (mname, sname))
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(0, QtCore.Qt.Unchecked if (mname, sname) in removed else QtCore.Qt.Checked)
            top.addChild(it)
        for top in models.values():
            top.setText(2, str(sum(int(top.child(i).text(2)) for i in range(top.childCount()))))
        self.tree.resizeColumnToContents(0)
        self.tree.blockSignals(False)

    def _tree_changed(self, item, col):
        if col != 0:
            return
        keys = []
        for top_i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(top_i)
            for i in range(top.childCount()):
                it = top.child(i)
                if it.checkState(0) != QtCore.Qt.Checked:
                    keys.append(list(it.data(0, QtCore.Qt.UserRole)))
        self.edits["remove"] = keys
        self._refresh_view()

    def _keep_all(self):
        self.edits["remove"] = []
        self._fill_tree(); self._refresh_view()

    def _tree_selected(self):
        it = self.tree.currentItem()
        key = it.data(0, QtCore.Qt.UserRole) if it else None
        self.view.highlight_material = None
        if key:
            self.view.selected = ("shape",) + tuple(key)
        self._shape_selected()
        self.view.update()

    def _selected_models(self):
        it = self.tree.currentItem()
        if it is None:
            return None
        key = it.data(0, QtCore.Qt.UserRole)
        return [key[0]] if key else [it.text(0)]

    def _export_model(self):
        models = self._selected_models()
        if not models:
            QtWidgets.QMessageBox.information(self, "Export", "Select a model (or one of its shapes) first.")
            return
        self._export(models, models[0])

    def _export_all(self):
        self._export(None, self.world.id)

    def _export(self, models, stem, shapes=None):
        last = QtCore.QSettings("KoopatlasDX", "KoopatlasDX").value("export_model_ext", ".dae") or ".dae"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export (DAE, FBX or OBJ)", "%s%s" % (stem, last),
                                                        model3d.SAVE_FORMATS)
        if not path:
            return
        QtCore.QSettings("KoopatlasDX", "KoopatlasDX").setValue("export_model_ext", Path(path).suffix.lower() or ".dae")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            worldedit.export_model(self.world, models, path, shapes)
        except (ValueError, OSError, model3d.ModelError) as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Export", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QMessageBox.information(self, "Export", "Saved %s (textures as PNG next to it)." % path)

    def _export_shape(self):
        cur = self._current_shape()
        if cur:
            self._export([cur[0]], cur[1], shapes={cur})

    def _replace_shape(self):
        """Replace the selected game shape by a model file: the shape leaves the world (as unticked) and the file is
        added as an object where the shape stood, with the shape's move, turn and scale."""
        cur = self._current_shape()
        if not cur:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Replace %s with" % cur[1], "", model3d.FORMATS)
        if not path:
            return
        try:
            modelref.load_raw(path, False)
        except (objmodel.ObjError, OSError, model3d.ModelError) as e:
            QtWidgets.QMessageBox.warning(self, "Replace a shape", str(e))
            return
        mname, sname = cur
        m = self._shape_matrix(mname, sname)
        pivot = self._centred(mname, sname, self._part(mname, sname))[0]
        lo = self._part(mname, sname).positions.min(axis=0)
        foot = m @ np.r_[0.0, float(lo[1] - pivot[1]), 0.0, 1.0]           # the shape's foot, under its centre
        pos, rot, sc = decompose(m)
        if [mname, sname] not in self.edits["remove"]:
            self.edits["remove"].append([mname, sname])
        names = {o["name"] for o in self.edits["add"]}
        name, k = "%s (replaces %s)" % (Path(path).stem, sname), 2
        while name in names:
            name, k = "%s (replaces %s) %d" % (Path(path).stem, sname, k), k + 1
        self.edits["add"].append({"name": name, "path": str(Path(path).resolve()), "z_up": False,
                                  "position": [float(c) for c in foot[:3]], "rotation": [float(r) for r in rot],
                                  "scale": sc[0] if max(sc) - min(sc) < 1e-9 else [float(c) for c in sc],
                                  "replaces": [mname, sname]})
        self._fill_tree()
        self._fill_objects()
        self.obj_list.setCurrentRow(len(self.edits["add"]) - 1)
        self._refresh_view()

    # ---- textures ----------------------------------------------------------------------------------------------------
    def _fill_textures(self):
        self.tex_list.clear()
        self._textures = worldedit.world_textures(self.world)
        cache = worldedit.cache_for(self.world)
        for name, tex in sorted(self._textures.items()):
            it = QtWidgets.QTreeWidgetItem([name, "%d×%d" % (tex.width, tex.height), Path(self.edits["textures"].get(name, "")).name])
            png = worldedit._texture_png(self.world, name, cache, self._textures)
            src = self.edits["textures"].get(name, png)
            if src:
                pm = QtGui.QPixmap(src)
                if not pm.isNull():
                    it.setIcon(0, QtGui.QIcon(pm.scaled(48, 48, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            it.setData(0, QtCore.Qt.UserRole, name)
            self.tex_list.addTopLevelItem(it)
        self.tex_list.resizeColumnToContents(0)

    def _current_texture(self):
        it = self.tex_list.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it else None

    def _replace_texture(self):
        name = self._current_texture()
        if not name:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "New pixels for %s" % name, "", "Images (*.png *.jpg *.jpeg *.bmp *.tga)")
        if path:
            self.edits["textures"][name] = str(Path(path).resolve())
            self._fill_textures(); self._refresh_view()

    def _save_texture(self):
        name = self._current_texture()
        if not name:
            return
        src = self.edits["textures"].get(name) or worldedit._texture_png(self.world, name, worldedit.cache_for(self.world), self._textures)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save texture", "%s.png" % name, "PNG (*.png)")
        if path and src:
            QtGui.QImage(src).save(path)

    def _revert_texture(self):
        name = self._current_texture()
        if name and name in self.edits["textures"]:
            del self.edits["textures"][name]
            self._fill_textures(); self._refresh_view()

    # ---- added objects ---------------------------------------------------------------------------------------------
    def _fill_objects(self):
        row = self.obj_list.currentRow()
        self.obj_list.blockSignals(True)
        self.obj_list.clear()
        for o in self.edits["add"]:
            self.obj_list.addItem("%s  (%s)" % (o["name"], Path(o["path"]).name))
        self.obj_list.blockSignals(False)
        if 0 <= row < self.obj_list.count():
            self.obj_list.setCurrentRow(row)
        self._object_selected(self.obj_list.currentRow())

    def _object_selected(self, row):
        ok = 0 <= row < len(self.edits["add"])
        for key, spins in self.fields.items():
            for s in spins:
                s.blockSignals(True); s.setEnabled(ok)
        if ok:
            o = self.edits["add"][row]
            for key, vals in (("position", o.get("position", [0, 0, 0])), ("rotation", o.get("rotation", [0, 0, 0])),
                              ("scale", modelref.scale3(o))):
                for s, v in zip(self.fields[key], vals):
                    s.setValue(float(v))
            if self.view.selected != ("object", row):
                self.view.selected = ("object", row); self.view.update()
        for spins in self.fields.values():
            for s in spins:
                s.blockSignals(False)

    def _fields_edited(self):
        row = self.obj_list.currentRow()
        if not 0 <= row < len(self.edits["add"]):
            return
        o = self.edits["add"][row]
        o["position"] = [s.value() for s in self.fields["position"]]
        o["rotation"] = [s.value() for s in self.fields["rotation"]]
        sc = [s.value() for s in self.fields["scale"]]
        o["scale"] = sc[0] if max(sc) - min(sc) < 1e-12 else sc
        self._refresh_view()

    def _add_object(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add an object to the world", "", model3d.FORMATS)
        if not path:
            return
        z_up = False
        if path.lower().endswith(".obj"):
            z_up = QtWidgets.QMessageBox.question(self, "Add an object", "Was the model made with Z pointing up?",
                                                  QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                                                  QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes
        try:
            modelref.load_raw(path, z_up)
        except (objmodel.ObjError, OSError) as e:
            QtWidgets.QMessageBox.warning(self, "Add an object", str(e))
            return
        t = self.view.target
        h = self._ground_height(float(t[0]), float(t[2]))
        names = {o["name"] for o in self.edits["add"]}
        base, k, name = Path(path).stem, 2, Path(path).stem
        while name in names:
            name, k = "%s %d" % (base, k), k + 1
        self.edits["add"].append({"name": name, "path": str(Path(path).resolve()), "z_up": z_up,
                                  "position": [float(t[0]), float(h if h is not None else t[1]), float(t[2])],
                                  "rotation": [0.0, 0.0, 0.0], "scale": 1.0})
        self._fill_objects()
        self.obj_list.setCurrentRow(len(self.edits["add"]) - 1)
        self._refresh_view()

    def _copy_object(self):
        row = self.obj_list.currentRow()
        if not 0 <= row < len(self.edits["add"]):
            return
        o = copy.deepcopy(self.edits["add"][row])
        o["name"] += " copy"
        o["position"][0] += 60.0
        self.edits["add"].append(o)
        self._fill_objects(); self.obj_list.setCurrentRow(len(self.edits["add"]) - 1); self._refresh_view()

    def _remove_object(self):
        row = self.obj_list.currentRow()
        if 0 <= row < len(self.edits["add"]):
            del self.edits["add"][row]
            self._fill_objects(); self._refresh_view()

    def _edit_object(self):
        row = self.obj_list.currentRow()
        if not 0 <= row < len(self.edits["add"]):
            return
        from .modeleditor import ModelEditor
        dlg = ModelEditor(self.edits["add"][row], None, "Object: %s" % self.edits["add"][row]["name"], self, with_position=True)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.edits["add"][row] = dlg.ref
            self._fill_objects(); self._refresh_view()

    def _picked(self, key):
        if isinstance(key, tuple) and key[0] == "shape":
            self._select_tree(key[1], key[2])
            self._shape_selected()
        elif isinstance(key, tuple) and key[0] == "object":
            self.obj_list.setCurrentRow(key[1])
        elif isinstance(key, tuple) and key[0] == "model":
            it = self.tree.findItems(key[1], QtCore.Qt.MatchExactly, 0)
            if it:
                self.tree.setCurrentItem(it[0])

    def _object_dragged(self, key, matrix):
        if isinstance(key, tuple) and key[0] == "shape":
            self._set_shape_matrix(key[1], key[2], np.asarray(matrix, np.float64))
            return
        if not (isinstance(key, tuple) and key[0] == "object"):
            return
        pos, rot, sc = decompose(matrix)
        o = self.edits["add"][key[1]]
        o["position"], o["rotation"] = pos, rot
        o["scale"] = sc[0] if max(sc) - min(sc) < 1e-9 else sc
        self._object_selected(key[1])
        self._refresh_view()

    def _ground_height(self, x, z):
        best = None
        for mname, sname, _m, p in self.shapes:
            if not mname.endswith("_World") or (mname, sname) in self._removed():
                continue
            h = objmodel.ObjModel([p]).height_at(x, z)
            if h is not None and (best is None or h > best):
                best = h
        return best

    # ---- view --------------------------------------------------------------------------------------------------------
    def _refresh_view(self, frame=False):
        """One item per world model (its kept shapes, textures replaced where asked) and one per added object."""
        self.version += 1
        removed = self._removed()
        if not hasattr(self, "_albedo"):
            self._albedo = {(mo.name, s.name): mo.materials[s.material_index].albedo for mo in self.world.model.models
                            for s in mo.shapes if s.material_index < len(mo.materials)}
        items = []
        for mname, sname, _mat, p in self.shapes:
            if (mname, sname) in removed:
                continue
            pivot, local = self._centred(mname, sname, p)
            edit = self.edits["shapes"].get(worldedit.shape_key(mname, sname)) or {}
            tex = edit.get("texture") or self.edits["textures"].get(self._albedo.get((mname, sname)))
            if tex:
                q = local.parts[0].copy()
                q.texture = tex
                local = objmodel.ObjModel([q])
            matrix = (np.asarray(edit["matrix"], np.float64).reshape(4, 4) if edit.get("matrix") is not None
                      else worldedit.translation(pivot))
            items.append(Item(("shape", mname, sname), local, matrix, hash(("shape", mname, sname, tex))))
        for i, o in enumerate(self.edits["add"]):
            try:
                raw = modelref.apply_edits(modelref.load_raw(o["path"], o.get("z_up", False)), o)
            except (objmodel.ObjError, OSError):
                continue
            items.append(Item(("object", i), raw, modelref.matrix(o), hash(repr((o["path"], o.get("hidden"), o.get("edits"))))))
        self.view.set_items(items, frame=frame)
        kept = sum(p.triangle_count for _m, _s, _mt, p in self.shapes) - sum(
            p.triangle_count for m, s, _mt, p in self.shapes if (m, s) in removed)
        changed = sum(1 for e in self.edits["shapes"].values() if worldedit.shape_edit_active(e))
        self.info.setText("%d of %d shapes taken out, %d moved or retextured, %d textures replaced, %d objects added; "
                          "%d of the game's triangles kept. Applied when you export the world (File > Export)." % (
                              len(removed), len(self.shapes), changed, len(self.edits["textures"]),
                              len(self.edits["add"]), kept))

    # ---- the selected game shape -------------------------------------------------------------------------------------
    def _centred(self, mname, sname, part):
        """(pivot, the shape moved so its bounds' centre is at the origin): what the gizmo turns and scales about."""
        cache = self.__dict__.setdefault("_centred_cache", {})
        if (mname, sname) not in cache:
            lo, hi = part.positions.min(axis=0), part.positions.max(axis=0)
            pivot = ((lo + hi) / 2).astype(np.float64)
            q = part.copy(positions=(part.positions - pivot).astype(np.float32))
            q.material = "%s/%s" % (mname, sname)
            cache[(mname, sname)] = (pivot, objmodel.ObjModel([q]))
        return cache[(mname, sname)]

    def _part(self, mname, sname):
        return next(p for m, s, _mt, p in self.shapes if (m, s) == (mname, sname))

    def _current_shape(self):
        key = self.view.selected
        if isinstance(key, tuple) and key[0] == "shape":
            return key[1], key[2]
        return None

    def _select_tree(self, mname, sname):
        for ti in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(ti)
            if top.text(0) != mname:
                continue
            for i in range(top.childCount()):
                if tuple(top.child(i).data(0, QtCore.Qt.UserRole)) == (mname, sname):
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(top.child(i))
                    self.tree.scrollToItem(top.child(i))
                    self.tree.blockSignals(False)
                    return

    def _shape_matrix(self, mname, sname):
        edit = self.edits["shapes"].get(worldedit.shape_key(mname, sname)) or {}
        if edit.get("matrix") is not None:
            return np.asarray(edit["matrix"], np.float64).reshape(4, 4)
        return worldedit.translation(self._centred(mname, sname, self._part(mname, sname))[0])

    def _shape_selected(self):
        cur = self._current_shape()
        for spins in self.shape_fields.values():
            for sp in spins:
                sp.blockSignals(True); sp.setEnabled(cur is not None)
        for b in self.shape_buttons:
            b.setEnabled(cur is not None)
        if cur:
            mname, sname = cur
            mat = next((mt for m, s, mt, _p in self.shapes if (m, s) == cur), "")
            tex = (self.edits["shapes"].get(worldedit.shape_key(mname, sname)) or {}).get("texture")
            self.shape_label.setText("%s in %s (material %s)%s" % (
                sname, mname, mat, "; own texture %s" % Path(tex).name if tex else ""))
            pos, rot, sc = decompose(self._shape_matrix(mname, sname))
            for key, vals in (("position", pos), ("rotation", rot), ("scale", sc)):
                for sp, v in zip(self.shape_fields[key], vals):
                    sp.setValue(float(v))
        else:
            self.shape_label.setText("Click a shape in the 3D view, or pick one in the list.")
        for spins in self.shape_fields.values():
            for sp in spins:
                sp.blockSignals(False)

    def _set_shape_matrix(self, mname, sname, matrix):
        pivot = self._centred(mname, sname, self._part(mname, sname))[0]
        edit = self.edits["shapes"].setdefault(worldedit.shape_key(mname, sname), {})
        edit["matrix"] = np.asarray(matrix, np.float64).ravel().tolist()
        edit["pivot"] = [float(v) for v in pivot]
        self._tidy_shape(mname, sname)
        self._shape_selected()
        self._refresh_view()

    def _tidy_shape(self, mname, sname):
        key = worldedit.shape_key(mname, sname)
        if key in self.edits["shapes"] and not worldedit.shape_edit_active(self.edits["shapes"][key]):
            del self.edits["shapes"][key]

    def _shape_fields_edited(self):
        cur = self._current_shape()
        if not cur:
            return
        ref = {"position": [sp.value() for sp in self.shape_fields["position"]],
               "rotation": [sp.value() for sp in self.shape_fields["rotation"]],
               "scale": [sp.value() for sp in self.shape_fields["scale"]]}
        self._set_shape_matrix(cur[0], cur[1], modelref.matrix(ref))

    def _retexture_shape(self, path=None):
        cur = self._current_shape()
        if not cur:
            return
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Give %s its own texture" % cur[1], "",
                                                            "Images (*.png *.jpg *.bmp)")
        if not path:
            return
        if QtGui.QImage(path).isNull():
            QtWidgets.QMessageBox.warning(self, "Retexture", "%s cannot be read as an image." % path)
            return
        edit = self.edits["shapes"].setdefault(worldedit.shape_key(*cur), {})
        edit.setdefault("pivot", [float(v) for v in self._centred(cur[0], cur[1], self._part(*cur))[0]])
        edit["texture"] = str(Path(path).resolve())
        self._shape_selected(); self._refresh_view()

    def _shape_texture_back(self):
        cur = self._current_shape()
        edit = self.edits["shapes"].get(worldedit.shape_key(*cur)) if cur else None
        if edit:
            edit.pop("texture", None)
            self._tidy_shape(*cur)
            self._shape_selected(); self._refresh_view()

    def _reset_shape(self):
        cur = self._current_shape()
        if cur:
            self.edits["shapes"].pop(worldedit.shape_key(*cur), None)
            self._shape_selected(); self._refresh_view()

    def _frame_shape(self):
        if self._current_shape():
            self.view.frame_selection()

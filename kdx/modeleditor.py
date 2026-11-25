"""
kdx/modeleditor.py - the model editor: change how a 3D model (the ground, or a scenery object) is used in a map.

Everything here is an edit stored in the map (kdx.modelref): the model file is never changed. Per material: show or
hide it, how it is drawn (opaque, cut-out, see-through), its texture or colour, double-sided, faces turned round.
For the whole model: turn and scale (and its file's up axis). "Save as DAE/OBJ" writes the edited model as a new file,
which can then replace the original in the map.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from . import model3d, modelref, objmodel
from .editor3d import Item, SceneView

MODE_NAMES = [("opaque", "Opaque"), ("mask", "Cut-out (alpha test)"), ("translucent", "See-through (blend)")]


class ModelEditor(QtWidgets.QDialog):
    """Edit a model reference. Returns the edited copy in .ref when accepted."""

    def __init__(self, ref, base=None, title="Model editor", parent=None, with_position=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1180, 720)
        self.ref = copy.deepcopy(ref)
        self.base = base
        self.with_position = with_position
        self.version = 0
        self.raw = None
        self._build()
        self._load()

    # ---- UI ----------------------------------------------------------------------------------------------------------
    def _build(self):
        lay = QtWidgets.QHBoxLayout(self)
        split = QtWidgets.QSplitter()
        lay.addWidget(split)
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        filebox = QtWidgets.QGroupBox("File")
        fl = QtWidgets.QGridLayout(filebox)
        self.file_label = QtWidgets.QLabel()
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.zup = QtWidgets.QCheckBox("Made with Z up (turn to Y up)")
        self.zup.setToolTip("OBJ files do not say their up axis. COLLADA and FBX do; tick this only if the file states it wrongly.")
        self.zup.toggled.connect(self._zup_changed)
        b_rep = QtWidgets.QPushButton("Replace file…"); b_rep.clicked.connect(self._replace)
        b_save = QtWidgets.QPushButton("Save as DAE/OBJ…"); b_save.clicked.connect(self._save_as)
        b_save.setToolTip("Write the model with these edits (materials, turn, scale) as a new COLLADA or OBJ file, "
                          "with its textures as PNG next to it.")
        fl.addWidget(self.file_label, 0, 0, 1, 3)
        fl.addWidget(self.zup, 1, 0, 1, 3)
        fl.addWidget(b_rep, 2, 0); fl.addWidget(b_save, 2, 1)
        ll.addWidget(filebox)

        tbox = QtWidgets.QGroupBox("Placement")
        tf = QtWidgets.QFormLayout(tbox)
        self.pos = [self._spin(-1e6, 1e6, 1) for _ in range(3)]
        self.rot = [self._spin(-360, 360, 1) for _ in range(3)]
        self.scl = [self._spin(-1e4, 1e4, 4) for _ in range(3)]
        self.uniform = QtWidgets.QCheckBox("Same scale on every axis"); self.uniform.setChecked(True)
        for row, (label, spins) in enumerate((("Position", self.pos), ("Turn (degrees x, y, z)", self.rot), ("Scale", self.scl))):
            if label == "Position" and not self.with_position:
                label = "Offset"
            h = QtWidgets.QHBoxLayout()
            for s in spins:
                h.addWidget(s)
            tf.addRow(label, h)
        tf.addRow(self.uniform)
        for s in self.pos + self.rot + self.scl:
            s.valueChanged.connect(self._placement_changed)
        ll.addWidget(tbox)

        mbox = QtWidgets.QGroupBox("Materials")
        ml = QtWidgets.QVBoxLayout(mbox)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Show", "Material", "Triangles", "Drawn as", "Texture / colour", "Two-sided", "Flip"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.table.itemChanged.connect(self._item_changed)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        ml.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("Show all", lambda: self._show_all(True)), ("Hide all", lambda: self._show_all(False)),
                           ("Reset edits", self._reset)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        row.addStretch(1)
        ml.addLayout(row)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        ml.addWidget(self.info)
        ll.addWidget(mbox, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        ll.addWidget(bb)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        self.view = SceneView(editable=False)
        self.view.show_points = False
        self.view.picked.connect(self._view_picked)
        cull = QtWidgets.QCheckBox("Draw front faces only, as the game does")
        cull.setChecked(True)
        cull.toggled.connect(lambda on: (setattr(self.view, "cull", on), self.view.update()))
        hint = QtWidgets.QLabel("Right drag: orbit · middle drag: pan · wheel: zoom · F: frame · click a face to find its material")
        hint.setStyleSheet("color: gray")
        rl.addWidget(self.view, 1); rl.addWidget(cull); rl.addWidget(hint)
        split.addWidget(right)
        split.setSizes([560, 620])

    @staticmethod
    def _spin(lo, hi, dec):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi); s.setDecimals(dec); s.setKeyboardTracking(False)
        s.setSingleStep(1.0 if dec <= 1 else 0.1)
        return s

    # ---- state -------------------------------------------------------------------------------------------------------
    def _load(self):
        path = modelref.resolve(self.ref, self.base)
        self.file_label.setText(str(path))
        self.zup.blockSignals(True); self.zup.setChecked(bool(self.ref.get("z_up", False))); self.zup.blockSignals(False)
        try:
            self.raw = modelref.load_raw(path, self.ref.get("z_up", False))
        except (objmodel.ObjError, OSError) as e:
            self.raw = objmodel.ObjModel([], path)
            QtWidgets.QMessageBox.warning(self, "Model editor", "The model could not be read:\n%s" % e)
        pos = self.ref.get("position", self.ref.get("offset", [0.0, 0.0, 0.0]))
        for s, v in zip(self.pos + self.rot + self.scl,
                        list(pos) + list(self.ref.get("rotation", [0.0, 0.0, 0.0])) + modelref.scale3(self.ref)):
            s.blockSignals(True); s.setValue(float(v)); s.blockSignals(False)
        sc = modelref.scale3(self.ref)
        self.uniform.setChecked(abs(sc[0] - sc[1]) < 1e-9 and abs(sc[1] - sc[2]) < 1e-9)
        self._fill_table()
        self._refresh(frame=True)

    def _fill_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        hidden = set(self.ref.get("hidden", ()))
        edits = self.ref.get("edits", {})
        for p in self.raw.parts:
            e = edits.get(p.material, {})
            r = self.table.rowCount()
            self.table.insertRow(r)
            show = QtWidgets.QTableWidgetItem(); show.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            show.setCheckState(QtCore.Qt.Unchecked if p.material in hidden else QtCore.Qt.Checked)
            self.table.setItem(r, 0, show)
            name = QtWidgets.QTableWidgetItem(p.material); name.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r, 1, name)
            tri = QtWidgets.QTableWidgetItem(str(p.triangle_count)); tri.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r, 2, tri)
            mode = QtWidgets.QComboBox()
            for k, label in MODE_NAMES:
                mode.addItem(label, k)
            mode.setCurrentIndex([k for k, _ in MODE_NAMES].index(e.get("mode", p.mode)) if e.get("mode", p.mode) in dict(MODE_NAMES) else 0)
            mode.currentIndexChanged.connect(lambda _i, m=p.material, w=mode: self._edit(m, "mode", w.currentData()))
            self.table.setCellWidget(r, 3, mode)
            tex = QtWidgets.QToolButton()
            tex.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            menu = QtWidgets.QMenu(tex)
            menu.addAction("Choose a texture…", lambda m=p.material: self._choose_texture(m))
            menu.addAction("Plain colour…", lambda m=p.material: self._choose_color(m))
            menu.addAction("Back to the file's", lambda m=p.material: self._clear_surface(m))
            tex.setMenu(menu)
            self._label_surface(tex, p, e)
            self.table.setCellWidget(r, 4, tex)
            for col, key in ((5, "double_sided"), (6, "flip")):
                it = QtWidgets.QTableWidgetItem(); it.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                val = e.get(key, p.double_sided if key == "double_sided" else False)
                it.setCheckState(QtCore.Qt.Checked if val else QtCore.Qt.Unchecked)
                self.table.setItem(r, col, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.blockSignals(False)

    @staticmethod
    def _label_surface(button, part, e):
        tex = e.get("texture", part.texture) if "texture" in e else part.texture
        if tex:
            button.setText(Path(tex).name)
            button.setToolTip(tex)
        else:
            c = e.get("color", part.color)
            button.setText("colour %d %d %d" % tuple(int(255 * v) for v in c[:3]))
            button.setToolTip("")

    def _edit(self, material, key, value):
        edits = self.ref.setdefault("edits", {})
        part = next((p for p in self.raw.parts if p.material == material), None)
        e = edits.setdefault(material, {})
        default = {"mode": part.mode if part else "opaque", "double_sided": part.double_sided if part else False,
                   "flip": False}.get(key, object())
        if value == default and key in ("mode", "double_sided", "flip"):
            e.pop(key, None)
        else:
            e[key] = value
        if not e:
            edits.pop(material, None)
        if not edits:
            self.ref.pop("edits", None)
        self._refresh()

    def _choose_texture(self, material):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Texture for %s" % material, "", "Images (*.png *.jpg *.jpeg *.bmp *.tga *.tif)")
        if path:
            e = self.ref.setdefault("edits", {}).setdefault(material, {})
            e["texture"] = str(Path(path).resolve())
            e.pop("color", None)
            self._fill_table(); self._refresh()

    def _choose_color(self, material):
        part = next(p for p in self.raw.parts if p.material == material)
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*part.color[:3]), self, "Colour of %s" % material)
        if c.isValid():
            e = self.ref.setdefault("edits", {}).setdefault(material, {})
            e["texture"] = ""
            e["color"] = [c.redF(), c.greenF(), c.blueF()]
            self._fill_table(); self._refresh()

    def _clear_surface(self, material):
        e = self.ref.get("edits", {}).get(material)
        if e:
            e.pop("texture", None); e.pop("color", None)
            if not e:
                self.ref["edits"].pop(material)
        self._fill_table(); self._refresh()

    def _item_changed(self, item):
        r, c = item.row(), item.column()
        material = self.table.item(r, 1).text()
        on = item.checkState() == QtCore.Qt.Checked
        if c == 0:
            hidden = [m for m in self.ref.get("hidden", []) if m != material] + ([] if on else [material])
            if hidden:
                self.ref["hidden"] = hidden
            else:
                self.ref.pop("hidden", None)
            self._refresh()
        elif c == 5:
            self._edit(material, "double_sided", on)
        elif c == 6:
            self._edit(material, "flip", on)

    def _show_all(self, on):
        self.ref.pop("hidden", None)
        if not on:
            self.ref["hidden"] = [p.material for p in self.raw.parts]
        self._fill_table(); self._refresh()

    def _reset(self):
        for k in ("hidden", "edits"):
            self.ref.pop(k, None)
        self._fill_table(); self._refresh()

    def _placement_changed(self):
        sender = self.sender()
        if self.uniform.isChecked() and sender in self.scl:
            for s in self.scl:
                if s is not sender:
                    s.blockSignals(True); s.setValue(sender.value()); s.blockSignals(False)
        key = "position" if self.with_position else "offset"
        self.ref[key] = [s.value() for s in self.pos]
        self.ref["rotation"] = [s.value() for s in self.rot]
        sc = [s.value() for s in self.scl]
        self.ref["scale"] = sc[0] if abs(sc[0] - sc[1]) < 1e-12 and abs(sc[1] - sc[2]) < 1e-12 else sc
        if not any(self.ref["rotation"]):
            self.ref.pop("rotation")
        self._refresh(geometry=False)

    def _zup_changed(self, on):
        self.ref["z_up"] = bool(on)
        self._load()

    def _replace(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Replace the model file", "", model3d.FORMATS)
        if path:
            self.ref["path"] = str(Path(path).resolve())
            self._load()

    def edited_model(self):
        """The edited model in model space (materials edited, not placed)."""
        return modelref.apply_edits(self.raw, self.ref)

    def _preview_matrix(self):
        r = dict(self.ref)
        r.pop("position", None); r.pop("offset", None)
        return modelref.matrix(r)

    def _refresh(self, frame=False, geometry=True):
        if geometry:
            self.version += 1
            self._edited = self.edited_model()
        self.view.set_items([Item("model", self._edited, self._preview_matrix(), self.version)], frame=frame)
        shown = sum(p.triangle_count for p in self._edited.parts)
        total = sum(p.triangle_count for p in self.raw.parts)
        self.info.setText("%d of %d materials shown, %d of %d triangles.%s" % (
            len(self._edited.parts), len(self.raw.parts), shown, total,
            " %d triangles faced inwards in the file and were turned round on loading." % self.raw.flipped if self.raw.flipped else ""))

    def _row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        self.view.highlight_material = self.table.item(rows[0].row(), 1).text() if rows else None
        self.view.update()

    def _view_picked(self, key):
        """A click in the preview selects the row of the material under the mouse."""
        pos = self.view.mapFromGlobal(QtGui.QCursor.pos())
        o, d = self.view.ray(pos.x(), pos.y())
        inv = np.linalg.inv(self._preview_matrix())
        lo = (inv @ np.r_[o, 1.0])[:3]
        ld = inv[:3, :3] @ d
        from .editor3d import ray_triangles
        best = None
        for p in self._edited.parts:
            t = ray_triangles(lo, ld, p.positions.reshape(-1, 3, 3).astype(np.float64))
            if len(t) and (best is None or t.min() < best[0]):
                best = (t.min(), p.material)
        self.view.selected = None
        if best:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 1).text() == best[1]:
                    self.table.selectRow(r)
                    self.table.scrollToItem(self.table.item(r, 1))

    def _save_as(self):
        src = modelref.resolve(self.ref, self.base)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the edited model", str(src.with_name(src.stem + "_edited.dae")),
                                                        model3d.SAVE_FORMATS)
        if not path:
            return
        r = dict(self.ref)
        r.pop("position", None); r.pop("offset", None)
        model = self.edited_model().transformed(modelref.matrix(r))
        try:
            model3d.save_model(model, path)
        except (model3d.ModelError, OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Save", str(e))
            return
        if QtWidgets.QMessageBox.question(self, "Save", "Saved %s.\n\nUse the new file for this model? Its edits, turn and "
                                          "scale are already in it." % Path(path).name) == QtWidgets.QMessageBox.Yes:
            for k in ("hidden", "edits", "rotation"):
                self.ref.pop(k, None)
            self.ref["scale"] = 1.0
            self.ref["z_up"] = False
            self.ref["path"] = str(Path(path).resolve())
            modelref.forget(path)
            self._load()

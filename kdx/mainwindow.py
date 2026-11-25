"""
kdx/mainwindow.py - the Koopatlas DX main window.

Left: the world's points, waypoints, routes and models. Centre: the 3D view. Right: the selected item's properties,
including its MarioOffset record. Every edit goes through an undo stack; nothing is written until File > Export.
"""
from __future__ import annotations

import os
import subprocess
import traceback
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from . import __version__, diagnostics, directions, ground, keyedit, libs, offsets, romfs, rules, theme, yaz0fast
from .maker2d import WorldMaker
from .map2d import Map2D
from .names import Names
from .scene import WorldScene
from .viewport import Viewport
from .world import POINT_COLUMNS, ROUTE_TYPES_KNOWN, World, WorldError, point_kind

DEFAULT_MSBT_EDITOR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MsbtEditor", "current")


class EditCommand(QtWidgets.QUndoCommand):
    """A reversible edit: do() and undo() are callables; after() refreshes the UI."""

    def __init__(self, text, do, undo, after):
        super().__init__(text)
        self._do, self._undo, self._after = do, undo, after

    def redo(self):
        self._do()
        self._refresh()

    def undo(self):
        self._undo()
        self._refresh()

    def _refresh(self):
        try:
            self._after()
        except Exception:                 # a refresh problem must never abort the program (Qt calls this)
            traceback.print_exc()


def spin(lo=-1e7, hi=1e7, decimals=3, step=10.0):
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setKeyboardTracking(False)
    return s


def _dot_icon(color, size=18):
    pm = QtGui.QPixmap(size * 2, size * 2)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor(color)); p.setPen(QtGui.QPen(QtGui.QColor("#0b1220"), 2))
    p.drawEllipse(QtCore.QRectF(5, 5, size * 2 - 10, size * 2 - 10))
    p.setPen(QtGui.QPen(QtGui.QColor("#0b1220"), 4))
    c = size
    p.drawLine(c - 7, c, c + 7, c); p.drawLine(c, c - 7, c, c + 7)
    p.end()
    return QtGui.QIcon(pm)


# --------------------------------------------------------------------------------------------- flags dialog
class FlagsDialog(QtWidgets.QDialog):
    """Tick the flags a point has, grouped by what they do (kdx/flaghelp.py). Hover or click one for its full story."""

    def __init__(self, parent, pid, current, slot=None):
        super().__init__(parent)
        from . import flaghelp as FH
        self.FH, self.slot = FH, slot
        self.setWindowTitle("Flags of %s" % pid)
        self.resize(820, 600)
        self.current = rules.split(current)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText("Search flags (name or meaning)…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.all_worlds = QtWidgets.QCheckBox("Show other worlds' flags")
        self.all_worlds.setChecked(slot is None)
        self.all_worlds.toggled.connect(self._filter)
        top.addWidget(self.search, 1); top.addWidget(self.all_worlds)
        lay.addLayout(top)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        host = QtWidgets.QWidget(); hl = QtWidgets.QVBoxLayout(host)
        self.boxes, self.groups, self.grids = {}, {}, {}
        for group, items in FH.grouped():
            g = QtWidgets.QGroupBox(group)
            grid = QtWidgets.QGridLayout(g)
            for i, f in enumerate(items):
                cb = QtWidgets.QCheckBox(f.label if f.label != f.name else f.name)
                cb.setChecked(f.name in self.current)
                cb.setToolTip(FH.tooltip(f.name, slot))
                cb.setProperty("flag", f.name)
                if f.worlds and slot and slot not in f.worlds:
                    cb.setStyleSheet("color: gray")
                cb.installEventFilter(self)
                cb.toggled.connect(lambda _on, n=f.name: self._show(n))
                grid.addWidget(cb, i // 3, i % 3)
                self.boxes[f.name] = cb
            hl.addWidget(g)
            self.groups[group] = g
            self.grids[group] = grid
        hl.addStretch(1)
        scroll.setWidget(host)
        split.addWidget(scroll)
        self.info = QtWidgets.QTextBrowser()
        self.info.setMinimumWidth(240)
        split.addWidget(self.info)
        split.setSizes([560, 260])
        lay.addWidget(split, 1)
        self.chosen = QtWidgets.QLabel()
        self.chosen.setWordWrap(True)
        lay.addWidget(self.chosen)
        self.other = QtWidgets.QLineEdit(",".join(f for f in self.current if f not in self.boxes))
        self.other.setToolTip("Flags not in the game's tables. The game ignores them; they are kept as they are.")
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Other flags:")); row.addWidget(self.other, 1)
        lay.addLayout(row)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for cb in self.boxes.values():
            cb.toggled.connect(self._update_chosen)
        self._filter()
        self._update_chosen()
        self._show(self.current[0] if self.current else "stop")

    def eventFilter(self, obj, ev):
        if ev.type() in (QtCore.QEvent.Enter, QtCore.QEvent.FocusIn) and obj.property("flag"):
            self._show(obj.property("flag"))
        return False

    def _show(self, name):
        f = self.FH.info(name)
        self.info.setHtml("<h3>%s</h3><p><code>%s</code> · %s</p><p>%s</p>%s%s<p><i>%s</i></p>" % (
            f.label, f.name, f.group, f.text,
            "<p style='color:#f59e0b'>%s</p>" % f.warn if f.warn else "",
            "<p>Used by: %s</p>" % ", ".join(f.worlds) if f.worlds else "",
            self.FH.SURE_TEXT[f.sure]))

    def _filter(self, *_):
        q = self.search.text().strip().lower()
        for group, g in self.groups.items():
            shown = 0
            for name, cb in self.boxes.items():
                f = self.FH.info(name)
                if f.group != group:
                    continue
                vis = (not q or q in name.lower() or q in f.label.lower() or q in f.text.lower())
                if (not self.all_worlds.isChecked() and f.worlds and self.slot and self.slot not in f.worlds
                        and not cb.isChecked()):
                    vis = False
                cb.setVisible(vis)
                if vis:                                      # re-flow the visible ones so no holes are left
                    self.grids[group].addWidget(cb, shown // 3, shown % 3)
                shown += vis
            g.setVisible(shown > 0)

    def _update_chosen(self, *_):
        on = [n for n, cb in self.boxes.items() if cb.isChecked()]
        self.chosen.setText("This point: " + (self.FH.summary(on) if on else "no flags (a plain point)"))

    def value(self):
        ticked = [n for n, cb in self.boxes.items() if cb.isChecked()]
        kept = [f for f in self.current if f in ticked]                    # keep the existing order
        added = [n for n in ticked if n not in kept]
        other = rules.split(self.other.text().replace(" ", ""))
        return ",".join(kept + added + [o for o in other if o not in ticked])


class UnlockCards(QtWidgets.QWidget):
    """The game values of an unlock column as readable cards: what each point is, which road each path draws, with a
    remove button; hovering a path card lights up just that path."""

    KIND_COLORS = {"Level": "#facc15", "Castle": "#f87171", "Tower": "#fb923c", "Toad house": "#f472b6",
                   "Bend": "#94a3b8", "Free point": "#93c5fd"}

    def __init__(self, panel, col):
        super().__init__()
        self.panel, self.col = panel, col
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(3)

    def refresh(self, pid, items, win):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)                     # gone now, not at the next event loop pass
        w = win.world
        for entry in items:
            card = QtWidgets.QFrame()
            card.setObjectName("unlockCard")
            card.setStyleSheet("#unlockCard { background: rgba(148,163,184,0.10); border-radius: 6px; }")
            hl = QtWidgets.QHBoxLayout(card); hl.setContentsMargins(6, 3, 4, 3)
            if self.col in (3, 6):                                  # a point
                name, _lab = win.names.point(entry) if win.names else (None, None)
                kind = point_kind(entry) if entry in w.point_ids else ("Bend" if entry[:1] == "K" else "not on this map")
                color = next((c for k, c in self.KIND_COLORS.items() if kind.startswith(k)), "#cbd5e1")
                text = "<b>%s</b> · %s%s" % (entry, kind, (" · " + name) if name else "")
                tip = "When %s's level is beaten, %s becomes available (its icon lights up)." % (pid, entry)
                ok = entry in w.point_ids or w.has_bone(entry)
            else:                                                   # a path
                ends = rules.route_ends(entry)
                chain = w.path_chain(entry) if ends else None
                if not ends:
                    text, ok = "<b>%s</b> · not a path name (R + 4 + 4 characters)" % entry, False
                elif chain is None:
                    text, ok = "<b>%s</b> · no road joins %s and %s" % (entry, ends[0], ends[1]), False
                else:
                    kinds = point_kind(ends[1]) if ends[1] in w.point_ids else "point"
                    text = "<b>%s → %s</b> (%s) · road of %d piece%s" % (ends[0], ends[1], kinds, len(chain),
                                                                      "" if len(chain) == 1 else "s")
                    ok = True
                color = "#4ade80" if self.col == 4 else "#f472b6"
                tip = ("Game value %s: when the level is beaten, the road from %s to %s is drawn (all its pieces). "
                       "Hover to see it." % (entry, ends[0], ends[1]) if ends else entry)
            pa = win.path_animation(entry) if self.col in (4, 7) else None
            if pa:
                text += " · 🎬 %s%d frames" % ("copy of %s, " % pa[2] if pa[2] else "", pa[1])
            dot = QtWidgets.QLabel("●"); dot.setStyleSheet("color: %s" % (color if ok else "#ef4444"))
            lab = QtWidgets.QLabel(text if ok else "<span style='color:#f87171'>%s</span>" % text)
            lab.setToolTip(tip)
            x = QtWidgets.QToolButton(); x.setText("×"); x.setAutoRaise(True); x.setToolTip("Remove " + entry)
            x.clicked.connect(lambda _=False, e=entry: self.panel._remove_entry(self.col, e))
            hl.addWidget(dot); hl.addWidget(lab, 1)
            if pa:
                pb = QtWidgets.QToolButton(); pb.setText("▶"); pb.setAutoRaise(True)
                pb.setToolTip("Play the animation this path shows when it opens")
                pb.clicked.connect(lambda _=False, e=entry: win.preview_path_anim(e))
                hl.addWidget(pb)
            hl.addWidget(x)
            if self.col in (4, 7) and ok:
                card.enterEvent = lambda _e, segs=chain: win.set_highlight({sgm: (1.0, 0.85, 0.2) for sgm in segs})
                card.leaveEvent = lambda _e: self.panel._show_segments(pid, w.row_of(pid))
            self.lay.addWidget(card)
        if not items:
            none = QtWidgets.QLabel("<i>nothing yet</i>"); none.setStyleSheet("color: #64748b")
            self.lay.addWidget(none)


class EventsPanel(QtWidgets.QWidget):
    """Phase 3: the world's animations as the game uses them (kdx/events.py): each path's appear animation (who opens
    it, how long it plays), the looping ambient ones and event animations. Preview plays one in the 3D view; Copy puts
    a vanilla appear animation (a bridge, steps) on another path of this world, added on export."""

    def __init__(self, win):
        super().__init__()
        self.win, self.anims, self.ev = win, None, None
        lay = QtWidgets.QVBoxLayout(self)
        self.info = QtWidgets.QLabel("Open a world.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Animation", "Opened by", "Length", "Notes"])
        self.tree.setRootIsDecorated(True)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self.preview())
        lay.addWidget(self.tree, 1)
        row = QtWidgets.QHBoxLayout()
        self.play_btn = QtWidgets.QPushButton("▶ Preview"); self.play_btn.clicked.connect(self.preview)
        self.copy_btn = QtWidgets.QPushButton("Copy to a new path…"); self.copy_btn.clicked.connect(self.copy_dialog)
        self.new_btn = QtWidgets.QPushButton("+ New appear animation…"); self.new_btn.clicked.connect(self.new_dialog)
        self.new_btn.setToolTip("Make any object of the world appear when a path opens: it rises, drops in, pops or "
                                "grows (added on export)")
        self.keys_btn = QtWidgets.QPushButton("Keyframes…"); self.keys_btn.clicked.connect(self.edit_keys)
        self.keys_btn.setToolTip("Edit a new appear animation key by key (move, turn, scale)")
        self.remove_btn = QtWidgets.QPushButton("Remove copy"); self.remove_btn.clicked.connect(self.remove_copy)
        ref = QtWidgets.QPushButton("Refresh"); ref.clicked.connect(lambda: self.refresh(force=True))
        for b in (self.play_btn, self.new_btn, self.keys_btn, self.copy_btn, self.remove_btn, ref):
            row.addWidget(b)
        lay.addLayout(row)
        self.detail = QtWidgets.QLabel(""); self.detail.setWordWrap(True); self.detail.setStyleSheet("color: #9fb3c8")
        lay.addWidget(self.detail)
        self._selected()

    def _load_anims(self, force=False):
        w = self.win.world
        if self.anims is not None and not force and getattr(self, "_for", None) is w:
            return self.anims
        self.anims, self._for = None, w
        from . import anim, export2d
        if export2d.engine_status()[0]:
            try:
                self.anims = anim.Animations.load(w)
            except Exception as e:                                # models only, then
                self.win.statusBar().showMessage("Animations could not be read: %s" % e, 6000)
        return self.anims

    def refresh(self, force=False):
        from . import events
        w = self.win.world
        self.tree.clear()
        if not w:
            self.info.setText("Open a world.")
            return
        self.ev = events.analyse(w, self._load_anims(force))
        ev = self.ev
        self.info.setText("When a level is beaten, every path it opens that has a <b>model named like the path</b> "
                          "plays that model's animation of the same name: bridges rise, steps drop in. %d path "
                          "animations, %d ambient, %d event animations. Double-click to preview." % (
                              len(ev["paths"]), len(ev["ambient"]), len(ev["events"])))
        top = QtWidgets.QTreeWidgetItem(["Path appear animations (%d)" % len(ev["paths"])])
        for p in ev["paths"]:
            by = ", ".join("%s (%s)" % o for o in p["opened_by"]) or "—"
            notes = ("copy of %s, added on export" % p["pending"]) if p["pending"] else (
                ("new: %s, added on export" % p["make"]) if p.get("make") else "; ".join(p["issues"]))
            extras = (" + " + ", ".join(e[1:] for e in p["extras"])) if p["extras"] else ""
            it = QtWidgets.QTreeWidgetItem([("🎬 " if p["skeletal"] else "⚠ ") + p["path"], by,
                                            ("%d frames%s%s" % (p["frames"], " loop" if p["loop"] else "", extras))
                                            if p["skeletal"] else "", notes])
            it.setData(0, QtCore.Qt.UserRole, ("path", p))
            it.setToolTip(3, notes)
            top.addChild(it)
        self.tree.addTopLevelItem(top)
        for title, key in (("Ambient (looping)", "ambient"), ("Event animations", "events")):
            t = QtWidgets.QTreeWidgetItem(["%s (%d)" % (title, len(ev[key]))])
            for a in ev[key]:
                it = QtWidgets.QTreeWidgetItem([a["name"], "", "%s frames%s" % (a["frames"], " loop" if a["loop"] else ""),
                                                "texture/colour animation" if a.get("material") else
                                                ("model " + a["name"] if a["model"] else "")])
                it.setData(0, QtCore.Qt.UserRole, (key, a))
                t.addChild(it)
            self.tree.addTopLevelItem(t)
        self.tree.expandAll()
        for c in range(4):
            self.tree.resizeColumnToContents(c)

    def _current(self):
        it = self.tree.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it else None

    def _selected(self):
        cur = self._current()
        is_path = bool(cur and cur[0] == "path")
        self.play_btn.setEnabled(bool(cur) and (not is_path or cur[1]["skeletal"]))
        self.copy_btn.setEnabled(is_path and cur[1]["skeletal"] and not cur[1]["pending"] and not cur[1].get("make"))
        self.remove_btn.setEnabled(is_path and bool(cur[1]["pending"] or cur[1].get("make")))
        self.keys_btn.setEnabled(is_path and bool(cur[1].get("make")))
        self.remove_btn.setText("Remove new animation" if is_path and cur[1].get("make") else "Remove copy")
        if is_path:
            p = cur[1]
            ends = rules.route_ends(p["path"])
            self.detail.setText("%s: the road from %s to %s. %s" % (
                p["path"], ends[0], ends[1], "Opened by " + ", ".join("%s's %s exit" % o for o in p["opened_by"])
                if p["opened_by"] else "Not opened by a point of this map."))
            if ends and self.win.world.position(ends[0]) is not None:
                self.win.select_point(ends[0])
        else:
            self.detail.setText("")

    def preview(self):
        cur = self._current()
        if not cur:
            return
        anims = self._load_anims()
        if anims is None:
            QtWidgets.QMessageBox.information(self, "Preview", "Previews need the model writer (.NET).")
            return
        kind, d = cur
        name = d["path"] if kind == "path" else d["name"]
        model = d.get("pending") or name
        frames = d.get("frames") or 0
        if kind == "path" and (d.get("pending") or d.get("make")):
            self.win.preview_pending(name, frames)
            return
        self.win.tabs.setCurrentIndex(1)
        if kind == "path":
            self.win.view.preview_anim(anims, model, frames)
            self.win.statusBar().showMessage("Playing %s (%d frames)%s." % (
                model, frames, (" — the copy for %s will play the same, moved" % name) if d.get("pending") else ""), 6000)
        else:
            self.win.view._preview = None
            self.win.view.play_animations(anims)

    def copy_dialog(self):
        cur = self._current()
        if not cur or cur[0] != "path":
            return
        from . import events
        src = cur[1]["path"]
        free = self.ev["free_paths"] if self.ev else []
        d = QtWidgets.QDialog(self); d.setWindowTitle("Copy %s's appear animation" % src)
        f = QtWidgets.QFormLayout(d)
        note = QtWidgets.QLabel("The model and its animations are copied under the new path's name, so they play when "
                                "that path opens. It's moved by the offset between the two paths' first points; adjust "
                                "it if needed. Added when you export.")
        note.setWordWrap(True)
        f.addRow(note)
        target = QtWidgets.QComboBox(); target.setEditable(True)
        for p in free:
            target.addItem(p)
        f.addRow("New path", target)
        spins = [spin() for _ in range(3)]
        row = QtWidgets.QHBoxLayout()
        for sp in spins:
            row.addWidget(sp)
        f.addRow("Offset X / Y / Z", row)

        def upd():
            off = events.copy_offset(self.win.world, src, target.currentText().strip())
            for sp, v in zip(spins, off):
                sp.setValue(v)
        target.currentTextChanged.connect(lambda _t: upd())
        upd()
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        f.addRow(bb)
        if not free:
            target.setPlaceholderText("R + from + to (a path some point opens)")
        if d.exec_():
            tgt = target.currentText().strip()
            if not rules.route_ends(tgt):
                QtWidgets.QMessageBox.information(self, "Copy", "%r is not a path name (R + 4 + 4 characters)." % tgt)
                return
            self.win.add_route_copy(src, tgt, [sp.value() for sp in spins])

    def remove_copy(self):
        cur = self._current()
        if cur and cur[0] == "path" and cur[1]["pending"]:
            self.win.remove_route_copy(cur[1]["path"])
        elif cur and cur[0] == "path" and cur[1].get("make"):
            self.win.remove_route_make(cur[1]["path"])

    def new_dialog(self):
        """Pick a path, the object(s) that should appear, and how."""
        from . import events
        w = self.win.world
        if not w:
            return
        try:
            tm, shapes = events.terrain_shapes(w)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "New appear animation", "The world's shapes could not be read:\n%s" % e)
            return
        d = QtWidgets.QDialog(self); d.setWindowTitle("New appear animation"); d.resize(560, 620)
        f = QtWidgets.QFormLayout(d)
        note = QtWidgets.QLabel("The objects you tick move out of the world's ground model into a model named like the "
                                "path, with an animation of the same name. The game hides them until the path opens, then "
                                "plays it. Added when you export.")
        note.setWordWrap(True)
        f.addRow(note)
        target = QtWidgets.QComboBox(); target.setEditable(True)
        for pth in (self.ev or {}).get("free_paths", []):
            target.addItem(pth)
        f.addRow("Path", target)
        search = QtWidgets.QLineEdit(); search.setPlaceholderText("Filter objects (e.g. dokan, kinoko, bridge)…")
        f.addRow("Objects", search)
        lst = QtWidgets.QListWidget()
        used_objs = {o for m in w.route_makes for o in m.get("objects", [])}
        for name, path, lo, hi in events.added_objects(w):         # objects added from DAE/FBX first
            it = QtWidgets.QListWidgetItem("➕ %s   (added from %s)" % (name, Path(path).name))
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable); it.setCheckState(QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, ("object", name, lo, hi))
            it.setToolTip("Added in World models; merged into the ground model on export")
            if name in used_objs:
                it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEnabled)
                it.setText(it.text() + "   — already appears with another path")
            lst.addItem(it)
        for name, mat, lo, hi in shapes:
            it = QtWidgets.QListWidgetItem("%s   (%s)" % (name.split("__")[0], mat or name.split("__")[-1]))
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable); it.setCheckState(QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, ("shape", name, lo, hi))
            lst.addItem(it)
        search.textChanged.connect(lambda t: [lst.item(i).setHidden(t.lower() not in lst.item(i).text().lower())
                                              for i in range(lst.count())])
        f.addRow(lst)
        preset = QtWidgets.QComboBox()
        for k, txt in events.PRESETS.items():
            preset.addItem("%s — %s" % (k.capitalize(), txt), k)
        preset.addItem("Custom — your own keyframes (move, turn, scale)", "custom")
        keys_btn = QtWidgets.QPushButton("Keyframes…")
        keys_btn.setToolTip("Edit the animation key by key: move, turn and scale over time")
        er = QtWidgets.QHBoxLayout(); er.addWidget(preset, 1); er.addWidget(keys_btn)
        f.addRow("Effect", er)
        custom = {"keys": None, "interp": {}}
        frames = QtWidgets.QSpinBox(); frames.setRange(10, 600); frames.setValue(90); frames.setSuffix(" frames (60 = 1 s)")
        f.addRow("Length", frames)
        amount = spin(0, 5000); amount.setValue(200)
        f.addRow("Height (rise / drop)", amount)
        info = QtWidgets.QLabel(""); info.setStyleSheet("color: #9fb3c8")
        f.addRow(info)

        def picked():
            return [lst.item(i).data(QtCore.Qt.UserRole) for i in range(lst.count())
                    if lst.item(i).checkState() == QtCore.Qt.Checked]

        def upd(*_):
            sel = picked()
            if sel:
                piv, h = events.pivot_of([(lo, hi) for _k, _n, lo, hi in sel])
                info.setText("%d object(s); anchored at their bottom centre %.0f, %.0f, %.0f (%.0f tall)." % (
                    len(sel), piv[0], piv[1], piv[2], h))
                if amount.value() == 200:
                    amount.setValue(max(60.0, round(h * 1.2)))
            else:
                info.setText("Tick the object(s) that should appear.")
        lst.itemChanged.connect(upd)
        upd()

        def item_now():
            sel = picked()
            if not sel:
                return None
            piv, _h = events.pivot_of([(lo, hi) for _k, _n, lo, hi in sel])
            it = {"target": target.currentText().strip(), "source_model": tm,
                  "shapes": [n for k, n, _lo, _hi in sel if k == "shape"],
                  "objects": [n for k, n, _lo, _hi in sel if k == "object"],
                  "preset": preset.currentData(), "frames": frames.value(), "amount": amount.value(), "pivot": piv}
            if it["preset"] == "custom":
                it["keys"] = custom["keys"] or events.preset_keys("rise", it["frames"], it["amount"])
                it["interp"] = dict(custom["interp"])
            return it

        def edit_keys():
            base = preset.currentData()
            keys = custom["keys"] if base == "custom" and custom["keys"] else events.preset_keys(
                "rise" if base == "custom" else base, frames.value(), amount.value())

            def pv(k, n, ip):
                it = item_now()
                if not it or not rules.route_ends(it["target"]):
                    QtWidgets.QMessageBox.information(d, "Preview", "Choose the path and tick the objects first.")
                    return
                it.update(preset="custom", keys=k, frames=n, interp=ip)
                self.win.preview_make_item(it)
            ed = keyedit.KeyframeEditor(keys, frames.value(), custom["interp"], pv, d)
            self._key_editor = ed
            if ed.exec_():
                custom["keys"], custom["interp"] = ed.keys, dict(ed.interp)
                frames.setValue(ed.frames)
                preset.setCurrentIndex(preset.findData("custom"))
        keys_btn.clicked.connect(edit_keys)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        f.addRow(bb)
        self._new_dialog = d
        if not d.exec_():
            return
        sel = picked()
        tgt = target.currentText().strip()
        if not sel or not rules.route_ends(tgt):
            QtWidgets.QMessageBox.information(self, "New appear animation", "Choose a path (R + 4 + 4 characters) and "
                                              "at least one object.")
            return
        self.win.add_route_make(item_now())

    def edit_keys(self):
        """The keyframe editor on a new appear animation already in the list (undoable)."""
        from . import events
        cur = self._current()
        if not cur or cur[0] != "path" or not cur[1].get("make"):
            return
        w = self.win.world
        mk = next((m for m in w.route_makes if m["target"] == cur[1]["path"]), None)
        if mk is None:
            return
        keys = mk.get("keys") or events.preset_keys(mk["preset"], mk["frames"], mk["amount"])

        def pv(k, n, ip):
            self.win.preview_make_item(dict(mk, preset="custom", keys=k, frames=n, interp=ip))
        ed = keyedit.KeyframeEditor(keys, mk["frames"], mk.get("interp"), pv, self)
        self._key_editor = ed
        if ed.exec_():
            self.win.replace_route_make(mk, dict(mk, preset="custom", keys=ed.keys, frames=ed.frames,
                                                 interp=dict(ed.interp)))


# --------------------------------------------------------------------------------------------- properties panel
class PropertiesPanel(QtWidgets.QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        host = QtWidgets.QWidget()
        self.setWidget(host)
        lay = QtWidgets.QVBoxLayout(host)
        self.title = QtWidgets.QLabel("Nothing selected")
        f = self.title.font(); f.setPointSize(f.pointSize() + 3); f.setBold(True); self.title.setFont(f)
        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)

        self.pos_box = QtWidgets.QGroupBox("Position (the point's bone in the map-point model)")
        pf = QtWidgets.QFormLayout(self.pos_box)
        self.px, self.py, self.pz = spin(), spin(), spin()
        for lab, sp in (("X", self.px), ("Y", self.py), ("Z", self.pz)):
            pf.addRow(lab, sp)
            sp.editingFinished.connect(self._position_edited)
        self.snap_btn = QtWidgets.QPushButton("Snap Y to terrain")
        self.snap_btn.clicked.connect(self._snap)
        pf.addRow(self.snap_btn)
        lay.addWidget(self.pos_box)

        self.table_box = QtWidgets.QGroupBox("What this point does")
        tf = QtWidgets.QFormLayout(self.table_box)
        self.meaning = QtWidgets.QLabel()
        self.meaning.setWordWrap(True)
        self.meaning.setStyleSheet("color: #cbd5e1")
        tf.addRow(self.meaning)
        self.yaz_btn = QtWidgets.QPushButton("Open the level in Yaz0moto…")
        self.yaz_btn.setToolTip("Edit this point's level in Yaz0moto, e.g. to give it a secret goal: a Goal Pole with "
                                "'Secret Exit' (nybble 5) set. Koopatlas DX reads the goals back when you save there.")
        self.yaz_btn.clicked.connect(self._open_yaz0moto)
        tf.addRow(self.yaz_btn)
        self.dir_btn = QtWidgets.QPushButton("Stick directions…")
        self.dir_btn.setToolTip("Which road each stick direction (↑ → ↓ ←) takes from this point; at a junction every "
                                "road needs its own direction")
        self.dir_btn.clicked.connect(lambda: self.current and self.win.directions_dialog(self.current[1]))
        self.branch_btn = QtWidgets.QPushButton("Branch to another point (junction)…")
        self.branch_btn.setToolTip("A new road from this point to another, leaving in a free direction")
        self.branch_btn.clicked.connect(lambda: self.current and self.win.branch_from(self.current[1]))
        brow = QtWidgets.QWidget(); bl = QtWidgets.QHBoxLayout(brow); bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.dir_btn); bl.addWidget(self.branch_btn)
        tf.addRow(brow)
        self.cells, self.counters, self.row_widgets = {}, {}, {}
        LABELS = {2: "Flags", 3: "Opens points", 4: "Opens paths", 5: "Shows objects",
                  6: "Opens points", 7: "Opens paths", 8: "Shows objects"}
        for col in range(2, 9):
            if col in (3, 6):
                head = QtWidgets.QLabel("<b>%s</b>" % ("When the level is beaten (normal exit)" if col == 3 else
                                                       "When the secret exit is found"))
                btn = QtWidgets.QPushButton("+ Unlock next point...")
                btn.setToolTip("Click the point this exit should open: it is added to Opens points, the path to it to "
                               "Opens paths, and a road is made if there is none.")
                btn.clicked.connect(lambda _=False, c=col: self._unlock_next(c))
                hrow = QtWidgets.QWidget(); hl = QtWidgets.QHBoxLayout(hrow); hl.setContentsMargins(0, 6, 0, 0)
                hl.addWidget(head, 1); hl.addWidget(btn)
                tf.addRow(hrow)
                self.row_widgets[("head", col)] = (hrow,)
            ed = QtWidgets.QLineEdit()
            ed.editingFinished.connect(lambda c=col: self._cell_edited(c))
            self.cells[col] = ed
            roww = QtWidgets.QWidget(); row = QtWidgets.QHBoxLayout(roww); row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(ed, 1)
            if col in rules.COLUMN_LIMITS:
                cnt = QtWidgets.QLabel()
                cnt.setMinimumWidth(34)
                self.counters[col] = cnt
                row.addWidget(cnt)
            if col == 2:
                b = QtWidgets.QPushButton("Choose…"); b.clicked.connect(self._choose_flags); row.addWidget(b)
            elif col in (3, 6):
                b = QtWidgets.QPushButton("Pick…"); b.setToolTip("Click a point on the map to add it")
                b.clicked.connect(lambda _=False, c=col: self._pick_for(c)); row.addWidget(b)
            elif col in (4, 7):
                b = QtWidgets.QPushButton("Pick…"); b.setToolTip("Click the point the path leads to: adds R + this point + that point")
                b.clicked.connect(lambda _=False, c=col: self._pick_for(c)); row.addWidget(b)
            lab = QtWidgets.QLabel(LABELS[col])
            tf.addRow(lab, roww)
            self.row_widgets[col] = (lab, roww)
            if col in (3, 4, 6, 7):
                ed.setPlaceholderText("game value, e.g. %s" % ("W104,W105" if col in (3, 6) else "RW103W104"))
                cards = UnlockCards(self, col)
                if not hasattr(self, "cards"):
                    self.cards = {}
                self.cards[col] = cards
                tf.addRow("", cards)
                self.row_widgets[col] = (lab, roww, cards)
            if col == 2:
                self.flag_summary = QtWidgets.QLabel()
                self.flag_summary.setWordWrap(True)
                self.flag_summary.setStyleSheet("color: #9fb3c8")
                tf.addRow("", self.flag_summary)
        self.advanced = QtWidgets.QCheckBox("Show all columns (appear animations, secret exit)")
        self.advanced.toggled.connect(lambda _on: self._apply_row_visibility())
        tf.addRow(self.advanced)
        self.cells[2].setToolTip("Comma list of flags; use Choose… for the full list with explanations.")
        self.cells[3].setToolTip("Points opened by the normal exit (the game keeps 4).")
        self.cells[4].setToolTip("Paths opened by the normal exit: R + this point + target (the game keeps 16).")
        self.cells[5].setToolTip("Names of map objects whose appear animation plays (the game keeps 2).")
        self.cells[6].setToolTip("Points opened by the secret exit (the game keeps 4).")
        self.cells[7].setToolTip("Paths opened by the secret exit (the game keeps 16).")
        self.cells[8].setToolTip("Map objects that appear on the secret exit (the game keeps 2).")
        self.segments_label = QtWidgets.QLabel()
        self.segments_label.setWordWrap(True)
        self.segments_label.setStyleSheet("color: #9fb3c8")
        tf.addRow(self.segments_label)
        lay.addWidget(self.table_box)

        self.mario_box = QtWidgets.QGroupBox("Camera while Mario stands here")
        mf = QtWidgets.QFormLayout(self.mario_box)
        self.mx, self.my, self.mz = spin(), spin(), spin()
        self.mflags = QtWidgets.QSpinBox(); self.mflags.setRange(0, 2 ** 31 - 1); self.mflags.setKeyboardTracking(False)
        for lab, sp in (("X", self.mx), ("Y", self.my), ("Z", self.mz)):
            mf.addRow(lab, sp)
            sp.editingFinished.connect(self._mario_edited)
        mf.addRow("Flags (unknown)", self.mflags)
        self.mflags.editingFinished.connect(self._mario_edited)
        self.mario_note = QtWidgets.QLabel("Where the camera sits relative to Mario while he is on this point (found by "
                                           "testing in game). Drag the cyan camera marker in either view (Shift-drag in 3D "
                                           "changes its height). The flags value is not understood yet.")
        self.cam_view_btn = QtWidgets.QPushButton("View from this camera (3D)")
        self.cam_view_btn.clicked.connect(self._view_from_camera)
        self.mario_note.setWordWrap(True)
        self.mario_note.setStyleSheet("color: #8a94a6")
        mf.addRow(self.mario_note)
        mf.addRow(self.cam_view_btn)
        lay.addWidget(self.mario_box)

        self.route_box = QtWidgets.QGroupBox("Route")
        rf = QtWidgets.QFormLayout(self.route_box)
        self.route_from, self.route_to = QtWidgets.QLabel(), QtWidgets.QLabel()
        self.route_type = QtWidgets.QComboBox(); self.route_type.setEditable(True)
        self.route_type.activated.connect(self._route_edited)
        self.route_type.lineEdit().editingFinished.connect(self._route_edited)
        rf.addRow("From", self.route_from)
        rf.addRow("To", self.route_to)
        rf.addRow("Type", self.route_type)
        self.climb_note = QtWidgets.QLabel()
        self.climb_note.setWordWrap(True)
        self.climb_note.setStyleSheet("color: #f59e0b")
        rf.addRow(self.climb_note)
        self.vertical_btn = QtWidgets.QPushButton("Make vertical (climbable)")
        self.vertical_btn.setToolTip("Moves one end (a bend or flag point if there is one) straight above or below "
                                     "the other, keeping its height: the game only climbs vertical routes.")
        self.vertical_btn.clicked.connect(lambda: self.current and self.win.make_vertical(self.current[1]))
        rf.addRow(self.vertical_btn)
        self.unlock_host = QtWidgets.QWidget()
        self.unlock_form = QtWidgets.QFormLayout(self.unlock_host); self.unlock_form.setContentsMargins(0, 6, 0, 0)
        rf.addRow(self.unlock_host)
        self.route_delete = QtWidgets.QPushButton("Delete this route")
        self.route_delete.clicked.connect(self._delete_route)
        rf.addRow(self.route_delete)
        lay.addWidget(self.route_box)
        lay.addStretch(1)
        self.current = None          # ("point", id) or ("route", id)
        self._loading = False
        self.show_nothing()

    def show_nothing(self):
        self.current = None
        if hasattr(self.win, "set_highlight"):
            self.win.set_highlight({})
        self.title.setText("Nothing selected")
        self.subtitle.setText("Click a point in the view or in the list.")
        for b in (self.pos_box, self.table_box, self.mario_box, self.route_box):
            b.setVisible(False)

    def show_point(self, pid):
        w = self.win.world
        self._loading = True
        self.current = ("point", pid)
        kind = point_kind(pid) if pid in w.point_ids else "Waypoint"
        name, label = self.win.names.point(pid) if self.win.names else (None, None)
        self.title.setText(pid)
        sub = "%s (%s)" % (kind, rules.CLASS_NAMES[rules.point_class(pid)])
        lf = rules.level_file(pid)
        if lf:
            sub += " · level file %s" % lf
        if name:
            sub += " · " + name
        self.subtitle.setText(sub)
        pos = w.position(pid)
        self.pos_box.setVisible(pos is not None)
        if pos:
            self.px.setValue(pos[0]); self.py.setValue(pos[1]); self.pz.setValue(pos[2])
        row = w.row_of(pid)
        self.table_box.setVisible(row is not None)
        if row is not None:
            for col, ed in self.cells.items():
                ed.setText(row[col] if col < len(row) else "")
            from . import pointhelp
            import html
            text = html.escape(pointhelp.explain(pid, rules.split(row[2]), name)).replace(chr(10), '<br>')
            ci = rules.course_index(pid)
            wn = self.win.world.number
            if ci >= 0 and ci != 37 and wn in rules.COURSE_SLOTS:
                ok_n, ok_s = rules.has_slot(wn, ci), rules.has_slot(wn, ci, True)
                text += ("<br><span style='color:%s'>Save: normal exit %s · secret exit %s</span>" % (
                    "#4ade80" if ok_n else "#f87171", "tracked ✓" if ok_n else "NOT tracked (its paths never open)",
                    "tracked ✓" if ok_s else "not tracked"))
            lf = self.win.level_path(pid)
            self.yaz_btn.setVisible(rules.level_file(pid) is not None)
            if lf is not None:
                try:
                    g = rules.level_goals(lf)
                    text += "<br>Level goals (%s): %d regular, %d secret%s" % (
                        html.escape(lf.name), g["normal"], g["secret"],
                        "" if g["secret"] or not rules.paths(row[7] if len(row) > 7 else "") else
                        " <span style='color:#f87171'>— the secret exit can't be found</span>")
                except Exception as e:
                    text += "<br>Level %s can't be read (%s)" % (html.escape(lf.name), html.escape(str(e)))
            roads = directions.roads_at(w, pid)
            if len(roads) >= 2:
                table = directions.compass(w, pid)
                parts = []
                for d in directions.DIRS + (None,):
                    for rid in table[d]:
                        clash = d is not None and len(table[d]) > 1
                        parts.append("<span style='color:%s'>%s %s</span>" % (
                            "#f87171" if clash else "#9fb3c8", directions.ARROWS[d],
                            html.escape(directions.far_end(w, pid, rid))))
                text += "<br>Stick: " + " · ".join(parts) + (
                    " <span style='color:#f87171'>(two roads the same way: only one works)</span>"
                    if any(len(v) > 1 for k, v in table.items() if k) else "")
            self.meaning.setText(text)
            self._update_flag_summary()
            self._apply_row_visibility()
            self._refresh_cards()
            self._update_counters()
            self._show_segments(pid, row)
        else:
            self.win.set_highlight({})
        rec = w.mario_record(pid)
        self.mario_box.setVisible(rec is not None)
        if rec is not None:
            self.mario_box.setTitle("Camera while Mario stands here (MarioOffsetDataW%d, slot %d)" % (w.number, w.mario.find(w.block, pid)))
            self.mx.setValue(rec.vector[0]); self.my.setValue(rec.vector[1]); self.mz.setValue(rec.vector[2])
            self.mflags.setValue(rec.flags)
        self.route_box.setVisible(False)
        self._loading = False

    def show_route(self, rid):
        w = self.win.world
        self._loading = True
        self.current = ("route", rid)
        r = next(x for x in w.routes if x["id"] == rid)
        self.title.setText(rid)
        from . import routeactions as RA
        from .routeui import action_icon
        act = RA.action(r["type"])
        self.subtitle.setText("Route · %s" % (act.label if RA.known(r["type"]) else (r["type"] or "no type") + " (walks as road)"))
        self.route_type.setToolTip(act.description)
        for b in (self.pos_box, self.table_box, self.mario_box):
            b.setVisible(False)
        self.route_box.setVisible(True)
        here = set(w.point_ids) | set(w.bones)
        self.route_from.setText(r["from"] + ("" if r["from"] in here else "  (in another world)"))
        self.route_to.setText(r["to"] + ("" if r["to"] in here else "  (in another world)"))
        types = list(ROUTE_TYPES_KNOWN) + sorted({x["type"] for x in w.routes if x["type"] and x["type"] not in ROUTE_TYPES_KNOWN})
        self.route_type.clear()
        self.route_type.setIconSize(QtCore.QSize(20, 20))
        for t in types:
            if RA.known(t):
                self.route_type.addItem(action_icon(t, 20), "%s  (%s)" % (RA.action(t).label, t), t)
                self.route_type.setItemData(self.route_type.count() - 1, RA.action(t).description, QtCore.Qt.ToolTipRole)
            else:
                self.route_type.addItem(t, t)
        idx = types.index(r["type"]) if r["type"] in types else -1
        self.route_type.setCurrentIndex(idx)
        probs = self.win.climb_problems(rid)
        self.climb_note.setText(chr(10).join(probs))
        self.climb_note.setVisible(bool(probs))
        self.vertical_btn.setVisible(any("not vertical" in t for t in probs))
        self._fill_unlocks(rid)
        self._loading = False

    def _fill_unlocks(self, rid):
        """'Opens when' for every unlock path this road is on: an expression like '1-2 secret OR 1-3'."""
        from . import unlocks
        w = self.win.world
        while self.unlock_form.rowCount():
            self.unlock_form.removeRow(0)
        on = unlocks.paths_through(rules.map_paths(w.point_rows, w.route_rows), rid)
        head = QtWidgets.QLabel("<b>Opens when…</b> (levels joined by OR; add 'secret' for a secret exit)")
        head.setWordWrap(True)
        self.unlock_form.addRow(head)
        if not on:
            self.unlock_form.addRow(QtWidgets.QLabel("This road is on no unlock path yet: it is open from the start."))
        for path in on:
            ed = QtWidgets.QLineEdit(unlocks.describe(w.point_rows, path))
            ed.setPlaceholderText("e.g. 1-2 OR 1-3 secret (empty: nothing opens it)")
            ed.setToolTip("Who opens %s. The game opens a path when any one of these is beaten." % path)
            ed.editingFinished.connect(lambda p=path, e=ed: self.win.set_unlock(p, e.text()))
            self.unlock_form.addRow(path, ed)

    def _refresh_cards(self):
        if not self.current or self.current[0] != "point" or not hasattr(self, "cards"):
            return
        for col, cards in self.cards.items():
            cards.refresh(self.current[1], rules.split(self.cells[col].text()), self.win)

    def _remove_entry(self, col, entry):
        items = [x for x in rules.split(self.cells[col].text()) if x != entry]
        self.cells[col].setText(",".join(items))
        self._cell_edited(col)

    def _update_flag_summary(self):
        from . import flaghelp
        fl = rules.split(self.cells[2].text())
        unknown = [f for f in fl if f not in rules.KNOWN_FLAGS]
        self.flag_summary.setText((flaghelp.summary(fl) if fl else "No flags: a plain point.") +
                                  ("  ·  ignored by the game: %s" % ", ".join(unknown) if unknown else ""))

    def _apply_row_visibility(self):
        pid = self.current[1] if self.current and self.current[0] == "point" else ""
        has_level = rules.level_file(pid) is not None or rules.point_class(pid) == 3
        adv = self.advanced.isChecked()
        filled = {c: bool(self.cells[c].text().strip()) for c in self.cells}
        show = has_level or filled[3] or filled[4] or adv          # the normal exit: levels, or points that have one
        for col in (3, 4):
            for w_ in self.row_widgets[col]:
                w_.setVisible(show)
        self.row_widgets[("head", 3)][0].setVisible(show)
        secret = adv or filled[6] or filled[7] or filled[8]
        for col in (6, 7):
            for w_ in self.row_widgets[col]:
                w_.setVisible(secret)
        self.row_widgets[("head", 6)][0].setVisible(secret or has_level)
        for col in (5, 8):
            for w_ in self.row_widgets[col]:
                w_.setVisible(adv or filled[col])

    def _open_yaz0moto(self):
        if not self.current or self.current[0] != "point":
            return
        self.win.open_in_yaz0moto(self.current[1])

    def _unlock_next(self, col):
        """One click: the exit opens that point and the path to it; a road is made when the two are not joined."""
        if not self.current or self.current[0] != "point":
            return
        pid = self.current[1]
        if col == 6:
            for c in (6, 7):
                for w_ in self.row_widgets[c]:
                    w_.setVisible(True)

        def picked(target):
            if target == pid:
                return
            w = self.win.world
            path = "R%s%s" % (pid, target)
            for c, entry in ((col, target), (col + 1, path)):
                items = rules.split(self.cells[c].text())
                if entry not in items:
                    self.cells[c].setText(",".join(items + [entry]))
                    self._cell_edited(c)
            if w.path_chain(path) is None and w.route_row(path) is None and w.route_row("R%s%s" % (target, pid)) is None:
                self.win.add_route(path)
            self.win.statusBar().showMessage("%s now opens %s (path %s)." % (pid, target, path), 8000)
        self.win.start_pick("Click the point this exit opens (Esc cancels).", picked)

    def _update_counters(self):
        for col, lab in self.counters.items():
            n = len(rules.split(self.cells[col].text()))
            limit = rules.COLUMN_LIMITS[col]
            lab.setText("%d/%d" % (n, limit))
            lab.setStyleSheet("color: %s" % ("#f87171" if n > limit else "#9fb3c8"))

    def _show_segments(self, pid, row):
        w = self.win.world
        colors, lines = {}, []
        for col, label, color in ((4, "Normal exit", (0.35, 1.0, 0.45)), (7, "Secret exit", (1.0, 0.35, 0.85))):
            segs_all = []
            for path in rules.paths(row[col] if col < len(row) else ""):
                chain = w.path_chain(path)
                if chain is None:
                    lines.append("%s: %s has no chain of segments" % (label, path))
                    continue
                segs_all += chain
                for sgm in chain:
                    colors.setdefault(sgm, color)
            if segs_all:
                lines.append("%s opens %d segments (highlighted %s)." % (label, len(segs_all), "green" if col == 4 else "pink"))
        self.segments_label.setText("\n".join(lines))
        self.win.set_highlight(colors)

    def _choose_flags(self):
        if not self.current or self.current[0] != "point":
            return
        pid = self.current[1]
        d = FlagsDialog(self, pid, self.cells[2].text(), self.win.world.id if self.win.world else None)
        if d.exec_():
            self.cells[2].setText(d.value())
            self._cell_edited(2)

    def _pick_for(self, col):
        if not self.current or self.current[0] != "point":
            return
        pid = self.current[1]

        def picked(target):
            entry = target if col in (3, 6) else "R%s%s" % (pid, target)
            items = rules.split(self.cells[col].text())
            if entry in items:
                return
            self.cells[col].setText(",".join(items + [entry]))
            self._cell_edited(col)
        what = "a point to open" if col in (3, 6) else "the point the path leads to"
        self.win.start_pick("Click %s (Esc cancels)." % what, picked)

    def _view_from_camera(self):
        if self.current and self.current[0] == "point":
            self.win.tabs.setCurrentIndex(1)
            if not self.win.view.view_from_camera(self.current[1]):
                QtWidgets.QMessageBox.information(self, "No camera offset", "This point's camera offset is zero or missing.")

    def _delete_route(self):
        if self.current and self.current[0] == "route":
            self.win.delete_route(self.current[1])

    # ---- edits -----------------------------------------------------------------------------------------------------
    def _position_edited(self):
        if self._loading or not self.current or self.current[0] != "point":
            return
        pid = self.current[1]
        new = (self.px.value(), self.py.value(), self.pz.value())
        old = self.win.world.position(pid)
        if old and any(abs(a - b) > 1e-4 for a, b in zip(old, new)):
            self.win.move_point(pid, new)

    def _snap(self):
        if not self.current or self.current[0] != "point":
            return
        pid = self.current[1]
        x, y, z = self.win.world.position(pid)
        h = self.win.view.terrain_height(x, z)
        if h is None:
            QtWidgets.QMessageBox.information(self, "No terrain here", "There is no terrain directly above or below %s." % pid)
            return
        self.win.move_point(pid, (x, h, z))

    def _cell_edited(self, col):
        if col == 2 and hasattr(self, "flag_summary"):
            self._update_flag_summary()
        if col in (3, 4, 6, 7) and hasattr(self, "cards"):
            QtCore.QTimer.singleShot(0, self._refresh_cards)
        if self._loading or not self.current or self.current[0] != "point":
            return
        pid = self.current[1]
        row = self.win.world.row_of(pid)
        old = row[col] if col < len(row) else ""
        new = self.cells[col].text().strip()
        if new != old:
            self.win.set_cell(pid, col, old, new)

    def _mario_edited(self):
        if self._loading or not self.current or self.current[0] != "point":
            return
        pid = self.current[1]
        rec = self.win.world.mario_record(pid)
        if rec is None:
            return
        new_vec = (self.mx.value(), self.my.value(), self.mz.value())
        new_flags = self.mflags.value()
        if any(abs(a - b) > 1e-6 for a, b in zip(rec.vector, new_vec)) or new_flags != rec.flags:
            self.win.set_mario(pid, rec.vector, rec.flags, new_vec, new_flags)

    def _route_edited(self, *_):
        if self._loading or not self.current or self.current[0] != "route":
            return
        rid = self.current[1]
        i = self.route_type.currentIndex()
        text = self.route_type.currentText().strip()
        new = self.route_type.itemData(i) if i >= 0 and self.route_type.itemText(i) == text else text.split("  (")[0]
        old = self.win.world.route_row(rid)[1]
        if new and new != old:
            self.win.set_route(rid, old, new)


# --------------------------------------------------------------------------------------------- offset tables
class OffsetTablesDialog(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        w = win.world
        self.setWindowTitle("Offset tables — world %d" % w.number)
        self.resize(820, 620)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs)
        info = QtWidgets.QLabel(
            "MarioOffsetDataW%d: 256 records; slots 0–127 belong to the first archive, 128–255 to the 'b' archive, in "
            "point-table order. The meaning of the vector and flags is not confirmed yet. LookOffsetDataW%d is shown "
            "as raw numbers (its layout is not known)." % (w.number, w.number))
        info.setWordWrap(True)
        lay.insertWidget(0, info)

        self.mario_table = QtWidgets.QTableWidget()
        if w.mario:
            t = w.mario
            self.mario_table.setColumnCount(6)
            self.mario_table.setHorizontalHeaderLabels(["Block", "Point", "X", "Y", "Z", "Flags"])
            self.mario_table.setRowCount(offsets.SLOTS)
            for i, rec in enumerate(t.records):
                block = "a/only" if i < offsets.BLOCK else "b"
                cells = [block, rec.name] + ["%g" % v for v in rec.vector] + [str(rec.flags)]
                for c, text in enumerate(cells):
                    item = QtWidgets.QTableWidgetItem(text)
                    if c < 2 or rec.unused:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    if rec.unused:
                        item.setForeground(QtGui.QBrush(QtGui.QColor("#6b7280")))
                    self.mario_table.setItem(i, c, item)
            self.mario_table.setVerticalHeaderLabels([str(i) for i in range(offsets.SLOTS)])
            tabs.addTab(self.mario_table, "MarioOffsetDataW%d" % w.number)
        else:
            tabs.addTab(QtWidgets.QLabel("MarioOffsetDataW%d.bin was not found next to the world archive or in the "
                                         "ROMFS folder set in Preferences." % w.number), "MarioOffset")
        self.look_table = QtWidgets.QTableWidget()
        if w.look:
            cols = 6
            rows = (len(w.look.values) + cols - 1) // cols
            self.look_table.setColumnCount(cols)
            self.look_table.setRowCount(rows)
            self.look_table.setHorizontalHeaderLabels(["+%d" % c for c in range(cols)])
            self.look_table.setVerticalHeaderLabels([str(r * cols) for r in range(rows)])
            for i, v in enumerate(w.look.values):
                item = QtWidgets.QTableWidgetItem("%g" % v)
                if v != 0.0:
                    item.setBackground(QtGui.QBrush(QtGui.QColor("#3b3f1d")))
                self.look_table.setItem(i // cols, i % cols, item)
            tabs.addTab(self.look_table, "LookOffsetDataW%d" % w.number)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if w.mario and w.mario.block_records(w.block):
            self.mario_table.scrollToItem(self.mario_table.item(w.block * offsets.BLOCK, 0))

    def _read_tables(self):
        w = self.win.world
        mario = look = None
        if w.mario:
            mario = offsets.MarioOffsetTable([r.copy() for r in w.mario.records])
            for i, rec in enumerate(mario.records):
                if rec.unused:
                    continue
                try:
                    vec = tuple(float(self.mario_table.item(i, c).text()) for c in (2, 3, 4))
                    flags = int(self.mario_table.item(i, 5).text())
                except ValueError:
                    raise WorldError("Slot %d (%s): numbers only." % (i, rec.name))
                mario.records[i] = offsets.MarioRecord(rec.name, vec, flags)
            mario.to_bytes()                                  # validates
        if w.look:
            vals = []
            for i in range(len(w.look.values)):
                try:
                    vals.append(float(self.look_table.item(i // 6, i % 6).text()))
                except ValueError:
                    raise WorldError("LookOffset value %d: numbers only." % i)
            look = offsets.LookOffsetTable(vals)
        return mario, look

    def apply(self):
        try:
            mario, look = self._read_tables()
        except (WorldError, offsets.OffsetError) as e:
            QtWidgets.QMessageBox.warning(self, "Not applied", str(e))
            return
        self.win.replace_tables(mario, look)
        self.accept()


# --------------------------------------------------------------------------------------------- ROMFS dialog
class RomfsDialog(QtWidgets.QDialog):
    """Ask for the dumped romfs folder. Accepts romfs itself, its parent, or its Model folder."""

    def __init__(self, parent, current="", language="USen", first_run=False):
        super().__init__(parent)
        self.setWindowTitle("Game files (ROMFS)")
        self.root = None
        self.language = language
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            ("Welcome to Koopatlas DX. " if first_run else "") +
            "Choose the <b>romfs</b> folder of your New Super Mario Bros. U Deluxe dump (it contains Model, Message, Course…). "
            "Koopatlas DX only reads from it; edits are exported to a separate mod folder.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit(current)
        self.path.setPlaceholderText(r"for example D:\Dumps\NSMBUDX\romfs")
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1); row.addWidget(browse)
        lay.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(QtWidgets.QLabel("Text language:"))
        self.lang = QtWidgets.QComboBox()
        lrow.addWidget(self.lang, 1)
        lay.addLayout(lrow)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("Skip (open files by hand)" if first_run else "Cancel")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.path.textChanged.connect(self._validate)
        self.resize(620, 220)
        self._validate()

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "romfs folder", self.path.text())
        if d:
            self.path.setText(d)

    def _validate(self):
        root = romfs.normalize(self.path.text().strip())
        ok = root is not None
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(ok)
        self.lang.clear()
        if ok:
            self.status.setText("<span style='color:#4ade80'>Found: %s</span><br>%s" % (root, romfs.describe(root)))
            langs = romfs.languages(root)
            self.lang.addItems(langs)
            if self.language in langs:
                self.lang.setCurrentText(self.language)
            elif "USen" in langs:
                self.lang.setCurrentText("USen")
        elif self.path.text().strip():
            self.status.setText("<span style='color:#f87171'>No world archives found here.</span> Pick the romfs folder "
                                "(it has a Model folder containing CS_W1.Nin_NX_NVN.szs).")
        else:
            self.status.setText("")
        self.lang.setEnabled(self.lang.count() > 0)
        return root

    def _accept(self):
        self.root = self._validate()
        if self.root:
            self.language = self.lang.currentText() or self.language
            self.accept()


# --------------------------------------------------------------------------------------------- main window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QtCore.QSettings("KoopatlasDX", "KoopatlasDX")
        self.world = None
        self.names = None
        self.lib_report = libs.setup()
        self.undo = QtWidgets.QUndoStack(self)
        # a bound method, not a lambda: PyQt drops it when the window is destroyed (a lambda fired on the deleted window)
        self.undo.cleanChanged.connect(self._update_title)
        self.setWindowTitle("Koopatlas DX")
        self.resize(1400, 860)

        self.scene = None
        self.view = Viewport()
        self.map = Map2D()
        for v in (self.view, self.map):
            v.routeClicked.connect(self.select_route)
            v.routeMenu.connect(self.route_menu)
        self.view.groundMenu.connect(self.ground_menu)
        self.view.pointMenu.connect(self.point_menu)
        for v in (self.view, self.map):
            v.pointClicked.connect(self.select_point)
            v.pointDragged.connect(self.move_point)
            v.cameraDragged.connect(self.move_camera)
            v.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 4000))
            v.camera_for = self._camera_offset
        self.maker = WorldMaker()
        self.maker.host = self
        self.maker.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.undo_group = QtWidgets.QUndoGroup(self)
        self.undo_group.addStack(self.undo)
        self.undo_group.addStack(self.maker.undo)
        self.undo_group.setActiveStack(self.undo)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.map, "2D map")
        self.tabs.addTab(self.view, "3D view")
        self.tabs.addTab(self.maker, "World Maker")
        self.tabs.setTabToolTip(2, "World Maker: make new worlds (2D tiles or your own 3D models)")
        self.tabs.currentChanged.connect(lambda i: self.undo_group.setActiveStack(self.maker.undo if i == 2 else self.undo))
        self.tabs.setCurrentIndex(int(self.settings.value("view_tab", 0) or 0))
        self.tabs.currentChanged.connect(lambda i: self.settings.setValue("view_tab", i))
        self.setCentralWidget(self.tabs)

        # left: browser
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left); ll.setContentsMargins(4, 4, 4, 4)
        self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText("Filter (ID or name)…")
        self.search.textChanged.connect(self._filter)
        self.tree = QtWidgets.QTreeWidget(); self.tree.setHeaderLabels(["Item", "Details"])
        self.tree.setColumnWidth(0, 120)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.focus_on(it.data(0, QtCore.Qt.UserRole + 1) or ""))
        self.models_list = QtWidgets.QListWidget()
        self.models_list.itemChanged.connect(self._model_toggled)
        mrow = QtWidgets.QHBoxLayout()
        mrow.addWidget(QtWidgets.QLabel("Music"))
        self.music_box = QtWidgets.QComboBox()
        self.music_box.setToolTip("The music this world's map plays. Deluxe's map music is one song with a part per "
                                  "world; pick another world's part or a custom song (CUSTOM_BGM_<nn>, added with "
                                  "nsmbudx-music). Saved as a hidden point (Fm0n / Fn<nn>) that the framework's map "
                                  "music hook reads (code/framework/map_music.cpp, 0x6080D540).")
        self._fill_music_box(0)
        self.music_box.activated.connect(self._world_music_changed)
        mrow.addWidget(self.music_box, 1)
        ll.addLayout(mrow)
        ll.addWidget(self.search)
        ll.addWidget(self.tree, 3)
        ll.addWidget(QtWidgets.QLabel("Models (untick to hide)"))
        ll.addWidget(self.models_list, 1)
        dock_l = QtWidgets.QDockWidget("World", self); dock_l.setObjectName("world"); dock_l.setWidget(left)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock_l)

        # left: the worlds found in the romfs folder
        wl = QtWidgets.QWidget()
        wv = QtWidgets.QVBoxLayout(wl); wv.setContentsMargins(4, 4, 4, 4)
        self.romfs_label = QtWidgets.QLabel()
        self.romfs_label.setWordWrap(True)
        self.worlds_list = QtWidgets.QListWidget()
        self.worlds_list.itemActivated.connect(self._world_activated)
        b1 = QtWidgets.QPushButton("Set ROMFS folder…"); b1.clicked.connect(self.ask_romfs)
        b2 = QtWidgets.QPushButton("Open a file by hand…"); b2.clicked.connect(self.open_world_dialog)
        wv.addWidget(self.romfs_label); wv.addWidget(self.worlds_list, 1); wv.addWidget(b1); wv.addWidget(b2)
        self.worlds_dock = QtWidgets.QDockWidget("Worlds", self); self.worlds_dock.setObjectName("worlds")
        self.worlds_dock.setWidget(wl)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.worlds_dock)
        self.tabifyDockWidget(self.worlds_dock, dock_l)
        self.worlds_dock.raise_()
        self.world_dock = dock_l

        # right: properties + problems
        self.props = PropertiesPanel(self)
        dock_r = QtWidgets.QDockWidget("Properties", self); dock_r.setObjectName("props"); dock_r.setWidget(self.props)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock_r)
        self.problems = QtWidgets.QListWidget()
        self.problems.itemDoubleClicked.connect(self._problem_fix)
        dock_p = QtWidgets.QDockWidget("Checks", self); dock_p.setObjectName("checks"); dock_p.setWidget(self.problems)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock_p)
        self.resizeDocks([dock_l, dock_r], [300, 330], QtCore.Qt.Horizontal)
        self.props_dock, self.checks_dock = dock_r, dock_p
        # the World Maker shows its point / path panel and its checks in these same docks
        sel_panel, chk_panel = self.maker.take_side_panels()
        self.props_stack = QtWidgets.QStackedWidget()
        self.props_stack.addWidget(self.props)
        sa = QtWidgets.QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(sel_panel)
        self.props_stack.addWidget(sa)
        dock_r.setWidget(self.props_stack)
        self.checks_stack = QtWidgets.QStackedWidget()
        self.checks_stack.addWidget(self.problems)
        self.checks_stack.addWidget(chk_panel)
        dock_p.setWidget(self.checks_stack)
        self.tabs.currentChanged.connect(self._side_panels_for_tab)
        self._side_panels_for_tab(self.tabs.currentIndex())
        self.events = EventsPanel(self)
        self.events_dock = QtWidgets.QDockWidget("Events and animations", self); self.events_dock.setObjectName("events")
        self.events_dock.setWidget(self.events)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.events_dock)
        self.tabifyDockWidget(dock_p, self.events_dock)
        dock_p.raise_()

        # bottom: the log (what the program did, errors included)
        self.log_view = QtWidgets.QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_dock = QtWidgets.QDockWidget("Log", self); self.log_dock.setObjectName("log")
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()
        diagnostics.listeners.append(self._log_line)
        self.statusBar().messageChanged.connect(lambda m: m and self._log_line(m, stamp=True))

        self._build_menus()
        self._build_toolbar()
        self._build_status()
        self._default_state = self.saveState()
        self._restore_layout()
        self._maker_hid = None                                   # the restored layout is the main editor's
        self._side_panels_for_tab(self.tabs.currentIndex())
        self.statusBar().showMessage(self.compression_summary())

    # ---- menus ---------------------------------------------------------------------------------------------------
    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._act(m, "&Open world from ROMFS…", self.show_worlds, QtGui.QKeySequence.Open)
        self._act(m, "Open a &file by hand…", self.open_world_dialog, "Ctrl+Shift+O")
        self._act(m, "Set &ROMFS folder…", self.ask_romfs)
        self._act(m, "Load &names (CommonMsg_*.szs) by hand…", self.load_names_dialog)
        m.addSeparator()
        self._act(m, "&Export to mod folder…", self.export_dialog, "Ctrl+E")
        m.addSeparator()
        self._act(m, "&Quit", self.close, QtGui.QKeySequence.Quit)
        e = mb.addMenu("&Edit")
        u = self.undo_group.createUndoAction(self, "&Undo"); u.setShortcut(QtGui.QKeySequence.Undo); e.addAction(u)
        r = self.undo_group.createRedoAction(self, "&Redo"); r.setShortcut(QtGui.QKeySequence.Redo); e.addAction(r)
        e.addSeparator()
        self.lock_action = self._act(e, "&Lock points (no dragging)", self._toggle_lock, "Ctrl+L", checkable=True)
        self._act(e, "&Connect two points (new route)…", self.connect_points, "C")
        e.addSeparator()
        for key, d in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)), ("Down", (0, 1))):
            for mod, step in (("", 1.0), ("Shift+", 10.0)):
                a = QtWidgets.QAction(self)
                a.setShortcut(QtGui.QKeySequence(mod + key))
                a.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
                a.triggered.connect(lambda _=False, dx=d[0] * step, dz=d[1] * step: self.nudge(dx, dz))
                self.view.addAction(a); self.map.addAction(a)
        esc = QtWidgets.QShortcut(QtGui.QKeySequence("Escape"), self)
        esc.activated.connect(self.cancel_pick)
        self.snap_action = self._act(e, "&Snap moved points to the terrain", self._toggle_snap, checkable=True)
        self.snap_action.setChecked(True)
        v = mb.addMenu("&View")
        self.labels_action = self._act(v, "Point &labels", self._toggle_labels, "L", checkable=True); self.labels_action.setChecked(True)
        self.wp_action = self._act(v, "&Waypoints", self._toggle_waypoints, "W", checkable=True); self.wp_action.setChecked(True)
        self.routes_action = self._act(v, "&Routes", self._toggle_routes, "R", checkable=True); self.routes_action.setChecked(True)
        self.grid_action = self._act(v, "2D &grid", lambda on: self.map.set_option("show_grid", on), "G", checkable=True); self.grid_action.setChecked(True)
        self.cams_action = self._act(v, "&Cameras for all points", self._toggle_cameras, "K", checkable=True)
        self.tex_action = self._act(v, "&Textures and materials", self.view.set_textures, "X", checkable=True)
        self.tex_action.setChecked(True)
        self.anim_action = self._act(v, "&Play animations (water, clouds, scenery)", self._toggle_animations, "Space",
                                     checkable=True)
        self._act(v, "&Frame the whole world", self.frame_all, "F")
        self._act(v, "Ride &along a path (camera preview)…", self.ride_along, "Shift+R")
        v.addSeparator()
        self._act(v, "&2D map", lambda: self.tabs.setCurrentIndex(0), "Ctrl+2")
        self._act(v, "&3D view", lambda: self.tabs.setCurrentIndex(1), "Ctrl+3")
        self._act(v, "&World Maker", lambda: self.tabs.setCurrentIndex(2), "Ctrl+4")
        v.addSeparator()
        panels = v.addMenu("&Panels")
        for d in (self.worlds_dock, self.world_dock, self.props_dock, self.checks_dock, self.log_dock):
            panels.addAction(d.toggleViewAction())
        self.log_dock.toggleViewAction().setShortcut(QtGui.QKeySequence("Ctrl+Shift+L"))
        panels.addSeparator()
        self._act(panels, "&Reset the layout", self.reset_layout)
        th = v.addMenu("&Theme")
        tg = QtWidgets.QActionGroup(self)
        self.theme_actions = {}
        for key, label in theme.NAMES.items():
            a = self._act(th, label, lambda _=False, k=key: self.set_theme(k), checkable=True)
            a.setChecked(key == theme.current()); tg.addAction(a)
            self.theme_actions[key] = a
        t = mb.addMenu("&Tools")
        self._act(t, "&Offset tables…", self.offset_dialog, "Ctrl+T")
        self._act(t, "&Run checks", self.run_checks)
        self._act(t, "Open the game text in your MSBT &editor", self.open_msbt_editor)
        t.addSeparator()
        self._act(t, "Edit this world's &models…", self.world_models_dialog, "Ctrl+M")
        self._act(t, "Port an NSMBW (Wii) &world…", self.port_nsmbw_dialog)
        self._act(t, "&Convert a 3D model (Wii, FBX, DAE, OBJ → DAE, FBX, OBJ)…", self.convert_model_dialog)
        self._act(t, "Copy a &level with its own tilesets…", self.copy_level_dialog)
        t.addSeparator()
        self._act(t, "&Preferences…", self.preferences_dialog)
        h = mb.addMenu("&Help")
        self._act(h, "Open the &log folder (for bug reports)", self.open_log_folder)
        self._act(h, "&About Koopatlas DX", self.about)

    def _build_toolbar(self):
        tb = QtWidgets.QToolBar("Main toolbar", self)
        tb.setObjectName("main_toolbar")
        tb.setIconSize(QtCore.QSize(20, 20))
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.addToolBar(QtCore.Qt.TopToolBarArea, tb)
        self.toolbar = tb

        def add(icon, text, slot, tip):
            a = tb.addAction(theme.icon(icon), text, slot)
            a.setToolTip(tip)
            a.setData(icon)
            return a
        add("open", "Open", self.show_worlds, "Open a world from the ROMFS (Ctrl+O)")
        add("export", "Export", self.export_dialog, "Export the world to your mod folder (Ctrl+E)")
        tb.addSeparator()
        u = self.undo_group.createUndoAction(self, "Undo"); u.setIcon(theme.icon("undo")); u.setData("undo")
        r = self.undo_group.createRedoAction(self, "Redo"); r.setIcon(theme.icon("redo")); r.setData("redo")
        tb.addAction(u); tb.addAction(r)
        tb.addSeparator()
        self.tab_actions = []
        grp = QtWidgets.QActionGroup(self)
        for i, (icon, text) in enumerate((("map2d", "2D map"), ("view3d", "3D view"), ("maker", "World Maker"))):
            a = add(icon, text, lambda _=False, k=i: self.tabs.setCurrentIndex(k), "%s (Ctrl+%d)" % (text, i + 2))
            a.setCheckable(True); grp.addAction(a); self.tab_actions.append(a)
        self.tab_actions[self.tabs.currentIndex()].setChecked(True)
        self.tabs.currentChanged.connect(lambda i: self.tab_actions[i].setChecked(True))
        tb.addSeparator()
        add("frame", "Frame", self.frame_all, "Frame the whole world (F)")
        add("models", "Models", self.world_models_dialog, "Edit this world's models: move, turn, scale, export as "
                                                         "DAE/FBX, replace, retexture (Ctrl+M)")
        add("check", "Checks", self.run_checks, "Run the checks on the world")
        tb.addSeparator()
        for kind, text, col, tip in (
                ("level", "+ Level", "#facc15", "Add a level: click the map where it goes. It gets the next free name "
                 "(W?01-W?0f), a road from the selected point, and the level before it unlocks it."),
                ("stop", "+ Stop", "#60a5fa", "Add a path stop (F, Mario always stops): click the map."),
                ("pass", "+ Point", "#cbd5e1", "Add a pass-through point (F, Mario walks through it; ladders start "
                 "and end on these): click the map."),
                ("bend", "+ Bend", "#94a3b8", "Add a bend (K, a turn in the road): click the map.")):
            a = tb.addAction(_dot_icon(col), text, lambda _=False, k=kind: self.start_place(k))
            a.setToolTip(tip)
        self.connect_action = tb.addAction("Connect")
        self.connect_action.setCheckable(True); self.connect_action.setChecked(True)
        self.connect_action.toggled.connect(lambda on: self.maker.chain_box.setChecked(on))
        self.connect_action.setToolTip("New points get a road from the selected point (of the selected route's "
                                       "action, else road), and a new level or stop is unlocked by the level before it.")

    def _build_status(self):
        sb = self.statusBar()
        self.world_label = QtWidgets.QLabel("No world open")
        self.size_bar = QtWidgets.QProgressBar()
        self.size_bar.setFixedWidth(230)
        self.size_bar.setRange(0, 1000)
        self.size_bar.setValue(0)
        self.size_bar.setFormat("World size: —")
        self.size_bar.setToolTip("The world archive's unpacked size. The game holds every world's archive in memory: "
                                 "past about %.1f MB another world may no longer fit and the game crashes at boot."
                                 % (self._size_limits()[0] / 1e6))
        sb.addPermanentWidget(self.world_label)
        sb.addPermanentWidget(self.size_bar)

    @staticmethod
    def _size_limits():
        from .export2d import WORLD_SIZE_SAFE, WORLD_SIZE_VANILLA_MAX
        return WORLD_SIZE_SAFE, WORLD_SIZE_VANILLA_MAX

    def show_world_size(self, unpacked, label=""):
        """Show a world archive's unpacked size in the status bar meter (green, amber past the known-safe size, red
        past the largest vanilla world)."""
        from .export2d import size_verdict
        safe, vmax = self._size_limits()
        verdict, sentence = size_verdict(int(unpacked))
        self.size_bar.setValue(min(1000, int(1000 * unpacked / vmax)))
        self.size_bar.setFormat("%s%.1f / %.1f MB" % (label + " " if label else "", unpacked / 1e6, safe / 1e6))
        self.size_bar.setProperty("verdict", verdict)
        self.size_bar.style().unpolish(self.size_bar); self.size_bar.style().polish(self.size_bar)
        self.size_bar.setToolTip(sentence)

    def _log_line(self, line, stamp=False):
        if stamp:
            import datetime
            line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), line)
        if getattr(self, "log_view", None) is not None:
            self.log_view.appendPlainText(line)

    def set_theme(self, name):
        theme.apply(QtWidgets.QApplication.instance(), name)
        for a in self.toolbar.actions():
            if a.data():
                a.setIcon(theme.icon(a.data()))
        for k, a in self.theme_actions.items():
            a.setChecked(k == name)
        if self.world:
            self.run_checks()                                  # its colours follow the theme

    def _restore_layout(self):
        geo, state = self.settings.value("main_geometry"), self.settings.value("main_state")
        if geo is not None:
            self.restoreGeometry(geo)
        if state is not None:
            self.restoreState(state)

    def reset_layout(self):
        self.restoreState(self._default_state)
        self.log_dock.hide()

    def _act(self, menu, text, slot, shortcut=None, checkable=False):
        a = QtWidgets.QAction(text, self)
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    # ---- loading -------------------------------------------------------------------------------------------------
    # ---- ROMFS ---------------------------------------------------------------------------------------------------
    def romfs_root(self):
        root = romfs.normalize(self.settings.value("romfs_root", "") or "")
        if root is None:                                       # 0.1 stored the Model folder instead
            root = romfs.normalize(self.settings.value("romfs_model", "") or "")
        return root

    def startup(self, path=None):
        """Called once the window is shown: ask for the romfs folder if it is not known, then list the worlds."""
        if self.romfs_root() is None:
            self.ask_romfs(first_run=True)
        else:
            self.fill_worlds()
        if path:
            self.open_world(path)

    def ask_romfs(self, first_run=False):
        d = RomfsDialog(self, self.settings.value("romfs_root", "") or "", self.settings.value("language", "USen") or "USen", first_run)
        if d.exec_() and d.root:
            self.set_romfs(d.root, d.language)
            return True
        self.fill_worlds()
        return False

    def set_romfs(self, root, language="USen"):
        self.settings.setValue("romfs_root", str(root))
        self.settings.setValue("language", language)
        names = romfs.names_archive(root, language)
        if names:
            try:
                self.names = Names.from_szs(names)
            except Exception as e:
                self.statusBar().showMessage("Names not loaded: %s" % e, 6000)
        self.fill_worlds()

    def fill_worlds(self):
        self.worlds_list.clear()
        root = self.romfs_root()
        if root is None:
            self.romfs_label.setText("No ROMFS folder set. Use <b>Set ROMFS folder…</b>, or open a world file by hand.")
            return
        if self.names is None:
            names = romfs.names_archive(root, self.settings.value("language", "USen") or "USen")
            if names:
                try:
                    self.names = Names.from_szs(names)
                except Exception:
                    pass
        self.romfs_label.setText("<b>ROMFS:</b> %s<br><small>%s</small>" % (root, romfs.describe(root)))
        for wid, path in romfs.worlds(root):
            name = self.names.world(int(wid[1])) if self.names else None
            it = QtWidgets.QListWidgetItem("%s%s" % (wid, ("  —  " + name) if name else ""))
            it.setData(QtCore.Qt.UserRole, str(path))
            it.setToolTip(str(path))
            self.worlds_list.addItem(it)

    def show_worlds(self):
        if self.romfs_root() is None and not self.ask_romfs():
            return
        self.worlds_dock.show()
        self.worlds_dock.raise_()

    def _world_activated(self, item):
        self.open_world(item.data(QtCore.Qt.UserRole))

    def frame_all(self):
        self.view.frame_all()
        self.map.fit_all()

    def open_world_dialog(self):
        root = self.romfs_root()
        start = str(romfs.model_dir(root)) if root else (self.settings.value("last_world", "") or "")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open a world archive", start,
                                                        "World archives (CS_W*.szs);;All files (*)")
        if path:
            self.open_world(path)

    def _fill_music_box(self, current):
        from . import project2d
        self.music_box.blockSignals(True)
        self.music_box.clear()
        for k, label in project2d.MUSIC_NAMES.items():
            self.music_box.addItem(label, k)
        if current >= project2d.MUSIC_CUSTOM:
            self.music_box.addItem(project2d.music_label(current), current)
        self.music_box.addItem("Custom song (nsmbudx-music)…", -1)
        self.music_box.setCurrentIndex(max(0, self.music_box.findData(current)))
        self.music_box.blockSignals(False)

    def world_music(self):
        from . import project2d
        return project2d.music_of(self.world.point_ids) if self.world else 0

    def set_world_music(self, value):
        """Give the open world's map music (0 the game's own): replaces its hidden music point. One undo step."""
        from . import project2d
        w = self.world
        if w is None:
            return
        old_pts = [q for q in w.point_ids if (q[:2] == "Fm" or (q[:2] == "Fn" and q[2:].isdigit())) and len(q) == 4]
        fixed = [q for q in old_pts if q not in w.new_bones]
        if fixed:
            QtWidgets.QMessageBox.information(self, "Music", "This world's own point %s picks its music; edit it in "
                                              "the point table." % ", ".join(fixed))
            self._fill_music_box(self.world_music())
            return
        start = next((q for q in w.point_ids if len(q) == 4 and q[0] == "W" and q[2] == "M"), None)
        base = w.position(start) if start and w.has_bone(start) else (0.0, 0.0, 0.0)
        pos = (base[0], base[1] - project2d.HIDDEN_DEPTH, base[2])
        new_pt = project2d.music_point(value) if value else None
        saved = {}

        def do():
            for q in old_pts:
                if q in w.point_ids:
                    saved[q] = w.remove_point(q)
            if new_pt:
                w.add_point(new_pt, pos)

        def undo():
            if new_pt and new_pt in w.point_ids:
                w.remove_point(new_pt)
            for q in old_pts:
                if q not in w.point_ids:
                    w.add_point(q, pos)

        def after():
            self._fill_music_box(self.world_music())
            self._after_edit(refresh_markers=True, refresh_tree=True)()
        self.undo.push(EditCommand("Music: %s" % project2d.music_label(value), do, undo, after))

    def _world_music_changed(self, _i):
        from . import project2d
        v = self.music_box.currentData()
        cur = self.world_music()
        if v == -1:
            n = project2d.ask_custom_song(self, cur - project2d.MUSIC_CUSTOM if cur >= project2d.MUSIC_CUSTOM else 0,
                                          self.settings.value("mod_root", ""), self.romfs_root())
            if n is None:
                self._fill_music_box(cur)
                return
            v = project2d.MUSIC_CUSTOM + n
        if v != cur:
            self.set_world_music(v)

    def open_world(self, path, model_dir=None, names_path=None):
        if self.world and self.world.unsaved and not self._confirm_discard():
            return False
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            world = World(path)
            folder = Path(path).parent
            world.load_offsets(folder)
            root = self.romfs_root()
            extra = model_dir or (romfs.model_dir(root) if root else None)
            if extra and (world.mario is None or world.look is None):
                world.load_offsets(extra)
            names_file = names_path or (romfs.names_archive(root, self.settings.value("language", "USen") or "USen") if root else None) \
                or self._find_names(folder)
            if names_file and (self.names is None or self.names.source is None or str(self.names.source) != str(names_file)):
                try:
                    self.names = Names.from_szs(names_file)
                except Exception as e:
                    self.statusBar().showMessage("Names not loaded: %s" % e, 6000)
            scene = WorldScene(world)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Could not open the world", "%s\n\n%s" % (e, traceback.format_exc(limit=2)))
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        self.world = world
        self._fill_music_box(self.world_music())
        self.scene = scene
        self.undo.clear()
        self.view.set_world(world, scene)
        self.map.set_world(world, scene)
        self.world_dock.raise_()
        self._fill_tree()
        self._fill_models()
        self.props.show_nothing()
        self.run_checks()
        QtCore.QTimer.singleShot(0, self.events.refresh)
        self.settings.setValue("last_world", str(path))
        self._update_title()
        self.world_label.setText("%s · %d points · %d routes" % (world.id, len(world.point_ids), len(world.routes)))
        try:
            from .export2d import unpacked_size
            self.show_world_size(unpacked_size(path), world.id)
        except OSError:
            pass
        msg = "%s loaded: %d points, %d waypoints, %d routes" % (world.id, len(world.point_ids), len(world.waypoint_ids), len(world.routes))
        if world.mario is None:
            msg += " · MarioOffsetDataW%d.bin not found (set the ROMFS folder in Preferences)" % world.number
        self.statusBar().showMessage(msg, 8000)
        return True

    def _find_names(self, folder):
        saved = self.settings.value("names_szs", "")
        if saved and Path(saved).exists():
            return saved
        for cand in [folder / "CommonMsg_USen.szs", folder.parent / "Message" / "CommonMsg_USen.szs"] + sorted(folder.glob("CommonMsg_*.szs")):
            if cand.exists():
                return str(cand)
        return None

    def load_names_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Game text archive", "", "Message archives (CommonMsg_*.szs);;All files (*)")
        if path:
            try:
                self.names = Names.from_szs(path)
                self.settings.setValue("names_szs", path)
                self._fill_tree()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Names not loaded", str(e))

    # ---- browser -------------------------------------------------------------------------------------------------
    def _fill_tree(self):
        self.tree.clear()
        if not self.world:
            return
        w = self.world
        groups = {}
        title = w.id + ((" — " + self.names.world(w.number)) if self.names and self.names.world(w.number) else "")
        self.tree.setHeaderLabels([title, "Details"])

        def group(name):
            if name not in groups:
                groups[name] = QtWidgets.QTreeWidgetItem(self.tree, [name])
                groups[name].setFirstColumnSpanned(True)
            return groups[name]

        for pid in w.point_ids:
            kind = point_kind(pid)
            g = group("Path stops" if kind in ("Path stop", "Pipe") else "Levels and special points")
            name = self.names.point(pid)[0] if self.names else None
            it = QtWidgets.QTreeWidgetItem(g, [pid, (kind + (": " + name if name else ""))])
            it.setData(0, QtCore.Qt.UserRole, "point"); it.setData(0, QtCore.Qt.UserRole + 1, pid)
        for k in w.waypoint_ids:
            it = QtWidgets.QTreeWidgetItem(group("Waypoints"), [k, "path bend"])
            it.setData(0, QtCore.Qt.UserRole, "point"); it.setData(0, QtCore.Qt.UserRole + 1, k)
        for r in w.routes:
            it = QtWidgets.QTreeWidgetItem(group("Routes"), [r["id"], "%s → %s  %s" % (r["from"], r["to"], r["type"])])
            it.setData(0, QtCore.Qt.UserRole, "route"); it.setData(0, QtCore.Qt.UserRole + 1, r["id"])
        for name, g in groups.items():
            g.setText(0, "%s (%d)" % (name, g.childCount()))
        groups.get("Levels and special points", QtWidgets.QTreeWidgetItem()).setExpanded(True)
        self._filter(self.search.text())

    def _fill_models(self):
        self.models_list.blockSignals(True)
        self.models_list.clear()
        for mo in self.world.model.models:
            tris = self.scene.triangle_count(mo.name) if self.scene else 0
            it = QtWidgets.QListWidgetItem("%s  (%s triangles)" % (mo.name, format(tris, ",")))
            it.setData(QtCore.Qt.UserRole, mo.name)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked)
            self.models_list.addItem(it)
        self.models_list.blockSignals(False)

    def _model_toggled(self, item):
        name, on = item.data(QtCore.Qt.UserRole), item.checkState() == QtCore.Qt.Checked
        self.view.set_model_visible(name, on)
        self.map.set_model_visible(name, on)

    def _filter(self, text):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                c = g.child(j)
                c.setHidden(bool(text) and text not in (c.text(0) + " " + c.text(1)).lower())

    def _tree_clicked(self, item, _col):
        kind = item.data(0, QtCore.Qt.UserRole)
        key = item.data(0, QtCore.Qt.UserRole + 1)
        if kind == "point":
            self.select_point(key, from_tree=True)
        elif kind == "route":
            self.view.select("")
            self.map.select("")
            self.props.show_route(key)

    def select_route(self, rid):
        """Show a route in Properties and light it up in both views."""
        if not self.world:
            return
        self.view.selected_route = rid
        self.map.select_route(rid)
        self.props.show_route(rid)
        self.view.update()

    def route_menu(self, rid, gpos):
        """Right click on a route: give it another action (grouped, with icons and what the game does)."""
        from . import routeactions as RA
        from .routeui import action_icon
        r = next((x for x in self.world.routes if x["id"] == rid), None) if self.world else None
        if r is None:
            return
        self.select_route(rid)
        menu = QtWidgets.QMenu(self)
        menu.addSection("%s → %s · %s" % (r["from"], r["to"], RA.action(r["type"]).label))
        for g in RA.GROUPS:
            sub = menu.addMenu(g)
            for a in [x for x in RA.ACTIONS if x.group == g]:
                act = sub.addAction(action_icon(a.name, 20), a.label + ("" if a.vanilla else "  (unused in vanilla)"))
                act.setCheckable(True); act.setChecked(a.name == r["type"]); act.setToolTip(a.description)
                act.triggered.connect(lambda _=False, n=a.name, old=r["type"]: self.set_route(rid, old, n) if n != old else None)
        menu.addSeparator()
        menu.addAction("Insert a point in the middle").triggered.connect(lambda: self.insert_point(rid))
        if rid in self.view.sinking:
            menu.addAction("Follow the ground (add bends; Mario walks inside it now)").triggered.connect(
                lambda: self.follow_ground(rid))
        probs = self.climb_problems(rid)
        if probs:
            menu.addAction("Fix climb (make it work in game)").triggered.connect(lambda: self.fix_climb(rid))
        if self.world.route_row(rid) and self.world.route_row(rid)[1] in rules.CLIMB_TYPES and self.landing_plan(rid):
            menu.addAction("Stop Mario after climbing (add landing stop)").triggered.connect(
                lambda: self.add_landings(rid))
        if any("not vertical" in t for t in probs):
            menu.addAction("Make vertical only").triggered.connect(lambda: self.make_vertical(rid))
        menu.setToolTipsVisible(True)
        menu.exec_(gpos)

    def _positions(self):
        w, out = self.world, {}
        for q in list(w.point_ids) + list(w.bones) + list(w.new_bones):
            try:
                pos = w.position(q)
            except Exception:
                pos = None
            if pos is not None:
                out[q] = tuple(pos)
        return out

    def climb_problems(self, rid):
        w = self.world
        row = w.route_row(rid) if w else None
        if row is None:
            return []
        flags = {r[1]: rules.split(r[2]) for r in w.point_rows if len(r) > 2}
        return [t for _r, t in rules.climb_problems([row], self._positions(), flags, w.route_rows)]

    # ---- adding points (rebuilt into the map-point model on export) ---------------------------------------------
    KIND_TEXT = {"level": "level", "stop": "path stop", "pass": "pass-through point", "bend": "bend", "toad": "Toad house",
                 "pipe": "pipe point"}

    def _opener_before(self, pid):
        """The level-like point (W, a stop) reached walking back from pid along routes; None if there is none."""
        w, seen, q = self.world, set(), pid
        while q and q not in seen:
            seen.add(q)
            if q[:1] == "W" and rules.point_class(q) in (2, 3, 4):
                return q
            back = [rules.route_ends(r[0])[0] for r in w.route_rows if r and rules.route_ends(r[0]) and
                    rules.route_ends(r[0])[1] == q]
            q = back[0] if back else None
        return None

    def _level_file_note(self, pid):
        lf = rules.level_file(pid)
        if not lf:
            return ""
        course = Path(self.world.path).parent.parent / lf
        return " Its level file is %s (%s)." % (lf, "found" if course.exists() else
                                                 "not in the game yet: make it with a level editor")

    def _side_panels_for_tab(self, i):
        maker = self.tabs.widget(i) is self.maker
        # the main editor's own panels (world tree, worlds, events) make way for the World Maker's canvas
        docks = (self.world_dock, self.worlds_dock, getattr(self, "events_dock", None))
        if maker and not getattr(self, "_maker_hid", None):
            self._maker_hid = [d for d in docks if d is not None and not d.isHidden()]
            for d in self._maker_hid:
                d.hide()
            self.resizeDocks([self.props_dock], [360], QtCore.Qt.Horizontal)   # the canvas gets the room
        elif not maker and getattr(self, "_maker_hid", None):
            for d in self._maker_hid:
                d.show()
            self._maker_hid = None
        self.props_stack.setCurrentIndex(1 if maker else 0)
        self.checks_stack.setCurrentIndex(1 if maker else 0)
        self.props_dock.setWindowTitle("Point / path (World Maker)" if maker else "Properties")
        self.checks_dock.setWindowTitle("Checks (World Maker)" if maker else "Checks")

    def start_place(self, kind):
        """Arm the 3D view and 2D map: the next click on the ground places a point of this kind."""
        if self.tabs.currentWidget() is self.maker:              # the World Maker has its own Point tool
            self.maker.arm_point(kind)
            return
        if not self.world:
            return
        try:
            name = self.world.free_name("stop" if kind == "pass" else kind)
        except WorldError as e:
            QtWidgets.QMessageBox.information(self, "No free name", str(e))
            return
        sel = self.view.selected or self.map.selected
        how = ""
        if sel and self.connect_action.isChecked():
            opener = self._opener_before(sel) if kind in ("level", "stop", "toad", "pipe") else None
            how = " A road from %s leads to it%s." % (sel, (", and %s's exit unlocks it" % opener) if opener else "")
        self.view.place_handler = lambda pos, k=kind: self.add_point_at(k, pos)
        self.view.place_label = name
        self.map.place_handler = self.view.place_handler
        self.statusBar().showMessage("Click the map to place %s (%s).%s%s Esc cancels." % (
            name, self.KIND_TEXT[kind], how, self._level_file_note(name)))

    def cancel_place(self):
        self.view.place_handler = self.map.place_handler = None
        self.view._ghost = None
        self.view.update()

    def add_point_at(self, kind, pos, connect=True):
        w = self.world
        self.cancel_place()
        try:
            pid = w.free_name("stop" if kind == "pass" else kind)
        except WorldError as e:
            QtWidgets.QMessageBox.information(self, "No free name", str(e))
            return None
        prev = (self.view.selected or self.map.selected) if (connect and self.connect_action.isChecked()) else None
        if prev and not w.has_bone(prev):
            prev = None
        rtype = "道"
        cur = self.props.current
        if cur and cur[0] == "route" and w.route_row(cur[1]) is not None:
            rtype = w.route_row(cur[1])[1] or "道"
        opener = self._opener_before(prev) if (prev and kind in ("level", "stop", "toad", "pipe")) else None
        old_row = list(w.row_of(opener)) if opener else None
        rid = "R%s%s" % (prev, pid) if prev else None

        def do():
            w.add_point(pid, pos)
            if kind == "stop":
                w.set_point_cell(pid, 2, "stop")
            if rid:
                w.add_route(rid, rtype)
            if opener:
                r = w.row_of(opener)
                r[3] = ",".join(rules.split(r[3]) + [pid])
                r[4] = ",".join(rules.split(r[4]) + ["R%s%s" % (opener, pid)])

        def undo():
            w.remove_point(pid)
            if opener:
                w.row_of(opener)[:] = old_row
        self.undo.push(EditCommand("Add %s %s" % (self.KIND_TEXT[kind], pid), do, undo,
                                   self._after_edit(refresh_markers=True, refresh_tree=True)))
        self.select_point(pid)
        warn = ""
        if prev:
            clash = rules.heading_clashes([r[0] for r in w.route_rows if r], self._positions()).get(
                (prev, rules.heading(w.position(prev), w.position(pid))))
            if clash:
                warn = ("  ⚠ It leaves %s the same way as %s: the game keeps one road per direction. Drag %s with "
                        "the arrows so it leaves %s in another direction." % (prev, [c for c in clash if pid not in c][0],
                                                                             pid, prev))
        self.statusBar().showMessage("Added %s %s%s%s.%s%s" % (
            self.KIND_TEXT[kind], pid, (", joined to %s" % prev) if prev else "",
            (", unlocked by %s" % opener) if opener else "", self._level_file_note(pid), warn), 15000)
        return pid

    def delete_point(self, pid):
        w = self.world
        if pid not in w.new_bones:
            QtWidgets.QMessageBox.information(self, "Game point", "%s is one of the game's own points; only points "
                                              "added in the editor can be deleted." % pid)
            return
        holder = {}

        def do():
            holder["saved"] = w.remove_point(pid)

        def undo():
            w.restore_point(pid, holder["saved"])
        self.props.show_nothing()
        self.undo.push(EditCommand("Delete %s" % pid, do, undo, self._after_edit(refresh_markers=True, refresh_tree=True)))

    def insert_point(self, rid):
        """Split a route at its middle with a pass-through point (same action on both halves)."""
        w = self.world
        a, b = rules.route_ends(rid)
        pa, pb = w.position(a), w.position(b)
        if pa is None or pb is None:
            return
        mid = self._on_ground(tuple((x + y) / 2 for x, y in zip(pa, pb)), w.route_row(rid))
        pid = w.free_name("stop")
        idx = next(i for i, r in enumerate(w.route_rows) if r and r[0] == rid)
        row = list(w.route_rows[idx])

        def do():
            w.add_point(pid, mid)
            w.remove_route(rid)
            w.insert_route(idx, ["R%s%s" % (a, pid), row[1], ""])
            w.insert_route(idx + 1, ["R%s%s" % (pid, b), row[1], ""])

        def undo():
            w.remove_point(pid)
            w.insert_route(idx, row)
        self.undo.push(EditCommand("Insert %s in %s" % (pid, rid), do, undo,
                                   self._after_edit(refresh_markers=True, refresh_tree=True)))
        self.select_point(pid)

    def _on_ground(self, pos, row=None):
        """pos moved onto the ground under/over it (nearest surface), for points placed between two others on a road;
        climbs and jumps keep their height. Only while Snap to terrain is on."""
        if not self.snap_action.isChecked() or (row is not None and not ground.walks_on_ground(row[1] if len(row) > 1 else "")):
            return pos
        try:
            h = self.ground().ground_at(pos[0], pos[2], pos[1])
        except Exception:
            h = None
        return pos if h is None else (pos[0], h, pos[2])

    def fix_climb(self, rid):
        """Make a ladder/vine work in game the way vanilla's do: it runs straight up between two pass-through points
        (plain F points with exactly two roads). The stick only picks compass directions (getDirFromKeyMask_
        0x601D8424), so a climb that starts at a stop can't be chosen; Mario only climbs by walking on through."""
        w = self.world
        a, b = rules.route_ends(rid)
        pos = self._positions()
        if a not in pos or b not in pos:
            return
        row = w.route_row(rid)
        flags = {r[1]: rules.split(r[2]) for r in w.point_rows if len(r) > 2}

        def degree(q):
            return sum(1 for r in w.route_rows if r and q in (rules.route_ends(r[0]) or ()))

        def needs_helper(q):
            return (q[:1] == "W" or "stop" in flags.get(q, []) or degree(q) != 2 or
                    any(f in rules.AMBUSH_FLAGS for f in flags.get(q, [])))
        ha, hb = needs_helper(a), needs_helper(b)
        if not ha and not hb:
            self.make_vertical(rid)
            return rid
        keep = b if ha and not hb else (a if hb and not ha else None)
        if keep:
            col = (pos[keep][0], pos[keep][2])
        else:
            col = ((pos[a][0] + pos[b][0]) / 2, (pos[a][2] + pos[b][2]) / 2)
        for q, need in ((a, ha), (b, hb)):
            if need and abs(pos[q][0] - col[0]) < 3 and abs(pos[q][2] - col[1]) < 3:
                if keep:
                    QtWidgets.QMessageBox.information(self, "Fix climb", "The climb sits right above %s. Move %s a bit "
                                                      "sideways first, then fix it again." % (q, keep))
                    return
                col = (col[0] + 40.0, col[1])
        names, helpers = [], {}
        taken = set()
        for q, need in ((a, ha), (b, hb)):
            if need:
                n = w.free_name("stop")
                while n in taken:
                    n = "F%03x" % (int(n[1:], 16) + 1)
                taken.add(n)
                helpers[q] = (n, (col[0], pos[q][1], col[1]))
        ea = helpers[a][0] if a in helpers else a
        eb = helpers[b][0] if b in helpers else b
        chain = [a] + ([ea] if a in helpers else []) + ([eb] if b in helpers else []) + [b]
        types = []
        for u, v in zip(chain, chain[1:]):
            types.append(row[1] if (u, v) == (ea, eb) else "道")
        idx = next(i for i, r in enumerate(w.route_rows) if r and r[0] == rid)
        moved = None
        for q in (a, b):                                          # an end without a helper stands on the column
            if q not in helpers and q != keep:
                moved = (q, pos[q], (col[0], pos[q][1], col[1]))

        def do():
            for q, (n, ppos) in helpers.items():
                w.add_point(n, ppos)
            if moved:
                w.move_point(moved[0], moved[2])
            w.remove_route(rid)
            for k, ((u, v), t) in enumerate(zip(zip(chain, chain[1:]), types)):
                w.insert_route(idx + k, ["R%s%s" % (u, v), t, ""])

        def undo():
            for k in range(len(chain) - 1):
                w.remove_route("R%s%s" % (chain[k], chain[k + 1]))
            for q, (n, _p) in helpers.items():
                w.remove_point(n)
            if moved:
                w.move_point(moved[0], moved[1])
            w.insert_route(idx, row)
        self.undo.push(EditCommand("Fix climb %s" % rid, do, undo, self._after_edit(refresh_markers=True, refresh_tree=True)))
        self.statusBar().showMessage("%s now climbs between %s and %s (pass-through points), the way vanilla ladders "
                                     "do: Mario walks onto it from either side." % (rid, ea, eb), 12000)
        rest = self.climb_problems("R%s%s" % (ea, eb))
        clashes = rules.heading_clashes([r[0] for r in w.route_rows if r], self._positions())
        for (pnt, h), rids in clashes.items():
            if any(n in "".join(rids) for n, _p in helpers.values()):
                rest.append("%s leaves %s the same way (%s) as %s: the game keeps one road per direction, so one is "
                            "lost. Drag the new point with the arrows to another side of %s." % (
                                [x for x in rids if any(n in x for n, _p in helpers.values())][0], pnt, h,
                                [x for x in rids if not any(n in x for n, _p in helpers.values())][0]
                                if any(not any(n in x for n, _p in helpers.values()) for x in rids) else rids[0], pnt))
        if rest:
            QtWidgets.QMessageBox.information(self, "Fix climb", "\n".join(rest))
        return "R%s%s" % (ea, eb)

    def _is_stop(self, q):
        w = self.world
        flags = rules.split((w.row_of(q) or ["", "", ""])[2]) if w.row_of(q) else []
        deg = sum(1 for r in w.route_rows if r and q in (rules.route_ends(r[0]) or ()))
        return q[:1] == "W" or "stop" in flags or deg != 2

    def landing_plan(self, rid):
        """Where Mario would walk on after a climb: [(end, road, next point)] for each ladder end that doesn't stop
        him, unless the next point is a stop right beside it."""
        w = self.world
        out = []
        ends = rules.route_ends(rid) or ()
        for e in ends:
            if self._is_stop(e) or w.position(e) is None:
                continue
            other = ends[1] if e == ends[0] else ends[0]
            is_top = w.position(other) is None or w.position(e)[1] >= w.position(other)[1]
            road = next((r for r in w.route_rows if r and r[0] != rid and e in (rules.route_ends(r[0]) or ())), None)
            if road is None or road[1] in rules.CLIMB_TYPES:
                continue
            a, b = rules.route_ends(road[0])
            x = b if a == e else a
            px, pe = w.position(x), w.position(e)
            if px is None:
                continue
            near = sum((a - b) ** 2 for a, b in zip(pe, px)) ** 0.5 < 80
            # at the top Mario stops unless a stop is right there; at the bottom the next stop is enough
            if not (self._is_stop(x) and (near or not is_top)):
                out.append((e, road, x))
        return out

    def add_landings(self, rid):
        """A stop just past each end of a climb where Mario would otherwise walk on, so he stops after climbing; from
        it he can walk back down the ladder or carry on. One undo step."""
        w = self.world
        plan = self.landing_plan(rid)
        if not plan:
            self.statusBar().showMessage("Mario already stops after %s." % rid, 6000)
            return []
        steps, made, taken = [], [], set()
        for e, road, x in plan:
            pe, px = w.position(e), w.position(x)
            length = max(1e-6, sum((a - b) ** 2 for a, b in zip(pe, px)) ** 0.5)
            t = min(60.0, 0.45 * length) / length
            pos = self._on_ground(tuple(a + (b - a) * t for a, b in zip(pe, px)), road)
            n = w.free_name("stop")
            while n in taken:
                n = "F%03x" % (int(n[1:], 16) + 1)
            taken.add(n)
            a, b = rules.route_ends(road[0])
            pieces = [("R%s%s" % (a, n)), ("R%s%s" % (n, b))]
            steps.append((n, pos, list(road), pieces))
            made.append(n)

        def do():
            for n, pos, road, pieces in steps:
                w.add_point(n, pos)
                w.set_point_cell(n, 2, "stop")
                i = next(k for k, r in enumerate(w.route_rows) if r and r[0] == road[0])
                w.remove_route(road[0])
                w.insert_route(i, [pieces[0], road[1], ""])
                w.insert_route(i + 1, [pieces[1], road[1], ""])

        def undo():
            for n, pos, road, pieces in reversed(steps):
                i = next(k for k, r in enumerate(w.route_rows) if r and r[0] == pieces[0])
                w.remove_point(n)
                w.insert_route(i, road)
        self.undo.push(EditCommand("Landing stops for %s" % rid, do, undo,
                                   self._after_edit(refresh_markers=True, refresh_tree=True)))
        self.statusBar().showMessage("Mario now stops at %s after climbing %s; from there he can go back down or walk "
                                     "on." % (", ".join(made), rid), 10000)
        return made

    def ground_menu(self, pos, gpos):
        if not self.world:
            return
        menu = QtWidgets.QMenu(self)
        sel = self.view.selected
        menu.addSection("Add here" + (" (joined to %s)" % sel if sel and self.connect_action.isChecked() else ""))
        for kind in ("level", "stop", "pass", "bend", "toad"):
            try:
                name = self.world.free_name("stop" if kind == "pass" else kind)
            except WorldError:
                continue
            menu.addAction(_dot_icon({"level": "#facc15", "stop": "#60a5fa", "pass": "#cbd5e1", "bend": "#94a3b8",
                                      "toad": "#f87171"}[kind]), "%s %s" % (self.KIND_TEXT[kind].capitalize(), name)
                           ).triggered.connect(lambda _=False, k=kind: self.add_point_at(k, pos))
        menu.exec_(gpos)

    def point_menu(self, pid, gpos):
        self.select_point(pid)
        menu = QtWidgets.QMenu(self)
        menu.addSection(pid)
        menu.addAction("+ Level after %s…" % pid).triggered.connect(lambda: self.start_place("level"))
        menu.addAction("+ Point after %s…" % pid).triggered.connect(lambda: self.start_place("pass"))
        menu.addAction("Road to another point…").triggered.connect(
            lambda: self.start_pick("Road from %s: click the point it goes to (Esc cancels)." % pid,
                                    lambda b: b != pid and self.add_route("R%s%s" % (pid, b))))
        menu.addAction("Branch to another point (junction)…").triggered.connect(lambda: self.branch_from(pid))
        if len(directions.roads_at(self.world, pid)) >= 2:
            menu.addAction("Stick directions…").triggered.connect(lambda: self.directions_dialog(pid))
        if pid in self.world.new_bones:
            menu.addSeparator()
            menu.addAction("Delete %s" % pid).triggered.connect(lambda: self.delete_point(pid))
        menu.exec_(gpos)

    def add_route_copy(self, source, target, offset):
        w = self.world
        if any(c["target"] == target for c in w.route_copies) or target in [m.name for m in w.model.models]:
            QtWidgets.QMessageBox.information(self, "Copy", "%s already has an appear animation." % target)
            return
        item = {"source": source, "target": target, "offset": [float(v) for v in offset]}
        self.undo.push(EditCommand("Copy %s's animation to %s" % (source, target), lambda: w.route_copies.append(item),
                                   lambda: w.route_copies.remove(item), self._after_edit()))
        self.events.refresh()
        self.statusBar().showMessage("%s will play %s's appear animation when it opens (added on export)." % (
            target, source), 8000)

    def add_route_make(self, item):
        w = self.world
        taken = {c["target"] for c in w.route_copies} | {m["target"] for m in w.route_makes} | {m.name for m in w.model.models}
        if item["target"] in taken:
            QtWidgets.QMessageBox.information(self, "New appear animation", "%s already has an appear animation." %
                                              item["target"])
            return
        used = {sh for m in w.route_makes for sh in m["shapes"]} | {o for m in w.route_makes for o in m.get("objects", [])}
        if used & (set(item["shapes"]) | set(item.get("objects", []))):
            QtWidgets.QMessageBox.information(self, "New appear animation", "%s already appears with another path." %
                                              ", ".join(sorted(used & (set(item["shapes"]) | set(item.get("objects", []))))))
            return
        self.undo.push(EditCommand("New appear animation for %s" % item["target"], lambda: w.route_makes.append(item),
                                   lambda: w.route_makes.remove(item), self._after_edit()))
        self.events.refresh()
        self.statusBar().showMessage("%s: %d object(s) will %s when the path opens (added on export)." % (
            item["target"], len(item["shapes"]) + len(item.get("objects", [])), item["preset"]), 8000)

    def replace_route_make(self, old, new):
        w = self.world
        if old not in w.route_makes:
            return

        def put(a, b):
            w.route_makes[w.route_makes.index(a)] = b
        self.undo.push(EditCommand("Edit %s's keyframes" % old["target"], lambda: put(old, new), lambda: put(new, old),
                                   self._after_edit()))
        self.events.refresh()

    def preview_make_item(self, item):
        """Preview a new appear animation that is not in the world yet (or edited keys of one that is)."""
        w = self.world
        saved = list(w.route_makes)
        w.route_makes[:] = [m for m in saved if m["target"] != item["target"]] + [item]
        try:                                            # bring the object on screen if it is off it
            scr, vis = self.view.project([item["pivot"]])
            x, y = scr[0][0], scr[0][1]
            if not (vis[0] and 0 <= x < self.view.width() and 0 <= y < self.view.height()):
                import numpy as np
                self.view.target = np.array(item["pivot"], float)
        except Exception:
            pass
        try:
            return self.preview_pending(item["target"], item["frames"])
        finally:
            w.route_makes[:] = saved

    def remove_route_make(self, target):
        w = self.world
        item = next((m for m in w.route_makes if m["target"] == target), None)
        if item is None:
            return
        i = w.route_makes.index(item)
        self.undo.push(EditCommand("Remove %s's new animation" % target, lambda: w.route_makes.remove(item),
                                   lambda: w.route_makes.insert(i, item), self._after_edit()))
        self.events.refresh()

    def remove_route_copy(self, target):
        w = self.world
        item = next((c for c in w.route_copies if c["target"] == target), None)
        if item is None:
            return
        i = w.route_copies.index(item)
        self.undo.push(EditCommand("Remove %s's copied animation" % target, lambda: w.route_copies.remove(item),
                                   lambda: w.route_copies.insert(i, item), self._after_edit()))
        self.events.refresh()

    def path_animation(self, path):
        """(model to play, frames, pending source) of a path's appear animation, or None."""
        ev = self.events.ev
        if ev is None:
            return None
        p = next((x for x in ev["paths"] if x["path"] == path and x["skeletal"]), None)
        return (p.get("pending") or path, p["frames"], p.get("pending")) if p else None

    def preview_pending(self, path, frames=0):
        """Play an appear animation that is only added on export (a copy or a new one): the world's BFRES is built
        with it in a temp copy (kdx.events.pending_preview) and those models are drawn from that build meanwhile."""
        from . import events
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        self.statusBar().showMessage("Building %s's animation for the preview…" % path)
        QtWidgets.QApplication.processEvents()
        try:
            pv = events.pending_preview(self.world)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Preview", "The pending animations could not be built:\n%s" % e)
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        self.tabs.setCurrentIndex(1)
        if not self.view.preview_models(pv["bfres"], pv["replace"]):
            self.statusBar().showMessage("Previews need View > Textures on and the 3D view shown.", 6000)
            return False
        a = pv["anims"].skeletal_for(path)
        self.view.preview_anim(pv["anims"], path, (a or {}).get("frames") or frames)
        self.statusBar().showMessage("Playing %s (%d frames), built as it will be exported." % (
            path, (a or {}).get("frames") or frames), 6000)
        return True

    def preview_path_anim(self, path):
        p = next((x for x in (self.events.ev or {}).get("paths", []) if x["path"] == path), None)
        if p and (p.get("pending") or p.get("make")):
            self.preview_pending(path, p.get("frames") or 0)
            return
        info = self.path_animation(path)
        anims = self.events._load_anims()
        if info and anims is not None:
            self.tabs.setCurrentIndex(1)
            self.view.preview_anim(anims, info[0], info[1])

    def make_vertical(self, rid):
        """Put a climb's ends at the same x and z: the bend or flag point moves (else the higher end), height kept."""
        ends = rules.route_ends(rid)
        if not ends:
            return
        pos = self._positions()
        a, b = ends
        if a not in pos or b not in pos:
            return
        movable = [q for q in (a, b) if rules.point_class(q) in (0, 1)]
        mover = movable[0] if len(movable) == 1 else (a if pos[a][1] > pos[b][1] else b)
        anchor = b if mover == a else a
        self.move_point(mover, (pos[anchor][0], pos[mover][1], pos[anchor][2]))
        self.statusBar().showMessage("%s moved above/below %s: %s is now vertical, so the game climbs it." % (
            mover, anchor, rid), 8000)

    def focus_on(self, pid):
        self.view.focus_on(pid)
        self.map.focus_on(pid)

    def select_point(self, pid, from_tree=False):
        self.view.select(pid)
        self.map.select(pid)
        if pid:
            self.props.show_point(pid)
            if not from_tree:
                items = self.tree.findItems(pid, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0)
                if items:
                    self.tree.setCurrentItem(items[0])
        else:
            self.props.show_nothing()

    # ---- edits (all undoable) -----------------------------------------------------------------------------------
    def _after_edit(self, refresh_markers=False, refresh_tree=False):
        def run():
            if refresh_markers:
                self.view.refresh_markers()
            self.map.refresh_markers()
            if refresh_tree:
                self._fill_tree()
            cur = self.props.current
            w = self.world
            if cur and cur[0] == "point":
                if cur[1] in w.point_ids or cur[1] in w.bones:
                    self.props.show_point(cur[1])
                else:
                    self.props.show_nothing()
            elif cur and cur[0] == "route":
                if w.route_row(cur[1]) is not None:
                    self.props.show_route(cur[1])
                else:
                    self.props.show_nothing()
            self.run_checks()
            self._update_title()
        return run

    def move_point(self, pid, new):
        w = self.world
        old = w.position(pid)
        if old is None:
            return
        self.undo.push(EditCommand("Move %s" % pid, lambda: w.move_point(pid, new), lambda: w.move_point(pid, old),
                                   self._after_edit(refresh_markers=True)))

    def _camera_offset(self, pid):
        rec = self.world.mario_record(pid) if self.world else None
        return rec.vector if rec else None

    def move_camera(self, pid, new_offset):
        rec = self.world.mario_record(pid)
        if rec is None:
            return
        self.set_mario(pid, rec.vector, rec.flags, tuple(new_offset), rec.flags)

    def set_highlight(self, colors):
        self.view.highlight = dict(colors)
        self.view.update()
        self.map.set_highlight(colors)

    def start_pick(self, prompt, callback):
        def handler(pid):
            self.cancel_pick()
            callback(pid)
        self.view.pick_handler = self.map.pick_handler = handler
        self.view.update(); self.map._apply_visibility(); self.map.viewport().update()
        self.statusBar().showMessage(prompt)

    def cancel_pick(self):
        if getattr(self.view, "place_handler", None):
            self.cancel_place()
            self.statusBar().showMessage("", 1)
        if self.view.pick_handler or self.map.pick_handler:
            self.view.pick_handler = self.map.pick_handler = None
            self.view.update(); self.map._apply_visibility(); self.map.viewport().update()
            self.statusBar().showMessage("", 1)

    def connect_points(self):
        if not self.world:
            return

        def second(a):
            def done(b):
                if a == b:
                    return
                self.add_route("R%s%s" % (a, b))
            self.start_pick("Route from %s: click the point it goes to (Esc cancels)." % a, done)
        self.start_pick("New route: click the point it starts from (Esc cancels).", second)

    def add_route(self, rid, rtype="道"):
        w = self.world
        if w.route_row(rid) is not None:
            QtWidgets.QMessageBox.information(self, "Route exists", "%s is already in the route table." % rid)
            return
        try:
            rules_ok = rules.route_ends(rid)
            if not rules_ok:
                raise WorldError("Route ids are R + 4-character point + 4-character point.")
        except WorldError as e:
            QtWidgets.QMessageBox.warning(self, "Not added", str(e))
            return
        self.undo.push(EditCommand("New route %s" % rid, lambda: w.add_route(rid, rtype), lambda: w.remove_route(rid),
                                   self._after_edit(refresh_tree=True)))
        self.props.show_route(rid)

    def delete_route(self, rid):
        w = self.world
        idx = next(i for i, r in enumerate(w.route_rows) if r and r[0] == rid)
        row = list(w.route_rows[idx])
        self.props.show_nothing()
        self.undo.push(EditCommand("Delete route %s" % rid, lambda: w.remove_route(rid), lambda: w.insert_route(idx, row),
                                   self._after_edit(refresh_tree=True)))

    def nudge(self, dx, dz):
        pid = self.view.selected or self.map.selected
        if not self.world or not pid or self.lock_action.isChecked():
            return
        x, y, z = self.world.position(pid)
        x, z = x + dx, z + dz
        if self.snap_action.isChecked():
            h = self.scene.height_at(x, z)
            if h is not None:
                y = h
        self.move_point(pid, (x, y, z))

    def set_cell(self, pid, col, old, new):
        w = self.world
        self.undo.push(EditCommand("%s: %s" % (pid, POINT_COLUMNS[col]), lambda: w.set_point_cell(pid, col, new),
                                   lambda: w.set_point_cell(pid, col, old), self._after_edit()))

    def set_route(self, rid, old, new, ask=True):
        w = self.world
        climb = ask and new in rules.CLIMB_TYPES and old not in rules.CLIMB_TYPES
        if climb:
            self.undo.beginMacro("%s → %s" % (rid, new))
        self.undo.push(EditCommand("%s type" % rid, lambda: w.set_route_type(rid, new), lambda: w.set_route_type(rid, old),
                                   self._after_edit(refresh_tree=True)))
        if not climb:
            return
        probs = self.climb_problems(rid)
        choice, landing = "two", True
        if any("is a stop" in t or "not vertical" in t for t in probs):
            got = self._ask_climb_way(rid)
            choice, landing = got if isinstance(got, tuple) else (got, True)
            if choice == "two":
                lad = self.fix_climb(rid)
                if landing and lad:
                    self.add_landings(lad)
        elif landing and self.landing_plan(rid):
            self.add_landings(rid)
        self.undo.endMacro()
        if choice == "cancel":
            self.undo.undo()

    def _ask_climb_way(self, rid):
        """Two-way (pass-through points added, like vanilla), one-way (as drawn), or cancel."""
        a, b = rules.route_ends(rid)
        w = self.world
        flags = {r[1]: rules.split(r[2]) for r in w.point_rows if len(r) > 2}

        def stop(q):
            deg = sum(1 for r in w.route_rows if r and q in (rules.route_ends(r[0]) or ()))
            return q[:1] == "W" or "stop" in flags.get(q, []) or deg != 2
        sa, sb = stop(a), stop(b)
        if sa and sb:
            one = "Mario couldn't use it at all: both ends are stops."
        elif sa or sb:
            s_end, p_end = (a, b) if sa else (b, a)
            one = "Mario only uses it when walking on through %s; at %s he can't choose it." % (p_end, s_end)
        else:
            one = "Both ends already pass Mario through: it works both ways once it is vertical."
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Ladder / vine: which way?")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText("<b>%s is now a climb.</b><br><br>In the game the stick only picks north/south/east/west, never "
                    "up/down, so a climb works both ways only between two pass-through points (plain points with two "
                    "roads), as every vanilla ladder is." % rid)
        box.setInformativeText("<b>Two-way</b>: add those points and make it vertical (one undo step).<br>"
                               "<b>One-way</b>: keep it as drawn. %s" % one)
        two = box.addButton("Two-way (recommended)", QtWidgets.QMessageBox.AcceptRole)
        oneb = box.addButton("One-way", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(two)
        cb = QtWidgets.QCheckBox("Mario stops after climbing (adds a stop just past the top or bottom where he "
                                 "would otherwise walk on)")
        cb.setChecked(True)
        box.setCheckBox(cb)
        box.exec_()
        clicked = box.clickedButton()
        return ("two" if clicked is two else ("one" if clicked is oneb else "cancel")), cb.isChecked()

    def set_mario(self, pid, old_vec, old_flags, new_vec, new_flags):
        w = self.world
        self.undo.push(EditCommand("%s MarioOffset" % pid, lambda: w.set_mario(pid, new_vec, new_flags),
                                   lambda: w.set_mario(pid, old_vec, old_flags), self._after_edit()))

    def replace_tables(self, mario, look):
        w = self.world
        old_m = offsets.MarioOffsetTable([r.copy() for r in w.mario.records]) if w.mario else None
        old_l = offsets.LookOffsetTable(list(w.look.values)) if w.look else None

        def do():
            if mario is not None:
                w.mario = offsets.MarioOffsetTable([r.copy() for r in mario.records])
            if look is not None:
                w.look = offsets.LookOffsetTable(list(look.values))

        def undo():
            if old_m is not None:
                w.mario = offsets.MarioOffsetTable([r.copy() for r in old_m.records])
            if old_l is not None:
                w.look = offsets.LookOffsetTable(list(old_l.values))
        self.undo.push(EditCommand("Edit offset tables", do, undo, self._after_edit()))

    # ---- toggles -------------------------------------------------------------------------------------------------
    def _toggle_lock(self, on):
        self.view.locked = on
        self.view.update()
        self.map.set_option("locked", on)

    def _toggle_cameras(self, on):
        self.view.show_all_cameras = on
        self.view.update()
        self.map.show_all_cameras = on
        self.map._update_cameras()

    def _toggle_snap(self, on):
        self.view.snap_to_terrain = on
        self.map.snap_to_terrain = on

    def _toggle_labels(self, on):
        self.view.show_labels = on; self.view.update()
        self.map.set_option("show_labels", on)

    def _toggle_waypoints(self, on):
        self.view.show_waypoints = on; self.view.update()
        self.map.set_option("show_waypoints", on)

    def _toggle_animations(self, on):
        """View > Play animations: the world's texture scrolling and moving scenery, read through the model writer."""
        if not on:
            self.view.play_animations(None)
            return
        if not self.world:
            self.anim_action.setChecked(False)
            return
        from . import anim, export2d
        ok, msg = export2d.engine_status()
        if not ok:
            self.anim_action.setChecked(False)
            QtWidgets.QMessageBox.information(self, "Animations", msg)
            return
        try:
            anims = anim.Animations.load(self.world)
        except Exception as e:
            self.anim_action.setChecked(False)
            QtWidgets.QMessageBox.warning(self, "Animations", "The animations could not be read:\n%s" % e)
            return
        self.view._preview = None
        self.view.play_animations(anims)
        self.statusBar().showMessage("Playing %d material and %d skeletal animations." % (
            len(anims.material_anims), len(anims.skeletal_anims)), 6000)

    def ride_along(self):
        """View > Ride along: Mario walks a path in the 3D view and the camera follows like the map camera."""
        from . import ridealong
        if not self.world:
            return
        walks = ridealong.path_walks(self.world)
        if not walks:
            QtWidgets.QMessageBox.information(self, "Ride along", "This world has no paths (path bones R<from><to>).")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Ride along a path")
        form = QtWidgets.QFormLayout(dlg)
        which = QtWidgets.QComboBox()
        which.addItem("Every path, in order", None)
        sel = self.view.selected or ""
        for p, pts in walks:
            which.addItem("%s  (%s)" % (p, " → ".join(pts[:3] + (["…", pts[-1]] if len(pts) > 3 else pts[3:]))), p)
            if sel and sel in pts and which.currentIndex() == 0:
                which.setCurrentIndex(which.count() - 1)
        reading = QtWidgets.QComboBox()
        reading.addItem("Offset = where the camera looks (Mario + offset, fixed map angle)", "look")
        reading.addItem("Offset = where the camera is (Mario + offset)", "position")
        reading.setToolTip("Which reading of the camera table matches the game is not confirmed yet: compare with the "
                           "game and keep the one that matches.")
        blend = QtWidgets.QComboBox()
        blend.addItem("Blend smoothly between points", "linear")
        blend.addItem("Hold each point's camera until the next point", "hold")
        speed = QtWidgets.QSpinBox(); speed.setRange(50, 2000); speed.setValue(250); speed.setSuffix(" units/s")
        form.addRow("Path", which); form.addRow("Camera offset", reading); form.addRow("Between points", blend)
        form.addRow("Speed", speed)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); form.addRow(bb)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        p = which.currentData()
        ride = ridealong.Ride(self.world, [p] if p else None, blend=blend.currentData())
        self.tabs.setCurrentIndex(1)
        self.view.start_ride(ride, reading.currentData(), speed.value())

    def _toggle_routes(self, on):
        self.view.show_routes = on; self.view.update()
        self.map.set_option("show_routes", on)

    # ---- tools ---------------------------------------------------------------------------------------------------
    def run_checks(self):
        """Run the game's rules (from the reverse engineering) and show errors, warnings and the play-through."""
        self.problems.clear()
        if not self.world:
            return []
        root = self.romfs_root()
        course_dir = (root / "Course") if root and (root / "Course").is_dir() else None
        rep = self.world.report(course_dir)
        for line in rep.summary:
            it = QtWidgets.QListWidgetItem(line); it.setForeground(QtGui.QBrush(theme.colour("#7dd3fc", "#0369a1"))); self.problems.addItem(it)
        if not rep.errors:
            it = QtWidgets.QListWidgetItem("No errors: the game will read this world."); it.setForeground(QtGui.QBrush(theme.colour("#4ade80", "#15803d")))
            self.problems.addItem(it)
        for m in rep.errors:
            it = QtWidgets.QListWidgetItem("Error: " + m); it.setForeground(QtGui.QBrush(theme.colour("#f87171", "#b91c1c"))); self.problems.addItem(it)
        for m in rep.warnings:
            it = QtWidgets.QListWidgetItem("Warning: " + m); it.setForeground(QtGui.QBrush(theme.colour("#facc15", "#a16207"))); self.problems.addItem(it)
        # roads Mario would walk inside the ground on (the game goes straight from point to point)
        sinking = {}
        try:
            sinking = self.ground().problems(self.world, ground.WARN)
        except Exception as e:
            print("ground check:", e)
        self.view.sinking = sinking
        self.view.secret_routes = self.secret_routes()
        self.view.update()
        for rid, (d, _t) in sorted(sinking.items()):
            it = QtWidgets.QListWidgetItem("Warning: Route %s goes up to %d units under the ground: the game moves Mario in a "
                                           "straight line between its points, so he walks inside the ground there. "
                                           "Double-click to add bends that follow the ground." % (rid, d))
            it.setForeground(QtGui.QBrush(theme.colour("#fb7185", "#be123c")))
            it.setData(QtCore.Qt.UserRole, ("ground", rid))
            it.setToolTip("Double-click: add bends on the ground (one undo step)")
            self.problems.addItem(it)
        for pid in self._missing_levels(rep):
            it = QtWidgets.QListWidgetItem("Fix: double-click to choose a level for %s (copied into the mod on export)." % pid)
            it.setForeground(QtGui.QBrush(theme.colour("#7dd3fc", "#0369a1")))
            it.setData(QtCore.Qt.UserRole, ("level", pid))
            self.problems.addItem(it)
        return rep.errors

    def level_path(self, pid):
        """The level archive a point opens: one chosen for the mod (course_copies), else the game's, or None."""
        lf = rules.level_file(pid)
        if lf is None or not self.world:
            return None
        name = lf[len("Course/"):]
        if name in self.world.course_copies and Path(self.world.course_copies[name]).exists():
            return Path(self.world.course_copies[name])
        root = self.romfs_root()
        p = root / "Course" / name if root else None
        return p if p is not None and p.exists() else None

    def yaz0moto_path(self):
        p = Path(self.settings.value("yaz0moto", "") or "")
        if not p.is_file():
            here = Path(__file__).resolve().parents[3]          # .../Switch Modding Project
            found = sorted(here.glob("Yaz0moto*/Yaz0moto.exe"), key=lambda q: q.stat().st_mtime, reverse=True)
            p = found[0] if found else p
        return p if p.is_file() else None

    def open_in_yaz0moto(self, pid):
        """Open a point's level in Yaz0moto (the user's level editor). A level the game has but the mod doesn't is
        copied to a working file first and used for this point on export, so the game's own file is never edited."""
        exe = self.yaz0moto_path()
        if exe is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Where is Yaz0moto.exe?", "", "Yaz0moto (Yaz0moto.exe)")
            if not path:
                return
            self.settings.setValue("yaz0moto", path)
            exe = Path(path)
        lf = self.level_path(pid)
        name = rules.level_file(pid)[len("Course/"):]
        if lf is None:
            QtWidgets.QMessageBox.information(self, "Yaz0moto", "%s has no level file yet (Course/%s). Choose one from "
                                              "the check's 'choose a level' line first." % (pid, name))
            return
        if name not in self.world.course_copies:                # work on a copy, used on export
            work = Path(self.settings.value("work_dir", "") or (Path.home() / "KoopatlasDX levels"))
            work.mkdir(parents=True, exist_ok=True)
            dst = work / ("W%s_%s" % (self.world.id[1:], name))
            if not dst.exists():
                import shutil
                shutil.copyfile(lf, dst)
            before = dict(self.world.course_copies)
            after = dict(before, **{name: str(dst)})

            def put(v):
                self.world.course_copies.clear(); self.world.course_copies.update(v)
            self.undo.push(EditCommand("Edit %s's level" % pid, lambda: put(after), lambda: put(before), self._after_edit()))
            lf = dst
        try:
            subprocess.Popen([str(exe), str(lf)], cwd=str(exe.parent))
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Yaz0moto", "Yaz0moto could not be started:\n%s" % e)
            return
        self.statusBar().showMessage("Opened %s in Yaz0moto (if it doesn't open the level by itself, use File > Open "
                                     "and pick %s). Save there; Koopatlas DX reads the goals back." % (lf.name, lf), 15000)

    # ---- junctions and stick directions -------------------------------------------------------------------------
    def set_direction(self, pid, rid, direction, macro=None):
        """Make road rid leave pid going `direction` (north/east/south/west): its own bend is moved, or a new bend (K)
        is put a short way out that way. Returns the bend's name."""
        w = self.world
        ground_at = None
        try:
            gr = self.ground()
            ground_at = gr.ground_at
        except Exception:
            pass
        reuse, pos = directions.bend_for(w, pid, rid, direction, ground_at)
        if reuse:
            old = w.position(reuse)
            self.undo.push(EditCommand("%s leaves %s %s" % (rid, pid, direction), lambda: w.move_point(reuse, pos),
                                       lambda: w.move_point(reuse, old), self._after_edit(refresh_markers=True)))
            return reuse
        a, b = rules.route_ends(rid)
        k = w.free_name("bend")
        row = list(w.route_row(rid))
        idx = next(i for i, r in enumerate(w.route_rows) if r and r[0] == rid)
        pieces = ["R%s%s" % (a, k), "R%s%s" % (k, b)]

        def do():
            w.add_point(k, pos)
            w.remove_route(rid)
            w.insert_route(idx, [pieces[0], row[1], ""])
            w.insert_route(idx + 1, [pieces[1], row[1], ""])
            self._rename_in_paths(rid, pieces)

        def undo():
            for pc in pieces:
                w.remove_route(pc)
            w.new_bones.pop(k, None)
            w.insert_route(idx, row)
        self.undo.push(EditCommand("%s leaves %s %s" % (rid, pid, direction), do, undo,
                                   self._after_edit(refresh_markers=True, refresh_tree=True)))
        return k

    def _rename_in_paths(self, rid, pieces):
        """Nothing to rename: unlock paths are R<from><to> over chains of segments, so a split segment stays on them."""
        return None

    def directions_dialog(self, pid):
        """A compass: for each stick direction, the road it takes from pid. Changing one makes that road leave that way
        (a short bend); two roads in one direction are shown red, because the game keeps only one."""
        w = self.world
        roads = directions.roads_at(w, pid)
        if len(roads) < 1:
            return
        d = QtWidgets.QDialog(self); d.setWindowTitle("Stick directions at %s" % pid)
        g = QtWidgets.QGridLayout(d)
        note = QtWidgets.QLabel("Which road Mario takes from <b>%s</b> when the stick points each way. The game keeps "
                                "one road per direction, so at a junction each road needs its own. Changing one adds a "
                                "short bend so the road leaves that way; the points don't move." % pid)
        note.setWordWrap(True)
        g.addWidget(note, 0, 0, 1, 3)
        names = {rid: "%s (to %s)" % (rid, directions.far_end(w, pid, rid)) for rid, _o, _h in roads}
        combos = {}
        cells = {"north": (1, 1), "west": (2, 0), "east": (2, 2), "south": (3, 1)}
        table = directions.compass(w, pid)
        for dname, (r, c) in cells.items():
            box = QtWidgets.QGroupBox("%s %s" % (directions.ARROWS[dname], dname.capitalize()))
            bl = QtWidgets.QVBoxLayout(box)
            cb = QtWidgets.QComboBox()
            cb.addItem("— nothing —", None)
            for rid, _o, _h in roads:
                cb.addItem(names[rid], rid)
            cur = table[dname]
            if cur:
                cb.setCurrentIndex(cb.findData(cur[-1]))         # the game keeps the last one
            if len(cur) > 1:
                box.setStyleSheet("QGroupBox { color: #f87171; }")
                box.setToolTip("Also leaving %s: %s (lost in game)" % (dname, ", ".join(cur[:-1])))
            bl.addWidget(cb)
            combos[dname] = cb
            g.addWidget(box, r, c)
        mid = QtWidgets.QLabel("<b>%s</b>" % pid); mid.setAlignment(QtCore.Qt.AlignCenter)
        g.addWidget(mid, 2, 1)
        vert = [rid for rid, _o, h in roads if h is None]
        if vert:
            g.addWidget(QtWidgets.QLabel("↕ Up/down (climbs, taken by walking on through): %s" % ", ".join(vert)), 4, 0, 1, 3)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        g.addWidget(bb, 5, 0, 1, 3)
        self._dir_dialog = d
        if not d.exec_():
            return
        want = {cb.currentData(): dname for dname, cb in combos.items() if cb.currentData()}
        changes = [(rid, dn) for rid, dn in want.items() if dict((r, h) for r, _o, h in roads).get(rid) != dn]
        if not changes:
            return
        self.undo.beginMacro("Stick directions at %s" % pid)
        for rid, dn in changes:
            cur_rid = self._road_from(pid, rid)
            self.set_direction(pid, cur_rid, dn)
        self.undo.endMacro()
        self.select_point(pid)

    def _road_from(self, pid, rid):
        """rid, or the piece of it that now touches pid (after earlier splits in the same batch)."""
        w = self.world
        if w.route_row(rid) is not None:
            return rid
        a, b = rules.route_ends(rid)
        other = b if a == pid else a
        for r, o, _h in directions.roads_at(w, pid):
            if directions.far_end(w, pid, r) == other:
                return r
        return rid

    def branch_from(self, pid):
        """Pick a point: a road from pid to it is made, leaving pid (and arriving) in a free direction."""
        def picked(b):
            w = self.world
            if b == pid:
                return
            rid = "R%s%s" % (pid, b)
            if w.route_row(rid) or w.route_row("R%s%s" % (b, pid)):
                self.statusBar().showMessage("%s and %s are joined already." % (pid, b), 6000)
                return
            self.undo.beginMacro("Branch %s → %s" % (pid, b))
            self.add_route(rid)
            for end, other in ((pid, b), (b, pid)):
                cur = self._road_from(end, rid)                     # the piece touching `end` (may be split now)
                ends = rules.route_ends(cur)
                nb = ends[1] if ends[0] == end else ends[0]
                h = rules.heading(w.position(end), w.position(nb))
                clash = [r for r in directions.compass(w, end).get(h, []) if r != cur] if h else []
                if clash:
                    free = directions.free_direction(w, end, w.position(other), exclude=cur)
                    if free:
                        self.set_direction(end, cur, free)
                    else:
                        self.statusBar().showMessage("%s already has a road in every direction." % end, 8000)
            self.undo.endMacro()
            self.select_point(pid)
            self.statusBar().showMessage("Branch %s → %s made; each road leaves in its own direction." % (pid, b), 8000)
        self.start_pick("Branch from %s: click the point the new road goes to (Esc cancels)." % pid, picked)

    def set_unlock(self, path, expr):
        """Set who opens a path from an expression ('1-2 secret OR 1-3'): rewrites the point table, undoable."""
        from . import unlocks
        w = self.world
        if unlocks.describe(w.point_rows, path) == expr.strip():
            return
        try:
            terms = unlocks.parse(expr, w.point_ids)
            new = unlocks.apply(w.point_rows, path, terms)
        except unlocks.UnlockError as e:
            QtWidgets.QMessageBox.information(self, "Opens when", str(e))
            return
        old = [list(r) for r in w.point_rows]

        def put(rows):
            w.point_rows[:] = [list(r) for r in rows]
        self.undo.push(EditCommand("%s opens when %s" % (path, expr.strip() or "nothing"), lambda: put(new),
                                   lambda: put(old), self._after_edit(refresh_tree=True)))
        self.statusBar().showMessage("%s now opens when %s." % (path, unlocks.describe(w.point_rows, path) or
                                                                 "nothing (no point opens it)"), 8000)

    def secret_routes(self):
        """Segments only a secret exit opens (drawn pink): phase 4's distinct look for secret paths."""
        w = self.world
        normal, secret = set(), set()
        for r in w.point_rows:
            for col, bag in ((4, normal), (7, secret)):
                for path in rules.paths(r[col] if len(r) > col else ""):
                    bag.update(w.path_chain(path) or [])
        return secret - normal

    def ground(self):
        """kdx.ground.Ground of the current world's ground models (built once per opened world)."""
        g = getattr(self, "_ground", None)
        if g is None or g[0] is not self.scene:
            self._ground = (self.scene, ground.Ground(self.scene))
        return self._ground[1]

    def _missing_levels(self, rep):
        import re
        out = []
        for m in rep.errors:
            hit = re.match(r"Point (\S+) \(row \d+\): level file Course/(\S+) does not exist", m)
            if hit:
                out.append(hit.group(1))
        return out

    def _problem_fix(self, item):
        d = item.data(QtCore.Qt.UserRole)
        if not d:
            return
        if d[0] == "ground":
            self.follow_ground(d[1])
        elif d[0] == "level":
            self.choose_level(d[1])

    def follow_ground(self, rid):
        """Split a road with bends (K) on the ground where the straight line would go inside it. One undo step."""
        w = self.world
        a, b = rules.route_ends(rid)
        pa, pb = w.position(a), w.position(b)
        row = w.route_row(rid)
        if pa is None or pb is None or row is None:
            return
        bends = self.ground().follow(pa, pb, ground.FIT)
        if not bends:
            self.statusBar().showMessage("%s already stays above the ground." % rid, 6000)
            return
        names, taken = [], set()
        for _p in bends:
            n = w.free_name("bend")
            while n in taken:
                n = "K%03x" % (int(n[1:], 16) + 1)
            taken.add(n)
            names.append(n)
        chain = [a] + names + [b]
        pieces = ["R%s%s" % (x, y) for x, y in zip(chain, chain[1:])]
        idx = next(i for i, r in enumerate(w.route_rows) if r and r[0] == rid)
        old = list(row)

        def do():
            for n, p in zip(names, bends):
                w.add_point(n, p)
            w.remove_route(rid)
            for k, pc in enumerate(pieces):
                w.insert_route(idx + k, [pc, old[1], ""])

        def undo():
            for pc in pieces:
                w.remove_route(pc)
            for n in names:
                w.new_bones.pop(n, None)
            w.insert_route(idx, old)
        self.undo.push(EditCommand("Follow the ground on %s" % rid, do, undo,
                                   self._after_edit(refresh_markers=True, refresh_tree=True)))
        self.statusBar().showMessage("%s now follows the ground through %d bend(s): %s." % (
            rid, len(names), ", ".join(names)), 8000)

    def choose_level(self, pid):
        """A level file for a new level point: picked from the game's (or any) .sarc, copied into the mod on export
        as Course/<world>-<n>.sarc."""
        w = self.world
        ci = rules.course_index(pid)
        if ci < 0:
            return
        name = "%d-%d.sarc" % (w.number, ci + 1)
        root = self.romfs_root()
        start = str(root / "Course") if root and (root / "Course").is_dir() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Level for %s (saved as Course/%s in the mod)" % (pid, name), start, "Levels (*.sarc *.szs)")
        if not path:
            return
        before = dict(w.course_copies)
        after = dict(before, **{name: path})

        def put(v):
            w.course_copies.clear(); w.course_copies.update(v)
        self.undo.push(EditCommand("Level for %s" % pid, lambda: put(after), lambda: put(before), self._after_edit()))
        self.statusBar().showMessage("%s will use %s (copied into the mod as Course/%s)." % (pid, Path(path).name, name), 8000)

    def offset_dialog(self):
        if not self.world:
            return
        if not self.world.mario and not self.world.look:
            QtWidgets.QMessageBox.information(self, "No offset tables", "MarioOffsetDataW%d.bin / LookOffsetDataW%d.bin were not found. "
                                              "Put them next to the world archive or set the ROMFS folder in Preferences." % ((self.world.number,) * 2))
            return
        OffsetTablesDialog(self).exec_()

    def msbt_editor_path(self):
        p = Path(self.settings.value("msbt_editor", DEFAULT_MSBT_EDITOR) or DEFAULT_MSBT_EDITOR)
        if p.is_dir():
            exes = sorted(p.glob("*.exe"))
            return exes[0] if exes else None
        return p if p.exists() else None

    def open_msbt_editor(self):
        exe = self.msbt_editor_path()
        if not exe:
            QtWidgets.QMessageBox.information(self, "MSBT editor not found",
                                              "Set your MSBT editor (its folder or .exe) in Tools > Preferences.")
            return
        target = str(self.names.source) if self.names and self.names.source else None
        try:
            subprocess.Popen([str(exe)] + ([target] if target else []))
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Could not start the MSBT editor", str(e))

    def preferences_dialog(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Preferences")
        f = QtWidgets.QFormLayout(d)
        romfs_edit = QtWidgets.QLineEdit(self.settings.value("romfs_root", ""))
        names = QtWidgets.QLineEdit(self.settings.value("names_szs", ""))
        editor = QtWidgets.QLineEdit(self.settings.value("msbt_editor", DEFAULT_MSBT_EDITOR))
        mod = QtWidgets.QLineEdit(self.settings.value("mod_root", ""))
        f.addRow("Game romfs folder", romfs_edit)
        f.addRow("Game text archive (CommonMsg_*.szs)", names)
        f.addRow("MSBT editor (folder or .exe)", editor)
        f.addRow("Mod folder (SD card root or a staging folder)", mod)
        f.addRow(QtWidgets.QLabel(libs.summary(self.lib_report)))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec_():
            for key, ed in (("names_szs", names), ("msbt_editor", editor), ("mod_root", mod)):
                self.settings.setValue(key, ed.text().strip())
            root = romfs.normalize(romfs_edit.text().strip())
            if root:
                self.set_romfs(root, self.settings.value("language", "USen") or "USen")
            elif romfs_edit.text().strip():
                QtWidgets.QMessageBox.warning(self, "ROMFS folder not changed", "No world archives were found in that folder.")

    # ---- export --------------------------------------------------------------------------------------------------
    def export_dialog(self):
        if not self.world:
            return
        if not self.world.dirty:
            QtWidgets.QMessageBox.information(self, "Nothing to export", "No changes have been made to this world.")
            return
        found = self.run_checks()
        if found and QtWidgets.QMessageBox.warning(
                self, "Checks found errors", "\n".join(found[:12]) + "\n\nExport anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        root = QtWidgets.QFileDialog.getExistingDirectory(self, "Mod folder (SD card root or a staging folder)",
                                                          self.settings.value("mod_root", ""))
        if not root:
            return
        self.export_to(root)

    def compression_summary(self):
        if yaz0fast.available():
            return "Yaz0 compression: FastYZ (built in)"
        if self.lib_report.get("compressor"):
            return "Yaz0 compression: FastYZ (from a library folder)"
        return "Yaz0 compression: none available (%s); archives are saved uncompressed" % yaz0fast.load_error

    def export_to(self, root, quiet=False):
        fast = yaz0fast.available()
        compressor = None if fast else self.lib_report.get("compressor")
        mode = "auto" if (fast or compressor) else "stored"
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            written = self.world.export(root, compressor=compressor, compress=mode)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            if quiet:
                raise
            QtWidgets.QMessageBox.critical(self, "Export failed", str(e))
            return None
        QtWidgets.QApplication.restoreOverrideCursor()
        self.settings.setValue("mod_root", str(root))
        self.undo.setClean()
        for w in written:
            if str(w).lower().endswith(".szs") and Path(w).name.startswith("CS_W"):
                try:
                    from .export2d import unpacked_size
                    self.show_world_size(unpacked_size(w), "Exported")
                except OSError:
                    pass
        if not quiet:
            note = "" if (fast or compressor) else ("\n\nFastYZ could not be loaded, so the archive was saved without compression "
                                                    "(valid, but larger).")
            QtWidgets.QMessageBox.information(self, "Exported", "Written:\n" + "\n".join(str(p) for p in written) +
                                              "\n\nThe files you opened were not changed. Not tested in game yet: try it on a "
                                              "backup first." + note)
        return written

    # ---- misc ----------------------------------------------------------------------------------------------------
    def _update_title(self, _clean=None):
        name =("%s — " % self.world.id) if self.world else ""
        star = "*" if self.world and self.world.unsaved else ""
        self.setWindowTitle("%s%sKoopatlas DX %s" % (star, name, __version__))

    def _confirm_discard(self):
        return QtWidgets.QMessageBox.question(self, "Unsaved changes", "Discard the changes to %s?" % self.world.id,
                                              QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def closeEvent(self, e):
        if self.world and self.world.unsaved and not self._confirm_discard():
            e.ignore()
            return
        if not self.maker.undo.isClean() and not self.maker._confirm():
            e.ignore()
            return
        for d in getattr(self, "_maker_hid", None) or []:       # save the main editor's layout, not the maker's
            d.show()
        self.settings.setValue("main_geometry", self.saveGeometry())
        self.settings.setValue("main_state", self.saveState())
        if self._log_line in diagnostics.listeners:
            diagnostics.listeners.remove(self._log_line)
        e.accept()

    def world_models_dialog(self):
        """Tools > Edit this world's models: shapes taken out, textures replaced, objects added (applied on export)."""
        if not self.world:
            QtWidgets.QMessageBox.information(self, "World models", "Open a world first (Worlds panel or File > Open).")
            return
        import copy
        from .worldmodels import WorldModelsDialog
        w = self.world
        dlg = WorldModelsDialog(w, self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted or dlg.edits == w.model_edits:
            return
        old, new = copy.deepcopy(w.model_edits), copy.deepcopy(dlg.edits)

        def setv(v):
            w.model_edits = copy.deepcopy(v)
        self.undo.push(EditCommand("Edit world models", lambda: setv(new), lambda: setv(old), self._after_edit()))
        self.statusBar().showMessage("World models edited: applied when you export the world.", 8000)

    def copy_level_dialog(self):
        """Tools > Copy a level: a level archive (Course/<w>-<n>.sarc) copied into a mod under another number, its own
        tilesets (Pa1-Pa3) copied into the mod's Unit folder under the new level's name (kdx.levels)."""
        from . import levels
        root = self.romfs_root()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Copy a level with its own tilesets")
        f = QtWidgets.QFormLayout(dlg)
        settings = QtCore.QSettings()
        src = QtWidgets.QLineEdit(str(Path(root) / "Course" / "1-1.sarc") if root else "")
        mod = QtWidgets.QLineEdit(settings.value("levels/mod", ""))
        dst = QtWidgets.QLineEdit("1-6")
        own = QtWidgets.QCheckBox("Give it its own tilesets (Pa1-Pa3 copied and renamed; Pa0, the common set, stays shared)")
        own.setChecked(True)

        def browse_src():
            p, _ = QtWidgets.QFileDialog.getOpenFileName(dlg, "Level archive to copy", src.text(), "Levels (*.sarc)")
            if p:
                src.setText(p)

        def browse_mod():
            d = QtWidgets.QFileDialog.getExistingDirectory(dlg, "The mod's romfs folder", mod.text())
            if d:
                mod.setText(d)
        for edit, slot, label in ((src, browse_src, "Copy level"), (mod, browse_mod, "Into mod romfs")):
            h = QtWidgets.QHBoxLayout(); h.addWidget(edit, 1)
            b = QtWidgets.QPushButton("Browse…"); b.clicked.connect(slot); h.addWidget(b)
            f.addRow(label, h)
        f.addRow("As level", dst)
        f.addRow(own)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); f.addRow(bb)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        settings.setValue("levels/mod", mod.text())
        s, m, d = Path(src.text()), Path(mod.text()), dst.text().strip()
        if m.name.lower() != "romfs" and (m / "romfs").is_dir():
            m = m / "romfs"
        units = [m / "Unit"] + ([Path(root) / "Unit"] if root else []) + [s.parent.parent / "Unit"]
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            ren = levels.copy_level(s, m / "Course" / ("%s.sarc" % d), units, m / "Unit", s.stem, d, own.isChecked())
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Copy a level", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QMessageBox.information(self, "Copy a level", "Wrote Course/%s.sarc%s" % (
            d, "".join("\n- tileset %s -> Unit/%s.szs" % kv for kv in ren.items()) if ren else " (tilesets shared)"))

    def port_nsmbw_dialog(self):
        """Tools > Port an NSMBW world: NSMBW folder, world number, output folder; opens the result in the World Maker."""
        from . import nsmbw
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Port an NSMBW world")
        f = QtWidgets.QFormLayout(dlg)
        settings = QtCore.QSettings()
        src = QtWidgets.QLineEdit(settings.value("nsmbw/root", ""))
        out = QtWidgets.QLineEdit(settings.value("nsmbw/out", ""))
        world = QtWidgets.QSpinBox(); world.setRange(1, 9)

        def browse(edit, title):
            d = QtWidgets.QFileDialog.getExistingDirectory(dlg, title, edit.text())
            if d:
                edit.setText(d)
        for edit, title, label in ((src, "NSMBW game folder (the one with WorldMap)", "NSMBW folder"),
                                   (out, "Folder for the ported map and its models", "Save in")):
            h = QtWidgets.QHBoxLayout(); h.addWidget(edit, 1)
            b = QtWidgets.QPushButton("Browse…"); b.clicked.connect(lambda _=False, e=edit, t=title: browse(e, t)); h.addWidget(b)
            f.addRow(label, h)
        f.addRow("World", world)
        note = QtWidgets.QLabel("Reads WorldMap/CS_W<n>.arc, RouteInfo.arc and the cob*.arc scenery from your own NSMBW "
                                "dump. The models are converted to COLLADA (.dae) with PNG textures in the folder you "
                                "choose; the map opens in the World Maker, ready to edit and export over the NSMBUDX world "
                                "of the same number.")
        note.setWordWrap(True)
        f.addRow(note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec_() != QtWidgets.QDialog.Accepted or not src.text() or not out.text():
            return
        settings.setValue("nsmbw/root", src.text()); settings.setValue("nsmbw/out", out.text())
        if not self.maker.undo.isClean() and not self.maker._confirm():
            return
        folder = Path(out.text()) / ("NSMBW World %d" % world.value())
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            proj, notes = nsmbw.port_world(src.text(), world.value(), folder)
            path = folder / ("NSMBW World %d.kdxmap" % world.value())
            proj.save(path)
        except (nsmbw.PortError, ValueError, OSError) as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Port an NSMBW world", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.maker.open(str(path))
        self.tabs.setCurrentIndex(2)
        QtWidgets.QMessageBox.information(self, "Port an NSMBW world", "Saved %s\n\n- %s" % (path, "\n- ".join(notes)))

    def convert_model_dialog(self):
        """Tools > Convert a 3D model: any readable model (Wii .brres/.arc, FBX, DAE, OBJ) to COLLADA or OBJ."""
        from . import model3d
        src, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Model to convert", "", model3d.FORMATS)
        if not src:
            return
        dst, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save as", str(Path(src).with_suffix(".dae")),
                                                       model3d.SAVE_FORMATS)
        if not dst:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            m = model3d.load(src)
            model3d.save_model(m, dst)
        except (model3d.ModelError, OSError) as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Convert a 3D model", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QMessageBox.information(self, "Convert a 3D model", "Saved %s: %d materials, %d triangles." % (
            dst, len(m.parts), sum(p.triangle_count for p in m.parts)))

    def open_log_folder(self):
        from .diagnostics import LOG_DIR
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(LOG_DIR)))

    def about(self):
        QtWidgets.QMessageBox.about(self, "Koopatlas DX", "Koopatlas DX %s\nWorld editor for New Super Mario Bros. U Deluxe.\n\n"
                                    "BFRES layout from BfresLibrary (MIT). Named after Treeki's Koopatlas for NSMBW." % __version__)

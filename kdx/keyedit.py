"""
kdx/keyedit.py - the keyframe editor for new appear animations (kdx-bfres routemake on the kdx_appear bone).

Three channels, each a list of keys [frame, x, y, z]: "translate" (Move, world units), "rotate" (Turn, degrees, Euler
XYZ) and "scale" (Scale, 1 = full size). Between keys a channel is "linear" or "smooth" (eases in and out of every key),
written by the engine as the cubic curves the game's own animations use. Frame 0 is the pose the object waits in, hidden
or not, until its path opens.

    dlg = KeyframeEditor(keys, frames, interp, preview=callable(keys, frames, interp) or None, parent=None)
    if dlg.exec_(): dlg.keys, dlg.frames, dlg.interp
"""
from __future__ import annotations

import bisect
import copy

from PyQt5 import QtCore, QtGui, QtWidgets

CHANNELS = (("translate", "Move", 0.0), ("rotate", "Turn", 0.0), ("scale", "Scale", 1.0))
AXIS_COLORS = ("#ef4444", "#22c55e", "#3b82f6")


def channel_value(rows, frame, smooth=False, rest=0.0):
    """[x, y, z] of a channel at a frame, as the game evaluates the engine's curves (clamped at the ends)."""
    if not rows:
        return [rest] * 3
    if frame <= rows[0][0] or len(rows) == 1:
        return list(rows[0][1:4])
    if frame >= rows[-1][0]:
        return list(rows[-1][1:4])
    i = bisect.bisect_right([r[0] for r in rows], frame) - 1
    a, b = rows[i], rows[i + 1]
    t = (frame - a[0]) / max(1e-9, b[0] - a[0])
    w = t * t * (3 - 2 * t) if smooth else t
    return [a[k] + (b[k] - a[k]) * w for k in (1, 2, 3)]


def clean(keys, frames):
    """Keys sorted, inside 0..frames, one per frame."""
    out = {}
    for ch, _label, _rest in CHANNELS:
        rows = {}
        for r in keys.get(ch, []):
            f = float(min(max(0.0, r[0]), frames))
            rows[round(f, 3)] = [f, float(r[1]), float(r[2]), float(r[3])]
        if rows:
            out[ch] = [rows[k] for k in sorted(rows)]
    return out


class Timeline(QtWidgets.QWidget):
    """Rows of key diamonds (Move / Turn / Scale) over the frames, a playhead, and below them the selected channel's
    curves (X red, Y green, Z blue). Click a diamond to select it and drag it along; click elsewhere to move the
    playhead; double-click to add a key there."""
    changed = QtCore.pyqtSignal()
    picked = QtCore.pyqtSignal()

    ROW, LEFT, TOP, PLOT = 26, 64, 22, 150

    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.setMinimumSize(560, self.TOP + 3 * self.ROW + self.PLOT + 24)
        self.setMouseTracking(True)
        self._drag = None

    def fx(self, f):
        return self.LEFT + (self.width() - self.LEFT - 12) * f / max(1.0, self.ed.frames)

    def xf(self, x):
        return max(0.0, min(self.ed.frames, (x - self.LEFT) * self.ed.frames / max(1.0, self.width() - self.LEFT - 12)))

    def row_y(self, i):
        return self.TOP + i * self.ROW + self.ROW // 2

    def paintEvent(self, _e):
        ed = self.ed
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        fg, mid = pal.text().color(), pal.mid().color()
        # frame ruler
        p.setPen(mid)
        step = max(1, int(ed.frames / 10 / 5) * 5) if ed.frames > 20 else 5
        for f in range(0, int(ed.frames) + 1, step):
            x = self.fx(f)
            p.drawLine(QtCore.QPointF(x, self.TOP - 4), QtCore.QPointF(x, self.height() - 18))
            p.setPen(fg); p.drawText(QtCore.QPointF(x + 2, self.TOP - 8), str(f)); p.setPen(mid)
        # rows
        for i, (ch, label, _rest) in enumerate(CHANNELS):
            y = self.row_y(i)
            if ch == ed.channel:
                p.fillRect(QtCore.QRectF(0, y - self.ROW / 2, self.width(), self.ROW), QtGui.QColor(80, 140, 220, 40))
            p.setPen(fg)
            p.drawText(QtCore.QRectF(6, y - 10, self.LEFT - 8, 20), QtCore.Qt.AlignVCenter, label)
            for k, r in enumerate(ed.keys.get(ch, [])):
                sel = (ch, k) == (ed.channel, ed.key)
                d = 7 if sel else 5
                x = self.fx(r[0])
                poly = QtGui.QPolygonF([QtCore.QPointF(x, y - d), QtCore.QPointF(x + d, y), QtCore.QPointF(x, y + d),
                                        QtCore.QPointF(x - d, y)])
                p.setPen(QtGui.QPen(fg, 1))
                p.setBrush(QtGui.QColor("#facc15") if sel else QtGui.QColor("#9fb3c8"))
                p.drawPolygon(poly)
        # curves of the selected channel
        rows = ed.keys.get(ed.channel, [])
        top = self.TOP + 3 * self.ROW + 8
        box = QtCore.QRectF(self.LEFT, top, self.width() - self.LEFT - 12, self.PLOT)
        p.setPen(mid); p.setBrush(QtCore.Qt.NoBrush); p.drawRect(box)
        rest = dict((c, r) for c, _l, r in CHANNELS)[ed.channel]
        n = 120
        samples = [channel_value(rows, ed.frames * j / n, ed.interp.get(ed.channel) == "smooth", rest) for j in range(n + 1)]
        vals = [v for s in samples for v in s] + [rest]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-6:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad

        def vy(v):
            return top + self.PLOT - (v - lo) / (hi - lo) * self.PLOT
        p.setPen(fg)
        p.drawText(QtCore.QPointF(4, top + 10), "%.4g" % hi)
        p.drawText(QtCore.QPointF(4, top + self.PLOT), "%.4g" % lo)
        for ax in range(3):
            p.setPen(QtGui.QPen(QtGui.QColor(AXIS_COLORS[ax]), 2))
            path = QtGui.QPainterPath()
            for j, s in enumerate(samples):
                pt = QtCore.QPointF(self.fx(ed.frames * j / n), vy(s[ax]))
                path.lineTo(pt) if j else path.moveTo(pt)
            p.drawPath(path)
        # playhead
        x = self.fx(ed.playhead)
        p.setPen(QtGui.QPen(QtGui.QColor("#f97316"), 2))
        p.drawLine(QtCore.QPointF(x, self.TOP - 4), QtCore.QPointF(x, self.height() - 18))
        p.setPen(fg)
        p.drawText(QtCore.QPointF(self.LEFT, self.height() - 4),
                   "X red · Y green · Z blue    frame %d of %d (60 = 1 s)" % (ed.playhead, ed.frames))
        p.end()

    def _hit(self, pos):
        for i, (ch, _l, _r) in enumerate(CHANNELS):
            if abs(pos.y() - self.row_y(i)) <= self.ROW / 2:
                for k, r in enumerate(self.ed.keys.get(ch, [])):
                    if abs(pos.x() - self.fx(r[0])) <= 7:
                        return ch, k
                return ch, None
        return None, None

    def mousePressEvent(self, e):
        ch, k = self._hit(e.pos())
        if ch:
            self.ed.channel = ch
        if k is not None:
            self.ed.key = k
            self._drag = (ch, k)
        else:
            self.ed.key = None
            self.ed.playhead = int(round(self.xf(e.x())))
        self.picked.emit()
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() & QtCore.Qt.LeftButton:
            ch, k = self._drag
            rows = self.ed.keys[ch]
            f = float(round(self.xf(e.x())))
            if all(abs(r[0] - f) > 0.5 for j, r in enumerate(rows) if j != k):
                rows[k][0] = f
                order = sorted(range(len(rows)), key=lambda j: rows[j][0])
                self.ed.keys[ch] = [rows[j] for j in order]
                k = order.index(k)
                self._drag, self.ed.key = (ch, k), k
                self.changed.emit()
                self.update()
        elif not e.buttons() and self._hit(e.pos())[1] is not None:
            self.setCursor(QtCore.Qt.SizeHorCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        ch, k = self._hit(e.pos())
        if ch and k is None:
            self.ed.channel = ch
            self.ed.playhead = int(round(self.xf(e.x())))
            self.ed.add_key()


class KeyframeEditor(QtWidgets.QDialog):
    def __init__(self, keys, frames, interp=None, preview=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyframes: the appear animation")
        self.resize(760, 560)
        self.frames = int(frames)
        self.keys = clean(copy.deepcopy(keys or {}), self.frames)
        self.interp = dict(interp or {})
        self.channel, self.key, self.playhead = "translate", None, 0
        self._preview = preview
        lay = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel("Frame 0 is how the object waits until its path opens (scale 0 = hidden). Click a "
                                "diamond to edit it, drag it to retime it, double-click a row to add a key.")
        note.setWordWrap(True)
        lay.addWidget(note)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Length"))
        self.len_spin = QtWidgets.QSpinBox(); self.len_spin.setRange(10, 600); self.len_spin.setValue(self.frames)
        self.len_spin.setSuffix(" frames")
        self.len_spin.valueChanged.connect(self._length)
        top.addWidget(self.len_spin)
        top.addStretch(1)
        top.addWidget(QtWidgets.QLabel("Start from"))
        self.preset = QtWidgets.QComboBox()
        from . import events
        for k, txt in events.PRESETS.items():
            self.preset.addItem(k.capitalize(), k)
        self.preset.setToolTip("Replace the keys with a preset's, to adjust")
        top.addWidget(self.preset)
        b = QtWidgets.QPushButton("Use"); b.clicked.connect(self._use_preset); top.addWidget(b)
        lay.addLayout(top)
        self.timeline = Timeline(self)
        self.timeline.changed.connect(self._sync_fields)
        self.timeline.picked.connect(self._sync_fields)
        lay.addWidget(self.timeline, 1)
        # the selected key
        box = QtWidgets.QGroupBox("Key")
        f = QtWidgets.QGridLayout(box)
        self.chan_label = QtWidgets.QLabel()
        f.addWidget(self.chan_label, 0, 0)
        self.frame_spin = QtWidgets.QDoubleSpinBox(); self.frame_spin.setDecimals(0); self.frame_spin.setPrefix("frame ")
        f.addWidget(self.frame_spin, 0, 1)
        self.val = []
        for ax, name in enumerate("XYZ"):
            sp = QtWidgets.QDoubleSpinBox(); sp.setRange(-100000, 100000); sp.setDecimals(2); sp.setPrefix(name + " ")
            sp.setStyleSheet("color: %s" % AXIS_COLORS[ax])
            f.addWidget(sp, 0, 2 + ax)
            self.val.append(sp)
        self.interp_box = QtWidgets.QComboBox()
        self.interp_box.addItem("Linear between keys", "linear"); self.interp_box.addItem("Smooth (ease in and out)", "smooth")
        f.addWidget(self.interp_box, 1, 0, 1, 2)
        addb = QtWidgets.QPushButton("+ Key at playhead"); addb.clicked.connect(self.add_key); f.addWidget(addb, 1, 2)
        self.del_btn = QtWidgets.QPushButton("Delete key"); self.del_btn.clicked.connect(self.delete_key)
        f.addWidget(self.del_btn, 1, 3)
        self.uniform = QtWidgets.QCheckBox("Same X/Y/Z"); self.uniform.setToolTip("Scale evenly")
        f.addWidget(self.uniform, 1, 4)
        lay.addWidget(box)
        for w in [self.frame_spin] + self.val:
            w.valueChanged.connect(self._edited)
        self.interp_box.currentIndexChanged.connect(self._interp)
        row = QtWidgets.QHBoxLayout()
        self.play_btn = QtWidgets.QPushButton("▶ Preview in 3D")
        self.play_btn.setToolTip("Builds the animation as it will be exported and plays it in the 3D view")
        self.play_btn.clicked.connect(lambda: self._preview and self._preview(self.result_keys(), self.frames,
                                                                              dict(self.interp)))
        self.play_btn.setEnabled(preview is not None)
        row.addWidget(self.play_btn)
        row.addStretch(1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self._sync_fields()

    # ---- data ----------------------------------------------------------------------------------------------------
    def result_keys(self):
        return clean(self.keys, self.frames)

    def accept(self):
        self.keys = self.result_keys()
        super().accept()

    def _rest(self, ch):
        return dict((c, r) for c, _l, r in CHANNELS)[ch]

    def add_key(self):
        rows = self.keys.setdefault(self.channel, [])
        f = float(self.playhead)
        k = next((j for j, r in enumerate(rows) if abs(r[0] - f) < 0.5), None)
        if k is None:
            v = channel_value(rows, f, self.interp.get(self.channel) == "smooth", self._rest(self.channel))
            rows.append([f] + v)
            rows.sort(key=lambda r: r[0])
            k = next(j for j, r in enumerate(rows) if r[0] == f)
        self.key = k
        self._sync_fields()
        self.timeline.update()

    def delete_key(self):
        rows = self.keys.get(self.channel, [])
        if self.key is not None and self.key < len(rows):
            del rows[self.key]
        self.key = None
        self._sync_fields()
        self.timeline.update()

    def _use_preset(self):
        from . import events
        self.keys = clean(events.preset_keys(self.preset.currentData(), self.frames, 200.0), self.frames)
        self.key = None
        self._sync_fields()
        self.timeline.update()

    def _length(self, n):
        old, self.frames = self.frames, int(n)
        for rows in self.keys.values():             # keys keep their place in time, relative to the length
            for r in rows:
                r[0] = round(r[0] * self.frames / max(1, old))
        self.keys = clean(self.keys, self.frames)
        self.playhead = min(self.playhead, self.frames)
        self._sync_fields()
        self.timeline.update()

    def _interp(self, _i):
        self.interp[self.channel] = self.interp_box.currentData()
        self.timeline.update()

    # ---- fields --------------------------------------------------------------------------------------------------
    def _sync_fields(self):
        self._syncing = True
        label = dict((c, l) for c, l, _r in CHANNELS)[self.channel]
        units = {"translate": "units", "rotate": "degrees", "scale": "× size"}[self.channel]
        self.chan_label.setText("<b>%s</b> (%s)" % (label, units))
        rows = self.keys.get(self.channel, [])
        has = self.key is not None and self.key < len(rows)
        self.frame_spin.setRange(0, self.frames)
        for w in [self.frame_spin] + self.val:
            w.setEnabled(has)
        self.del_btn.setEnabled(has)
        self.uniform.setVisible(self.channel == "scale")
        if has:
            r = rows[self.key]
            self.frame_spin.setValue(r[0])
            for sp, v in zip(self.val, r[1:4]):
                sp.setValue(v)
        self.interp_box.setCurrentIndex(1 if self.interp.get(self.channel) == "smooth" else 0)
        self._syncing = False

    def _edited(self, _v):
        if getattr(self, "_syncing", False):
            return
        rows = self.keys.get(self.channel, [])
        if self.key is None or self.key >= len(rows):
            return
        r = rows[self.key]
        vals = [sp.value() for sp in self.val]
        if self.channel == "scale" and self.uniform.isChecked():
            changed = next((v for v, old in zip(vals, r[1:4]) if abs(v - old) > 1e-9), None)
            if changed is not None:
                vals = [changed] * 3
                self._syncing = True
                for sp in self.val:
                    sp.setValue(changed)
                self._syncing = False
        f = self.frame_spin.value()
        if all(abs(o[0] - f) > 0.5 for j, o in enumerate(rows) if j != self.key):
            r[0] = f
        r[1:4] = vals
        rows.sort(key=lambda x: x[0])
        self.key = rows.index(r)
        self.timeline.update()

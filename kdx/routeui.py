"""
kdx/routeui.py - how routes look and are picked in the editors.

RouteItem draws a route the way its action plays in the game, so a map reads at a glance:
  walk   a road band in the surface's colour with footstep dots;
  slide  a band with chevrons pointing downhill (towards the lower end);
  climb  two rails and rungs (ladders), or a twisting stem with leaves (vine);
  jump   a dotted arc over the gap.
Each route has a badge with its action's icon in the middle, arrows showing its direction, a glow when selected,
a highlight on hover and a red dashed outline when the checks report it.

ActionPalette is a Mario Maker style grid of the 23 actions (icon buttons in groups, tooltips saying what the game
does). action_icon(name) is the same icon for menus and lists.
"""
from __future__ import annotations

import math

from PyQt5 import QtCore, QtGui, QtWidgets

from . import routeactions as RA

P = QtCore.QPointF


# ---- icons ------------------------------------------------------------------------------------------------------------
def paint_glyph(p, a, rect):
    """Draw the action's glyph inside rect (QRectF) with painter p."""
    c = QtGui.QColor(a.colour)
    dark = QtGui.QColor("#1f2937")
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cx, cy = x + w / 2, y + h / 2
    pen = QtGui.QPen(dark, max(1.2, w / 14), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    p.setPen(pen)
    if a.style == "climb" and a.name == "ツタ":
        path = QtGui.QPainterPath(P(cx, y + h * 0.9))
        path.cubicTo(P(cx - w * 0.3, y + h * 0.6), P(cx + w * 0.3, y + h * 0.4), P(cx, y + h * 0.1))
        p.setPen(QtGui.QPen(QtGui.QColor("#15803d"), w / 9, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawPath(path)
        p.setBrush(c); p.setPen(QtGui.QPen(dark, w / 20))
        for fy, sx in ((0.35, -1), (0.6, 1)):
            p.drawEllipse(P(cx + sx * w * 0.18, y + h * fy), w * 0.13, h * 0.08)
    elif a.style == "climb":
        p.save()
        if a.facing:
            p.translate(cx, cy); p.rotate(-25 if a.facing == "left" else 25); p.translate(-cx, -cy)
        rail = QtGui.QPen(dark, w / 10, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        p.setPen(rail)
        if "縄" in a.name:
            for sx in (-1, 1):
                path = QtGui.QPainterPath(P(cx + sx * w * 0.2, y + h * 0.1))
                path.cubicTo(P(cx + sx * w * 0.28, y + h * 0.4), P(cx + sx * w * 0.12, y + h * 0.6),
                             P(cx + sx * w * 0.2, y + h * 0.9))
                p.drawPath(path)
        else:
            p.drawLine(P(cx - w * 0.2, y + h * 0.1), P(cx - w * 0.2, y + h * 0.9))
            p.drawLine(P(cx + w * 0.2, y + h * 0.1), P(cx + w * 0.2, y + h * 0.9))
        p.setPen(QtGui.QPen(c.darker(115) if "岩" not in a.name else c, w / 11, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        for k in range(4):
            yy = y + h * (0.22 + 0.19 * k)
            p.drawLine(P(cx - w * 0.2, yy), P(cx + w * 0.2, yy))
        p.restore()
    elif a.style == "jump":
        path = QtGui.QPainterPath(P(x + w * 0.12, y + h * 0.8))
        path.quadTo(P(cx, y - h * 0.15), P(x + w * 0.88, y + h * 0.8))
        p.setPen(QtGui.QPen(dark, w / 12, QtCore.Qt.DotLine, QtCore.Qt.RoundCap))
        p.drawPath(path)
        p.setBrush(c); p.setPen(QtGui.QPen(dark, w / 20))
        p.drawEllipse(P(cx, y + h * 0.3), w * 0.13, w * 0.13)
        p.drawLine(P(x + w * 0.05, y + h * 0.85), P(x + w * 0.3, y + h * 0.85))
        p.drawLine(P(x + w * 0.7, y + h * 0.85), P(x + w * 0.95, y + h * 0.85))
    elif a.style == "slide":
        p.setBrush(c)
        p.drawPolygon(QtGui.QPolygonF([P(x + w * 0.1, y + h * 0.25), P(x + w * 0.9, y + h * 0.8), P(x + w * 0.1, y + h * 0.8)]))
        p.setPen(QtGui.QPen(dark, w / 12, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        for k in (0.35, 0.55):
            p.drawPolyline(QtGui.QPolygonF([P(x + w * k, y + h * 0.42), P(x + w * (k + 0.12), y + h * 0.55),
                                             P(x + w * k, y + h * 0.68)]))
    else:
        _walk_glyph(p, a, x, y, w, h, c, dark)


def _walk_glyph(p, a, x, y, w, h, c, dark):
    """A small picture of the surface."""
    cx, cy = x + w / 2, y + h / 2
    thin = QtGui.QPen(dark, max(1.0, w / 16), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    n = a.name
    if n in ("木", "石橋"):                                    # bridge: deck with planks or an arch
        p.setPen(thin); p.setBrush(c)
        if n == "石橋":
            path = QtGui.QPainterPath(P(x + w * 0.05, y + h * 0.75))
            path.lineTo(P(x + w * 0.05, y + h * 0.4)); path.lineTo(P(x + w * 0.95, y + h * 0.4))
            path.lineTo(P(x + w * 0.95, y + h * 0.75)); path.lineTo(P(x + w * 0.78, y + h * 0.75))
            path.quadTo(P(cx, y + h * 0.42), P(x + w * 0.22, y + h * 0.75)); path.closeSubpath()
            p.drawPath(path)
        else:
            p.drawRect(QtCore.QRectF(x + w * 0.05, y + h * 0.38, w * 0.9, h * 0.26))
            for k in range(1, 5):
                xx = x + w * (0.05 + 0.18 * k)
                p.drawLine(P(xx, y + h * 0.38), P(xx, y + h * 0.64))
            p.drawLine(P(x + w * 0.05, y + h * 0.3), P(x + w * 0.95, y + h * 0.3))
    elif n == "雲":
        p.setPen(thin); p.setBrush(c)
        path = QtGui.QPainterPath()
        for ex, ey, r in ((0.32, 0.58, 0.18), (0.5, 0.45, 0.22), (0.7, 0.58, 0.17)):
            path.addEllipse(P(x + w * ex, y + h * ey), w * r, h * r)
        path.addRect(QtCore.QRectF(x + w * 0.2, y + h * 0.58, w * 0.6, h * 0.16))
        p.drawPath(path.simplified())
    elif n == "水":
        p.setPen(QtGui.QPen(c.darker(130), w / 9, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        for yy in (0.38, 0.62):
            path = QtGui.QPainterPath(P(x + w * 0.1, y + h * yy))
            for k in range(4):
                path.quadTo(P(x + w * (0.2 + 0.2 * k), y + h * (yy - 0.12 if k % 2 == 0 else yy + 0.12)),
                            P(x + w * (0.3 + 0.2 * k), y + h * yy))
            p.drawPath(path)
    elif n == "雪":
        p.setPen(QtGui.QPen(QtGui.QColor("#38bdf8"), w / 11, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        for k in range(3):
            ang = math.radians(60 * k)
            dx, dy = math.cos(ang) * w * 0.32, math.sin(ang) * h * 0.32
            p.drawLine(P(cx - dx, cy - dy), P(cx + dx, cy + dy))
    elif n == "氷":
        p.setPen(thin); p.setBrush(c)
        p.drawPolygon(QtGui.QPolygonF([P(cx, y + h * 0.12), P(x + w * 0.78, cy), P(cx, y + h * 0.88), P(x + w * 0.22, cy)]))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 220), w / 14))
        p.drawLine(P(cx - w * 0.08, y + h * 0.32), P(cx + w * 0.06, y + h * 0.45))
    elif n == "スイッチブロック":
        p.setPen(thin); p.setBrush(c)
        p.drawRoundedRect(QtCore.QRectF(x + w * 0.2, y + h * 0.2, w * 0.6, h * 0.6), w * 0.08, w * 0.08)
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), w / 9, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawLine(P(cx, y + h * 0.32), P(cx, y + h * 0.55)); p.drawPoint(P(cx, y + h * 0.68))
    elif n == "石":
        p.setPen(thin); p.setBrush(c)
        for ex, ey, rx, ry in ((0.3, 0.42, 0.17, 0.13), (0.66, 0.4, 0.15, 0.12), (0.46, 0.66, 0.2, 0.13)):
            p.drawEllipse(P(x + w * ex, y + h * ey), w * rx, h * ry)
    elif n == "流砂":
        p.setPen(thin); p.setBrush(c)
        p.drawEllipse(P(cx, cy), w * 0.36, h * 0.24)
        p.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath(P(cx + w * 0.22, cy))
        path.quadTo(P(cx, cy - h * 0.18), P(cx - w * 0.14, cy))
        path.quadTo(P(cx, cy + h * 0.12), P(cx + w * 0.04, cy))
        p.drawPath(path)
    else:                                                   # road, dirt, sand: a path strip
        p.setPen(thin); p.setBrush(c)
        path = QtGui.QPainterPath(P(x + w * 0.05, y + h * 0.78))
        path.cubicTo(P(x + w * 0.35, y + h * 0.78), P(x + w * 0.45, y + h * 0.3), P(x + w * 0.95, y + h * 0.28))
        st = QtGui.QPainterPathStroker(); st.setWidth(h * 0.3); st.setCapStyle(QtCore.Qt.RoundCap)
        p.drawPath(st.createStroke(path))
        if n == "砂":
            p.setPen(QtCore.Qt.NoPen); p.setBrush(c.darker(140))
            for ex, ey in ((0.25, 0.75), (0.5, 0.55), (0.75, 0.33)):
                p.drawEllipse(P(x + w * ex, y + h * ey), w * 0.035, w * 0.035)


_ICON_CACHE = {}


def action_icon(name, size=32):
    key = (name, size)
    if key not in _ICON_CACHE:
        a = RA.action(name)
        pm = QtGui.QPixmap(size * 2, size * 2)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setBrush(QtGui.QColor(255, 255, 255, 235)); p.setPen(QtGui.QPen(QtGui.QColor(a.colour).darker(140), 3))
        p.drawRoundedRect(QtCore.QRectF(2, 2, size * 2 - 4, size * 2 - 4), size * 0.45, size * 0.45)
        paint_glyph(p, a, QtCore.QRectF(size * 0.25, size * 0.25, size * 1.5, size * 1.5))
        p.end()
        _ICON_CACHE[key] = QtGui.QIcon(pm)
    return _ICON_CACHE[key]


# ---- the route on a map -----------------------------------------------------------------------------------------------
class RouteItem(QtWidgets.QGraphicsObject):
    """One route between two map points, drawn as its action plays. Scene units; the band keeps a screen width."""
    WIDTH = 9.0                     # screen pixels

    def __init__(self, rid, a, b, rtype, selected=False, problem="", heights=(0.0, 0.0)):
        super().__init__()
        self.rid, self.rtype = rid, rtype
        self.a, self.b = QtCore.QPointF(*a), QtCore.QPointF(*b)
        self.selected, self.problem, self.hover = selected, problem, False
        self.glow = None
        self._k = 1.0                   # scene units per screen pixel the bounding box was made for
        self._rezoom_queued = False
        self.heights = heights
        self.setAcceptHoverEvents(True)
        self.setZValue(25 if selected else 20)
        act = RA.action(rtype)
        tip = "%s → %s\n%s" % (rid[1:5], rid[5:9], act.description)
        if not RA.known(rtype):
            tip += "\nThe CSV says %r, which the game does not know: it walks as road." % rtype
        if problem:
            tip += "\n\n⚠ " + problem
        self.setToolTip(tip)

    def line(self):
        return QtCore.QLineF(self.a, self.b)

    def setLine(self, line):
        """Move the ends (QLineF), as QGraphicsLineItem does."""
        self.prepareGeometryChange()
        self.a, self.b = QtCore.QPointF(line.p1()), QtCore.QPointF(line.p2())
        self.update()

    def set_highlight(self, colour=None, dim=False):
        """A coloured glow (a path being shown) or dimmed (another path is shown)."""
        self.glow = QtGui.QColor(colour) if colour is not None else None
        self.setOpacity(0.45 if dim and colour is None else 1.0)
        self.setZValue(26 if colour is not None else (25 if self.selected else 20))
        self.update()

    def _scale(self):
        v = self.scene().views()[0] if self.scene() and self.scene().views() else None
        return 1.0 / (v.transform().m11() or 1.0) if v else 1.0

    def _rezoom(self):
        """The view zoomed: tell the scene the box changes before it does (a box that changes silently crashes Qt)."""
        try:
            self._rezoom_queued = False
            k = self._scale()
        except RuntimeError:                                  # the item was deleted before the timer fired
            return
        if abs(k - self._k) > 1e-9 * max(k, 1.0):
            self.prepareGeometryChange()
            self._k = k
            self.update()

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemSceneHasChanged and value is not None:
            QtCore.QTimer.singleShot(0, self._rezoom)
        return super().itemChange(change, value)

    def boundingRect(self):
        k = self._k * (self.WIDTH * 3 + 24)
        r = QtCore.QRectF(self.a, self.b).normalized()
        return r.adjusted(-k, -k, k, k)

    def shape(self):
        path = QtGui.QPainterPath(self.a)
        path.lineTo(self.b)
        st = QtGui.QPainterPathStroker()
        st.setWidth(self._k * (self.WIDTH + 8))
        return st.createStroke(path)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def paint(self, p, _opt, _w=None):
        a = RA.action(self.rtype)
        k = self._k
        if not self._rezoom_queued and abs(self._scale() - k) > 1e-9 * max(k, 1.0):
            self._rezoom_queued = True
            QtCore.QTimer.singleShot(0, self._rezoom)
        W = self.WIDTH * k
        L = QtCore.QLineF(self.a, self.b)
        n = L.length()
        if n < 1e-6:
            return
        ux, uy = (self.b.x() - self.a.x()) / n, (self.b.y() - self.a.y()) / n
        nx, ny = -uy, ux
        col = QtGui.QColor(a.colour)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if self.selected or self.hover or self.glow is not None:      # glow
            g = QtGui.QColor(self.glow) if self.glow is not None else QtGui.QColor("#d946ef" if self.selected else "#ffffff")
            g.setAlpha(200 if self.glow is not None else 150 if self.selected else 90)
            p.setPen(QtGui.QPen(g, W + 8 * k, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(L)
        if a.style == "jump":
            lift = min(n * 0.35, 60 * k + n * 0.15)
            ctrl = P((self.a.x() + self.b.x()) / 2 + nx * 0 - 0, (self.a.y() + self.b.y()) / 2 - lift)
            path = QtGui.QPainterPath(self.a)
            path.quadTo(ctrl, self.b)
            p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 110), W * 0.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(L)                                    # the ground under the jump, faint
            p.setPen(QtGui.QPen(col, W * 0.55, QtCore.Qt.DotLine, QtCore.Qt.RoundCap))
            p.drawPath(path)
            mid = path.pointAtPercent(0.5)
        else:
            outline = QtGui.QColor(col).darker(170)
            p.setPen(QtGui.QPen(outline, W + 2.5 * k, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(L)
            p.setPen(QtGui.QPen(col, W, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(L)
            mid = L.pointAt(0.5)
            dark = QtGui.QColor(col).darker(200)
            step = 14 * k
            count = int(n / step)
            if a.style == "climb" and a.name == "ツタ":
                p.setPen(QtGui.QPen(QtGui.QColor("#166534"), W * 0.25))
                path = QtGui.QPainterPath(self.a)
                for i in range(1, count + 1):
                    t = i / max(count, 1)
                    s = (1 if i % 2 else -1) * W * 0.35
                    path.lineTo(P(self.a.x() + (self.b.x() - self.a.x()) * t + nx * s,
                                  self.a.y() + (self.b.y() - self.a.y()) * t + ny * s))
                p.drawPath(path)
            elif a.style == "climb":
                p.setPen(QtGui.QPen(dark, max(1.0, W * 0.18)))
                for s in (-0.38, 0.38):
                    p.drawLine(P(self.a.x() + nx * W * s, self.a.y() + ny * W * s),
                               P(self.b.x() + nx * W * s, self.b.y() + ny * W * s))
                for i in range(1, count):
                    t = i / count
                    cxp, cyp = self.a.x() + (self.b.x() - self.a.x()) * t, self.a.y() + (self.b.y() - self.a.y()) * t
                    p.drawLine(P(cxp + nx * W * 0.38, cyp + ny * W * 0.38), P(cxp - nx * W * 0.38, cyp - ny * W * 0.38))
            elif a.style == "slide":
                down = 1.0 if self.heights[1] <= self.heights[0] else -1.0   # chevrons point to the lower end
                p.setPen(QtGui.QPen(dark, max(1.0, W * 0.16), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
                for i in range(1, count):
                    t = i / count
                    cxp, cyp = self.a.x() + (self.b.x() - self.a.x()) * t, self.a.y() + (self.b.y() - self.a.y()) * t
                    tip = P(cxp + ux * W * 0.3 * down, cyp + uy * W * 0.3 * down)
                    p.drawPolyline(QtGui.QPolygonF([P(cxp + nx * W * 0.35 - ux * W * 0.1 * down, cyp + ny * W * 0.35 - uy * W * 0.1 * down),
                                                     tip,
                                                     P(cxp - nx * W * 0.35 - ux * W * 0.1 * down, cyp - ny * W * 0.35 - uy * W * 0.1 * down)]))
            else:                                            # walk: footprints, planks on bridges
                bridge = "橋" in a.name or a.name == "木"
                p.setPen(QtGui.QPen(dark, max(1.0, W * 0.12)) if bridge else QtCore.Qt.NoPen)
                p.setBrush(dark)
                for i in range(1, count):
                    t = i / count
                    cxp, cyp = self.a.x() + (self.b.x() - self.a.x()) * t, self.a.y() + (self.b.y() - self.a.y()) * t
                    if bridge:
                        p.drawLine(P(cxp + nx * W * 0.45, cyp + ny * W * 0.45), P(cxp - nx * W * 0.45, cyp - ny * W * 0.45))
                    else:
                        s = (0.18 if i % 2 else -0.18) * W
                        p.drawEllipse(P(cxp + nx * s, cyp + ny * s), W * 0.11, W * 0.11)
        if self.problem:
            p.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), 2.0 * k, QtCore.Qt.DashLine))
            p.setBrush(QtCore.Qt.NoBrush)
            for s in (-1, 1):
                p.drawLine(P(self.a.x() + nx * (W * 0.75) * s, self.a.y() + ny * (W * 0.75) * s),
                           P(self.b.x() + nx * (W * 0.75) * s, self.b.y() + ny * (W * 0.75) * s))
        # direction arrows at a third and two thirds
        if n > 60 * k:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 255, 255, 200))
            for t in (0.25, 0.75):
                c = L.pointAt(t)
                s = W * 0.5
                p.drawPolygon(QtGui.QPolygonF([P(c.x() + ux * s, c.y() + uy * s),
                                                P(c.x() - ux * s * 0.6 + nx * s * 0.7, c.y() - uy * s * 0.6 + ny * s * 0.7),
                                                P(c.x() - ux * s * 0.6 - nx * s * 0.7, c.y() - uy * s * 0.6 - ny * s * 0.7)]))
        # the action badge
        if n > 70 * k or self.selected:
            r = 11 * k
            p.setPen(QtGui.QPen(QtGui.QColor("#d946ef") if self.selected else QtGui.QColor(a.colour).darker(150), 1.6 * k))
            p.setBrush(QtGui.QColor(255, 255, 255, 240))
            p.drawEllipse(mid, r, r)
            paint_glyph(p, a, QtCore.QRectF(mid.x() - r * 0.72, mid.y() - r * 0.72, r * 1.44, r * 1.44))
            if self.problem:
                p.setBrush(QtGui.QColor("#ef4444")); p.setPen(QtCore.Qt.NoPen)
                p.drawEllipse(P(mid.x() + r * 0.8, mid.y() - r * 0.8), r * 0.38, r * 0.38)


# ---- the palette ------------------------------------------------------------------------------------------------------
class ActionPalette(QtWidgets.QWidget):
    """A grid of the 23 route actions, in groups. Combo-box-like API: currentData(), findData(), setCurrentIndex(),
    currentIndex(), activated(int) (a click by the user)."""
    activated = QtCore.pyqtSignal(int)

    def __init__(self, parent=None, columns=6, icon=30, show_labels=False):
        super().__init__(parent)
        self._buttons = []
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(3)
        row = 0
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        for g in RA.GROUPS:
            lab = QtWidgets.QLabel(g)
            lab.setObjectName("hint")
            grid.addWidget(lab, row, 0, 1, columns)
            row += 1
            col = 0
            for a in [x for x in RA.ACTIONS if x.group == g]:
                b = QtWidgets.QToolButton()
                b.setIcon(action_icon(a.name, icon))
                b.setIconSize(QtCore.QSize(icon, icon))
                b.setCheckable(True)
                b.setAutoRaise(True)
                b.setToolTip(a.description)
                b.setStatusTip(a.description)
                if show_labels:
                    b.setText(a.label.replace(" (", chr(10) + "("))
                    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
                    b.setMinimumWidth(icon + 46)
                    f = b.font(); f.setPointSizeF(max(7.0, f.pointSizeF() - 1)); b.setFont(f)
                idx = len(self._buttons)
                b.clicked.connect(lambda _=False, i=idx: (self.setCurrentIndex(i), self.activated.emit(i)))
                self._group.addButton(b, idx)
                self._buttons.append((a.name, b))
                grid.addWidget(b, row, col)
                col += 1
                if col == columns:
                    col, row = 0, row + 1
            if col:
                row += 1
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.info.setObjectName("hint")
        grid.addWidget(self.info, row, 0, 1, columns)
        self._current = 0
        self.setCurrentIndex(0)

    def count(self):
        return len(self._buttons)

    def currentIndex(self):
        return self._current

    def currentData(self):
        return self._buttons[self._current][0]

    def itemData(self, i):
        return self._buttons[i][0]

    def findData(self, name):
        return next((i for i, (n, _b) in enumerate(self._buttons) if n == name), -1)

    def setCurrentIndex(self, i):
        if 0 <= i < len(self._buttons):
            self._current = i
            self._buttons[i][1].setChecked(True)
            a = RA.action(self._buttons[i][0])
            self.info.setText("<b>%s</b> · %s" % (a.label, a.description.split(": ", 1)[1]))
        else:                                              # an unknown type: nothing pressed
            b = self._group.checkedButton()
            if b:
                self._group.setExclusive(False); b.setChecked(False); self._group.setExclusive(True)
            self.info.setText("Not one of the game's actions: it walks as road.")


# ---- routes in the 3D views ---------------------------------------------------------------------------------------------
def _rgb(colour):
    c = QtGui.QColor(colour)
    return c.redF(), c.greenF(), c.blueF()


def route_lines_3d(a, b, rtype, width=12.0, alpha=0.95, glow=None, up=(0.0, 1.0, 0.0)):
    """GL_LINES rows (x, y, z, r, g, b, a) that draw a route between 3D points a and b the way its action plays:
    a band with footsteps or planks (walk), chevrons (slide), rails and rungs or a twisting stem (climb), a dashed arc
    (jump), plus direction arrows. glow: an (r, g, b) outline drawn around it (selection, a path being shown)."""
    import numpy as np
    act = RA.action(rtype)
    A, B = np.asarray(a, np.float64), np.asarray(b, np.float64)
    d = B - A
    n = float(np.linalg.norm(d))
    if n < 1e-6:
        return []
    u = d / n
    U = np.asarray(up, np.float64)
    side = np.cross(u, U)
    if np.linalg.norm(side) < 1e-6:                         # a vertical route: the side is any horizontal
        side = np.array([1.0, 0.0, 0.0])
    side /= np.linalg.norm(side)
    rgb = _rgb(act.colour)
    dark = tuple(c * 0.45 for c in rgb)
    rows = []

    def seg(p, q, col, al=alpha):
        rows.append((*p, *col, al))
        rows.append((*q, *col, al))
    hw = width / 2
    lift = U * 2.0                                          # just above the ground
    steps = max(1, int(n / max(width * 1.4, 1.0)))
    if act.style == "jump":
        h = min(n * 0.35, 40.0 + n * 0.15)
        pts = [A + d * t + U * (4 * h * t * (1 - t)) + lift for t in np.linspace(0, 1, 25)]
        for i, (p, q) in enumerate(zip(pts, pts[1:])):
            if i % 2 == 0:
                seg(p, q, rgb)
        seg(A + lift, B + lift, rgb, alpha * 0.3)          # the ground under the jump, faint
        if glow is not None:
            for p, q in zip(pts, pts[1:]):
                seg(p + side * hw * 0.5, q + side * hw * 0.5, glow, 0.9)
        return rows
    if glow is not None:
        for s in (-1.35, 1.35):
            seg(A + side * hw * s + lift, B + side * hw * s + lift, glow, 0.95)
    if act.style == "climb" and act.name != "ツタ":
        for s in (-1, 1):
            seg(A + side * hw * s + lift, B + side * hw * s + lift, dark)
        for i in range(1, steps * 2):
            t = i / (steps * 2)
            c = A + d * t + lift
            seg(c - side * hw, c + side * hw, rgb)
    elif act.name == "ツタ":
        pts = [A + d * (i / (steps * 3)) + side * hw * 0.6 * math.sin(i * 1.3) + lift for i in range(steps * 3 + 1)]
        for p, q in zip(pts, pts[1:]):
            seg(p, q, (0.09, 0.4, 0.2))
        for i in range(1, steps * 3, 3):
            c = pts[i]
            seg(c, c + side * hw * 0.8 + u * hw * 0.3, rgb)
    else:
        for s in (-1, 1):                                   # band edges
            seg(A + side * hw * s + lift, B + side * hw * s + lift, rgb)
        for s in (-0.5, 0.0, 0.5):                          # filled look
            seg(A + side * hw * s + lift, B + side * hw * s + lift, tuple(min(1, c * 1.08) for c in rgb), alpha * 0.8)
        for i in range(1, steps):
            t = i / steps
            c = A + d * t + lift * 1.5
            if act.style == "slide":
                seg(c + side * hw * 0.7 - u * hw * 0.4, c + u * hw * 0.3, dark)
                seg(c - side * hw * 0.7 - u * hw * 0.4, c + u * hw * 0.3, dark)
            elif "橋" in act.name or act.name == "木":
                seg(c - side * hw, c + side * hw, dark)
            else:
                s = 0.3 if i % 2 else -0.3
                seg(c + side * hw * s - u * hw * 0.12, c + side * hw * s + u * hw * 0.12, dark)
    for t in (0.25, 0.75):                                  # direction arrows
        c = A + d * t + lift * 2.5
        seg(c + u * hw * 0.6, c - u * hw * 0.3 + side * hw * 0.6, (1, 1, 1), 0.9)
        seg(c + u * hw * 0.6, c - u * hw * 0.3 - side * hw * 0.6, (1, 1, 1), 0.9)
    return rows


def route_badge_3d(a, b, rtype):
    """Where a route's icon badge goes in 3D: its middle (the top of the arc for a jump)."""
    import numpy as np
    A, B = np.asarray(a, np.float64), np.asarray(b, np.float64)
    m = (A + B) / 2
    if RA.action(rtype).style == "jump":
        n = float(np.linalg.norm(B - A))
        m = m + np.array([0.0, min(n * 0.35, 40.0 + n * 0.15) + 2.0, 0.0])
    return m


def paint_badges(p, badges, size=22):
    """Icon badges on a 3D view: badges = [(screen x, screen y, rtype, selected, problem)]."""
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    for x, y, rtype, sel, problem in badges:
        r = size / 2
        if sel:
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(217, 70, 239, 160))
            p.drawEllipse(P(x, y), r + 5, r + 5)
        action_icon(rtype, size).paint(p, QtCore.QRect(int(x - r), int(y - r), size, size))
        if problem:
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor("#ef4444"))
            p.drawEllipse(P(x + r * 0.8, y - r * 0.8), 4, 4)


def thick_lines(prog, vp, count, width, height, px=3):
    """Draw the bound GL_LINES buffer px pixels wide (core GL lines are 1 px): the same lines a pixel apart."""
    import numpy as np
    from OpenGL import GL
    loc = GL.glGetUniformLocation(prog, "mvp")
    ox, oy = 2.0 / max(width, 1), 2.0 / max(height, 1)
    rng = range(-(px // 2), px // 2 + 1)
    for dx in rng:
        for dy in rng:
            m = np.array(vp, np.float64, copy=True)
            m[0] += dx * ox * m[3]
            m[1] += dy * oy * m[3]
            GL.glUniformMatrix4fv(loc, 1, True, m.astype(np.float32))
            GL.glDrawArrays(GL.GL_LINES, 0, count)


def pick_route_screen(segments, x, y, radius=8.0):
    """segments = [(rid, (sx, sy), (sx, sy))] on screen; the route id within radius pixels of (x, y), or None."""
    best, bd = None, radius
    for rid, a, b in segments:
        if a is None or b is None:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L2))
        dd = math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy))
        if dd < bd:
            best, bd = rid, dd
    return best


# ---- point actions: pipes, cannons, links, rides --------------------------------------------------------------------------
def paint_point_glyph(p, key, rect):
    """A point action's glyph (kdx.pointactions keys) inside rect."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cx, cy = x + w / 2, y + h / 2
    dark = QtGui.QColor("#111827")
    pen = QtGui.QPen(dark, max(1.0, w / 14), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    p.setPen(pen)
    if key in ("warp_pipe", "island_pipe"):
        green = QtGui.QColor("#22c55e" if key == "warp_pipe" else "#15803d")
        p.setBrush(green)
        p.drawRect(QtCore.QRectF(x + w * 0.28, y + h * 0.38, w * 0.44, h * 0.5))
        p.drawRoundedRect(QtCore.QRectF(x + w * 0.18, y + h * 0.18, w * 0.64, h * 0.24), w * 0.05, w * 0.05)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 170), w / 14))
        p.drawLine(P(x + w * 0.36, y + h * 0.45), P(x + w * 0.36, y + h * 0.82))
        if key == "island_pipe":                            # a little island under it
            p.setPen(pen); p.setBrush(QtGui.QColor("#fde68a"))
            p.drawEllipse(P(cx, y + h * 0.9), w * 0.42, h * 0.08)
    elif key == "cannon":
        p.setBrush(QtGui.QColor("#facc15"))
        p.drawRect(QtCore.QRectF(x + w * 0.15, y + h * 0.7, w * 0.7, h * 0.18))
        p.setBrush(QtGui.QColor("#374151"))
        p.save(); p.translate(cx, y + h * 0.62); p.rotate(-35)
        p.drawRoundedRect(QtCore.QRectF(-w * 0.15, -h * 0.42, w * 0.3, h * 0.5), w * 0.08, w * 0.08)
        p.restore()
        p.drawEllipse(P(cx, y + h * 0.66), w * 0.16, h * 0.14)
    elif key == "link":
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor("#0284c7"), w / 9, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawEllipse(QtCore.QRectF(x + w * 0.1, y + h * 0.32, w * 0.46, h * 0.36))
        p.drawEllipse(QtCore.QRectF(x + w * 0.44, y + h * 0.32, w * 0.46, h * 0.36))
    elif key == "yoshi":
        p.setBrush(QtGui.QColor("#ffffff"))
        p.drawEllipse(P(cx, cy + h * 0.04), w * 0.28, h * 0.36)
        p.setBrush(QtGui.QColor("#4ade80")); p.setPen(QtCore.Qt.NoPen)
        for ex, ey in ((-0.1, -0.1), (0.12, 0.08), (-0.05, 0.22)):
            p.drawEllipse(P(cx + w * ex, cy + h * ey), w * 0.07, h * 0.07)
    elif key == "lift":
        p.setPen(QtGui.QPen(dark, w / 14)); p.drawLine(P(x + w * 0.1, y + h * 0.2), P(x + w * 0.9, y + h * 0.2))
        p.setBrush(QtGui.QColor("#f59e0b"))
        p.drawLine(P(cx, y + h * 0.2), P(cx, y + h * 0.42))
        p.drawRoundedRect(QtCore.QRectF(x + w * 0.25, y + h * 0.42, w * 0.5, h * 0.36), w * 0.06, w * 0.06)


_PICON = {}


def point_icon(key, size=24):
    if (key, size) not in _PICON:
        from . import pointactions as PA
        pm = QtGui.QPixmap(size * 2, size * 2)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setBrush(QtGui.QColor(255, 255, 255, 240))
        p.setPen(QtGui.QPen(QtGui.QColor(PA.POINT[key].colour), 3))
        p.drawEllipse(QtCore.QRectF(2, 2, size * 2 - 4, size * 2 - 4))
        paint_point_glyph(p, key, QtCore.QRectF(size * 0.35, size * 0.3, size * 1.3, size * 1.3))
        p.end()
        _PICON[(key, size)] = QtGui.QIcon(pm)
    return _PICON[(key, size)]


class PointActionBar(QtWidgets.QWidget):
    """Toggle buttons for a point's actions (warp pipe, island pipe, cannon, link, map Yoshi, lift). triggered(key)."""
    triggered = QtCore.pyqtSignal(str)

    def __init__(self, parent=None, icon=26):
        super().__init__(parent)
        from . import pointactions as PA
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.buttons = {}
        for i, a in enumerate(PA.POINT_ACTIONS):
            b = QtWidgets.QToolButton()
            b.setIcon(point_icon(a.key, icon)); b.setIconSize(QtCore.QSize(icon, icon))
            b.setText(a.label.split(" (")[0]); b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True); b.setAutoRaise(True); b.setMinimumWidth(icon + 40)
            b.setToolTip("<b>%s</b><br>%s" % (a.label, a.description))
            b.clicked.connect(lambda _=False, k=a.key: self.triggered.emit(k))
            lay.addWidget(b, i // 3, i % 3)
            self.buttons[a.key] = b

    def show_actions(self, keys, slot=None):
        from . import pointactions as PA
        for k, b in self.buttons.items():
            b.blockSignals(True); b.setChecked(k in keys); b.blockSignals(False)
            usable = not PA.POINT[k].worlds or slot is None or slot in PA.POINT[k].worlds
            b.setEnabled(usable or k in keys)


def warp_lines_3d(point_rows, pos_of, slot=None):
    """GL_LINES rows for the warp-pipe hop to World 1's island (dokan → Fs01) and each cannon's flight (an arc
    towards the world it shoots Mario to). pos_of(pid) -> (x, y, z) or None."""
    import numpy as np
    from . import pointactions as PA
    from . import rules as _r
    rows = []
    flags = {r[1]: _r.split(r[2]) for r in point_rows if len(r) > 2}

    def arc(a, b, h, col, dashed=True):
        A, B = np.asarray(a, np.float64), np.asarray(b, np.float64)
        pts = [A + (B - A) * t + np.array([0.0, 4 * h * t * (1 - t), 0.0]) for t in np.linspace(0, 1, 33)]
        for i, (p, q) in enumerate(zip(pts, pts[1:])):
            if not dashed or i % 2 == 0:
                rows.append((*p, *col, 0.95)); rows.append((*q, *col, 0.95))
    pipe = next((q for q, f in flags.items() if "dokan" in f), None)
    if pipe and "Fs01" in flags and slot in (None, "W1"):
        a, b = pos_of(pipe), pos_of("Fs01")
        if a is not None and b is not None:
            arc(a, b, 0.25 * float(np.linalg.norm(np.subtract(b, a))) + 60, (0.13, 0.77, 0.37))
    for q, r in ((r[1], r) for r in point_rows if len(r) > 4 and PA.is_cannon(r[1])):
        a = pos_of(q)
        if a is None:
            continue
        _w, far = PA.cannon_target(r)
        b = pos_of(far) if far else None
        if b is None:                                       # the target is on another map: up and away
            b = np.asarray(a) + np.array([600.0, 0.0, -600.0])
        arc(a, b, 260.0, (0.98, 0.8, 0.08))
    return rows

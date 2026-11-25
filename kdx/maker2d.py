"""
kdx/maker2d.py - the World Maker: paint a new 2D world from tiles and lay out its points and routes.

Layout: tile palette (left), canvas (centre), map/layers/selection/checks (right), tools on top. Every change goes on
the World Maker's own undo stack. The canvas draws the map in cached 16x16-tile chunks so large maps stay responsive.
The Height tool shapes the ground (corner heights, shown as hill shading). Projects are saved as .kdxmap, checked
against the game's rules as you work, and exported to the game by export2d (Export to game…).
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from . import export2d, model3d, objmodel, project2d, romfs, rules
from .diagnostics import guarded
from .tileset import TILE, Tileset, load as load_tileset

CHUNK = 16
NODE_COLORS = {"start": "#f472b6", "level": "#facc15", "tower": "#fb7185", "castle": "#ef4444", "ghost": "#c084fc",
               "airship": "#fb923c", "toad": "#4ade80", "link": "#38bdf8", "stop": "#f1f5f9", "pipe": "#22c55e", "bend": "#94a3b8"}
TOOLS = [("brush", "Brush", "B"), ("erase", "Eraser", "E"), ("fill", "Fill", "U"), ("rect", "Rectangle", "Q"),
         ("pick", "Eyedropper", "I"), ("height", "Height", "H"), ("tier", "Islands", "T"), ("node", "Point", "N"),
         ("route", "Route", "P"),
         ("action", "Action brush", "A"), ("select", "Select / move", "V")]
PATH_TOOLS = ("node", "route", "action", "select")
PATH_HINTS = {
    "route": "Click a point (or the map) to start, click to add bends (straight lines), click a point or double-click "
             "to end. Right-click or Enter: end here. Backspace: back one click. Esc: cancel. Click a path to branch off it.",
    "node": "Click the map to place a point of the kind on the left (joined to the selected point when Connect is on). "
            "Or right-click the map with any tool and pick what to add.",
    "action": "Drag over paths to give them the action on the left (walk, ladder, jump...). Or select a path and pick "
              "its action in the toolbar.",
    "select": "Drag points, bends and images (Alt: fine moves; arrows nudge, Alt+arrows finely). Click a path to edit it "
              "on the right; right-click anything for more.",
}


class Command(QtWidgets.QUndoCommand):
    def __init__(self, text, do, undo, after):
        super().__init__(text)
        self._do, self._undo, self._after = do, undo, after
        self._first = True

    def redo(self):
        self._do()
        self._after()

    def undo(self):
        self._undo()
        self._after()


# --------------------------------------------------------------------------------------------- palette
class Palette(QtWidgets.QWidget):
    selectionChanged = QtCore.pyqtSignal()

    def __init__(self, tileset, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.set_tileset(tileset)

    def set_tileset(self, tileset):
        self.tileset = tileset
        self.sel = (0, 0, 0, 0)                 # column, row, columns wide, rows high
        self.setFixedSize(tileset.columns * TILE + 1, tileset.rows * TILE + 1)
        self.update()
        self.selectionChanged.emit()

    def pattern(self):
        c, r, w, h = self.sel
        return [[(r + dy) * self.tileset.columns + (c + dx) for dx in range(w + 1)] for dy in range(h + 1)]

    def select_tile(self, tile):
        if 0 <= tile < self.tileset.count:
            self.sel = (tile % self.tileset.columns, tile // self.tileset.columns, 0, 0)
            self.update()
            self.selectionChanged.emit()

    def _cell(self, pos):
        return (max(0, min(self.tileset.columns - 1, pos.x() // TILE)), max(0, min(self.tileset.rows - 1, pos.y() // TILE)))

    @guarded
    def mousePressEvent(self, e):
        self._anchor = self._cell(e.pos())
        self.sel = (*self._anchor, 0, 0)
        self.update()

    @guarded
    def mouseMoveEvent(self, e):
        c, r = self._cell(e.pos())
        t = r * self.tileset.columns + c
        if t < self.tileset.count:
            self.setToolTip("%s (%s)" % (self.tileset.names[t], self.tileset.categories[t]))
        if e.buttons() & QtCore.Qt.LeftButton:
            a = self._anchor
            self.sel = (min(a[0], c), min(a[1], r), abs(c - a[0]), abs(r - a[1]))
            self.update()

    @guarded
    def mouseReleaseEvent(self, e):
        self.selectionChanged.emit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#20262f"))
        p.drawImage(0, 0, self.tileset.image)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1))
        for x in range(0, self.width(), TILE):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), TILE):
            p.drawLine(0, y, self.width(), y)
        c, r, w, h = self.sel
        p.setPen(QtGui.QPen(QtGui.QColor("#d946ef"), 2))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(c * TILE + 1, r * TILE + 1, (w + 1) * TILE - 2, (h + 1) * TILE - 2)
        p.end()


# --------------------------------------------------------------------------------------------- canvas
class MapItem(QtWidgets.QGraphicsItem):
    """Draws the visible layers in cached chunks."""

    def __init__(self, maker):
        super().__init__()
        self.maker = maker
        self.cache = {}
        self.show_grid = True
        self.preview = None              # (cells dict) shown on top while dragging a rectangle
        self.backdrop = None             # QImage of the 3D ground model seen from above, under the tiles

    def boundingRect(self):
        p = self.maker.project
        return QtCore.QRectF(0, 0, p.width * TILE, p.height * TILE)

    def invalidate(self, cells=None):
        if cells is None:
            self.cache.clear()
        else:
            for (x, y) in cells:
                self.cache.pop((x // CHUNK, y // CHUNK), None)
        self.update()

    def _chunk(self, cx, cy):
        pm = self.cache.get((cx, cy))
        if pm is not None:
            return pm
        proj, ts = self.maker.project, self.maker.tileset
        pm = QtGui.QPixmap(CHUNK * TILE, CHUNK * TILE)
        pm.fill(QtCore.Qt.transparent if (self.backdrop is not None or proj.picture) else QtGui.QColor("#142030"))
        p = QtGui.QPainter(pm)
        for li, lay in enumerate(proj.layers):
            if not lay.visible:
                continue
            for y in range(cy * CHUNK, min(proj.height, (cy + 1) * CHUNK)):
                row = y * proj.width
                for x in range(cx * CHUNK, min(proj.width, (cx + 1) * CHUNK)):
                    t = lay.data[row + x]
                    if 0 <= t < ts.count:
                        p.drawPixmap((x - cx * CHUNK) * TILE, (y - cy * CHUNK) * TILE, ts.pixmap(t))
        if proj.heights is not None:
            self._shade(p, proj, cx, cy)
        if proj.tiers:
            self._shade_tiers(p, proj, cx, cy)
        p.end()
        self.cache[(cx, cy)] = pm
        return pm

    def _shade_tiers(self, p, proj, cx, cy):
        """Islands as seen from above: higher tiers a little lighter, a cliff band along each drop (darker on the
        south and east, where the reference maps show their walls) and a thin rim on top."""
        from . import islands
        theme = islands.THEMES.get(proj.theme, islands.THEMES["grass"])
        wall, rim = QtGui.QColor(theme["cliff"][1]), QtGui.QColor(theme["top_edge"])
        p.setPen(QtCore.Qt.NoPen)
        band = max(3, TILE // 4)
        for y in range(cy * CHUNK, min(proj.height, (cy + 1) * CHUNK)):
            for x in range(cx * CHUNK, min(proj.width, (cx + 1) * CHUNK)):
                t = proj.tier(x, y)
                if t <= 0:
                    continue
                ox, oy = (x - cx * CHUNK) * TILE, (y - cy * CHUNK) * TILE
                p.setBrush(QtGui.QColor(255, 255, 255, 18 * t))
                p.drawRect(ox, oy, TILE, TILE)
                if proj.tier(x, y + 1) < t:
                    p.setBrush(wall); p.drawRect(ox, oy + TILE - band, TILE, band)
                if proj.tier(x + 1, y) < t:
                    p.setBrush(wall.darker(115)); p.drawRect(ox + TILE - band // 2, oy, band // 2, TILE)
                if proj.tier(x, y - 1) < t:
                    p.setBrush(rim); p.drawRect(ox, oy, TILE, 2)
                if proj.tier(x - 1, y) < t:
                    p.setBrush(rim); p.drawRect(ox, oy, 2, TILE)
                if getattr(proj, "island_round", False):
                    self._round_corners(p, proj, x, y, t, ox, oy, wall)
        if getattr(proj, "island_round", False):            # a soft shadow on the lower ground south-east of each drop
            p.setBrush(QtGui.QColor(0, 0, 0, 55))
            sh = TILE // 3
            for y in range(cy * CHUNK, min(proj.height, (cy + 1) * CHUNK)):
                for x in range(cx * CHUNK, min(proj.width, (cx + 1) * CHUNK)):
                    t = proj.tier(x, y)
                    ox, oy = (x - cx * CHUNK) * TILE, (y - cy * CHUNK) * TILE
                    if proj.tier(x, y - 1) > t:
                        p.drawRect(ox + sh // 2, oy, TILE, sh)
                    if proj.tier(x - 1, y) > t:
                        p.drawRect(ox, oy + sh // 2, sh, TILE)

    def _round_corners(self, p, proj, x, y, t, ox, oy, wall):
        """Rounded island corners as seen from above: the cut-off corner shows the cliff, a quarter circle the top."""
        from . import islands
        _pts, arcs = islands.tile_outline(proj.tier, x, y, t)
        for (ci, cj), arc, _lo in arcs:
            path = QtGui.QPainterPath(QtCore.QPointF((ci - x) * TILE + ox, (cj - y) * TILE + oy))
            for i, j in arc:
                path.lineTo((i - x) * TILE + ox, (j - y) * TILE + oy)
            path.closeSubpath()
            p.setBrush(wall.darker(130))
            p.drawPath(path)

    def _shade(self, p, proj, cx, cy):
        """Hill shading: light from the top left, so slopes facing it brighten and the others darken."""
        p.setPen(QtCore.Qt.NoPen)
        span = proj.tile_size
        for y in range(cy * CHUNK, min(proj.height, (cy + 1) * CHUNK)):
            for x in range(cx * CHUNK, min(proj.width, (cx + 1) * CHUNK)):
                h00, h10, h01, h11 = proj.corner(x, y), proj.corner(x + 1, y), proj.corner(x, y + 1), proj.corner(x + 1, y + 1)
                slope = ((h10 + h11) - (h00 + h01) + (h01 + h11) - (h00 + h10)) / (2 * span)
                a = int(max(-1.0, min(1.0, slope)) * 110)
                if a:
                    p.setBrush(QtGui.QColor(0, 0, 0, a) if a > 0 else QtGui.QColor(255, 255, 255, -a))
                    p.drawRect((x - cx * CHUNK) * TILE, (y - cy * CHUNK) * TILE, TILE, TILE)

    def paint(self, painter, option, widget=None):
        proj = self.maker.project
        r = option.exposedRect
        c0, c1 = max(0, int(r.left() // (CHUNK * TILE))), min((proj.width - 1) // CHUNK, int(r.right() // (CHUNK * TILE)))
        r0, r1 = max(0, int(r.top() // (CHUNK * TILE))), min((proj.height - 1) // CHUNK, int(r.bottom() // (CHUNK * TILE)))
        if self.backdrop is not None:
            painter.fillRect(self.boundingRect(), QtGui.QColor("#142030"))
            painter.drawImage(self.boundingRect(), self.backdrop)
        pic = self.maker.picture_image()
        over = pic is not None and bool(proj.picture.get("over"))
        if pic is not None:
            if self.backdrop is None:
                painter.fillRect(self.boundingRect(), QtGui.QColor("#142030"))
            if not over:
                painter.drawImage(self.boundingRect(), pic)
        for cy in range(r0, r1 + 1):
            for cx in range(c0, c1 + 1):
                painter.drawPixmap(cx * CHUNK * TILE, cy * CHUNK * TILE, self._chunk(cx, cy))
        if over:                                              # a picture with clear parts laid over the tiles
            painter.drawImage(self.boundingRect(), pic)
        if self.preview:
            painter.setOpacity(0.6)
            ts = self.maker.tileset
            for (x, y), t in self.preview.items():
                if t >= 0 and proj.inside(x, y):
                    painter.drawPixmap(x * TILE, y * TILE, ts.pixmap(t))
            painter.setOpacity(1.0)
        if self.show_grid:
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 28), 0)
            painter.setPen(pen)
            for x in range(max(0, int(r.left() // TILE)), min(proj.width, int(r.right() // TILE) + 1) + 1):
                painter.drawLine(QtCore.QLineF(x * TILE, max(0, r.top()), x * TILE, min(proj.height * TILE, r.bottom())))
            for y in range(max(0, int(r.top() // TILE)), min(proj.height, int(r.bottom() // TILE) + 1) + 1):
                painter.drawLine(QtCore.QLineF(max(0, r.left()), y * TILE, min(proj.width * TILE, r.right()), y * TILE))
        painter.setPen(QtGui.QPen(QtGui.QColor("#d946ef"), 0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(self.boundingRect())


HANDLE_PX = 9                         # screen pixels: the selected image's corner handles


class StickerItem(QtWidgets.QGraphicsItem):
    """The map's images (kdx.pictures), animated live; the selected one has a dashed frame."""

    def __init__(self, maker):
        super().__init__()
        self.maker = maker
        self.t_ms = 0.0

    def boundingRect(self):
        p = self.maker.project
        return QtCore.QRectF(-8 * TILE, -8 * TILE, (p.width + 16) * TILE, (p.height + 16) * TILE)

    def paint(self, painter, option, widget=None):
        from . import pictures
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        sel = self.maker.selected_sticker()
        for i, s in enumerate(self.maker.project.stickers):
            a = self.maker.reveal_alpha(s["reveal"]) if s.get("reveal") else 1.0
            if a <= 0.0:
                continue
            clip = self.maker.reveal_clip(s["reveal"], a) if s.get("reveal") and a < 1.0 else None
            try:
                painter.save()
                if clip is not None:
                    painter.setClipPath(clip, QtCore.Qt.IntersectClip)
                else:
                    painter.setOpacity(a)
                pictures.draw(painter, s, TILE, self.t_ms)
                painter.restore()
            except Exception:
                painter.restore()
                continue
            if i == sel:
                r = self.maker.sticker_rect(s)
                painter.setPen(QtGui.QPen(QtGui.QColor("#facc15"), 0, QtCore.Qt.DashLine))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRect(r)
                # corner handles: drag one to resize (kept in proportion)
                k = HANDLE_PX / max(1e-6, self.maker.canvas.transform().m11())
                painter.setPen(QtGui.QPen(QtGui.QColor("#1f2937"), 0))
                painter.setBrush(QtGui.QColor("#facc15"))
                for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                    painter.drawRect(QtCore.QRectF(c.x() - k / 2, c.y() - k / 2, k, k))


class Canvas(QtWidgets.QGraphicsView):
    def __init__(self, maker):
        self._scene = QtWidgets.QGraphicsScene()
        super().__init__(self._scene)
        self.maker = maker
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        self.setBackgroundBrush(QtGui.QColor("#0d131c"))
        self.setMouseTracking(True)
        self.map_item = None
        self.node_items = {}
        self.route_items = {}
        self.cursor_item = None
        self._pan = None
        self._stroke = None          # {(x, y): (old, new)} while painting
        self._height = None          # the Height tool's stroke while dragging
        self._rect_start = None
        self._drag_node = None
        self._route_from = None
        self._pen = None                    # Route tool: {"start": target, "segs": [[(x, y)...] per click]}
        self._pen_item = None

    def rebuild(self):
        self.stop_walk()
        self._stop_flash()
        self._pen, self._pen_item = None, None
        self._scene.clear()
        self.map_item = MapItem(self.maker)
        self._scene.addItem(self.map_item)
        self.sticker_item = StickerItem(self.maker)
        self.sticker_item.setZValue(5)
        self._scene.addItem(self.sticker_item)
        self.cursor_item = QtWidgets.QGraphicsRectItem(0, 0, TILE, TILE)
        pen = QtGui.QPen(QtGui.QColor("#ffffff"), 0, QtCore.Qt.DashLine)
        self.cursor_item.setPen(pen)
        self.cursor_item.setZValue(50)
        self._scene.addItem(self.cursor_item)
        p = self.maker.project
        self._scene.setSceneRect(-4 * TILE, -4 * TILE, (p.width + 8) * TILE, (p.height + 8) * TILE)
        self.node_items, self.route_items = {}, {}
        self.refresh_nodes()

    def refresh_nodes(self):
        for it in list(self.node_items.values()) + list(self.route_items.values()):
            self._scene.removeItem(it)
        self.node_items, self.route_items = {}, {}
        proj = self.maker.project
        from .routeui import RouteItem
        for r in proj.routes:
            a, b = rules.route_ends(r["id"])
            if a in proj.nodes and b in proj.nodes:
                na, nb = proj.nodes[a], proj.nodes[b]
                sel = self.maker.selected == ("route", r["id"])
                problem = "\n".join(t for t in self.maker.problem_text if r["id"] in t)
                heights = tuple(float((n.get("pos") or [0, 0, 0])[1]) for n in (na, nb))
                line = RouteItem(r["id"], (na["x"] * TILE, na["y"] * TILE), (nb["x"] * TILE, nb["y"] * TILE), r["type"],
                                 sel, problem, heights)
                self._scene.addItem(line)
                self.route_items[r["id"]] = line
                if r.get("kp"):
                    from . import koopatlas as _kp
                    line.setToolTip(((line.toolTip() + "\n") if line.toolTip() else "") + _kp.path_note(r))
                    if r.get("hidden"):
                        line.setOpacity(0.45)
        from . import pointactions as PA
        from .routeui import point_icon
        flags_of = {q: rules.split(n["row"][2]) for q, n in proj.nodes.items()}
        pipe = next((q for q, f in flags_of.items() if "dokan" in f), None)
        isl = next((q for q, f in flags_of.items() if "dokan2" in f), None)
        warps = []
        if pipe and "Fs01" in proj.nodes and proj.slot in (None, "W1"):
            warps.append((pipe, "Fs01", "#22c55e", "warp to the island"))
        if isl and pipe:
            warps.append((isl, pipe, "#15803d", "island pipe ↔ warp pipe"))
        for a, b, col, tip in warps:
            na, nb = proj.nodes[a], proj.nodes[b]
            path = QtGui.QPainterPath(QtCore.QPointF(na["x"] * TILE, na["y"] * TILE))
            mx, my = (na["x"] + nb["x"]) / 2 * TILE, (na["y"] + nb["y"]) / 2 * TILE
            path.quadTo(QtCore.QPointF(mx, my - 0.25 * math.hypot(nb["x"] - na["x"], nb["y"] - na["y"]) * TILE),
                        QtCore.QPointF(nb["x"] * TILE, nb["y"] * TILE))
            it = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor(col), 2.5, QtCore.Qt.DashLine); pen.setCosmetic(True)
            it.setPen(pen); it.setZValue(19); it.setToolTip(tip)
            self._scene.addItem(it)
            self.route_items["~warp~%s%s" % (a, b)] = it
        for pid in [q for q in proj.nodes if PA.is_cannon(q)]:
            n = proj.nodes[pid]
            w, far = PA.cannon_target(n["row"])
            x0, y0 = n["x"] * TILE, n["y"] * TILE
            path = QtGui.QPainterPath(QtCore.QPointF(x0, y0))
            path.cubicTo(QtCore.QPointF(x0 + 2 * TILE, y0 - 6 * TILE), QtCore.QPointF(x0 + 6 * TILE, y0 - 7 * TILE),
                         QtCore.QPointF(x0 + 8 * TILE, y0 - 4 * TILE))
            it = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor("#facc15"), 2.5, QtCore.Qt.DashLine); pen.setCosmetic(True)
            it.setPen(pen); it.setZValue(19)
            it.setToolTip("Cannon: shoots Mario to %s" % (far or "nowhere yet (set its normal exit)"))
            self._scene.addItem(it)
            self.route_items["~cannon~" + pid] = it
            lab = QtWidgets.QGraphicsSimpleTextItem("→ %s" % (far or "?"))
            f = lab.font(); f.setBold(True); f.setPointSize(9); lab.setFont(f)
            lab.setBrush(QtGui.QColor("#facc15"))
            lab.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            lab.setPos(x0 + 8 * TILE, y0 - 4 * TILE); lab.setZValue(40)
            self._scene.addItem(lab)
            self.route_items["~cannonlabel~" + pid] = lab
        for pid, n in proj.nodes.items():
            sel = self.maker.selected == ("node", pid)
            rad = 7 if n["kind"] in ("stop", "bend", "pipe") else 10
            rad += 3 if sel else 0
            it = QtWidgets.QGraphicsEllipseItem(-rad, -rad, 2 * rad, 2 * rad)
            it.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            it.setBrush(QtGui.QBrush(QtGui.QColor("#d946ef" if sel else NODE_COLORS.get(n["kind"], "#ffffff"))))
            bad = pid in self.maker.problem_points
            it.setPen(QtGui.QPen(QtGui.QColor("#ef4444" if bad else "#111827"), 3.5 if bad else 1.5))
            if bad:
                it.setToolTip("\n".join(m for m in self.maker.problem_text if pid in m))
            it.setPos(n["x"] * TILE, n["y"] * TILE)
            it.setZValue(30)
            why = self.maker.point_purpose(pid)
            label = QtWidgets.QGraphicsSimpleTextItem(rules.point_label(pid) + (" · " + why if why and n["kind"] != "start" else ""), it)
            f = label.font(); f.setPointSize(8); f.setBold(True); label.setFont(f)
            label.setBrush(QtGui.QBrush(QtGui.QColor("#fff7d6")))
            label.setPos(rad + 2, -rad - 10)
            for k, a in enumerate(PA.actions_of(pid, flags_of[pid])):    # action badges beside the point
                badge = QtWidgets.QGraphicsPixmapItem(point_icon(a.key, 22).pixmap(22, 22), it)
                badge.setOffset(-rad - 24 - 23 * k, -11)
                badge.setToolTip("%s: %s" % (a.label, a.description))
            self._scene.addItem(it)
            self.node_items[pid] = it
        # points may sit outside the painted map (vanilla imports): keep them all reachable by scrolling
        r = QtCore.QRectF(-4 * TILE, -4 * TILE, (proj.width + 8) * TILE, (proj.height + 8) * TILE)
        for it in self.node_items.values():
            r = r.united(QtCore.QRectF(it.pos(), it.pos()).adjusted(-6 * TILE, -6 * TILE, 6 * TILE, 6 * TILE))
        self._scene.setSceneRect(r)
        if self.map_item:
            self.map_item.update()

    # ---- walk test ---------------------------------------------------------------------------------------------------
    WALK_SPEED = 6.0                 # tiles per second

    def walk_test(self):
        """Animate a marker along every path in play-through order (Project.walk_plan)."""
        self.stop_walk()
        plan = self.maker.project.walk_plan()
        if not plan:
            self.maker.status("Walk test: no paths to walk (place a start point and routes).")
            return
        self._walk = {"plan": plan, "leg": 0, "t": 0.0}
        import time
        self._reveal = {plan[0][0].split()[0]: time.monotonic()}
        self._walker = QtWidgets.QGraphicsEllipseItem(-9, -9, 18, 18)
        self._walker.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self._walker.setBrush(QtGui.QBrush(QtGui.QColor("#ef4444")))
        self._walker.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
        self._walker.setZValue(60)
        self._scene.addItem(self._walker)
        self._walk_timer = QtCore.QTimer(self)
        self._walk_timer.timeout.connect(self._walk_step)
        self._walk_timer.start(33)
        self.setFocus()

    def reveal_preview(self):
        """Every path's art fades in, in the order a play-through opens the paths (no Mario)."""
        self.stop_walk()
        plan = self.maker.project.walk_plan()
        import time
        now = time.monotonic()
        order = [label.split()[0] for label, _pts in plan]
        self._reveal = {pth: now + 0.6 + 0.9 * k for k, pth in enumerate(order)}
        self._reveal_end = now + 0.6 + 0.9 * len(order) + 1.5
        self._reveal_timer = QtCore.QTimer(self)
        self._reveal_timer.timeout.connect(self._reveal_step)
        self._reveal_timer.start(40)
        n = sum(1 for p in order if p in self.maker._opened_paths() and any(
            s.get("reveal") == p for s in self.maker.project.stickers))
        self.maker.status("Reveal preview: %d paths open in play-through order, %d with path art." % (len(order), n))

    def _reveal_step(self):
        import time
        self.sticker_item.update()
        if time.monotonic() >= getattr(self, "_reveal_end", 0):
            self._reveal_timer.stop()
            self._reveal = None
            self.sticker_item.update()

    def stop_walk(self):
        self._reveal = None
        if getattr(self, "_reveal_timer", None):
            self._reveal_timer.stop()
            self._reveal_timer = None
        if getattr(self, "_walk_timer", None):
            self._walk_timer.stop()
            self._walk_timer = None
        if getattr(self, "_walker", None) is not None:
            self._scene.removeItem(self._walker)
            self._walker = None
        self._walk = None

    @guarded
    def _walk_step(self):
        w, nodes = self._walk, self.maker.project.nodes
        if w is None:
            return
        label, pts = w["plan"][w["leg"]]
        xy = [(nodes[q]["x"], nodes[q]["y"]) for q in pts if q in nodes]
        lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:])]
        from . import routeactions as RA
        kinds = [self._route_kind(q, r) for q, r in zip(pts, pts[1:])]
        seg, dd = 0, w["t"]
        for i, L in enumerate(lens):
            if dd <= L:
                seg = i
                break
            dd -= L
        kind = kinds[seg] if kinds else "道"
        w["t"] += RA.walk_speed(kind) * 0.033
        if not lens or w["t"] >= sum(lens):
            w["leg"] += 1
            w["t"] = 0.0
            if w["leg"] < len(w["plan"]) and getattr(self, "_reveal", None) is not None:
                import time
                self._reveal.setdefault(w["plan"][w["leg"]][0].split()[0], time.monotonic())
            if w["leg"] >= len(w["plan"]):
                self.maker.status("Walk test done: %d paths walked." % len(w["plan"]))
                self.stop_walk()
            return
        d = w["t"]
        self.sticker_item.update()
        for i, ((a, b), L) in enumerate(zip(zip(xy, xy[1:]), lens)):
            if d <= L:
                f = d / L if L else 1.0
                act = RA.action(kinds[i] if i < len(kinds) else "道")
                rt = self._route_of(pts[i], pts[i + 1]) if i + 1 < len(pts) else None
                kp = (rt or {}).get("kp")
                lift = math.sin(math.pi * f) * min(L * 0.35, 2.0 + L * 0.15) * TILE if act.style == "jump" else 0.0
                wob = math.sin(f * L * 6.0) * 0.12 * TILE if act.style == "climb" else 0.0
                self._walker.setPos((a[0] + (b[0] - a[0]) * f) * TILE + wob, (a[1] + (b[1] - a[1]) * f) * TILE - lift)
                self._walker.setBrush(QtGui.QBrush(QtGui.QColor(act.colour if act.style != "walk" else "#ef4444")))
                self._walker.setOpacity(0.3 if (rt or {}).get("hidden") else 1.0)
                if kp in ("Swim", "WalkWater"):
                    self._walker.setBrush(QtGui.QBrush(QtGui.QColor("#38bdf8")))
                    self._walker.moveBy(0, math.sin(f * L * 9.0) * 0.1 * TILE)
                self.maker.status("Walk test %d/%d: %s · %s%s" % (w["leg"] + 1, len(w["plan"]), label, act.label,
                                                                 (" (Koopatlas %s)" % kp) if kp else ""))
                break
            d -= L

    def _route_of(self, a, b):
        """The route between points a and b (either direction), or None."""
        for r in self.maker.project.routes:
            if set(rules.route_ends(r["id"]) or ()) == {a, b}:
                return r
        return None

    def _route_kind(self, a, b):
        """The action of the route between points a and b (either direction)."""
        for r in self.maker.project.routes:
            if set(rules.route_ends(r["id"]) or ()) == {a, b}:
                return r["type"]
        return "道"

    # ---- Route tool: a pen that draws a whole path (start, bends along the road, end) -------------------------------
    PEN_KEYS = (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Backspace)

    def _pen_target(self, pos):
        """What a click hits: ("node", pid), ("split", route id, x, y) on a road, or ("new", x, y) on the map."""
        hit = self.node_at(pos)
        if hit:
            return ("node", hit)
        sp = self.mapToScene(pos)
        rid = self._route_at(pos, radius=7)
        if rid:
            l = self.route_items[rid].line()
            vx, vy = l.x2() - l.x1(), l.y2() - l.y1()
            t = max(0.0, min(1.0, ((sp.x() - l.x1()) * vx + (sp.y() - l.y1()) * vy) / ((vx * vx + vy * vy) or 1)))
            return ("split", rid, (l.x1() + t * vx) / TILE, (l.y1() + t * vy) / TILE)
        return ("new", sp.x() / TILE, sp.y() / TILE)

    def _pen_xy(self, tgt):
        if tgt[0] == "node":
            n = self.maker.project.nodes[tgt[1]]
            return n["x"], n["y"]
        return tgt[-2], tgt[-1]

    def _pen_last(self):
        segs = [q for seg in self._pen["segs"] for q in seg]
        return segs[-1] if segs else self._pen_xy(self._pen["start"])

    def _pen_follow(self, a, b):
        m = self.maker
        if not m.follow_road.isChecked():
            return []
        from . import roadtrace
        try:
            return roadtrace.trace(m.road_image(), (m.project.width, m.project.height), a, b,
                                   colours=(self._pen or {}).get("colours", [])[:1])
        except Exception as ex:                               # never lose the click over a tracing problem
            m.status("Follow the road: %s" % ex)
            return []

    def _pen_colour(self, xy):
        """The road colour where a path starts (followed on every click of that path)."""
        m = self.maker
        try:
            from . import roadtrace
            return [roadtrace.colour_at(m.road_image(), (m.project.width, m.project.height), xy)]
        except Exception:
            return []

    def _pen_press(self, e):
        m = self.maker
        self.setFocus()
        tgt = self._pen_target(e.pos())
        if tgt[0] == "new" and not m.project.inside(int(tgt[1]), int(tgt[2])):
            m.status("Click inside the map.")
            return
        shift = bool(e.modifiers() & QtCore.Qt.ShiftModifier)
        if self._pen is None:
            start = ("new", m.pen_kind(start=True), tgt[1], tgt[2]) if tgt[0] == "new" else tgt
            self._pen = {"start": start, "segs": [], "colours": self._pen_colour(self._pen_xy(start))}
            self._pen_draw()
            m.status("Drawing a path from %s: click along the road, then click a point or double-click to end." %
                     (start[1] if start[0] == "node" else "a new " + start[1]))
            return
        if tgt[0] == "new" and not shift:                    # a bend (plus the road's own bends on the way)
            here = (tgt[1], tgt[2])
            self._pen["segs"].append(self._pen_follow(self._pen_last(), here) + [here])
            self._pen_draw(QtCore.QPointF(here[0] * TILE, here[1] * TILE))
            return
        if tgt[0] == "new":                                   # Shift-click: a new point here, and keep drawing
            tgt = ("new", m.pen_kind(), tgt[1], tgt[2])
        self._pen_finish(tgt, keep=shift)

    def _pen_finish(self, end=None, keep=False):
        """End the path at `end` (a target), or at the last click (which becomes a point)."""
        m, pen = self.maker, self._pen
        if pen is None:
            return None
        bends = [q for seg in pen["segs"] for q in seg]
        if end is None:
            if not bends:
                self.pen_cancel()
                return None
            x, y = bends.pop()
            end = ("new", m.pen_kind(), x, y)
        else:
            bends += self._pen_follow(self._pen_last(), self._pen_xy(end))
        if end[0] == "node" and pen["start"] == end:
            if not bends:
                self.pen_cancel()
                return None
        self.pen_cancel()
        pid = m.commit_path(pen["start"], bends, end)
        if keep and pid:
            self._pen = {"start": ("node", pid), "segs": [], "colours": self._pen_colour(self._pen_xy(("node", pid)))}
            self._pen_draw()
        return pid

    def pen_cancel(self):
        self._pen = None
        if self._pen_item is not None:
            self._scene.removeItem(self._pen_item)
            self._pen_item = None

    def _pen_draw(self, cursor=None):
        if self._pen is None:
            return
        pts = [self._pen_xy(self._pen["start"])] + [q for seg in self._pen["segs"] for q in seg]
        path = QtGui.QPainterPath(QtCore.QPointF(pts[0][0] * TILE, pts[0][1] * TILE))
        for x, y in pts[1:]:
            path.lineTo(x * TILE, y * TILE)
        if cursor is not None:
            key = (round(cursor.x() / TILE * 4), round(cursor.y() / TILE * 4), len(pts))
            if self._pen.get("ahead_key") != key:             # the road the next click would follow
                self._pen["ahead_key"] = key
                self._pen["ahead"] = self._pen_follow(pts[-1], (cursor.x() / TILE, cursor.y() / TILE))
            for x, y in self._pen.get("ahead", []):
                path.lineTo(x * TILE, y * TILE)
            path.lineTo(cursor)
        for x, y in pts[1:]:
            path.addEllipse(QtCore.QPointF(x * TILE, y * TILE), 0.18 * TILE, 0.18 * TILE)
        if self._pen_item is None:
            self._pen_item = QtWidgets.QGraphicsPathItem()
            pen = QtGui.QPen(QtGui.QColor("#38bdf8"), 3, QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            self._pen_item.setPen(pen)
            self._pen_item.setZValue(55)
            self._scene.addItem(self._pen_item)
        self._pen_item.setPath(path)

    def event(self, e):
        if e.type() == QtCore.QEvent.ShortcutOverride and self._pen is not None and e.key() in self.PEN_KEYS:
            e.accept()
            return True
        return super().event(e)

    @guarded
    def mouseDoubleClickEvent(self, e):
        if self.maker.tool != "route" or self._pen is None or e.button() != QtCore.Qt.LeftButton:
            return super().mouseDoubleClickEvent(e)
        tgt = self._pen_target(e.pos())
        if tgt[0] != "new":
            return
        x, y = tgt[1], tgt[2]
        segs = self._pen["segs"]
        if segs and math.hypot(segs[-1][-1][0] - x, segs[-1][-1][1] - y) < 0.6:
            segs.pop()                                        # the first click of the double-click added a bend
        if not segs and math.hypot(*(a - b for a, b in zip(self._pen_xy(self._pen["start"]), (x, y)))) < 0.6:
            return                                            # double-clicked where the path starts
        self._pen_finish(("new", self.maker.pen_kind(), x, y))

    def keyPressEvent(self, e):
        if self._pen is not None and e.key() in self.PEN_KEYS:
            if e.key() == QtCore.Qt.Key_Escape:
                self.pen_cancel()
                self.maker.status("Path cancelled.")
            elif e.key() == QtCore.Qt.Key_Backspace:
                if self._pen["segs"]:
                    self._pen["segs"].pop()
                    self._pen_draw()
                else:
                    self.pen_cancel()
            else:
                self._pen_finish()
            return
        sel = self.maker.selected
        arrows = {QtCore.Qt.Key_Left: (-1, 0), QtCore.Qt.Key_Right: (1, 0), QtCore.Qt.Key_Up: (0, -1), QtCore.Qt.Key_Down: (0, 1)}
        if e.key() in arrows and sel and sel[0] == "node" and sel[1] in self.maker.project.nodes:
            mods = e.modifiers()
            step = 0.05 if mods & QtCore.Qt.AltModifier else (2.0 if mods & QtCore.Qt.ShiftModifier else 0.5)
            dx, dy = arrows[e.key()]
            n = self.maker.project.nodes[sel[1]]
            old = (n["x"], n["y"])
            self.maker.move_node(sel[1], old, (old[0] + dx * step, old[1] + dy * step))
            return
        if e.key() == QtCore.Qt.Key_Escape and getattr(self, "_walk", None):
            self.stop_walk()
            self.maker.status("Walk test stopped.")
            return
        super().keyPressEvent(e)

    def tile_at(self, pos):
        sp = self.mapToScene(pos)
        return int(math.floor(sp.x() / TILE)), int(math.floor(sp.y() / TILE))

    def node_at(self, pos, radius=12):
        best, best_d = None, None
        for pid, it in self.node_items.items():
            c = self.mapFromScene(it.pos())
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d <= radius and (best_d is None or d < best_d):
                best, best_d = pid, d
        return best

    # ---- mouse -----------------------------------------------------------------------------------------------------
    @guarded
    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        if 0.05 < self.transform().m11() * f < 12:
            self.scale(f, f)

    @guarded
    def mousePressEvent(self, e):
        if getattr(self, "_walk", None):
            self.stop_walk()
            self.maker.status("Walk test stopped.")
            return
        if e.button() in (QtCore.Qt.RightButton, QtCore.Qt.MiddleButton):
            self._pan = e.pos()
            self._pan_start = e.pos()
            return
        if e.button() != QtCore.Qt.LeftButton:
            return
        m = self.maker
        tool = m.tool
        x, y = self.tile_at(e.pos())
        if tool == "route":
            self._pen_press(e)
            return
        hit = self.node_at(e.pos())
        if tool in ("select", "node") and hit:
            m.select(("node", hit))
            n = m.project.nodes[hit]
            self._drag_node = (hit, n["x"], n["y"])
            self._drag_pos, self._fine = None, None
            return
        if tool == "node":
            if m.project.inside(x, y):
                m.place_node(m.node_kind(), x + 0.5, y + 0.5)
            return
        if tool == "select":
            rid = self._route_at(e.pos())
            if not rid:
                sp = self.mapToScene(e.pos())
                hi = m.sticker_handle_at(sp)
                if hi is not None:                            # a corner handle of the selected image: resize it
                    s = m.project.stickers[hi]
                    d0 = max(1e-3, math.hypot(sp.x() / TILE - s["x"], sp.y() / TILE - s["y"]))
                    self._resize_sticker = (hi, float(s.get("w", 3.0)), d0)
                    return
                si = m.sticker_at(sp)
                if si is not None:                            # an image: select it in the panel and drag it
                    m.sticker_list.setCurrentRow(si)
                    s = m.project.stickers[si]
                    self._drag_sticker = (si, s["x"], s["y"], sp.x() / TILE, sp.y() / TILE)
                    return
            m.select(("route", rid) if rid else None)
            return
        if tool == "action":
            self._brushed = set()
            self._brush_route(e.pos())
            return
        if tool == "route":
            return
        if m.current_layer_locked():
            m.status("The current layer is locked.")
            return
        if tool in ("brush", "erase"):
            self._stroke = {}
            self._paint_at(x, y)
        elif tool == "fill":
            m.paint(m.project.fill_cells(m.layer, x, y, m.palette.pattern()[0][0]), "Fill")
        elif tool == "rect":
            self._rect_start = (x, y)
        elif tool == "tier":
            self._tiers = {"changes": {}, "level": 0 if e.modifiers() & QtCore.Qt.ShiftModifier else m.tier_level.value()}
            self._tier_at(x, y)
        elif tool == "height":
            sp = self.mapToScene(e.pos())
            fx, fy = sp.x() / TILE, sp.y() / TILE
            ctrl, shift = e.modifiers() & QtCore.Qt.ControlModifier, e.modifiers() & QtCore.Qt.ShiftModifier
            self._height = {"changes": {}, "flat": m.project.height_at(fx, fy) if ctrl else None,
                            "sign": -1 if shift else 1, "last": None}
            self._height_at(fx, fy)
        elif tool == "pick":
            t = m.project.get(m.layer, x, y)
            if t >= 0:
                m.palette.select_tile(t)
                m.set_tool("brush")

    def _brush_route(self, pos):
        """Action brush: give the route under the cursor the palette's action (a drag paints every route it crosses,
        one undo step)."""
        m = self.maker
        rid = self._route_at(pos, radius=9)
        if not rid or rid in self._brushed:
            return
        new = m.route_type_box.currentData()
        r = next((r for r in m.project.routes if r["id"] == rid), None)
        if r is None or r["type"] == new:
            self._brushed.add(rid)
            return
        if not self._brushed:
            m.undo.beginMacro("Action brush")
        self._brushed.add(rid)
        m.set_route_type(rid, new)

    @staticmethod
    def _dot(colour, d=14):
        pm = QtGui.QPixmap(d, d)
        pm.fill(QtCore.Qt.transparent)
        pt = QtGui.QPainter(pm)
        pt.setRenderHint(QtGui.QPainter.Antialiasing)
        pt.setBrush(QtGui.QColor(colour)); pt.setPen(QtGui.QPen(QtGui.QColor("#111827"), 1.2))
        pt.drawEllipse(QtCore.QRectF(1, 1, d - 2, d - 2))
        pt.end()
        return QtGui.QIcon(pm)

    @staticmethod
    def _menu_title(menu, text):
        a = menu.addAction(text)
        a.setEnabled(False)
        f = a.font(); f.setBold(True); a.setFont(f)
        menu.addSeparator()

    MENU_KINDS = ("level", "stop", "bend", "tower", "castle", "ghost", "toad", "airship", "pipe", "link", "start")

    def map_menu_actions(self, x, y):
        """[(label, kind or None, callable)] of the right-click menu on the map at (x, y) tiles."""
        m = self.maker
        out = []
        for kind in self.MENU_KINDS:
            if kind == "start" and m.project.start_node():
                continue
            out.append((project2d.NODE_KINDS[kind][1], kind, lambda k=kind: m.place_node(k, x, y)))
        out.append(("Draw a path from here", None, lambda: m.start_path_at(("new", m.pen_kind(start=True), x, y))))
        if m.project.needed_points():
            out.append(("Add the points the game needs here", None, lambda: m.add_needed_points(at=(x, y))))
        return out

    def _map_menu(self, x, y, gpos):
        """Right click on the map: add a point of any kind there (joined to the selected point when Connect is on)."""
        m = self.maker
        if not m.project.inside(int(x), int(y)):
            return
        sel = m.selected[1] if m.selected and m.selected[0] == "node" and m.selected[1] in m.project.nodes else None
        join = sel if sel and m.chain_box.isChecked() else None
        menu = QtWidgets.QMenu(self)
        self._menu_title(menu, "Add here" + (" (joined to %s)" % join if join else ""))
        for label, kind, fn in self.map_menu_actions(x, y):
            if kind is None and not menu.actions()[-1].isSeparator():   # a separator before the other actions
                menu.addSeparator()
            menu.addAction(self._dot(NODE_COLORS.get(kind, "#ffffff")) if kind else QtGui.QIcon(), label, fn)
        menu.exec_(gpos)

    def _point_menu(self, pid, gpos):
        m = self.maker
        menu = QtWidgets.QMenu(self)
        self._menu_title(menu, rules.point_label(pid))
        menu.addAction("Draw a path from here", lambda: m.start_path_at(("node", pid)))
        menu.addAction("Select", lambda: m.select(("node", pid)))
        groups = {}
        for group, label, new, kind, ok, tip in m.change_options(pid):
            if group not in groups:
                groups[group] = None
        sub = menu.addMenu("Change to (World %d)" % m.project.world)
        sub.setToolTipsVisible(True)
        last = None
        for group, label, new, kind, ok, tip in m.change_options(pid):
            if group != last:
                self._menu_title(sub, group)
                last = group
            a = sub.addAction(self._dot(NODE_COLORS.get(kind, "#ffffff")), label,
                              lambda n=new, k=kind: m.change_point(pid, n, k))
            a.setEnabled(ok)
            a.setToolTip(tip)
        flags = m.flag_options(pid)
        if flags:
            fm = menu.addMenu("Mark as")
            fm.setToolTipsVisible(True)
            for label, flag, on, tip in flags:
                a = fm.addAction(label, lambda f=flag: m.toggle_flag(pid, f))
                a.setCheckable(True); a.setChecked(on); a.setToolTip(tip)
        menu.addSeparator()
        menu.addAction("Delete point", lambda: (m.select(("node", pid)), m.delete_selected()))
        menu.exec_(gpos)

    def flash(self, pids, ms=4000):
        """Pulsing rings round some points (just added) and the view brought to them."""
        self._stop_flash()
        its = [self.node_items[q] for q in pids if q in self.node_items]
        if not its:
            return
        r = QtCore.QRectF(its[0].pos(), its[0].pos())
        rings = []
        for it in its:
            r = r.united(QtCore.QRectF(it.pos(), it.pos()))
            ring = QtWidgets.QGraphicsEllipseItem(-20, -20, 40, 40)
            ring.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            pen = QtGui.QPen(QtGui.QColor("#facc15"), 3); pen.setCosmetic(True)
            ring.setPen(pen); ring.setZValue(58); ring.setPos(it.pos())
            self._scene.addItem(ring)
            rings.append(ring)
        if not self.mapToScene(self.viewport().rect()).boundingRect().contains(r):
            c = r.center()
            self.centerOn(c)
        self._flash = {"rings": rings, "t": 0, "end": ms}
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.timeout.connect(self._flash_step)
        self._flash_timer.start(50)

    def _flash_step(self):
        f = getattr(self, "_flash", None)
        if not f:
            return
        f["t"] += 50
        s = 1.0 + 0.45 * abs(math.sin(f["t"] / 260.0))
        for ring in f["rings"]:
            ring.setScale(s)
        if f["t"] >= f["end"]:
            self._stop_flash()

    def _stop_flash(self):
        if getattr(self, "_flash_timer", None):
            self._flash_timer.stop()
            self._flash_timer = None
        f = getattr(self, "_flash", None)
        if f:
            for ring in f["rings"]:
                if ring.scene() is self._scene:
                    self._scene.removeItem(ring)
        self._flash = None

    def _route_menu(self, rid, gpos):
        """Right click on a route: its action (in groups), reverse it, select it, delete it."""
        from . import routeactions as RA
        from .routeui import action_icon
        m = self.maker
        r = next((r for r in m.project.routes if r["id"] == rid), None)
        if r is None:
            return
        menu = QtWidgets.QMenu(self)
        menu.addSection("%s → %s" % (rid[1:5], rid[5:9]))
        for g in RA.GROUPS:
            sub = menu.addMenu(g)
            for a in [x for x in RA.ACTIONS if x.group == g]:
                act = sub.addAction(action_icon(a.name, 20), a.label + ("" if a.vanilla else "  (unused in vanilla)"))
                act.setCheckable(True); act.setChecked(a.name == r["type"])
                act.setToolTip(a.description)
                act.triggered.connect(lambda _=False, n=a.name: m.set_route_type(rid, n))
        menu.addSeparator()
        menu.addAction("Curve left", lambda: m.curve_route(rid, -1))
        menu.addAction("Curve right", lambda: m.curve_route(rid, 1))
        if rid[1] == "K" or rid[5] == "K":
            menu.addAction("Straighten (remove bends)", lambda: m.straighten_route(rid))
        menu.addSeparator()
        menu.addAction("Select", lambda: m.select(("route", rid)))
        menu.addAction("Use as the brush's action", lambda: (m.route_type_box.setCurrentIndex(
            m.route_type_box.findData(r["type"])), m.set_tool("action")))
        menu.addAction("Delete route", lambda: (m.select(("route", rid)), m.delete_selected()))
        menu.setToolTipsVisible(True)
        menu.exec_(gpos)

    def _route_at(self, pos, radius=6):
        sp = self.mapToScene(pos)
        tol = radius / (self.transform().m11() or 1)
        for rid, line in self.route_items.items():
            if rid.startswith("~"):                          # warp and cannon lines are not routes
                continue
            l = line.line()
            vx, vy = l.x2() - l.x1(), l.y2() - l.y1()
            L = vx * vx + vy * vy or 1
            t = max(0, min(1, ((sp.x() - l.x1()) * vx + (sp.y() - l.y1()) * vy) / L))
            if math.hypot(sp.x() - (l.x1() + t * vx), sp.y() - (l.y1() + t * vy)) <= tol:
                return rid
        return None

    def _tier_at(self, x, y):
        m, st = self.maker, self._tiers
        size = m.brush_size.value()
        cells = [(x + dx, y + dy) for dy in range(size) for dx in range(size)]
        ch = m.project.set_tiers(cells, st["level"])
        for k, (old, new) in ch.items():
            st["changes"][k] = (st["changes"][k][0], new) if k in st["changes"] else (old, new)
        if ch:
            self.map_item.invalidate({(cx + dx, cy + dy) for (cx, cy) in ch for dx in (-1, 0, 1) for dy in (-1, 0, 1)})
            m.status("Island tier %d (%d world units up)" % (st["level"], st["level"] * m.project.tier_height))

    def _height_at(self, fx, fy):
        m, st = self.maker, self._height
        key = (round(fx * 2), round(fy * 2))                     # one dab per half tile
        if key == st["last"]:
            return
        st["last"] = key
        ch = m.project.height_dab(fx, fy, m.brush_size.value(), st["sign"] * m.height_step.value(), st["flat"])
        for k, (old, new) in ch.items():
            st["changes"][k] = (st["changes"][k][0], new) if k in st["changes"] else (old, new)
        if ch:
            self.map_item.invalidate({(i - di, j - dj) for (i, j) in ch for di in (0, 1) for dj in (0, 1)})
            m.status("Height %.0f" % m.project.height_at(fx, fy))

    def _paint_at(self, x, y):
        m = self.maker
        if m.tool == "erase":
            size = m.brush_size.value()
            cells = {(x + dx, y + dy): -1 for dy in range(size) for dx in range(size)}
        else:
            cells = m.project.stamp_cells(x, y, m.palette.pattern())
        ch = m.project.set_many(m.layer, cells)
        for k, (old, new) in ch.items():
            if k in self._stroke:
                self._stroke[k] = (self._stroke[k][0], new)
            else:
                self._stroke[k] = (old, new)
        if ch:
            self.map_item.invalidate(ch.keys())

    @guarded
    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.pos() - self._pan
            self._pan = e.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            return
        x, y = self.tile_at(e.pos())
        m = self.maker
        if self._pen is not None:
            self._pen_draw(self.mapToScene(e.pos()))
        if self.cursor_item:
            pat = m.palette.pattern()
            w = len(pat[0]) if m.tool == "brush" else (m.brush_size.value() if m.tool == "erase" else 1)
            h = len(pat) if m.tool == "brush" else (m.brush_size.value() if m.tool == "erase" else 1)
            self.cursor_item.setRect(x * TILE, y * TILE, w * TILE, h * TILE)
            self.cursor_item.setVisible(m.tool in ("brush", "erase", "fill", "rect", "pick", "node"))
            if m.tool == "height":
                r = m.brush_size.value()
                sp = self.mapToScene(e.pos())
                self.cursor_item.setRect(sp.x() - r * TILE, sp.y() - r * TILE, 2 * r * TILE, 2 * r * TILE)
                self.cursor_item.setVisible(True)
        m.status_pos(x, y)
        if m.tool == "action" and self.route_items:
            self.viewport().setCursor(QtCore.Qt.PointingHandCursor if self._route_at(e.pos()) else QtCore.Qt.CrossCursor)
        if m.tool == "select" and not e.buttons():
            hover = m.sticker_handle_at(self.mapToScene(e.pos())) is not None
            self.viewport().setCursor(QtCore.Qt.SizeFDiagCursor if hover else QtCore.Qt.ArrowCursor)
        if not e.buttons() & QtCore.Qt.LeftButton:
            return
        if m.tool == "action" and getattr(self, "_brushed", None) is not None:
            self._brush_route(e.pos())
            return
        if getattr(self, "_resize_sticker", None):
            sp = self.mapToScene(e.pos())
            i, w0, d0 = self._resize_sticker
            st = m.project.stickers[i]
            d = math.hypot(sp.x() / TILE - st["x"], sp.y() / TILE - st["y"])
            st["w"] = max(0.25, min(200.0, w0 * d / d0))
            self.sticker_item.update()
            m.status("Image size: %.1f tiles wide" % st["w"])
            return
        if getattr(self, "_drag_sticker", None):
            sp = self.mapToScene(e.pos())
            i, x0, y0, px, py = self._drag_sticker
            st = m.project.stickers[i]
            st["x"], st["y"] = x0 + sp.x() / TILE - px, y0 + sp.y() / TILE - py
            self.sticker_item.update()
            return
        if self._drag_node:
            sp = self.mapToScene(e.pos())
            nx, ny = sp.x() / TILE, sp.y() / TILE
            if e.modifiers() & QtCore.Qt.AltModifier:                # Alt: fine, a quarter of the mouse's movement
                if getattr(self, "_fine", None) is None:
                    self._fine = ((nx, ny), getattr(self, "_drag_pos", None) or self._drag_node[1:3])
                (cx0, cy0), (px0, py0) = self._fine
                nx, ny = px0 + (nx - cx0) * 0.25, py0 + (ny - cy0) * 0.25
            else:
                self._fine = None
                if not e.modifiers() & QtCore.Qt.ControlModifier:    # snap to tile centres unless Ctrl is held
                    nx, ny = math.floor(nx) + 0.5, math.floor(ny) + 0.5
            pid = self._drag_node[0]
            it = self.node_items.get(pid)
            if it:
                it.setPos(nx * TILE, ny * TILE)
            self._drag_pos = (nx, ny)
            return
        if self._stroke is not None:
            self._paint_at(x, y)
        elif self._height is not None:
            sp = self.mapToScene(e.pos())
            self._height_at(sp.x() / TILE, sp.y() / TILE)
        elif getattr(self, "_tiers", None) is not None:
            self._tier_at(x, y)
        elif self._rect_start is not None:
            self.map_item.preview = m.project.rect_cells(*self._rect_start, x, y, m.palette.pattern())
            self.map_item.update()

    @guarded
    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() in (QtCore.Qt.RightButton, QtCore.Qt.MiddleButton):
            self._pan = None
            start = getattr(self, "_pan_start", None)
            if e.button() == QtCore.Qt.RightButton and start is not None and (e.pos() - start).manhattanLength() < 4:
                if self._pen is not None:                     # Route tool: right-click ends the path here
                    self._pen_finish()
                    return
                rid = self._route_at(e.pos())
                hit = self.node_at(e.pos())
                if hit:
                    self._point_menu(hit, e.globalPos())
                elif rid:
                    self._route_menu(rid, e.globalPos())
                else:
                    sp = self.mapToScene(e.pos())
                    self._map_menu(sp.x() / TILE, sp.y() / TILE, e.globalPos())
            return
        if e.button() != QtCore.Qt.LeftButton:
            return
        if getattr(self, "_resize_sticker", None):
            i, w0, _d0 = self._resize_sticker
            self._resize_sticker = None
            st = self.maker.project.stickers[i]
            w = st["w"]
            st["w"] = w0                                      # the undo step resizes it
            if abs(w - w0) > 1e-6:
                self.maker.resize_sticker(i, w)
            return
        if getattr(self, "_drag_sticker", None):
            i, x0, y0, _px, _py = self._drag_sticker
            self._drag_sticker = None
            st = self.maker.project.stickers[i]
            x, y = st["x"], st["y"]
            st["x"], st["y"] = x0, y0                         # the undo step moves it
            if (x, y) != (x0, y0):
                self.maker.move_sticker(i, x, y)
            return
        if getattr(self, "_brushed", None) is not None:
            n = len(self._brushed)
            self._brushed = None
            if n:
                self.maker.undo.endMacro()
            return
        m = self.maker
        if self._drag_node:
            pid, ox, oy = self._drag_node
            self._drag_node = None
            pos = getattr(self, "_drag_pos", None)
            self._drag_pos = None
            if pos and (abs(pos[0] - ox) > 1e-6 or abs(pos[1] - oy) > 1e-6):
                m.move_node(pid, (ox, oy), pos)
            return
        if self._stroke is not None:
            stroke, self._stroke = self._stroke, None
            if stroke:
                m.record_paint(stroke, "Erase" if m.tool == "erase" else "Paint")
        elif self._height is not None:
            st, self._height = self._height, None
            if st["changes"]:
                m.record_heights(st["changes"])
        elif getattr(self, "_tiers", None) is not None:
            st, self._tiers = self._tiers, None
            if st["changes"]:
                m.record_tiers(st["changes"])
        elif self._rect_start is not None:
            x, y = self.tile_at(e.pos())
            cells = m.project.rect_cells(*self._rect_start, x, y, m.palette.pattern())
            self._rect_start = None
            self.map_item.preview = None
            m.paint(cells, "Rectangle")


# --------------------------------------------------------------------------------------------- the workspace
class WorldMaker(QtWidgets.QWidget):
    statusMessage = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.undo = QtWidgets.QUndoStack(self)
        self.host = None              # the main window (ROMFS folder, settings); set by MainWindow
        self.project = project2d.Project()
        self.tileset = Tileset.starter()
        self.layer = 0
        self.selected = None
        self.problem_points, self.problem_text = set(), []      # points named in the checks' errors (red rings)
        self.tool = "brush"
        self._build()
        self.new_project(confirm=False)

    # ---- UI --------------------------------------------------------------------------------------------------------
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QToolBar()
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        for text, slot in (("New", lambda: self.new_project()), ("Open…", self.open_dialog), ("Save", self.save),
                           ("Save as…", self.save_as), ("Export picture…", self.export_png)):
            bar.addAction(text, slot)
        imp = bar.addAction("Import Koopatlas…", self.import_koopatlas)
        imp.setToolTip("Turn a Koopatlas world map (.kpmap, New Super Mario Bros. Wii / Newer) into a Deluxe world: its "
                       "picture, moving doodads, points, paths, unlocks and music")
        port = bar.addAction("Port Newer…", self.port_newer)
        port.setToolTip("Import every Newer world at once into the Deluxe world it replaces (W1 Yoshi's Island ... "
                        "W9b Special World), check them and plan the framework slot hook for all of them together; "
                        "optionally export them all into a mod folder")
        exp = bar.addAction("Export to game…", self.export_game)
        exp.setToolTip("Write this map into a mod folder as the world archive it replaces")
        bar2 = QtWidgets.QToolBar()                          # second row: points and paths
        bar2.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        lab = QtWidgets.QLabel("  Paths: ")
        lab.setStyleSheet("font-weight: bold")
        bar2.addWidget(lab)
        walk = bar2.addAction("Walk test (T)", lambda: self.canvas.walk_test())
        walk.setShortcut(QtGui.QKeySequence("T")); walk.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        walk.setToolTip("Mario walks every path in the order a play-through opens them (click or Esc to stop)")
        self.addAction(walk)
        bar.addSeparator()
        self.tool_group = QtWidgets.QActionGroup(self)
        self.tool_actions = {}
        for key, text, sc in TOOLS:
            a = QtWidgets.QAction("%s (%s)" % (text, sc), self)
            a.setCheckable(True)
            a.setShortcut(QtGui.QKeySequence(sc))
            a.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_group.addAction(a)
            (bar2 if key in PATH_TOOLS else bar).addAction(a)
            self.addAction(a)
            self.tool_actions[key] = a
        bar.addSeparator()
        bar.addWidget(QtWidgets.QLabel(" Size "))
        self.brush_size = QtWidgets.QSpinBox(); self.brush_size.setRange(1, 16)
        self.brush_size.setToolTip("Eraser: tiles across. Height: radius in tiles.")
        bar.addWidget(self.brush_size)
        bar.addWidget(QtWidgets.QLabel(" Height step "))
        self.height_step = QtWidgets.QDoubleSpinBox(); self.height_step.setRange(1, 500); self.height_step.setValue(10)
        self.height_step.setDecimals(0)
        self.height_step.setToolTip("World units per dab of the Height tool: drag raises, Shift-drag lowers, Ctrl-drag "
                                    "flattens to the height where you pressed. Mario is about 60 units tall.")
        bar.addWidget(self.height_step)
        bar.addWidget(QtWidgets.QLabel("  Island tier "))
        self.tier_level = QtWidgets.QSpinBox(); self.tier_level.setRange(0, project2d.MAX_TIER); self.tier_level.setValue(1)
        self.tier_level.setToolTip("Islands tool (T): paint tiles up to this tier, a raised plateau with cliff walls "
                                   "(0 = back to the ground). Shift-drag lowers to 0. Paint the ground first; it shows on "
                                   "the island tops.")
        bar.addWidget(self.tier_level)
        self.theme_box = QtWidgets.QComboBox()
        from . import islands as _isl
        for k, t in _isl.THEMES.items():
            self.theme_box.addItem(t["label"] + " cliffs", k)
        self.theme_box.setToolTip("Theme preset: the island cliffs, the sea round the map and the road colour")
        self.theme_box.activated.connect(self._theme_changed)
        bar.addWidget(self.theme_box)
        self.round_check = QtWidgets.QCheckBox("Round edges")
        self.round_check.setToolTip("Islands get rounded corners (in the game too) and a soft shadow on the ground "
                                    "below them.")
        self.round_check.toggled.connect(self._round_changed)
        bar.addWidget(self.round_check)
        self.roads_check = QtWidgets.QCheckBox("Paint roads")
        self.roads_check.setToolTip("Thick roads along every route, in the theme's road colour, drawn into the map "
                                    "(left out: hidden roads, climbs and jumps).")
        self.roads_check.toggled.connect(self._roads_changed)
        bar.addWidget(self.roads_check)
        bar2.addSeparator()
        bar2.addWidget(QtWidgets.QLabel("  Point kind "))
        self.kind_box = QtWidgets.QComboBox()
        for k, (pat, desc) in project2d.NODE_KINDS.items():
            self.kind_box.addItem("%s (%s)" % (desc, pat.split("{")[0] + "…"), k)
        self.kind_box.setCurrentIndex(1)
        self.kind_box.setToolTip("What the Point tool places, and what the Route tool puts at the end of a path")
        bar2.addWidget(self.kind_box)
        self.chain_box = QtWidgets.QCheckBox("Connect")
        self.chain_box.setChecked(True)
        self.chain_box.setToolTip("A new point joins the selected point with a road of the current action, and the "
                                  "level before it opens it when beaten. Place points one after another to draw a path.")
        bar2.addWidget(self.chain_box)
        self.plot_btn = QtWidgets.QToolButton()
        self.plot_btn.setText("+ Level")
        self.plot_btn.setToolTip("Level plot: the Point tool with the Level kind. Click the map to place the next "
                                 "level (W?01-W?0f); with Connect on it is joined and unlocked automatically.")
        self.plot_btn.clicked.connect(lambda: (self.kind_box.setCurrentIndex(self.kind_box.findData("level")),
                                               self.set_tool("node")))
        bar2.addWidget(self.plot_btn)
        bar2.addWidget(QtWidgets.QLabel("  Action "))
        from .routeui import ActionPalette, action_icon
        self.route_type_box = ActionPalette(columns=5, icon=28, show_labels=True)
        self.action_button = QtWidgets.QToolButton()
        self.action_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.action_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.action_button.setIconSize(QtCore.QSize(22, 22))
        pop = QtWidgets.QMenu(self.action_button)
        wa = QtWidgets.QWidgetAction(pop); wa.setDefaultWidget(self.route_type_box); pop.addAction(wa)
        self.action_button.setMenu(pop)

        def show_action(_i=None):
            name = self.route_type_box.currentData()
            from . import routeactions as RA
            self.action_button.setIcon(action_icon(name, 22))
            self.action_button.setText(RA.action(name).label)
            self.action_button.setToolTip("Action for new routes and the Action brush (A): " + RA.action(name).description)
        self.route_type_box.activated.connect(lambda i: (show_action(), pop.hide(), self._toolbar_action()))
        show_action()
        self._show_action = show_action
        bar2.addWidget(self.action_button)
        self.follow_road = QtWidgets.QCheckBox("Follow the road")
        self.follow_road.setChecked(False)
        self.follow_road.setToolTip("Off (the default): straight lines between your clicks, a bend at every click.\n"
                                    "On: between clicks the path tries to follow the road drawn in your picture (or "
                                    "painted with tiles). Click on the road itself.")
        bar2.insertWidget(self.tool_actions["action"], self.follow_road)   # right after Point / Route
        need = QtWidgets.QToolButton()
        need.setText("Add needed points")
        need.setToolTip("Places the points this world needs so the game never crashes: the entry point, the castle, "
                        "the map-music point and the points the map enemies and Yoshi start on. They go in a row; move "
                        "them where you like and join them with the Route tool.")
        need.clicked.connect(self.add_needed_points)
        bar2.insertWidget(self.tool_actions["action"], need)
        bar2.insertSeparator(self.tool_actions["action"])
        self.path_hint = QtWidgets.QLabel("")
        self.path_hint.setStyleSheet("color: #93c5fd")
        self.path_hint.setWordWrap(True)
        self.path_hint.setContentsMargins(8, 0, 8, 2)
        self.grid_check = QtWidgets.QCheckBox("Grid"); self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._toggle_grid)
        bar.addWidget(self.grid_check)
        lay.addWidget(bar)
        bar2.removeAction(walk)
        bar2.addSeparator()
        bar2.addAction(walk)                                 # last: the least used
        rv = bar2.addAction("Reveal preview", lambda: self.canvas.reveal_preview())
        rv.setToolTip("Watch the paths open one after another in play-through order: their path art (images set to "
                      "'Appears when path opens') fades in as it will in the game")
        lay.addWidget(bar2)
        self.paths_bar = bar2
        lay.addWidget(self.path_hint)

        split = QtWidgets.QSplitter()
        lay.addWidget(split, 1)
        left = QtWidgets.QWidget(); ll = QtWidgets.QVBoxLayout(left); ll.setContentsMargins(4, 4, 4, 4)
        self.tileset_label = QtWidgets.QLabel()
        b_ts = QtWidgets.QPushButton("Use a PNG tile sheet…"); b_ts.clicked.connect(self.load_tileset_dialog)
        b_st = QtWidgets.QPushButton("Use the starter tileset"); b_st.clicked.connect(lambda: self.use_tileset("starter"))
        self.palette = Palette(self.tileset)
        sa = QtWidgets.QScrollArea(); sa.setWidget(self.palette); sa.setWidgetResizable(False)
        self.tile_name = QtWidgets.QLabel()
        self.palette.selectionChanged.connect(self._palette_changed)
        ll.addWidget(self.tileset_label); ll.addWidget(sa, 1); ll.addWidget(self.tile_name); ll.addWidget(b_ts); ll.addWidget(b_st)
        split.addWidget(left)

        self.canvas = Canvas(self)
        self.views = QtWidgets.QTabWidget()
        self.views.addTab(self.canvas, "2D map")
        self.views.addTab(self._build_3d(), "3D world")
        self.views.currentChanged.connect(self._view_tab_changed)
        split.addWidget(self.views)

        right = QtWidgets.QWidget(); rl = QtWidgets.QVBoxLayout(right); rl.setContentsMargins(4, 4, 4, 4)
        mapbox = QtWidgets.QGroupBox("Map")
        mf = QtWidgets.QFormLayout(mapbox)
        self.name_edit = QtWidgets.QLineEdit()
        self.world_spin = QtWidgets.QSpinBox(); self.world_spin.setRange(1, 9)
        self.slot_box = QtWidgets.QComboBox()
        self.slot_box.setToolTip("The world archive this map replaces. The game only loads these twelve, and each needs a "
                                 "few points its code looks up by name; the checks list them and the export adds any "
                                 "that are missing, hidden.")
        self.world_spin.valueChanged.connect(self._fill_slots)
        self.link_check = QtWidgets.QCheckBox("Castle leads on to the next world")
        self.link_check.setToolTip("On export, your point with the vanilla exit's name (W1C0 for World 1) gets the game's "
                                   "exit: it opens the next world's start, and the vanilla walk there is copied next to "
                                   "your castle. Untick it for a world that ends at its castle.")
        self.tile_size_spin = QtWidgets.QDoubleSpinBox(); self.tile_size_spin.setRange(1, 1000); self.tile_size_spin.setDecimals(1)
        self.tile_size_spin.setToolTip("World units per tile. 30 is about half of Mario (an estimate from the map's markers).")
        self.stretch_spin = QtWidgets.QDoubleSpinBox(); self.stretch_spin.setRange(1.0, 3.0); self.stretch_spin.setSingleStep(0.1)
        self.stretch_spin.setDecimals(2)
        self.stretch_spin.setToolTip("In game only: the map is stretched this much front to back. The game's camera looks "
                                     "down at a slant, so a map drawn for a straight-down view (Koopatlas) looks squashed "
                                     "to about half its height; 2 brings circles, fences and trees back to their drawn "
                                     "shape. 1 = as drawn.")
        self.w_spin = QtWidgets.QSpinBox(); self.w_spin.setRange(8, 512)
        self.h_spin = QtWidgets.QSpinBox(); self.h_spin.setRange(8, 512)
        apply_btn = QtWidgets.QPushButton("Apply"); apply_btn.clicked.connect(self._apply_map_settings)
        mf.addRow("Name", self.name_edit); mf.addRow("World number", self.world_spin); mf.addRow("Replaces", self.slot_box)
        mf.addRow(self.link_check)
        self.music_box = QtWidgets.QComboBox()
        for k, label in project2d.MUSIC_NAMES.items():
            self.music_box.addItem(label, k)
        self.music_box.addItem("Custom song (nsmbudx-music)…", -1)
        self.music_box.setToolTip("The music this world map plays. Deluxe's map music is one song with a part per world "
                                  "(SndBgmMgr, 0x6080D540); pick whose. Needs the framework's map music hook "
                                  "(nsmbud-reverse, code/framework/map_music.cpp); without it the game plays its own.")
        self.music_box.activated.connect(self._music_changed)
        mf.addRow("Music", self.music_box)
        self.slot_check = QtWidgets.QCheckBox("More levels per world (framework slot hook)")
        self.slot_check.setToolTip("Deluxe keeps clears only for some courses per world (World 1: 1-1 to 1-5, its tower, "
                                   "castle and cannon). With this on, every other level keeps its clear in a free entry "
                                   "of the world (hidden points Fq/Fr, read by the framework's course_slots.cpp hook "
                                   "at 0x6040E5B0), so its exits open paths. Needs the Koopatlas DX framework build.")
        self.slot_check.toggled.connect(self._slot_hook_changed)
        mf.addRow(self.slot_check)
        self.reveal_box = QtWidgets.QComboBox()
        self.reveal_box.addItem("Sweeps along the path", "sweep")
        self.reveal_box.addItem("Fades in", "fade")
        self.reveal_box.setToolTip("How path art (images that appear when a path opens) shows up in game: the route "
                                   "mask slides along the path from where it starts (like the game's own roads), or "
                                   "the whole piece fades in at once.")
        self.reveal_box.activated.connect(self._reveal_style_changed)
        mf.addRow("Path art", self.reveal_box)
        self.model_label = QtWidgets.QLabel("Ground: painted tiles")
        self.model_label.setWordWrap(True)
        b_model = QtWidgets.QPushButton("3D ground model…"); b_model.clicked.connect(self.load_model_dialog)
        b_model.setToolTip("Use a 3D model as the world's ground: FBX, COLLADA (.dae), Wavefront OBJ (with its MTL), or "
                           "a Wii model (.brres / .arc). It is fitted into the map; points stand on its surface.")
        self.b_editmodel = QtWidgets.QPushButton("Edit…"); self.b_editmodel.clicked.connect(self.edit_ground_model)
        self.b_editmodel.setToolTip("Model editor: hide materials, change how they are drawn, their textures or colours, "
                                    "turn and scale the model, save it as DAE/OBJ.")
        self.b_nomodel = QtWidgets.QPushButton("Remove"); self.b_nomodel.clicked.connect(self.remove_model)
        mrow = QtWidgets.QHBoxLayout(); mrow.addWidget(b_model, 1); mrow.addWidget(self.b_editmodel); mrow.addWidget(self.b_nomodel)
        mf.addRow(self.model_label); mf.addRow(mrow)
        mf.addRow("Units per tile", self.tile_size_spin); mf.addRow("Front-to-back stretch", self.stretch_spin)
        mf.addRow("Width (tiles)", self.w_spin); mf.addRow("Height (tiles)", self.h_spin)
        mf.addRow(apply_btn)
        self.side_tabs = QtWidgets.QTabWidget()
        self.side_tabs.setDocumentMode(True)

        def page(*widgets):
            w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(2, 4, 2, 2)
            for x in widgets:
                v.addWidget(x)
            v.addStretch(1)
            sa = QtWidgets.QScrollArea(); sa.setWidget(w); sa.setWidgetResizable(True)
            sa.setFrameShape(QtWidgets.QFrame.NoFrame)
            return sa
        self._map_page_widgets = [mapbox]
        art_box, scenery_box = self._build_picture_box(), self._build_scenery_box()
        rl.addWidget(self.side_tabs, 2)

        laybox = QtWidgets.QGroupBox("Layers (top of the list draws last)")
        lv = QtWidgets.QVBoxLayout(laybox)
        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.currentRowChanged.connect(self._layer_selected)
        self.layer_list.itemChanged.connect(self._layer_item_changed)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("+", self.add_layer), ("−", self.remove_layer), ("↑", lambda: self.move_layer(1)), ("↓", lambda: self.move_layer(-1))):
            b = QtWidgets.QPushButton(text); b.setFixedWidth(34); b.clicked.connect(slot); row.addWidget(b)
        self.lock_check = QtWidgets.QCheckBox("Locked"); self.lock_check.toggled.connect(self._lock_toggled)
        row.addWidget(self.lock_check)
        lv.addWidget(self.layer_list); lv.addLayout(row)
        self.side_tabs.addTab(page(mapbox, laybox), "Map")
        self.side_tabs.addTab(page(art_box), "Your art")
        self.side_tabs.addTab(page(scenery_box), "3D scenery")
        self.side_tabs.setToolTip("Map: name, size, ground and layers · Your art: your drawn map and moving images · "
                                  "3D scenery: castles, houses and props")

        self.sel_box = QtWidgets.QGroupBox("Point / path")
        sf = QtWidgets.QFormLayout(self.sel_box)
        self.sel_form = sf
        self.sel_title = QtWidgets.QLabel("Nothing selected")
        self.sel_title.setWordWrap(True)
        self.sel_title.setStyleSheet("font-size: 13pt; font-weight: bold; padding: 2px 0 4px 0")
        self.sel_help = QtWidgets.QLabel()
        self.sel_help.setWordWrap(True)
        self.sel_help.setStyleSheet("color: #94a3b8")
        self.id_edit = QtWidgets.QLineEdit(); self.id_edit.editingFinished.connect(self._rename_node)
        self.cells = {}
        sf.addRow(self.sel_title)
        sf.addRow(self.sel_help)
        sf.addRow("Name", self.id_edit)
        from .routeui import PointActionBar
        self.point_bar = PointActionBar()
        self.point_bar.triggered.connect(self.toggle_point_action)
        sf.addRow("Point action", self.point_bar)
        self.flag_pick = QtWidgets.QComboBox()
        self.flag_pick.setToolTip("Add a flag to the selected point. The first ones are what this world needs: "
                                  "map enemies start on their flag, the map Yoshi waits on its flag.")
        self.flag_pick.activated.connect(self._flag_picked)
        sf.addRow("Add flag", self.flag_pick)
        self.cam = [QtWidgets.QDoubleSpinBox() for _ in range(3)]
        cam_row = QtWidgets.QHBoxLayout(); cam_row.setContentsMargins(0, 0, 0, 0)
        for sp in self.cam:
            sp.setRange(-100000, 100000); sp.setDecimals(1); sp.setKeyboardTracking(False)
            sp.editingFinished.connect(self._camera_edited); cam_row.addWidget(sp)
        self.cam_w = QtWidgets.QWidget(); self.cam_w.setLayout(cam_row)
        self.cam_w.setToolTip("Where the camera looks from at this point (0, 0, 0: the game's own view of Mario)")
        sf.addRow("Camera offset", self.cam_w)
        self.adv_btn = QtWidgets.QToolButton()
        self.adv_btn.setText("Game table (advanced)")
        self.adv_btn.setCheckable(True)
        self.adv_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.adv_btn.setArrowType(QtCore.Qt.RightArrow)
        self.adv_btn.setAutoRaise(True)
        self.adv_btn.toggled.connect(lambda on: (self.adv_btn.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow),
                                                 self._show_selection()))
        sf.addRow(self.adv_btn)
        for col in range(2, 9):
            ed = QtWidgets.QLineEdit(); ed.editingFinished.connect(lambda c=col: self._cell_edited(c))
            self.cells[col] = ed
            sf.addRow(rules.COLUMNS[col], ed)
        from .routeui import ActionPalette as _Pal
        self.route_type_sel = _Pal(columns=6, icon=26)
        self.route_type_sel.activated.connect(self._route_type_changed)
        sf.addRow("Action", self.route_type_sel)
        self.route_btns = QtWidgets.QWidget()
        rb = QtWidgets.QHBoxLayout(self.route_btns); rb.setContentsMargins(0, 0, 0, 0)
        for text, fn, tip in (("Curve left", lambda: self._sel_route(lambda r: self.curve_route(r, -1)), "Bend this path into an arc"),
                              ("Curve right", lambda: self._sel_route(lambda r: self.curve_route(r, 1)), "Bend this path into an arc"),
                              ("Straighten", lambda: self._sel_route(self.straighten_route), "Remove the bends of this path")):
            b = QtWidgets.QPushButton(text); b.clicked.connect(fn); b.setToolTip(tip); rb.addWidget(b)
        sf.addRow(self.route_btns)
        self.unlock_host = QtWidgets.QWidget()
        self.unlock_form = QtWidgets.QFormLayout(self.unlock_host); self.unlock_form.setContentsMargins(0, 0, 0, 0)
        sf.addRow(self.unlock_host)
        self.del_btn = QtWidgets.QPushButton("Delete"); self.del_btn.clicked.connect(self.delete_selected)
        sf.addRow(self.del_btn)
        rl.addWidget(self.sel_box)
        self._right_layout = rl

        chk = QtWidgets.QGroupBox("Checks (the game's rules)")
        self.checks_box = chk
        cv = QtWidgets.QVBoxLayout(chk)
        self.checks = QtWidgets.QListWidget()
        cv.addWidget(self.checks)
        rl.addWidget(chk, 1)
        scroll = QtWidgets.QScrollArea(); scroll.setWidget(right); scroll.setWidgetResizable(True)
        split.addWidget(scroll)
        split.setSizes([220, 1000, 300])
        # the canvas gets the room: on a small screen the side panels used to squeeze it to a sliver
        for i, stretch in enumerate((0, 1, 0)):
            split.setStretchFactor(i, stretch)
        split.setChildrenCollapsible(False)
        self.canvas.setMinimumWidth(360)
        left.setMinimumWidth(160)
        scroll.setMinimumWidth(220)
        dele = QtWidgets.QShortcut(QtGui.QKeySequence.Delete, self.canvas)
        dele.activated.connect(self.delete_selected)
        self.set_tool("brush")

    # ---- small helpers ---------------------------------------------------------------------------------------------
    def status(self, text):
        self.statusMessage.emit(text)

    def status_pos(self, x, y):
        p = self.project
        if p.inside(x, y):
            t = p.get(self.layer, x, y)
            name = self.tileset.names[t] if 0 <= t < self.tileset.count else "empty"
            self.statusMessage.emit("Tile %d, %d (world %.0f, %.0f, height %.0f) · %s: %s" % (
                x, y, (x + 0.5) * p.tile_size, (y + 0.5) * p.tile_size, p.height_at(x + 0.5, y + 0.5),
                p.layers[self.layer].name, name))

    def node_kind(self):
        return self.kind_box.currentData()

    def current_layer_locked(self):
        return self.project.layers[self.layer].locked

    def set_tool(self, key):
        self.tool = key
        self.tool_actions[key].setChecked(True)
        self.canvas._route_from = None
        self.canvas.pen_cancel()
        self.path_hint.setText("  " + PATH_HINTS.get(key, ""))
        self.canvas.setCursor(QtCore.Qt.CrossCursor if key not in ("select",) else QtCore.Qt.ArrowCursor)

    def _toggle_grid(self, on):
        if self.canvas.map_item:
            self.canvas.map_item.show_grid = on
            self.canvas.map_item.update()

    def _palette_changed(self):
        pat = self.palette.pattern()
        t = pat[0][0]
        self.tile_name.setText("%s%s" % (self.tileset.names[t] if t < self.tileset.count else "?",
                                         "  (stamp %d×%d)" % (len(pat[0]), len(pat)) if len(pat) > 1 or len(pat[0]) > 1 else ""))

    # ---- project lifecycle -----------------------------------------------------------------------------------------
    def new_project(self, confirm=True, **kwargs):
        if confirm and not self.undo.isClean() and not self._confirm():
            return
        self.project = project2d.Project(**kwargs)
        self.use_tileset(self.project.tileset, refresh=False)
        self._after_project_change()

    def _confirm(self):
        return QtWidgets.QMessageBox.question(self, "Unsaved changes", "Discard the changes to this map?",
                                              QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def _after_project_change(self):
        if hasattr(self, "theme_box"):
            self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(self.project.theme)))
        for box, v in (("round_check", self.project.island_round), ("roads_check", bool(self.project.road_colour))):
            if hasattr(self, box):
                getattr(self, box).blockSignals(True); getattr(self, box).setChecked(bool(v))
                getattr(self, box).blockSignals(False)
        self.undo.clear()
        self.layer = 0
        self.selected = None
        self.canvas.rebuild()
        self._fill_map_fields()
        self._fill_layers()
        self._pic_cache = None
        if hasattr(self, "sticker_list"):
            self._fill_pictures()
        self._show_selection()
        self._model_changed()                       # the 3D ground's picture (if any); runs the checks
        self.canvas.fitInView(self.canvas.map_item.boundingRect(), QtCore.Qt.KeepAspectRatio)

    def open_dialog(self):
        if not self.undo.isClean() and not self._confirm():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open a World Maker map", "", "Koopatlas DX maps (*.kdxmap);;All files (*)")
        if path:
            self.open(path)

    def open(self, path):
        try:
            proj = project2d.Project.load(path)
            ts = load_tileset(proj.tileset)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Could not open the map", str(e))
            return False
        self.project = proj
        self.tileset = ts
        self.palette.set_tileset(ts)
        self.tileset_label.setText("Tileset: %s" % ts.name)
        self._after_project_change()
        return True

    def save(self):
        if self.project.path is None:
            return self.save_as()
        self.project.save(self.project.path)
        self.undo.setClean()
        self.status("Saved %s" % self.project.path)
        return True

    def save_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the map", (self.project.name or "world") + ".kdxmap",
                                                        "Koopatlas DX maps (*.kdxmap)")
        if not path:
            return False
        self.project.save(path)
        self.undo.setClean()
        self.status("Saved %s" % path)
        return True

    def export_png(self, path=None):
        if path is None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export the map as a picture", (self.project.name or "world") + ".png", "PNG (*.png)")
            if not path:
                return None
        img = self.render_image()
        img.save(str(path))
        self.status("Exported %s (%d×%d)" % (path, img.width(), img.height()))
        return img

    # ---- your own art: picture ground and animated images (kdx.pictures) ---------------------------------------------
    def _build_picture_box(self):
        from . import pictures
        box = QtWidgets.QGroupBox("Your art: picture ground and animated images")
        f = QtWidgets.QFormLayout(box)
        tip = QtWidgets.QLabel("Draw or paint your world in any art program, import it here, then put the points and "
                               "paths on top. Add trees, ships, clouds or waterfalls as separate images (PNG, or GIF "
                               "for frames) and make them move.")
        tip.setWordWrap(True); tip.setStyleSheet("color: #9fb3c8")
        f.addRow(tip)
        self.pic_label = QtWidgets.QLabel("Ground picture: none (tiles)")
        self.pic_label.setWordWrap(True)
        b1 = QtWidgets.QPushButton("Picture ground…"); b1.clicked.connect(self.picture_dialog)
        b1.setToolTip("Use your own drawn map as the ground. It covers the whole map; tiles painted on top stay visible.")
        self.b_nopic = QtWidgets.QPushButton("Remove"); self.b_nopic.clicked.connect(lambda: self.set_picture(None))
        r = QtWidgets.QHBoxLayout(); r.addWidget(b1, 1); r.addWidget(self.b_nopic)
        f.addRow(self.pic_label); f.addRow(r)
        self.pic_over = QtWidgets.QCheckBox("Picture over the tiles (its clear parts show them)")
        self.pic_over.setToolTip("For a picture with transparency (PNG, WebP, GIF): your island art sits on top and the "
                                 "tiles you painted (sea, sky) show through where it is clear")
        self.pic_over.toggled.connect(self.set_picture_over)
        self.b_picislands = QtWidgets.QPushButton("Raise islands from the picture's shape")
        self.b_picislands.setToolTip("Every tile where the picture is solid (not transparent) becomes an island at the "
                                     "Islands tool's tier, with cliffs round its edge")
        self.b_picislands.clicked.connect(self.islands_from_picture)
        f.addRow(self.pic_over); f.addRow(self.b_picislands)
        self.sticker_list = QtWidgets.QListWidget(); self.sticker_list.setMaximumHeight(110)
        self.sticker_list.currentRowChanged.connect(lambda _r: self._sticker_selected())
        f.addRow(self.sticker_list)
        r2 = QtWidgets.QHBoxLayout()
        for text, slot, t in (("+ Image / GIF…", self.add_sticker_dialog, "A tree, a ship, a waterfall… placed in the "
                               "middle of the view; drag it with the Select tool"),
                              ("Duplicate", self.duplicate_sticker, "Another one of the selected image"),
                              ("Remove", self.remove_sticker, "Take the selected image off the map")):
            b = QtWidgets.QPushButton(text); b.clicked.connect(slot); b.setToolTip(t); r2.addWidget(b)
        f.addRow(r2)
        self.st_anim = QtWidgets.QComboBox()
        for k, label in pictures.ANIMS.items():
            self.st_anim.addItem(label, k)
        self.st_strip = QtWidgets.QSpinBox(); self.st_strip.setRange(1, 64)
        self.st_strip.setToolTip("A PNG strip: how many frames side by side (GIFs know their frames)")
        self.st_speed = QtWidgets.QDoubleSpinBox(); self.st_speed.setRange(0.1, 10); self.st_speed.setSingleStep(0.1)
        self.st_amount = QtWidgets.QDoubleSpinBox(); self.st_amount.setRange(0.1, 10); self.st_amount.setSingleStep(0.1)
        self.st_w = QtWidgets.QDoubleSpinBox(); self.st_w.setRange(0.25, 200); self.st_w.setSingleStep(0.5)
        self.st_w.setSuffix(" tiles wide")
        f.addRow("Animation", self.st_anim); f.addRow("Frames in strip", self.st_strip)
        f.addRow("Speed", self.st_speed); f.addRow("Amount", self.st_amount); f.addRow("Size", self.st_w)
        for wdg in (self.st_strip, self.st_speed, self.st_amount, self.st_w):
            wdg.setKeyboardTracking(False)
            wdg.valueChanged.connect(lambda _v: self._sticker_edited())
        self.st_anim.activated.connect(lambda _i: self._sticker_edited())
        self.st_reveal = QtWidgets.QComboBox()
        self.st_reveal.setToolTip("Path art (Koopatlas's associate layers): the image stays hidden until this path opens "
                                  "and then fades in, in the game too. Only paths some level's exit opens are listed.")
        self.st_reveal.activated.connect(lambda _i: self._sticker_edited())
        f.addRow("Appears when", self.st_reveal)
        self.anim_note = QtWidgets.QLabel("")
        self.anim_note.setWordWrap(True); self.anim_note.setStyleSheet("color: #9fb3c8")
        f.addRow(self.anim_note)
        self._sticker_timer = QtCore.QTimer(self)
        self._sticker_timer.timeout.connect(self._sticker_tick)
        self._sticker_clock = QtCore.QElapsedTimer(); self._sticker_clock.start()
        self._sticker_timer.start(40)
        return box

    def picture_image(self):
        """The ground picture as a QImage (cached by path), or None."""
        pic = self.project.picture
        if not pic:
            return None
        cached = getattr(self, "_pic_cache", None)
        if cached and cached[0] == pic["path"]:
            return cached[1]
        img = QtGui.QImage(pic["path"])
        img = None if img.isNull() else img
        self._pic_cache = (pic["path"], img)
        return img

    def picture_dialog(self):
        from . import pictures
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Your drawn map (the ground)", "", pictures.IMAGE_FILTER)
        if not path:
            return
        img = QtGui.QImage(path)
        if img.isNull():
            QtWidgets.QMessageBox.warning(self, "Picture ground", "%s can't be read as an image." % path)
            return
        p = self.project
        aspect = img.width() / max(1, img.height())
        fit = QtWidgets.QMessageBox.question(
            self, "Picture ground", "Fit the map to the picture's shape (%d×%d px)?\n\nYes: the map keeps its width "
            "(%d tiles) and gets the picture's proportions.\nNo: the picture is stretched over the map as it is." % (
                img.width(), img.height(), p.width)) == QtWidgets.QMessageBox.Yes
        self.set_picture({"path": str(Path(path).resolve())},
                         (p.width, max(8, min(512, round(p.width / aspect)))) if fit else None)

    def set_picture(self, pic, size=None):
        import copy
        p = self.project
        snap = lambda: ([list(l.data) for l in p.layers], copy.copy(p.heights), copy.copy(p.tiers),
                        {k: (n["x"], n["y"]) for k, n in p.nodes.items()})
        old = (p.picture, p.width, p.height, snap())
        new = (pic, size[0] if size else p.width, size[1] if size else p.height, None)

        def put(v):
            p.picture = v[0]
            if (v[1], v[2]) != (p.width, p.height):
                p.resize(v[1], v[2])
                if v[3] is not None:                          # undo: everything the resize cut comes back
                    for lay, data in zip(p.layers, v[3][0]):
                        lay.data = list(data)
                    p.heights, p.tiers = v[3][1], v[3][2]
                    for k, (x, y) in v[3][3].items():
                        if k in p.nodes:
                            p.nodes[k]["x"], p.nodes[k]["y"] = x, y
                self.canvas.rebuild()
            self._pic_cache = None
            self._fill_pictures()
            self.canvas.map_item.invalidate()
        self.undo.push(Command("Picture ground" if pic else "Remove picture ground", lambda: put(new), lambda: put(old),
                               lambda: None))

    def set_picture_over(self, on):
        pic = self.project.picture
        if not pic or bool(pic.get("over")) == bool(on):
            return
        new = dict(pic, over=bool(on))
        if not on:
            new.pop("over")
        self.set_picture(new)

    def picture_solid(self):
        """{(x, y): solid} per tile from the ground picture's transparency (alpha >= 50% at the tile's middle), or None
        without a picture."""
        img = self.picture_image()
        if img is None:
            return None
        p = self.project
        small = img.scaled(p.width, p.height, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        return {(x, y): QtGui.qAlpha(small.pixel(x, y)) >= 128 for y in range(p.height) for x in range(p.width)}

    def islands_from_picture(self):
        solid = self.picture_solid()
        if solid is None:
            return
        if all(solid.values()):
            QtWidgets.QMessageBox.information(self, "Islands from the picture", "This picture has no transparent parts, "
                                              "so there is no shape to raise. Save your art as a PNG with a clear "
                                              "(transparent) sea or sky round the islands.")
            return
        level = self.tier_level.value()
        p = self.project
        changes = p.set_tiers([c for c, v in solid.items() if v], level)
        changes.update(p.set_tiers([c for c, v in solid.items() if not v], 0))
        if changes:
            self.record_tiers(changes)
            self._heights_changed()
            self.status("Raised %d tiles to tier %d from the picture's shape." % (sum(solid.values()), level))

    def _fill_pictures(self):
        p = self.project
        self.pic_label.setText("Ground picture: %s" % (Path(p.picture["path"]).name if p.picture else "none (tiles)"))
        self.b_nopic.setEnabled(bool(p.picture))
        self.pic_over.blockSignals(True)
        self.pic_over.setChecked(bool(p.picture and p.picture.get("over")))
        self.pic_over.blockSignals(False)
        self.pic_over.setEnabled(bool(p.picture))
        self.b_picislands.setEnabled(bool(p.picture))
        row = self.sticker_list.currentRow()
        self.sticker_list.blockSignals(True)
        self.sticker_list.clear()
        from . import pictures
        for s in p.stickers:
            self.sticker_list.addItem("%s — %s" % (s.get("name", "image"), pictures.ANIMS.get(s.get("anim", "none"), "")))
        self.sticker_list.setCurrentRow(min(row, len(p.stickers) - 1) if p.stickers else -1)
        self.sticker_list.blockSignals(False)
        self._sticker_selected()

    def selected_sticker(self):
        i = self.sticker_list.currentRow() if hasattr(self, "sticker_list") else -1
        return i if 0 <= i < len(self.project.stickers) else None

    def sticker_rect(self, s):
        from . import pictures
        frames, _d = pictures.load_frames(s["path"], s.get("strip", 1))
        img = frames[0] if frames else None
        w = float(s.get("w", 3.0)) * TILE
        h = float(s["h"]) * TILE if s.get("h") else w * (img.height() / max(1, img.width()) if img is not None else 1.0)
        return QtCore.QRectF(s["x"] * TILE - w / 2, s["y"] * TILE - h / 2, w, h)

    def sticker_at(self, scene_pos):
        for i in reversed(range(len(self.project.stickers))):
            if self.sticker_rect(self.project.stickers[i]).contains(scene_pos):
                return i
        return None

    def _set_stickers(self, new, text):
        import copy
        p = self.project
        old, new = copy.deepcopy(p.stickers), copy.deepcopy(new)

        def put(v):
            p.stickers = copy.deepcopy(v)
            self._fill_pictures()
            self.canvas.sticker_item.update()
        self.undo.push(Command(text, lambda: put(new), lambda: put(old), lambda: None))

    def add_sticker_dialog(self):
        from . import pictures
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Images to put on the map", "", pictures.IMAGE_FILTER)
        if paths:
            self.add_stickers(paths)

    def add_stickers(self, paths, at=None):
        import copy
        from . import pictures
        p = self.project
        c = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        x, y = at or (c.x() / TILE, c.y() / TILE)
        new = copy.deepcopy(p.stickers)
        for k, path in enumerate(paths):
            frames, _d = pictures.load_frames(path)
            if not frames:
                continue
            anim = "frames" if len(frames) > 1 else "none"
            new.append({"name": Path(path).stem, "path": str(Path(path).resolve()), "x": x + k, "y": y,
                        "w": max(1.0, min(12.0, frames[0].width() / TILE)), "anim": anim, "strip": 1,
                        "speed": 1.0, "amount": 1.0})
        self._set_stickers(new, "Add image")
        self.sticker_list.setCurrentRow(len(p.stickers) - 1)
        self.set_tool("select")

    def duplicate_sticker(self):
        import copy
        i = self.selected_sticker()
        if i is None:
            return
        new = copy.deepcopy(self.project.stickers)
        s = dict(new[i]); s["x"] += 1.0; s["y"] += 0.5
        new.append(s)
        self._set_stickers(new, "Duplicate image")
        self.sticker_list.setCurrentRow(len(new) - 1)

    def remove_sticker(self):
        import copy
        i = self.selected_sticker()
        if i is None:
            return
        new = copy.deepcopy(self.project.stickers)
        del new[i]
        self._set_stickers(new, "Remove image")

    def sticker_handle_at(self, scene_pos):
        """The selected image's index if scene_pos is on one of its corner handles, else None."""
        i = self.selected_sticker()
        if i is None or i >= len(self.project.stickers):
            return None
        r = self.sticker_rect(self.project.stickers[i])
        k = HANDLE_PX / max(1e-6, self.canvas.transform().m11())
        for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            if abs(scene_pos.x() - c.x()) <= k and abs(scene_pos.y() - c.y()) <= k:
                return i
        return None

    def resize_sticker(self, i, w):
        import copy
        new = copy.deepcopy(self.project.stickers)
        if new[i].get("h"):
            new[i]["h"] = float(new[i]["h"]) * float(w) / max(1e-6, float(new[i].get("w", 3.0)))
        new[i]["w"] = float(w)
        self._set_stickers(new, "Resize image")
        self.sticker_list.setCurrentRow(i)

    def move_sticker(self, i, x, y):
        import copy
        new = copy.deepcopy(self.project.stickers)
        new[i]["x"], new[i]["y"] = float(x), float(y)
        self._set_stickers(new, "Move image")

    def _sticker_selected(self):
        i = self.selected_sticker()
        on = i is not None
        for wdg in (self.st_anim, self.st_strip, self.st_speed, self.st_amount, self.st_w, self.st_reveal):
            wdg.setEnabled(on)
            wdg.blockSignals(True)
        self.st_reveal.clear()
        self.st_reveal.addItem("Always shown", "")
        for pth in sorted(self._opened_paths()):
            self.st_reveal.addItem("path %s opens" % pth, pth)
        if on:
            s = self.project.stickers[i]
            self.st_anim.setCurrentIndex(max(0, self.st_anim.findData(s.get("anim", "none"))))
            self.st_strip.setValue(int(s.get("strip", 1)))
            self.st_speed.setValue(float(s.get("speed", 1.0)))
            self.st_amount.setValue(float(s.get("amount", 1.0)))
            self.st_w.setValue(float(s.get("w", 3.0)))
            if s.get("reveal") and self.st_reveal.findData(s["reveal"]) < 0:
                self.st_reveal.addItem("path %s opens (nothing opens it now)" % s["reveal"], s["reveal"])
            self.st_reveal.setCurrentIndex(max(0, self.st_reveal.findData(s.get("reveal", ""))))
        for wdg in (self.st_anim, self.st_strip, self.st_speed, self.st_amount, self.st_w, self.st_reveal):
            wdg.blockSignals(False)
        self.anim_note.setText("Drag the image with the Select tool. It plays here as it will on the map. In the game "
                               "it moves the same way (GIFs keep up to 24 frames); still images are painted into the map."
                               if on else "Select an image to animate it.")
        if hasattr(self.canvas, "sticker_item"):
            self.canvas.sticker_item.update()

    def _sticker_edited(self):
        import copy
        i = self.selected_sticker()
        if i is None:
            return
        new = copy.deepcopy(self.project.stickers)
        s = new[i]
        s.update(anim=self.st_anim.currentData(), strip=self.st_strip.value(), speed=self.st_speed.value(),
                 amount=self.st_amount.value(), w=self.st_w.value())
        if self.st_reveal.currentData():
            s["reveal"] = self.st_reveal.currentData()
        else:
            s.pop("reveal", None)
        if s != self.project.stickers[i]:
            self._set_stickers(new, "Image settings")

    def _sticker_tick(self):
        if not self.project.stickers or not hasattr(self.canvas, "sticker_item"):
            return
        if any(s.get("anim", "none") != "none" for s in self.project.stickers):
            self.canvas.sticker_item.t_ms = float(self._sticker_clock.elapsed())
            self.canvas.sticker_item.update()

    def _opened_paths(self):
        from .export2d import open_routes
        return open_routes(self.project)

    def reveal_alpha(self, path):
        """How much of a path's art the editor shows: 1, or less while the reveal preview / walk test plays."""
        st = getattr(self.canvas, "_reveal", None)
        if st is None or path not in self._opened_paths():
            return 1.0
        t0 = st.get(path)
        if t0 is None:
            return 0.0
        import time
        span = 1.4 if self.project.reveal_style == "sweep" else 0.8
        return max(0.0, min(1.0, (time.monotonic() - t0) / span))

    def reveal_clip(self, path, a):
        """While a path's art sweeps in (reveal style "sweep"): the part of the map along the path it has reached, as
        a QPainterPath in scene units, or None (fade, or no such path)."""
        if self.project.reveal_style != "sweep":
            return None
        from . import pictures
        line = pictures.path_line(self.project, path)
        if len(line) < 2:
            return None
        pts = pictures.line_until(line, a)
        pp = QtGui.QPainterPath(QtCore.QPointF(pts[0][0] * TILE, pts[0][1] * TILE))
        for x, y in pts[1:]:
            pp.lineTo(x * TILE, y * TILE)
        st = QtGui.QPainterPathStroker()
        st.setWidth(pictures.SWEEP_WIDTH * TILE)
        st.setCapStyle(QtCore.Qt.RoundCap)
        st.setJoinStyle(QtCore.Qt.RoundJoin)
        return st.createStroke(pp)

    def render_image(self, with_nodes=True, moving=True):
        """The map as a picture. moving False leaves out the images that move (the game export gives them quads of
        their own, kdx.pictures.sticker_parts)."""
        p = self.project
        img = QtGui.QImage(p.width * TILE, p.height * TILE, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtGui.QColor(p.sea_colour or "#142030"))
        painter = QtGui.QPainter(img)
        pic = self.picture_image()
        over = pic is not None and bool(p.picture.get("over"))
        if pic is not None and not over:
            painter.drawImage(QtCore.QRectF(0, 0, img.width(), img.height()), pic)
        mi = self.canvas.map_item
        for cy in range((p.height + CHUNK - 1) // CHUNK):
            for cx in range((p.width + CHUNK - 1) // CHUNK):
                painter.drawPixmap(cx * CHUNK * TILE, cy * CHUNK * TILE, mi._chunk(cx, cy))
        if over:
            painter.drawImage(QtCore.QRectF(0, 0, img.width(), img.height()), pic)
        if p.road_colour:
            self.paint_roads(painter)
        from . import pictures
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        for s in p.stickers:                                  # the first frame (animation: see the sticker panel)
            if not moving and (pictures.moves(s) or (s.get("reveal") and s["reveal"] in self._opened_paths())):
                continue
            try:
                pictures.draw(painter, s, TILE, 0.0)
            except Exception as e:
                self.status("Image %s: %s" % (s.get("name"), e))
        if with_nodes:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtGui.QPen(QtGui.QColor("#fde68a"), 3))
            for r in p.routes:
                a, b = rules.route_ends(r["id"])
                if a in p.nodes and b in p.nodes:
                    painter.drawLine(QtCore.QPointF(p.nodes[a]["x"] * TILE, p.nodes[a]["y"] * TILE), QtCore.QPointF(p.nodes[b]["x"] * TILE, p.nodes[b]["y"] * TILE))
            for pid, n in p.nodes.items():
                painter.setBrush(QtGui.QColor(NODE_COLORS.get(n["kind"], "#ffffff")))
                painter.setPen(QtGui.QPen(QtGui.QColor("#111827"), 1.5))
                painter.drawEllipse(QtCore.QPointF(n["x"] * TILE, n["y"] * TILE), 9, 9)
        painter.end()
        return img

    def paint_roads(self, painter):
        """Thick roads along the routes in the map's road colour, with a darker outline (Koopatlas-style maps); hidden
        roads (pipes, tunnels) and climbs are left out."""
        from . import routeactions as RA
        p = self.project
        col = QtGui.QColor(p.road_colour)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        for width, c in ((TILE * 0.62, col.darker(150)), (TILE * 0.42, col)):
            pen = QtGui.QPen(c, width)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)
            for r in p.routes:
                if r.get("hidden") or RA.action(r["type"]).style in ("climb", "jump"):
                    continue
                a, b = rules.route_ends(r["id"])
                if a in p.nodes and b in p.nodes:
                    painter.drawLine(QtCore.QPointF(p.nodes[a]["x"] * TILE, p.nodes[a]["y"] * TILE),
                                     QtCore.QPointF(p.nodes[b]["x"] * TILE, p.nodes[b]["y"] * TILE))
        painter.restore()

    def export_game(self):
        ExportDialog(self).exec_()

    # ---- tilesets --------------------------------------------------------------------------------------------------
    def use_tileset(self, ref, refresh=True, tile_px=TILE):
        try:
            ts = load_tileset(ref, tile_px)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Tileset not loaded", str(e))
            return
        self.tileset = ts
        self.project.tileset = ref
        self.palette.set_tileset(ts)
        self.tileset_label.setText("Tileset: %s (%d tiles)" % (ts.name, ts.count))
        if refresh and self.canvas.map_item:
            self.canvas.map_item.invalidate()

    def load_tileset_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "PNG tile sheet (32×32 px tiles)", "", "Images (*.png)")
        if path:
            self.use_tileset(path)

    # ---- map settings & layers --------------------------------------------------------------------------------------
    def _fill_slots(self, world, select=None):
        self.slot_box.clear()
        for s in rules.SLOTS:
            if s[1] == str(world):
                self.slot_box.addItem("CS_%s" % s, s)
        i = self.slot_box.findData(select)
        self.slot_box.setCurrentIndex(max(i, 0))

    def _fill_map_fields(self):
        p = self.project
        self.name_edit.setText(p.name); self.world_spin.setValue(p.world); self.tile_size_spin.setValue(p.tile_size)
        self.stretch_spin.setValue(getattr(p, "depth_stretch", 1.0))
        self._fill_slots(p.world, p.slot)
        self.link_check.setChecked(p.link_next)
        self._show_music(p.music)
        self.slot_check.blockSignals(True); self.slot_check.setChecked(bool(p.slot_hook)); self.slot_check.blockSignals(False)
        self.reveal_box.setCurrentIndex(max(0, self.reveal_box.findData(p.reveal_style)))
        self.w_spin.setValue(p.width); self.h_spin.setValue(p.height)

    def _slot_hook_changed(self, on):
        old, new = bool(self.project.slot_hook), bool(on)
        if old == new:
            return

        def put(v):
            self.project.slot_hook = v
            self.slot_check.blockSignals(True); self.slot_check.setChecked(v); self.slot_check.blockSignals(False)
        self.undo.push(Command("More levels per world: %s" % ("on" if new else "off"), lambda: put(new),
                               lambda: put(old), self.run_checks))

    def _reveal_style_changed(self, _i):
        old, new = self.project.reveal_style, self.reveal_box.currentData()
        if old == new:
            return

        def put(v):
            self.project.reveal_style = v
            self.reveal_box.setCurrentIndex(max(0, self.reveal_box.findData(v)))
            self.canvas.sticker_item.update()
        self.undo.push(Command("Path art: %s" % new, lambda: put(new), lambda: put(old), self.run_checks))

    def _show_music(self, v):
        """Select music v in the box (a custom song gets an item of its own)."""
        i = self.music_box.findData(v)
        if i < 0:
            self.music_box.insertItem(self.music_box.count() - 1, project2d.music_label(v), v)
            i = self.music_box.findData(v)
        self.music_box.setCurrentIndex(max(0, i))

    def _music_changed(self, _i):
        old, new = self.project.music, self.music_box.currentData()
        if new == -1:                                    # Custom song…: CUSTOM_BGM_<nn>
            cur = old - project2d.MUSIC_CUSTOM if old >= project2d.MUSIC_CUSTOM else 0
            n = project2d.ask_custom_song(self, cur, self.host.settings.value("mod_root", "") if self.host else None,
                                          self.host.romfs_root() if self.host else None)
            if n is None:
                self._show_music(old)
                return
            new = project2d.MUSIC_CUSTOM + n
        if old == new:
            self._show_music(old)
            return

        def put(v):
            self.project.music = v
            self._show_music(v)
        self.undo.push(Command("Music: %s" % project2d.music_label(new), lambda: put(new), lambda: put(old),
                               self.run_checks))

    def import_koopatlas(self, path=None, out_dir=None, world=None, tilesets_dir=None, ask=True, game_dir=None):
        """Koopatlas map -> a new World Maker map saved next to its pictures (kdx.koopatlas). Returns the report."""
        from . import koopatlas
        settings = self.host.settings if self.host else QtCore.QSettings()
        if ask and not self.undo.isClean() and not self._confirm():
            return None
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "A Koopatlas world map (New Super Mario Bros. Wii / Newer)",
                                                            settings.value("koopatlas_dir", "") or "",
                                                            "Koopatlas maps (*.kpmap)")
            if not path:
                return None
        path = Path(path)
        settings.setValue("koopatlas_dir", str(path.parent))
        if ask:
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("Import %s" % path.name)
            f = QtWidgets.QFormLayout(dlg)
            info = QtWidgets.QLabel("Koopatlas maps are for New Super Mario Bros. Wii. Their layers become this world's "
                                    "picture, moving doodads become moving images, nodes and paths become points and "
                                    "routes with their actions, unlocks and music. Only the map data is used.")
            info.setWordWrap(True)
            wspin = QtWidgets.QSpinBox(); wspin.setRange(1, 9); wspin.setValue(world or self.project.world)
            tdir = QtWidgets.QLineEdit(tilesets_dir or settings.value("koopatlas_tilesets", "") or "")
            tdir.setPlaceholderText("Optional: Koopatlas's Tilesets folder (.arc), for the tile layers")
            tb = QtWidgets.QPushButton("Browse…")
            tb.clicked.connect(lambda: tdir.setText(QtWidgets.QFileDialog.getExistingDirectory(dlg, "Koopatlas Tilesets folder",
                                                                                               tdir.text()) or tdir.text()))
            trow = QtWidgets.QHBoxLayout(); trow.addWidget(tdir, 1); trow.addWidget(tb)
            from . import koopatlas as _k
            guess = game_dir or settings.value("koopatlas_game", "") or ""
            for c in (path.parent, path.parent.parent):
                if not guess and _k.game_maps_dir(c):
                    guess = str(c)
            gdir = QtWidgets.QLineEdit(guess)
            gdir.setPlaceholderText("Best: the mod's Riivolution folder (e.g. NewerSMBW, with Maps/Texture): the real tiles")
            gb = QtWidgets.QPushButton("Browse…")
            gb.clicked.connect(lambda: gdir.setText(QtWidgets.QFileDialog.getExistingDirectory(dlg, "The mod's Riivolution folder",
                                                                                               gdir.text()) or gdir.text()))
            grow = QtWidgets.QHBoxLayout(); grow.addWidget(gdir, 1); grow.addWidget(gb)
            odir = QtWidgets.QLineEdit(str(out_dir or path.parent / (path.stem + " (Koopatlas DX)")))
            fit = QtWidgets.QComboBox()
            fit.addItem("Keep their numbers; the framework slot hook keeps the clears", "hook")
            fit.addItem("Rename them to courses the game keeps clears for", True)
            fit.addItem("Keep their numbers, no hook (their exits open nothing)", False)
            fit.setToolTip("Deluxe records clears only for some courses per world (World 1: 1-1 to 1-5). Newer's worlds "
                           "have 8 or more levels. The slot hook (Koopatlas DX framework build) gives the others free "
                           "entries; without it, levels beyond them can take free tracked numbers instead.")
            f.addRow(info)
            f.addRow("Becomes World", wspin)
            f.addRow("Mod folder", grow)
            f.addRow("Or tilesets", trow)
            f.addRow("Save into", odir)
            f.addRow("Levels past 1-5", fit)
            bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
            f.addRow(bb)
            dlg.resize(560, 10)
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return None
            world, tilesets_dir, out_dir, fit_slots = wspin.value(), tdir.text().strip() or None, Path(odir.text()), fit.currentData()
            game_dir = gdir.text().strip() or None
            if game_dir:
                settings.setValue("koopatlas_game", game_dir)
            if tilesets_dir:
                settings.setValue("koopatlas_tilesets", tilesets_dir)
        else:
            out_dir, fit_slots = Path(out_dir or path.parent / (path.stem + " (Koopatlas DX)")), "hook"
        root = self.host.romfs_root() if self.host else None
        exists = (lambda f: (Path(root) / f).exists()) if root else None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            proj, rep = koopatlas.import_map(path, out_dir, world=world or self.project.world, tilesets_dir=tilesets_dir,
                                             fit_slots=fit_slots, course_exists=exists, game_dir=game_dir)
            target = Path(out_dir) / (path.stem + ".kdxmap")
            proj.save(str(target))
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Koopatlas import", str(e))
            return None
        QtWidgets.QApplication.restoreOverrideCursor()
        self.open(str(target))
        self.status("Imported %s: %s" % (path.name, rep["summary"][0] if rep["summary"] else ""))
        if ask:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Koopatlas import")
            box.setText("<b>%s</b> is now a World Maker map:<br>%s" % (path.name, "<br>".join(rep["summary"])))
            if rep["warnings"]:
                box.setDetailedText("\n".join(rep["warnings"]))
                box.setInformativeText("%d notes (Show Details). The checks on the right list what the game still needs."
                                       % len(rep["warnings"]))
            box.exec_()
        return rep

    def port_newer(self):
        """Port Newer's worlds (kdx.newerport.PLAN) into a folder of World Maker maps, optionally exported."""
        from . import newerport
        settings = self.host.settings if self.host else QtCore.QSettings()
        kdir = QtWidgets.QFileDialog.getExistingDirectory(self, "The folder with Newer's .kpmap files",
                                                          settings.value("koopatlas_dir", "") or "")
        if not kdir:
            return None
        gdir = QtWidgets.QFileDialog.getExistingDirectory(self, "Newer's Riivolution folder (NewerSMBW, with Maps/)",
                                                          settings.value("koopatlas_game", "") or "")
        if not gdir:
            return None
        out = QtWidgets.QFileDialog.getExistingDirectory(self, "Save the ported worlds into (an empty folder)")
        if not out:
            return None
        mod = None
        if QtWidgets.QMessageBox.question(self, "Port Newer", "Export every world into a mod folder as well?") == \
                QtWidgets.QMessageBox.Yes:
            mod = QtWidgets.QFileDialog.getExistingDirectory(self, "Mod folder", settings.value("mod_root", "") or "")
        settings.setValue("koopatlas_dir", kdir)
        settings.setValue("koopatlas_game", gdir)
        root = self.host.romfs_root() if self.host else None
        lines = []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            rows, plan = newerport.port_all(kdir, gdir, root, out, mod or None,
                                            log=lambda t: (lines.append(t), self.status(t), QtWidgets.QApplication.processEvents()))
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Port Newer", str(e))
            return None
        QtWidgets.QApplication.restoreOverrideCursor()
        short = sorted("World %d course %d%s" % (w, c, " (secret)" if sc else "") for (w, c, sc), v in plan.items() if v is None)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Port Newer")
        box.setText("<b>%d worlds ported</b> into %s:<br>%s" % (len(rows), out, "<br>".join(lines)))
        box.setInformativeText("Slot hook: every level keeps its clear." if not short else
                               "Slot hook: no entry left for %s." % ", ".join(short))
        box.setDetailedText("\n\n".join("%s %s\n%s" % (r["slot"], r["name"], "\n".join(
            r["errors"] + r.get("export_errors", []) + r["warnings"])) for r in rows))
        box.exec_()
        return rows, plan

    def _apply_map_settings(self):
        p = self.project
        old = (p.name, p.world, p.tile_size, p.width, p.height, [list(l.data) for l in p.layers],
               {k: dict(v, row=list(v["row"])) for k, v in p.nodes.items()}, p.variant, p.link_next,
               list(p.heights) if p.heights is not None else None, p.depth_stretch)
        new_vals = (self.name_edit.text().strip() or "World", self.world_spin.value(), self.tile_size_spin.value(), self.w_spin.value(), self.h_spin.value(),
                    (self.slot_box.currentData() or "")[2:], self.link_check.isChecked(), self.stretch_spin.value())

        def do():
            p.name, p.world, p.tile_size, p.variant, p.link_next = new_vals[0], new_vals[1], new_vals[2], new_vals[5], new_vals[6]
            p.depth_stretch = new_vals[7]
            if (p.width, p.height) != (new_vals[3], new_vals[4]):
                p.resize(new_vals[3], new_vals[4])

        def undo():
            p.name, p.world, p.tile_size, p.width, p.height, p.variant, p.link_next = old[0], old[1], old[2], old[3], old[4], old[7], old[8]
            p.heights = list(old[9]) if old[9] is not None else None
            p.depth_stretch = old[10]
            for lay, data in zip(p.layers, old[5]):
                lay.data = list(data)
            p.nodes = {k: dict(v, row=list(v["row"])) for k, v in old[6].items()}
        self.undo.push(Command("Map settings", do, undo, self._full_refresh))

    # ---- 3D ground model -------------------------------------------------------------------------------------------
    def load_model_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "3D ground model", "", model3d.FORMATS)
        if not path:
            return
        self.set_model(path, self._ask_z_up(path, "3D ground model"))

    def _ask_z_up(self, path, title):
        """OBJ files do not say which way is up; COLLADA, FBX and Wii models do."""
        if not path.lower().endswith(".obj"):
            return False
        return QtWidgets.QMessageBox.question(
            self, title, "Was the model made with Z pointing up (3ds Max, SketchUp, some Blender exports)?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def edit_ground_model(self):
        p = self.project
        if not p.model3d:
            return
        from .modeleditor import ModelEditor
        dlg = ModelEditor(p.model3d, Path(p.path).parent if p.path else None, "Ground model", self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted and dlg.ref != p.model3d:
            self._set_scene("Edit ground model", dlg.ref, [dict(o) for o in p.objects])

    def set_model(self, path, z_up=False):
        p = self.project
        old = (p.model3d, p._model)
        try:
            p.set_model3d(path, z_up)
        except project2d.ProjectError as e:
            QtWidgets.QMessageBox.warning(self, "3D ground model", str(e))
            return False
        new = (p.model3d, p._model)

        def setm(v):
            p.model3d, p._model = v
        self.undo.push(Command("3D ground model", lambda: setm(new), lambda: setm(old), self._model_changed))
        return True

    def remove_model(self):
        p = self.project
        if not p.model3d:
            return
        old = (p.model3d, p._model)

        def setm(v):
            p.model3d, p._model = v
        self.undo.push(Command("Remove 3D ground model", lambda: setm((None, None)), lambda: setm(old), self._model_changed))

    def _model_changed(self):
        p = self.project
        try:
            model = p.terrain_model() if p.model3d else None
        except project2d.ProjectError as e:
            model = None
            self.status("The 3D ground model could not be loaded: %s" % e)
        missing = p.model3d is not None and model is None
        if self.canvas.map_item:
            scene = p.scene_model() if (model is not None or p.objects) else None
            self.canvas.map_item.backdrop = None if scene is None else objmodel.topdown_image(
                scene, p.width * TILE, p.height * TILE, p.width * p.tile_size, p.height * p.tile_size)
            self.canvas.map_item.invalidate()
        self._fill_scenery()
        self._refresh_3d()
        if missing:
            self.model_label.setText("Ground: 3D model NOT FOUND (%s): flat until it is back, or Remove" % p.model3d["path"])
        elif model is None:
            self.model_label.setText("Ground: painted tiles")
        else:
            self.model_label.setText("Ground: %s (%d triangles, %d materials%s)" % (
                Path(p.model3d["path"]).name, sum(x.triangle_count for x in model.parts), len(model.parts),
                ", %d faced inwards and were turned round" % model.flipped if model.flipped else ""))
        self.b_nomodel.setEnabled(p.model3d is not None)
        self.b_editmodel.setEnabled(p.model3d is not None and model is not None)
        self.run_checks()

    # ---- 3D world: view, scenery objects ---------------------------------------------------------------------------
    def _build_3d(self):
        from .editor3d import SceneView
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QToolBar()
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        grp = QtWidgets.QActionGroup(self)
        self.mode3d = {}
        for key, text, tip in (("move", "Move", "W · drag an arrow to move along it, or the object over the ground "
                                                "(Shift: up and down)"),
                               ("turn", "Turn", "E · drag a ring to turn around its axis, or drag sideways: around the "
                                                "up axis (Shift: x axis, Ctrl: z axis)"),
                               ("scale", "Scale", "R · drag a handle to stretch along it, the centre square to scale "
                                                  "evenly, or drag up to grow, down to shrink")):
            a = bar.addAction(text); a.setCheckable(True); a.setToolTip(tip)
            a.triggered.connect(lambda _=False, k=key: self._set_mode3d(k))
            grp.addAction(a)
            self.mode3d[key] = a
        self.mode3d["move"].setChecked(True)
        bar.addSeparator()
        self.snap3d = QtWidgets.QCheckBox("Stand on the ground"); self.snap3d.setChecked(True)
        self.snap3d.setToolTip("Moved objects and points keep their feet on the ground model")
        self.snap3d.toggled.connect(lambda on: setattr(self.view3d, "snap", on))
        self.lock3d = QtWidgets.QCheckBox("Lock the ground"); self.lock3d.setChecked(True)
        self.lock3d.setToolTip("Clicks on the ground do not select it (so it is not moved by accident)")
        self.lock3d.toggled.connect(lambda on: setattr(self.view3d, "lock_ground", on))
        self.points3d = QtWidgets.QCheckBox("Points"); self.points3d.setChecked(True)
        self.points3d.toggled.connect(lambda on: (setattr(self.view3d, "show_points", on), self.view3d.update()))
        self.cull3d = QtWidgets.QCheckBox("Front faces only"); self.cull3d.setChecked(True)
        self.cull3d.setToolTip("Draw like the game: faces seen from behind are invisible, so an inside-out model shows")
        self.cull3d.toggled.connect(lambda on: (setattr(self.view3d, "cull", on), self.view3d.update()))
        for wdg in (self.snap3d, self.lock3d, self.points3d, self.cull3d):
            bar.addWidget(wdg)
        bar.addAction("Frame all", lambda: self.view3d.frame_all())
        v.addWidget(bar)
        self.view3d = SceneView()
        from .editor3d import gizmo_options
        for wdg in gizmo_options(self.view3d, "maker_gizmo"):
            bar.insertWidget(bar.actions()[3], wdg)
        self.view3d.modeChanged.connect(lambda k: self.mode3d[k].setChecked(True))
        self.view3d.lock_ground = True
        self.view3d.ground_height = self._ground_height
        self.view3d.picked.connect(self._picked_3d)
        self.view3d.objectEdited.connect(self._object_dragged)
        self.view3d.pointDragged.connect(self._point_dragged_3d)
        self.view3d.routeMenu.connect(lambda rid, gpos: self.canvas._route_menu(rid, gpos))
        self.view3d.statusMessage.connect(self.status)
        v.addWidget(self.view3d, 1)
        hint = QtWidgets.QLabel("Click: select · W/E/R: move/turn/scale gizmo (drag a handle: one axis) · X: local axes · "
                                "right drag: orbit · middle drag: pan · wheel: zoom · F: frame · Delete: remove · "
                                "Ctrl+D: duplicate")
        hint.setObjectName("hint"); hint.setWordWrap(True)
        v.addWidget(hint)
        for seq, slot in ((QtGui.QKeySequence.Delete, self.remove_object), ("Ctrl+D", self.duplicate_object)):
            sc = QtWidgets.QShortcut(QtGui.QKeySequence(seq), self.view3d)
            sc.activated.connect(slot)
        self._items_version = {}
        return w

    def _set_mode3d(self, key):
        self.view3d.set_mode(key)
        self.mode3d[key].setChecked(True)

    def _build_scenery_box(self):
        box = QtWidgets.QGroupBox("3D scenery (castles, houses, props)")
        v = QtWidgets.QVBoxLayout(box)
        self.obj_list = QtWidgets.QListWidget()
        self.obj_list.setMaximumHeight(130)
        self.obj_list.currentRowChanged.connect(self._object_selected)
        v.addWidget(self.obj_list)
        row = QtWidgets.QHBoxLayout()
        for text, slot, tip in (("Add…", self.add_object_dialog, "Place a model (FBX, DAE, OBJ, Wii .brres/.arc) in the world"),
                                ("Copy", self.duplicate_object, "Duplicate the selected object"),
                                ("Remove", self.remove_object, "Remove the selected object"),
                                ("Edit model…", self.edit_object_model, "Model editor for the selected object")):
            b = QtWidgets.QPushButton(text); b.setToolTip(tip); b.clicked.connect(slot); row.addWidget(b)
        v.addLayout(row)
        f = QtWidgets.QFormLayout()
        self.obj_name = QtWidgets.QLineEdit(); self.obj_name.editingFinished.connect(self._object_fields_edited)
        f.addRow("Name", self.obj_name)
        self.obj_fields = {}
        for key, lo, hi, dec in (("position", -1e6, 1e6, 1), ("rotation", -360, 360, 1), ("scale", -1e4, 1e4, 3)):
            h = QtWidgets.QHBoxLayout()
            spins = []
            for _ in range(3):
                s = QtWidgets.QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(dec); s.setKeyboardTracking(False)
                s.editingFinished.connect(self._object_fields_edited)
                h.addWidget(s); spins.append(s)
            self.obj_fields[key] = spins
            f.addRow({"position": "Position", "rotation": "Turn (°)", "scale": "Scale"}[key], h)
        v.addLayout(f)
        self.obj_info = QtWidgets.QLabel(); self.obj_info.setWordWrap(True); self.obj_info.setStyleSheet("color: gray")
        v.addWidget(self.obj_info)
        return box

    def _view_tab_changed(self, i):
        if i == 1:
            self._refresh_3d()

    def _ground_height(self, x, z):
        try:
            m = self.project.terrain_model() if self.project.model3d else None
        except project2d.ProjectError:
            m = None
        if m is not None:
            return m.height_at(x, z)
        return self.project.height_at(x / self.project.tile_size, z / self.project.tile_size)

    def _refresh_3d(self):
        """Give the 3D view the ground, the objects, the points and routes (only re-reads models that changed)."""
        if not hasattr(self, "view3d"):
            return
        from . import modelref
        from .editor3d import Item
        p = self.project
        base = Path(p.path).parent if p.path else None
        items = []

        if self.views.currentIndex() != 1 and not self.view3d.items:
            return                                     # built when the 3D tab is first shown

        def version(key, ref):
            # unique across items: after a removal ("object", 2) becomes ("object", 1), and must not reuse the old GPU copy
            sig = repr((ref.get("path"), ref.get("z_up"), ref.get("hidden"), ref.get("edits")))
            old = self._items_version.get(key)
            if old is None or old[0] != sig:
                self._version_counter = getattr(self, "_version_counter", 0) + 1
                self._items_version[key] = (sig, self._version_counter)
            return self._items_version[key][1]
        if p.model3d:
            try:
                raw = modelref.apply_edits(modelref.load_raw(modelref.resolve(p.model3d, base), p.model3d.get("z_up", False)), p.model3d)
                items.append(Item("ground", raw, modelref.matrix(p.model3d), version("ground", p.model3d)))
            except (objmodel.ObjError, OSError) as e:
                self.status("Ground model: %s" % e)
        elif self.canvas.map_item is not None:
            items.append(Item("ground", self._painted_ground(), __import__("numpy").identity(4),
                              version("ground", {"path": "painted", "edits": self.undo.index()}), pickable=True))
        for i, o in enumerate(p.objects):
            try:
                raw = modelref.apply_edits(modelref.load_raw(modelref.resolve(o, base), o.get("z_up", False)), o)
            except (objmodel.ObjError, OSError) as e:
                self.status("%s: %s" % (o.get("name"), e))
                continue
            items.append(Item(("object", i), raw, modelref.matrix(o), version(("object", o.get("name"), o["path"]), o)))
        self.view3d.area = (p.width * p.tile_size, p.height * p.tile_size)
        first = not self.view3d.items
        self.view3d.set_items(items, frame=first)
        self._points_3d()

    def _painted_ground(self):
        """The painted map as a flat textured model (a 2D map seen in the 3D view)."""
        import numpy as np
        import tempfile
        p = self.project
        w, d = p.width * p.tile_size, p.height * p.tile_size
        img = self.render_image(with_nodes=False)
        path = Path(tempfile.gettempdir()) / ("kdx_painted_%d.png" % id(self))
        img.save(str(path))
        verts, _n, idx = export2d.terrain_mesh(p)
        v = np.asarray(verts, np.float32).reshape(-1, 14)
        tri = v[np.asarray(idx, np.int64)]
        part = objmodel.Part("painted map", tri[:, 0:3].copy(), tri[:, 3:6].copy(), tri[:, 6:8].copy(), str(path))
        from . import islands
        return objmodel.ObjModel([part] + islands.island_parts(p, str(path)))

    def _points_3d(self):
        p = self.project
        kinds = {"start": "Level", "level": "Level", "tower": "Tower", "castle": "Castle", "ghost": "Ghost house",
                 "airship": "Airship", "toad": "Toad house", "link": "World link", "stop": "Path stop", "pipe": "Pipe",
                 "bend": "Waypoint"}
        pts = {pid: (p.world_position(pid), kinds.get(n.get("kind"), "Other")) for pid, n in p.nodes.items()}
        routes = []
        for r in p.routes:
            ends = rules.route_ends(r["id"])
            if ends and ends[0] in pts and ends[1] in pts:
                routes.append((pts[ends[0]][0], pts[ends[1]][0], r["type"], r["id"]))
        self.view3d.route_problems = {r["id"]: t for r in p.routes for t in self.problem_text if r["id"] in t}
        from . import pointactions as PA
        self.view3d.point_actions = {q: [a.key for a in PA.actions_of(q, rules.split(n["row"][2]))]
                                     for q, n in p.nodes.items()}
        self.view3d.warp_rows = [n["row"] for n in p.nodes.values()]
        self.view3d.warp_slot = p.slot
        self.view3d.set_points(pts, routes)

    def _set_scene(self, text, model3d_ref, objects, select=None):
        """Undoable change of the ground reference and the object list (snapshots of both)."""
        import copy
        p = self.project
        old = (copy.deepcopy(p.model3d), copy.deepcopy(p.objects))
        new = (copy.deepcopy(model3d_ref), copy.deepcopy(objects))

        def apply(state):
            m, objs = copy.deepcopy(state[0]), copy.deepcopy(state[1])
            if m != p.model3d:
                p.update_model3d(m)
            p.objects = objs
        self.undo.push(Command(text, lambda: apply(new), lambda: apply(old), self._model_changed))
        if select is not None:
            self.obj_list.setCurrentRow(select)

    def _fill_scenery(self):
        if not hasattr(self, "obj_list"):
            return
        row = self.obj_list.currentRow()
        self.obj_list.blockSignals(True)
        self.obj_list.clear()
        for o in self.project.objects:
            self.obj_list.addItem("%s  (%s)" % (o["name"], Path(o["path"]).name))
        self.obj_list.blockSignals(False)
        if 0 <= row < self.obj_list.count():
            self.obj_list.setCurrentRow(row)
        self._object_selected(self.obj_list.currentRow())

    def _object_selected(self, row):
        p = self.project
        ok = 0 <= row < len(p.objects)
        for w in [self.obj_name] + [s for v in self.obj_fields.values() for s in v]:
            w.setEnabled(ok)
            w.blockSignals(True)
        if ok:
            from . import modelref
            o = p.objects[row]
            self.obj_name.setText(o["name"])
            for key, vals in (("position", o.get("position", [0, 0, 0])), ("rotation", o.get("rotation", [0, 0, 0])),
                              ("scale", modelref.scale3(o))):
                for s, val in zip(self.obj_fields[key], vals):
                    s.setValue(float(val))
            m = p.object_model(row)
            self.obj_info.setText("%s · %s" % (o["path"], "%d triangles, %d materials" % (
                sum(x.triangle_count for x in m.parts), len(m.parts)) if m else "FILE NOT FOUND"))
            if hasattr(self, "view3d") and self.view3d.selected != ("object", row):
                self.view3d.selected = ("object", row)
                self.view3d.update()
        else:
            self.obj_name.clear()
            self.obj_info.setText("Objects are part of the exported world, like the ground. Place them in the 3D world tab.")
        for w in [self.obj_name] + [s for v in self.obj_fields.values() for s in v]:
            w.blockSignals(False)

    def _object_fields_edited(self):
        row = self.obj_list.currentRow()
        p = self.project
        if not 0 <= row < len(p.objects):
            return
        o = dict(p.objects[row])
        o["name"] = self.obj_name.text().strip() or o["name"]
        o["position"] = [s.value() for s in self.obj_fields["position"]]
        o["rotation"] = [s.value() for s in self.obj_fields["rotation"]]
        sc = [s.value() for s in self.obj_fields["scale"]]
        o["scale"] = sc[0] if abs(sc[0] - sc[1]) < 1e-12 and abs(sc[1] - sc[2]) < 1e-12 else sc
        if o != p.objects[row]:
            objs = [dict(x) for x in p.objects]
            objs[row] = o
            self._set_scene("Edit %s" % o["name"], p.model3d, objs)

    def add_object_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add a 3D object", "", model3d.FORMATS)
        if not path:
            return
        p = self.project
        # where to put it: the 3D view's centre, standing on the ground
        t = self.view3d.target if hasattr(self, "view3d") and self.view3d.items else (
            p.width * p.tile_size / 2, 0.0, p.height * p.tile_size / 2)
        h = self._ground_height(float(t[0]), float(t[2]))
        probe = project2d.Project(8, 8)
        try:
            probe.add_object(path, (float(t[0]), h or 0.0, float(t[2])), z_up=self._ask_z_up(path, "Add a 3D object"))
        except project2d.ProjectError as e:
            QtWidgets.QMessageBox.warning(self, "Add a 3D object", str(e))
            return
        o = probe.objects[0]
        names = {x["name"] for x in p.objects}
        base, k = o["name"], 2
        while o["name"] in names:
            o["name"], k = "%s %d" % (base, k), k + 1
        self._set_scene("Add %s" % o["name"], p.model3d, [dict(x) for x in p.objects] + [o], select=len(p.objects))
        self.views.setCurrentIndex(1)

    def duplicate_object(self):
        row = self.obj_list.currentRow()
        p = self.project
        if not 0 <= row < len(p.objects):
            return
        import copy
        o = copy.deepcopy(p.objects[row])
        names = {x["name"] for x in p.objects}
        base, k = re.sub(r" \d+$", "", o["name"]), 2
        while "%s %d" % (base, k) in names:
            k += 1
        o["name"] = "%s %d" % (base, k)
        o["position"] = [o["position"][0] + 4 * p.tile_size, o["position"][1], o["position"][2]]
        self._set_scene("Copy %s" % o["name"], p.model3d, [dict(x) for x in p.objects] + [o], select=len(p.objects))

    def remove_object(self):
        row = self.obj_list.currentRow()
        p = self.project
        if not 0 <= row < len(p.objects):
            return
        objs = [dict(x) for i, x in enumerate(p.objects) if i != row]
        self._set_scene("Remove %s" % p.objects[row]["name"], p.model3d, objs)

    def edit_object_model(self):
        row = self.obj_list.currentRow()
        p = self.project
        if not 0 <= row < len(p.objects):
            return
        from .modeleditor import ModelEditor
        dlg = ModelEditor(p.objects[row], Path(p.path).parent if p.path else None, "Object: %s" % p.objects[row]["name"],
                          self, with_position=True)
        if dlg.exec_() == QtWidgets.QDialog.Accepted and dlg.ref != p.objects[row]:
            objs = [dict(x) for x in p.objects]
            objs[row] = dlg.ref
            self._set_scene("Edit %s" % dlg.ref["name"], p.model3d, objs)

    def _picked_3d(self, key):
        if isinstance(key, tuple) and key[0] == "route":
            if self.tool == "action":                       # the Action brush works in 3D too
                self.set_route_type(key[1], self.route_type_box.currentData())
            self.select(("route", key[1]))
            return
        if isinstance(key, tuple) and key[0] == "object":
            self.obj_list.setCurrentRow(key[1])
        elif isinstance(key, tuple) and key[0] == "point":
            self.select(("node", key[1]))
        elif key is None:
            self.obj_list.setCurrentRow(-1)

    def _object_dragged(self, key, matrix):
        from .editor3d import decompose
        p = self.project
        pos, rot, sc = decompose(matrix)
        if key == "ground":
            ref = dict(p.model3d)
            ref["offset"] = pos
            if any(abs(r) > 1e-6 for r in rot):
                ref["rotation"] = rot
            else:
                ref.pop("rotation", None)
            ref["scale"] = sc[0] if max(sc) - min(sc) < 1e-9 else sc
            self._set_scene("Move ground model", ref, [dict(x) for x in p.objects])
            return
        i = key[1]
        o = dict(p.objects[i])
        o["position"], o["rotation"] = pos, rot
        o["scale"] = sc[0] if max(sc) - min(sc) < 1e-9 else sc
        objs = [dict(x) for x in p.objects]
        objs[i] = o
        self._set_scene({"move": "Move", "turn": "Turn", "scale": "Scale"}[self.view3d.mode] + " " + o["name"], p.model3d, objs, select=i)

    def _point_dragged_3d(self, pid, pos):
        n = self.project.nodes.get(pid)
        if n is None:
            return
        ts = self.project.tile_size
        self.move_node(pid, (n["x"], n["y"]), (pos[0] / ts, pos[2] / ts), height=pos[1])

    def _full_refresh(self):
        self.canvas.rebuild()
        self._fill_map_fields()
        self._fill_layers()
        self._show_selection()
        self._model_changed()                       # restores the 3D ground's picture; runs the checks

    def _fill_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for i in reversed(range(len(self.project.layers))):
            lay = self.project.layers[i]
            it = QtWidgets.QListWidgetItem(lay.name + ("  🔒" if lay.locked else ""))
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEditable)
            it.setCheckState(QtCore.Qt.Checked if lay.visible else QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, i)
            self.layer_list.addItem(it)
        self.layer_list.setCurrentRow(len(self.project.layers) - 1 - self.layer)
        self.layer_list.blockSignals(False)
        self.lock_check.blockSignals(True)
        self.lock_check.setChecked(self.project.layers[self.layer].locked)
        self.lock_check.blockSignals(False)

    def _layer_selected(self, row):
        if row >= 0:
            self.layer = self.layer_list.item(row).data(QtCore.Qt.UserRole)
            self.lock_check.blockSignals(True)
            self.lock_check.setChecked(self.project.layers[self.layer].locked)
            self.lock_check.blockSignals(False)

    def _layer_item_changed(self, item):
        i = item.data(QtCore.Qt.UserRole)
        lay = self.project.layers[i]
        lay.visible = item.checkState() == QtCore.Qt.Checked
        name = item.text().replace("  🔒", "").strip()
        if name:
            lay.name = name
        self.canvas.map_item.invalidate()

    def _lock_toggled(self, on):
        self.project.layers[self.layer].locked = on
        self._fill_layers()

    def add_layer(self):
        p = self.project
        idx = self.layer + 1
        name = "Layer %d" % (len(p.layers) + 1)
        self.undo.push(Command("Add layer", lambda: p.add_layer(name, idx), lambda: p.remove_layer(idx), self._layers_changed))
        self.layer = idx
        self._fill_layers()

    def remove_layer(self):
        p = self.project
        if len(p.layers) <= 1:
            return
        idx = self.layer
        lay = p.layers[idx]
        self.undo.push(Command("Remove layer", lambda: p.remove_layer(idx), lambda: p.layers.insert(idx, lay), self._layers_changed))
        self.layer = max(0, idx - 1)
        self._fill_layers()

    def move_layer(self, d):
        p = self.project
        i, j = self.layer, self.layer + d
        if not (0 <= j < len(p.layers)):
            return

        def swap():
            p.layers[i], p.layers[j] = p.layers[j], p.layers[i]
        self.undo.push(Command("Move layer", swap, swap, self._layers_changed))
        self.layer = j
        self._fill_layers()

    def _layers_changed(self):
        self.layer = min(self.layer, len(self.project.layers) - 1)
        self._fill_layers()
        self.canvas.map_item.invalidate()

    # ---- painting -----------------------------------------------------------------------------------------------------
    def paint(self, cells, text):
        ch = self.project.set_many(self.layer, cells)
        if ch:
            self.canvas.map_item.invalidate(ch.keys())
            self.record_paint(ch, text)

    def record_tiers(self, changes):
        """A finished Islands stroke (already applied while dragging) goes on the undo stack."""
        self.undo.push(Command("Islands", lambda: self.project.apply_tiers(changes),
                               lambda: self.project.apply_tiers(changes, undo=True), self._heights_changed))

    def _theme_changed(self, _i):
        old, new = self.project.theme, self.theme_box.currentData()
        if old == new:
            return

        from . import islands as _isl
        p = self.project
        before = (p.theme, p.sea_colour, p.road_colour)
        th = _isl.THEMES.get(new, {})
        after = (new, th.get("sea", p.sea_colour), th.get("road", "") if p.road_colour else p.road_colour)

        def put(v):
            p.theme, p.sea_colour, p.road_colour = v
            self.theme_box.setCurrentIndex(self.theme_box.findData(v[0]))
        self.undo.push(Command("Theme: %s" % th.get("label", new), lambda: put(after), lambda: put(before),
                               self._heights_changed))

    def _round_changed(self, on):
        p = self.project
        old, new = bool(p.island_round), bool(on)
        if old == new:
            return

        def put(v):
            p.island_round = v
            self.round_check.blockSignals(True); self.round_check.setChecked(v); self.round_check.blockSignals(False)
        self.undo.push(Command("Rounded island edges: %s" % ("on" if new else "off"), lambda: put(new),
                               lambda: put(old), self._heights_changed))

    def _roads_changed(self, on):
        from . import islands as _isl
        p = self.project
        old = p.road_colour
        new = (_isl.THEMES.get(p.theme, _isl.THEMES["grass"])["road"]) if on else ""
        if old == new:
            return

        def put(v):
            p.road_colour = v
            self.roads_check.blockSignals(True); self.roads_check.setChecked(bool(v)); self.roads_check.blockSignals(False)
        self.undo.push(Command("Paint roads: %s" % ("on" if new else "off"), lambda: put(new), lambda: put(old),
                               self._heights_changed))

    def record_heights(self, changes):
        """A finished Height stroke (already applied while dragging) goes on the undo stack."""
        self.undo.push(Command("Height", lambda: self.project.apply_heights(changes),
                               lambda: self.project.apply_heights(changes, undo=True), self._heights_changed))

    def _heights_changed(self):
        if self.canvas.map_item:
            self.canvas.map_item.invalidate()
        self.run_checks()

    def record_paint(self, changes, text):
        """The changes are already applied; this records them for undo."""
        layer = self.layer
        cmd = Command(text, lambda: self._apply(layer, changes, False), lambda: self._apply(layer, changes, True), lambda: None)
        self.undo.push(cmd)

    def _apply(self, layer, changes, undo):
        if layer < len(self.project.layers):
            self.project.apply(layer, changes, undo)
            self.canvas.map_item.invalidate(changes.keys())

    # ---- nodes and routes ------------------------------------------------------------------------------------------
    def add_node(self, kind, x, y):
        p = self.project
        try:
            pid = p.next_id(kind)
        except project2d.ProjectError as e:
            QtWidgets.QMessageBox.information(self, "No free name", str(e))
            return None
        self.undo.push(Command("Add %s" % pid, lambda: p.add_node(kind, x, y, pid), lambda: p.remove_node(pid), self._nodes_changed))
        self.select(("node", pid))
        return pid

    STOP_KINDS = ("start", "level", "tower", "castle", "ghost", "airship", "toad", "link", "stop", "pipe")

    def _opener_before(self, pid):
        """The level-like point a path ends at when walking back from pid through bends and free points."""
        p, seen, q = self.project, set(), pid
        while q and q not in seen:
            seen.add(q)
            if rules.point_class(q) in (2, 3, 4) and q[:1] == "W":
                return q
            back = [rules.route_ends(r["id"])[0] for r in p.routes if rules.route_ends(r["id"])[1] == q]
            q = back[0] if back else None
        return None

    def place_node(self, kind, x, y):
        """Point tool: add a point; with Connect on, join it to the selected point with a road, and when it is a stop
        let the level before it open it (point + path). One undo step."""
        p = self.project
        prev = self.selected[1] if (self.selected and self.selected[0] == "node" and self.chain_box.isChecked()
                                    and self.selected[1] in p.nodes) else None
        if prev is None:
            return self.add_node(kind, x, y)
        try:
            pid = p.next_id(kind)
        except project2d.ProjectError as e:
            QtWidgets.QMessageBox.information(self, "No free name", str(e))
            return None
        rtype = self.route_type_box.currentData()
        rid = "R%s%s" % (prev, pid)
        opener = self._opener_before(prev) if kind in self.STOP_KINDS else None
        old_row = list(p.nodes[opener]["row"]) if opener else None

        def do():
            p.add_node(kind, x, y, pid)
            p.add_route(prev, pid, rtype)
            if opener:
                row = p.nodes[opener]["row"]
                row[3] = ",".join(rules.split(row[3]) + [pid])
                row[4] = ",".join(rules.split(row[4]) + ["R%s%s" % (opener, pid)])

        def undo():
            p.routes = [r for r in p.routes if r["id"] != rid]
            p.remove_node(pid)
            if opener:
                p.nodes[opener]["row"][:] = old_row
        self.undo.push(Command("Add %s after %s" % (pid, prev), do, undo, self._nodes_changed))
        self.select(("node", pid))
        self.status("%s joined to %s%s." % (pid, prev, (", and %s's exit now opens it" % opener) if opener else ""))
        return pid

    def _snapshot(self):
        p = self.project
        nodes = {k: dict(v, row=list(v["row"])) for k, v in p.nodes.items()}
        routes = [dict(r) for r in p.routes]

        def restore():
            p.nodes = {k: dict(v, row=list(v["row"])) for k, v in nodes.items()}
            p.routes = [dict(r) for r in routes]
        return restore

    def curve_route(self, rid, side=1, bends=3):
        """Bend a straight route into an arc: bends (K points) along a curve, the same action on every piece. Unlock
        paths keep working: they run stop to stop through bends."""
        p = self.project
        r = next((r for r in p.routes if r["id"] == rid), None)
        if r is None:
            return
        a, b = rules.route_ends(rid)
        na, nb = p.nodes[a], p.nodes[b]
        ax, ay, bx, by = na["x"], na["y"], nb["x"], nb["y"]
        L = math.hypot(bx - ax, by - ay)
        if L < 1.5:
            self.status("%s is too short to curve." % rid)
            return
        cx, cy = (ax + bx) / 2 - (by - ay) / L * 0.35 * L * side, (ay + by) / 2 + (bx - ax) / L * 0.35 * L * side
        before = self._snapshot()
        names = []
        for k in range(bends):
            t = (k + 1) / (bends + 1)
            x = (1 - t) ** 2 * ax + 2 * (1 - t) * t * cx + t * t * bx
            y = (1 - t) ** 2 * ay + 2 * (1 - t) * t * cy + t * t * by
            names.append((p.add_node("bend", x, y), x, y))       # added now to reserve the name, re-added in do()
        for q, _x, _y in names:
            p.remove_node(q)
        chain = [a] + [q for q, _x, _y in names] + [b]
        rtype = r["type"]

        def do():
            for q, x, y in names:
                p.add_node("bend", x, y, q)
            p.routes = [x for x in p.routes if x["id"] != rid]
            for u, v in zip(chain, chain[1:]):
                p.add_route(u, v, rtype)
        self.undo.push(Command("Curve %s" % rid, do, before, self._nodes_changed))
        self.select(("node", names[len(names) // 2][0]))
        self.status("%s curved through %s: drag the bends to shape it." % (rid, ", ".join(c for c in chain[1:-1])))

    def straighten_route(self, rid):
        """Remove the bends (K points) of the chain this route is part of: one straight route stop to stop."""
        p = self.project
        a, b = rules.route_ends(rid)
        outs = {}
        for r in p.routes:
            u, v = rules.route_ends(r["id"])
            outs.setdefault(u, []).append((v, r))
        ins = {}
        for r in p.routes:
            u, v = rules.route_ends(r["id"])
            ins.setdefault(v, []).append((u, r))
        start, end, ks = a, b, []
        while start[:1] == "K" and len(ins.get(start, [])) == 1:
            ks.append(start); start = ins[start][0][0]
        while end[:1] == "K" and len(outs.get(end, [])) == 1:
            ks.append(end); end = outs[end][0][0]
        if not ks or start == end:
            return
        rtype = next(r["type"] for r in p.routes if r["id"] == rid)
        before = self._snapshot()

        def do():
            for k in ks:
                p.remove_node(k)
            if not any(r["id"] == "R%s%s" % (start, end) for r in p.routes):
                p.add_route(start, end, rtype)
        self.undo.push(Command("Straighten %s" % rid, do, before, self._nodes_changed))
        self.select(("route", "R%s%s" % (start, end)))

    def move_node(self, pid, old, new, height=None):
        p = self.project
        node = p.nodes[pid]
        # a point with its own 3D position (ported or copied from a template) moves with it, standing on the ground
        old_pos = list(node["pos"]) if "pos" in node else None
        new_pos = None
        if old_pos is not None:
            h = p.height_at(*new) if height is None else height
            new_pos = [new[0] * p.tile_size, h, new[1] * p.tile_size]

        def setp(v, pos):
            p.nodes[pid]["x"], p.nodes[pid]["y"] = v
            if pos is not None:
                p.nodes[pid]["pos"] = list(pos)
        self.undo.push(Command("Move %s" % pid, lambda: setp(new, new_pos), lambda: setp(old, old_pos), self._nodes_changed))

    def pen_kind(self, start=False):
        """The kind of a point the Route tool makes: the start point first if the map has none, else the Point kind
        box (a bend there means a path stop, since a path ends on a point)."""
        p = self.project
        if start and p.start_node() is None:
            return "start"
        k = self.node_kind()
        if k == "bend":
            return "stop"
        if k == "start" and p.start_node() is not None:
            return "level"
        return k

    def road_image(self):
        """The picture the Route tool follows roads on: your own ground picture, else the painted tiles."""
        pic = self.picture_image()
        if pic is not None and not self.project.picture.get("over"):
            return pic
        key = (id(self.project), self.undo.index())
        if getattr(self, "_road_img", (None,))[0] != key:
            self._road_img = (key, self.render_image(with_nodes=False, moving=False))
        return self._road_img[1]

    def _path_ends(self, u, v):
        """The stops a route u->v runs between, walking through bends (the path's name is R<stop><stop>)."""
        p = self.project
        ins, outs = {}, {}
        for r in p.routes:
            a, b = rules.route_ends(r["id"])
            outs.setdefault(a, []).append(b)
            ins.setdefault(b, []).append(a)
        seen = set()
        while p.nodes.get(u, {}).get("kind") == "bend" and len(ins.get(u, [])) == 1 and u not in seen:
            seen.add(u); u = ins[u][0]
        while p.nodes.get(v, {}).get("kind") == "bend" and len(outs.get(v, [])) == 1 and v not in seen:
            seen.add(v); v = outs[v][0]
        return u, v

    def _clean_bends(self, start, bends, end):
        """Drop bends where the road would turn back on itself: both its roads leaving the same compass way, which
        the game cannot keep (rules.heading_problems)."""
        ts = self.project.tile_size

        def xy(t):
            if t[0] == "node":
                n = self.project.nodes[t[1]]
                return n["x"], n["y"]
            return t[-2], t[-1]
        a, b = xy(start), xy(end)
        changed = True
        while changed and bends:
            changed = False
            pts = [a] + bends + [b]
            for i in range(1, len(pts) - 1):
                h = lambda q: rules.heading((pts[i][0] * ts, 0, pts[i][1] * ts), (q[0] * ts, 0, q[1] * ts))
                if h(pts[i - 1]) is None or h(pts[i - 1]) == h(pts[i + 1]) or h(pts[i + 1]) is None:
                    del bends[i - 1]
                    changed = True
                    break
        return bends

    def commit_path(self, start, bends, end):
        """Route tool: one path from start to end through bends, one undo step. start / end: ("node", pid),
        ("new", kind, x, y) or ("split", route id, x, y) (a path stop put into that route: a branch). With Connect on,
        a new level or stop at the end is opened by the level before it. Returns the end point's name."""
        p = self.project
        rtype = self.route_type_box.currentData()
        bends = self._clean_bends(start, list(bends), end)
        before = self._snapshot()
        made, splits = [], []

        def make(t):
            if t[0] == "node":
                return t[1]
            kind = t[1] if t[0] == "new" else "stop"
            q = p.add_node(kind, t[-2], t[-1])
            made.append((q, kind, t[-2], t[-1]))
            if t[0] == "split":
                splits.append((t[1], q))
            return q
        try:
            a = make(start)
            ks = []
            for x, y in bends:
                q = p.add_node("bend", x, y)
                made.append((q, "bend", x, y))
                ks.append(q)
            b = make(end)
        except project2d.ProjectError as ex:
            before()
            QtWidgets.QMessageBox.information(self, "Route", str(ex))
            return None
        before()                                              # back as it was; do() puts it all in with these names
        chain = [a] + ks + [b]
        end_kind = p.nodes[b]["kind"] if b in p.nodes else next(k for q, k, _x, _y in made if q == b)
        unlock = self.chain_box.isChecked() and end[0] != "node" and end_kind in self.STOP_KINDS

        def do():
            for q, kind, x, y in made:
                p.add_node(kind, x, y, q)
            for rid, q in splits:
                r = next((r for r in p.routes if r["id"] == rid), None)
                if r is None:
                    continue
                u, v = rules.route_ends(rid)
                S, E = self._path_ends(u, v)
                p.routes = [x for x in p.routes if x["id"] != rid]
                p.add_route(u, q, r["type"])
                p.add_route(q, v, r["type"])
                for n in p.nodes.values():                   # unlocks of the split path open both halves
                    row = n["row"]
                    paths = rules.split(row[4])
                    if "R%s%s" % (S, E) in paths:
                        i = paths.index("R%s%s" % (S, E))
                        paths[i:i + 1] = ["R%s%s" % (S, q), "R%s%s" % (q, E)]
                        row[4] = ",".join(paths)
                        if q not in rules.split(row[3]):
                            row[3] = ",".join(rules.split(row[3]) + [q])
            for u, v in zip(chain, chain[1:]):
                if not any(r["id"] == "R%s%s" % (u, v) for r in p.routes):
                    p.add_route(u, v, rtype)
            if unlock:
                opener = self._opener_before(a)
                if opener and opener != b:
                    row = p.nodes[opener]["row"]
                    if b not in rules.split(row[3]):
                        row[3] = ",".join(rules.split(row[3]) + [b])
                    if "R%s%s" % (opener, b) not in rules.split(row[4]):
                        row[4] = ",".join(rules.split(row[4]) + ["R%s%s" % (opener, b)])
        self.undo.push(Command("Path %s to %s" % (a, b), do, before, self._nodes_changed))
        self.select(("node", b))
        self.status("Path %s to %s%s." % (a, b, (" through %d bends" % len(ks)) if ks else ""))
        return b

    def add_needed_points(self, at=None):
        p = self.project
        if not p.needed_points():
            self.status("This world already has every point the game needs.")
            return []
        before = self._snapshot()
        if at is None:                                        # where you are looking
            c = self.canvas.mapToScene(self.canvas.viewport().rect().center())
            at = (c.x() / TILE - 4.0, c.y() / TILE - 6.0)
        added = p.add_needed_points(at=at)
        made = {q: dict(p.nodes[q], row=list(p.nodes[q]["row"])) for q, _w in added}
        before()

        def do():
            for q, n in made.items():
                p.nodes[q] = dict(n, row=list(n["row"]))
        self.undo.push(Command("Add needed points", do, before, self._nodes_changed))
        self.status("Added %s. Move them where you like and join them with the Route tool." %
                    "; ".join("%s (%s)" % (q, why.split(" (0x")[0].split(";")[0]) for q, why in added))
        self.canvas.flash([q for q, _w in added])
        if added:
            self.select(("node", added[0][0]))
        return added

    def _toolbar_action(self):
        """The toolbar's Action also changes the selected path."""
        sel = self.selected
        if sel and sel[0] == "route":
            self.set_route_type(sel[1], self.route_type_box.currentData())

    def _sel_route(self, fn):
        if self.selected and self.selected[0] == "route":
            fn(self.selected[1])

    def take_side_panels(self):
        """Hand the point / path panel and the checks to the main window's Properties and Checks docks."""
        for w in (self.sel_box, self.checks_box):
            self._right_layout.removeWidget(w)
            w.setParent(None)
        return self.sel_box, self.checks_box

    def point_purpose(self, pid):
        """Why the game needs this point ('map music', 'map enemy starts here (W1Item)'), or ''."""
        p = self.project
        need_points, need_flags = rules.required_names(p.slot)
        bits = []
        if pid in need_points:
            bits.append("map music" if "music" in need_points[pid] else need_points[pid].split(" (")[0])
        n = p.nodes.get(pid)
        for f in rules.split(n["row"][2]) if n else ():
            if f in need_flags:
                bits.append(("map Yoshi waits here (%s)" if "Yoshi" in need_flags[f] else "map enemy starts here (%s)") % f)
        if n and n["kind"] == "start":
            bits.append("Mario starts here")
        return ", ".join(bits)

    def start_path_at(self, target):
        """Route tool, already drawing from target (("node", pid) or ("new", kind, x, y))."""
        self.set_tool("route")
        c = self.canvas
        c._pen = {"start": target, "segs": [], "colours": c._pen_colour(c._pen_xy(target))}
        c._pen_draw()
        self.status("Drawing a path: click to add bends, click a point or double-click to end, Esc to cancel.")

    def change_options(self, pid):
        """[(group, label, new name, kind, allowed, tooltip)] a point can become in this world: what the world still
        needs first, then the levels the game keeps clears for, the other levels and specials, then stops and bends."""
        from . import koopatlas
        p, w = self.project, self.project.world
        root = self.host.romfs_root() if self.host else None
        has_file = (lambda f: (Path(root) / f).exists()) if root else (lambda f: True)
        cur_kind = p.nodes[pid]["kind"]
        out = []
        need = [(q, k, why) for q, k, f, why in p.needed_points() if not f]
        for q, k, why in need:
            name = q or ("W%dM0" % w if k == "start" else "W%dC0" % w if k == "castle" else None)
            if name and name != pid:
                out.append(("Needed by this world", "%s · %s" % (name, why.split(" (0x")[0]), name, k,
                            name not in p.nodes, why))
        names = koopatlas.course_names(w)
        pnames = list(p.game_nodes()[0])
        tracked = [nm for c, nm in sorted(names.items()) if nm[2] == "0" and rules.has_slot(w, c - 1, names=pnames)]
        room = rules.slot_room(w, pnames) if p.slot_hook else 0
        hooked_grp = "More levels (slot hook: %d free entr%s)" % (room, "y" if room == 1 else "ies")
        for c, nm in sorted(names.items()):
            if nm[2] != "0":
                continue
            lf = rules.level_file(nm)
            ok = nm not in p.nodes and has_file(lf)
            grp = ("Levels the game keeps clears for" if nm in tracked else hooked_grp if p.slot_hook and room
                   else "Other levels (no clear kept: exits open nothing)")
            out.append((grp, "%s · %s" % (nm, lf[len("Course/"):-len(".sarc")]), nm, "level", ok,
                        "%s%s" % (lf, "" if has_file(lf) else " is missing from the game: entering it would crash")))
        for kind, letter, label in (("start", "M", "Start"), ("tower", "T", "Tower"), ("castle", "C", "Castle"),
                                    ("ghost", "G", "Ghost house"), ("toad", "K", "Toad house"), ("airship", "A", "Airship"),
                                    ("level", "S", "Cannon"), ("link", "J", "Link to another world")):
            nm = "W%d%s0" % (w, letter)
            lf = rules.level_file(nm)
            ok = nm not in p.nodes and (lf is None or has_file(lf))
            out.append(("Special points", "%s · %s%s" % (nm, label, (" (" + lf[len("Course/"):-len(".sarc")] + ")") if lf else ""),
                        nm, kind, ok and nm != pid, (lf or label) + ("" if (lf is None or has_file(lf)) else " is missing")))
        for kind, label in (("stop", "Path stop (F)"), ("bend", "Bend (K, Mario walks through)")):
            out.append(("Stops", label, None, kind, cur_kind != kind, label))
        return out

    def change_point(self, pid, new, kind):
        """Rename a point into another one (its routes, unlocks and kind follow). One undo step."""
        p = self.project
        if new is None:
            new = p.next_id(kind)
        if new == pid or new in p.nodes:
            return
        old_kind = p.nodes[pid]["kind"]

        def do():
            p.rename_node(pid, new)
            p.nodes[new]["kind"] = kind

        def undo():
            p.nodes[new]["kind"] = old_kind
            p.rename_node(new, pid)
        self.selected = ("node", new)
        self.undo.push(Command("%s becomes %s" % (pid, new), do, undo, self._nodes_changed_rename(pid, new)))
        self.status("%s is now %s (%s)." % (pid, new, project2d.NODE_KINDS.get(kind, ("", kind))[1]))

    def flag_options(self, pid):
        """[(label, flag, on, tooltip)]: the flags this world needs some point to carry."""
        _pts, flags = rules.required_names(self.project.slot)
        have = rules.split(self.project.nodes[pid]["row"][2])
        return [(("Map Yoshi waits here (%s)" if "Yoshi" in why else "Map enemy starts here (%s)") % f, f, f in have,
                 why) for f, why in flags.items()]

    def toggle_flag(self, pid, flag):
        row = self.project.nodes[pid]["row"]
        old = row[2]
        fl = rules.split(old)
        new = ",".join([x for x in fl if x != flag] if flag in fl else fl + [flag])

        def put(v):
            self.project.nodes[pid]["row"][2] = v
        self.undo.push(Command("%s: %s" % (pid, flag), lambda: put(new), lambda: put(old), self._nodes_changed))

    def arm_point(self, kind):
        """The main toolbar's + Level / + Stop / + Point / + Bend while the World Maker is shown."""
        k = {"pass": "stop"}.get(kind, kind)
        i = self.kind_box.findData(k)
        if i >= 0:
            self.kind_box.setCurrentIndex(i)
        self.set_tool("node")

    def add_route(self, a, b):
        p = self.project
        rtype = self.route_type_box.currentData()
        rid = "R%s%s" % (a, b)
        if any(r["id"] == rid for r in p.routes):
            self.status("%s already exists." % rid)
            return None
        idx = len(p.routes)
        self.undo.push(Command("Route %s" % rid, lambda: p.add_route(a, b, rtype), lambda: p.routes.pop(idx), self._nodes_changed))
        self.select(("route", rid))
        return rid

    def delete_selected(self):
        p = self.project
        if not self.selected:
            return
        kind, key = self.selected
        if kind == "node" and key in p.nodes:
            snap_node = dict(p.nodes[key], row=list(p.nodes[key]["row"]))
            snap_routes = [dict(r) for r in p.routes]

            def undo():
                p.nodes[key] = dict(snap_node, row=list(snap_node["row"]))
                p.routes = [dict(r) for r in snap_routes]
            self.undo.push(Command("Delete %s" % key, lambda: p.remove_node(key), undo, self._nodes_changed))
        elif kind == "route":
            idx = next((i for i, r in enumerate(p.routes) if r["id"] == key), None)
            if idx is None:
                return
            row = dict(p.routes[idx])
            self.undo.push(Command("Delete %s" % key, lambda: p.routes.pop(idx), lambda: p.routes.insert(idx, dict(row)), self._nodes_changed))
        self.select(None)

    def _nodes_changed(self):
        if self.selected:
            kind, key = self.selected
            if (kind == "node" and key not in self.project.nodes) or (kind == "route" and not any(r["id"] == key for r in self.project.routes)):
                self.selected = None
        self.canvas.refresh_nodes()
        self._show_selection()
        self.run_checks()
        if hasattr(self, "view3d"):
            self._points_3d()

    def select(self, what):
        self.selected = what
        self.canvas.refresh_nodes()
        self._show_selection()

    def _show_selection(self):
        p = self.project
        sel = self.selected
        node = bool(sel and sel[0] == "node" and sel[1] in p.nodes)
        route = bool(sel and sel[0] == "route")
        adv = self.adv_btn.isChecked()
        for w in [self.id_edit] + list(self.cells.values()) + self.cam:
            w.setEnabled(bool(node))
        for w, on in ([(self.id_edit, node), (self.point_bar, node), (self.flag_pick, node), (self.cam_w, node),
                       (self.adv_btn, node), (self.route_type_sel, route), (self.route_btns, route), (self.del_btn, bool(sel))]
                      + [(ed, node and adv) for ed in self.cells.values()]):
            lab = self.sel_form.labelForField(w)
            if lab is not None:
                lab.setVisible(on)
            w.setVisible(on)
        self.sel_help.setVisible(True)
        self.route_type_sel.setEnabled(bool(route))
        self._fill_flag_pick()
        self.flag_pick.setEnabled(bool(node))
        if hasattr(self, "point_bar"):
            from . import pointactions as PA
            keys = [a.key for a in PA.actions_of(sel[1], rules.split(p.nodes[sel[1]]["row"][2]))] if node else []
            self.point_bar.show_actions(keys, p.slot)
            self.point_bar.setEnabled(bool(node))
        self.del_btn.setEnabled(bool(sel))
        if node:
            n = p.nodes[sel[1]]
            lf = rules.level_file(sel[1])
            why = self.point_purpose(sel[1])
            special = n["kind"] == "level" and sel[1][2:3] != "0"     # W1S0 and the like: no level of their own
            from . import pointactions as _PA
            cannon = _PA.is_cannon(sel[1])
            desc = ("cannon (shoots Mario on to another point)" if cannon else "special point") if special else (
                project2d.NODE_KINDS.get(n["kind"], ("", n["kind"]))[1])
            self.sel_title.setText("%s · %s%s" % (sel[1], desc, (" · level file " + lf) if lf and not special else ""))
            self.sel_help.setText(("Needed by the game: %s. " % why if why else "") +
                                  "Drag it with Select (V); Alt-drag or Alt+arrows for fine moves, arrows to nudge. "
                                  "Right-click it to draw a path from it.")
            self.id_edit.setText(sel[1])
            for col, ed in self.cells.items():
                ed.setText(n["row"][col] if col < len(n["row"]) else "")
            for sp, v in zip(self.cam, n.get("camera", [0, 0, 0])):
                sp.blockSignals(True); sp.setValue(v); sp.blockSignals(False)
        elif route:
            r = next((r for r in p.routes if r["id"] == sel[1]), None)
            a, b = rules.route_ends(sel[1]) or ("?", "?")
            self.sel_title.setText("Path %s → %s" % (a, b))
            self.sel_help.setText("Pick its action here or in the toolbar (walk, ladder, jump...). Right-click it on the "
                                  "map for more.")
            if r:
                i = self.route_type_sel.findData(r["type"])
                self.route_type_sel.setCurrentIndex(i)
                self.route_type_box.setCurrentIndex(self.route_type_box.findData(r["type"]))
                self._show_action()
        else:
            self.sel_title.setText("Nothing selected")
            self.sel_help.setText("Click a point or a path with Select (V) to edit it here. Right-click the map to add "
                                  "a level, a stop or a castle there; the Route tool (P) draws paths.")
            self.id_edit.clear()
            for ed in self.cells.values():
                ed.clear()
        self._fill_unlocks(sel[1] if route else None)

    def _fill_unlocks(self, rid):
        """Koopatlas-style 'Opens when' (e.g. '1-2 secret OR 1-3') for each unlock path the selected route is on."""
        from . import unlocks
        while self.unlock_form.rowCount():
            self.unlock_form.removeRow(0)
        self.unlock_host.setVisible(bool(rid))
        if not rid:
            return
        p = self.project
        rows = p.point_rows()
        on = unlocks.paths_through(rules.map_paths(rows, p.route_rows()), rid)
        self.unlock_form.addRow(QtWidgets.QLabel("<b>Opens when…</b> (levels joined by OR; 'secret' for a secret exit)"))
        if not on:
            self.unlock_form.addRow(QtWidgets.QLabel("On no unlock path: open from the start."))
        for path in on:
            ed = QtWidgets.QLineEdit(unlocks.describe(rows, path))
            ed.setPlaceholderText("e.g. 1-2 OR 1-3 secret")
            ed.editingFinished.connect(lambda pth=path, e=ed: self.set_unlock(pth, e.text()))
            self.unlock_form.addRow(path, ed)

    def set_unlock(self, path, expr):
        from . import unlocks
        p = self.project
        rows = p.point_rows()
        if unlocks.describe(rows, path) == expr.strip():
            return
        try:
            new = unlocks.apply(rows, path, unlocks.parse(expr, [r[1] for r in rows]))
        except unlocks.UnlockError as e:
            QtWidgets.QMessageBox.information(self, "Opens when", str(e))
            return
        old = {pid: list(n["row"]) for pid, n in p.nodes.items()}
        newrows = {r[1]: list(r) for r in new}

        def put(table):
            for pid, row in table.items():
                if pid in p.nodes:
                    p.nodes[pid]["row"] = [p.nodes[pid]["row"][0]] + list(row[1:9])
        self.undo.push(Command("%s opens when %s" % (path, expr.strip() or "nothing"), lambda: put(newrows),
                               lambda: put(old), self._nodes_changed))

    def _fill_flag_pick(self):
        """This world's needs first (with where they are), then the common flags."""
        p, box = self.project, self.flag_pick
        box.blockSignals(True)
        box.clear()
        box.addItem("Add a flag…", None)
        where = {}
        for pid, n in p.nodes.items():
            for f in rules.split(n["row"][2]):
                where.setdefault(f, pid)
        _, needed = rules.required_names(p.slot)
        for f, why in needed.items():
            kind = "map Yoshi" if "Yoshi" in why else "map enemy"
            box.addItem("%s (%s%s)" % (f, kind, ", on %s" % where[f] if f in where else ", not placed: hidden on export"), f)
        from . import flaghelp
        for f in ("stop", "onestop", "restart", "merge", "focus", "fixX", "fixY", "fixZ", "noUI", "board1", "water"):
            box.addItem("%s  (%s)" % (flaghelp.info(f).label, f), f)
            box.setItemData(box.count() - 1, flaghelp.tooltip(f, p.slot), QtCore.Qt.ToolTipRole)
        box.addItem("More flags…", "__more__")
        box.setCurrentIndex(0)
        box.blockSignals(False)

    def _flag_picked(self, index):
        f = self.flag_pick.itemData(index)
        self.flag_pick.setCurrentIndex(0)
        if not f or not self.selected or self.selected[0] != "node":
            return
        if f == "__more__":
            from .mainwindow import FlagsDialog
            d = FlagsDialog(self, self.selected[1], self.cells[2].text(), self.project.slot)
            if d.exec_():
                self.cells[2].setText(d.value())
                self._cell_edited(2)
            return
        have = rules.split(self.cells[2].text())
        if f not in have:
            self.cells[2].setText(",".join(have + [f]))
            self._cell_edited(2)

    def toggle_point_action(self, key):
        """Give the selected point an action or take it away (one undo step): the flag, or for a cannon the name
        W<n>S0 and a path to the world it shoots Mario to."""
        from . import pointactions as PA
        if not self.selected or self.selected[0] != "node":
            return
        p, pid = self.project, self.selected[1]
        flags = rules.split(p.nodes[pid]["row"][2])
        have = key in [a.key for a in PA.actions_of(pid, flags)]
        if key == "cannon":
            if have:
                self.status("%s is a cannon by its name: rename it to stop being one." % pid)
                self._selection_changed() if hasattr(self, "_selection_changed") else None
                return
            new = "W%xS0" % p.world
            if new in p.nodes:
                QtWidgets.QMessageBox.information(self, "Cannon", "This world already has its cannon (%s)." % new)
                self.select(("node", pid))
                return
            target, ok = QtWidgets.QInputDialog.getInt(self, "Cannon", "Shoot Mario to which world?",
                                                       min(p.world + 2, 9), 1, 9)
            if not ok:
                self.select(("node", pid))
                return
            old_row = list(p.nodes[pid]["row"])
            path, opens = "R%sW%xJ0" % (new, target), "W%x01" % target
            self.undo.beginMacro("%s: cannon to World %d" % (pid, target))
            self.undo.push(Command("Rename %s" % pid, lambda: p.rename_node(pid, new), lambda: p.rename_node(new, pid),
                                   self._nodes_changed_rename(pid, new)))

            def setv(row):
                p.nodes[new]["row"][3], p.nodes[new]["row"][4] = row
            self.undo.push(Command("%s exit" % new, lambda: setv((opens, ",".join([path] + rules.split(old_row[4])))),
                                   lambda: setv((old_row[3], old_row[4])), self._nodes_changed))
            self.undo.endMacro()
            self.select(("node", new))
            self.status("%s is now the cannon: its normal exit opens %s, so Mario is shot to World %d (W%xJ0)."
                        % (new, path, target, target))
            return
        flag = {"warp_pipe": "dokan", "island_pipe": "dokan2", "yoshi": PA.YOSHI_FLAGS.get(p.slot, "balloon1"),
                "lift": "boarding"}.get(key)
        if key == "link":
            used = {f for n in p.nodes.values() for f in rules.split(n["row"][2]) if f in PA.LINK_FLAGS}
            flag = next((f for f in PA.LINK_FLAGS if f not in used), "link1")
        if have:
            drop = {"warp_pipe": {"dokan"}, "island_pipe": {"dokan2"}, "link": set(PA.LINK_FLAGS),
                    "yoshi": set(PA.YOSHI_FLAGS.values()), "lift": set(PA.LIFT_FLAGS)}[key]
            new_flags = [f for f in flags if f not in drop]
        else:
            new_flags = flags + [flag]
        changes = {pid: (",".join(flags), ",".join(new_flags))}
        if key == "warp_pipe" and not have:                    # one warp pipe per world: the others lose the flag
            for q, n in p.nodes.items():
                fq = rules.split(n["row"][2])
                if q != pid and "dokan" in fq:
                    changes[q] = (",".join(fq), ",".join(f for f in fq if f != "dokan"))

        def apply(which):
            for q, (old, new) in changes.items():
                p.nodes[q]["row"][2] = new if which else old
        label = PA.POINT[key].label
        self.undo.push(Command("%s: %s %s" % (pid, "no" if have else "", label), lambda: apply(True),
                               lambda: apply(False), self._nodes_changed))
        if key == "island_pipe" and not have and "Fs01" not in p.nodes:
            self.status("Island pipe set. Warping to the island lands on the point named Fs01: add a stop and name it Fs01.")
        elif key == "warp_pipe" and not have and len(changes) > 1:
            self.status("Warp pipe moved to %s (a world has one)." % pid)

    def _rename_node(self):
        if not self.selected or self.selected[0] != "node":
            return
        old, new = self.selected[1], self.id_edit.text().strip()
        if new == old or not new:
            return
        p = self.project
        try:
            if new in p.nodes or len(new) != 4:
                raise project2d.ProjectError("%s is taken or not 4 characters." % new)
        except project2d.ProjectError as e:
            QtWidgets.QMessageBox.information(self, "Not renamed", str(e))
            self.id_edit.setText(old)
            return
        self.selected = ("node", new)
        self.undo.push(Command("Rename %s" % old, lambda: p.rename_node(old, new), lambda: p.rename_node(new, old), self._nodes_changed_rename(old, new)))

    def _nodes_changed_rename(self, old, new):
        def run():
            if self.selected and self.selected[1] in (old, new):
                self.selected = ("node", new if new in self.project.nodes else old)
            self._nodes_changed()
        return run

    def _cell_edited(self, col):
        if not self.selected or self.selected[0] != "node":
            return
        pid = self.selected[1]
        n = self.project.nodes[pid]
        old, new = n["row"][col], self.cells[col].text().strip()
        if old == new:
            return

        def setv(v):
            self.project.nodes[pid]["row"][col] = v
        self.undo.push(Command("%s: %s" % (pid, rules.COLUMNS[col]), lambda: setv(new), lambda: setv(old), self._nodes_changed))

    def _camera_edited(self):
        if not self.selected or self.selected[0] != "node":
            return
        pid = self.selected[1]
        old = list(self.project.nodes[pid].get("camera", [0, 0, 0]))
        new = [sp.value() for sp in self.cam]
        if old == new:
            return

        def setv(v):
            self.project.nodes[pid]["camera"] = list(v)
        self.undo.push(Command("%s camera" % pid, lambda: setv(new), lambda: setv(old), self._nodes_changed))

    def _route_type_changed(self, _i):
        if not self.selected or self.selected[0] != "route":
            return
        self.set_route_type(self.selected[1], self.route_type_sel.currentData())

    def set_route_type(self, rid, new):
        """Give a route another action (one undo step)."""
        r = next((r for r in self.project.routes if r["id"] == rid), None)
        if r is None or r["type"] == new:
            return
        old = r["type"]

        def setv(v):
            next(x for x in self.project.routes if x["id"] == rid)["type"] = v
        from . import routeactions as RA
        self.undo.push(Command("%s: %s" % (rid, RA.action(new).label), lambda: setv(new), lambda: setv(old),
                               self._nodes_changed))

    # ---- checks ------------------------------------------------------------------------------------------------------
    def run_checks(self):
        self.checks.clear()
        rep = self.project.check()
        ids = set(self.project.nodes)
        bad = set()
        for m in rep.errors:
            for tok in re.findall(r"\b[A-Za-z0-9]{4,9}\b", m):
                if tok in ids:
                    bad.add(tok)
                elif len(tok) == 9 and tok[0] == "R" and tok[1:5] in ids and tok[5:9] in ids:
                    bad.update((tok[1:5], tok[5:9]))
        if bad != self.problem_points:
            self.problem_points, self.problem_text = bad, list(rep.errors)
            self.canvas.refresh_nodes()
        self.problem_text = list(rep.errors)
        for line in rep.summary:
            it = QtWidgets.QListWidgetItem(line); it.setForeground(QtGui.QBrush(QtGui.QColor("#7dd3fc"))); self.checks.addItem(it)
        if not rep.errors:
            it = QtWidgets.QListWidgetItem("No errors."); it.setForeground(QtGui.QBrush(QtGui.QColor("#4ade80"))); self.checks.addItem(it)
        for m in rep.errors:
            it = QtWidgets.QListWidgetItem("Error: " + m); it.setForeground(QtGui.QBrush(QtGui.QColor("#f87171"))); self.checks.addItem(it)
        for m in rep.warnings:
            it = QtWidgets.QListWidgetItem("Warning: " + m); it.setForeground(QtGui.QBrush(QtGui.QColor("#facc15"))); self.checks.addItem(it)
        return rep


# --------------------------------------------------------------------------------------------- export dialog
class ExportDialog(QtWidgets.QDialog):
    """Export the World Maker map as a world archive (CS_W<n>) plus its camera table, into a LayeredFS mod folder."""

    def __init__(self, maker):
        super().__init__(maker)
        self.maker = maker
        self.setWindowTitle("Export to game")
        self.resize(640, 360)
        p = maker.project
        host = maker.host
        lay = QtWidgets.QVBoxLayout(self)
        nodes, hidden = p.game_nodes()
        intro = QtWidgets.QLabel(
            "The map replaces CS_%s. The vanilla archive is the template: its shaders, point markers and world-link bones "
            "are reused, its scenery is replaced by your painted map, and every point becomes a bone of the map-point "
            "model. Your game files are not changed.%s" % (p.slot, (
                "<br><br>The game's code needs these names, which your map does not have; they are added hidden under "
                "the start point, out of reach: <b>%s</b>." % ", ".join(hidden)) if hidden else ""))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self.template = QtWidgets.QComboBox()
        self.template.setEditable(True)
        root = host.romfs_root() if host else None
        if root:
            for wid, path in sorted(romfs.worlds(root), key=lambda wp: wp[0] != p.slot):
                if int(wid[1]) == p.world:
                    self.template.addItem(str(path))
        browse_t = QtWidgets.QPushButton("Browse…"); browse_t.clicked.connect(self._browse_template)
        row_t = QtWidgets.QHBoxLayout(); row_t.addWidget(self.template, 1); row_t.addWidget(browse_t)
        form.addRow("Template (vanilla CS_W%d)" % p.world, row_t)
        self.mod_root = QtWidgets.QLineEdit(host.settings.value("mod_root", "") if host else "")
        browse_m = QtWidgets.QPushButton("Browse…"); browse_m.clicked.connect(self._browse_mod)
        row_m = QtWidgets.QHBoxLayout(); row_m.addWidget(self.mod_root, 1); row_m.addWidget(browse_m)
        self.mod_root.setToolTip("Any level works: the SD card root, its atmosphere folder, the title folder or its romfs.")
        form.addRow("Mod folder (SD card root or staging)", row_m)
        self.tex_size = QtWidgets.QComboBox()
        for n in (1024, 2048, 4096):
            self.tex_size.addItem("%d px (longest side)" % n, n)
        self.tex_size.setCurrentIndex(1)
        form.addRow("Map texture", self.tex_size)
        lay.addLayout(form)
        ok, msg = export2d.engine_status()
        self.engine_label = QtWidgets.QLabel(("<span style='color:#4ade80'>%s</span>" if ok else "<span style='color:#f87171'>%s</span>") % msg)
        self.engine_label.setWordWrap(True)
        self.engine_label.setOpenExternalLinks(True)
        lay.addWidget(self.engine_label)
        note = QtWidgets.QLabel("Exports have been checked with Koopatlas DX's and wmtool's rules but not yet on a console: "
                                "keep a backup and test on hardware.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9fb3c8")
        lay.addWidget(note)
        bb = QtWidgets.QDialogButtonBox()
        self.export_btn = bb.addButton("Export", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        self.export_btn.setEnabled(ok)
        bb.accepted.connect(self._export)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.result = None

    def _browse_template(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Vanilla world archive", "", "World archives (CS_W*.szs)")
        if path:
            self.template.setEditText(path)

    def _browse_mod(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Mod folder", self.mod_root.text())
        if d:
            self.mod_root.setText(d)

    def do_export(self):
        """Run the export with the dialog's settings. Returns (written, report); raises ExportError on failure."""
        template = Path(self.template.currentText().strip())
        mod = self.mod_root.text().strip()
        if not template.exists():
            raise export2d.ExportError("Choose the vanilla CS_W%d archive as the template." % self.maker.project.world)
        if not mod:
            raise export2d.ExportError("Choose a mod folder.")
        host = self.maker.host
        root = host.romfs_root() if host else None
        model_dir = romfs.model_dir(root) if root else template.parent
        image = self.maker.render_image(with_nodes=False, moving=False)
        written, report = export2d.export_world(self.maker.project, image, template, mod, model_dir,
                                                max_texture=self.tex_size.currentData())
        if host:
            host.settings.setValue("mod_root", mod)
            if getattr(report, "unpacked_size", None) and hasattr(host, "show_world_size"):
                host.show_world_size(report.unpacked_size, "Exported")
        self.result = (written, report)
        return written, report

    def _export(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            written, report = self.do_export()
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Export failed", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        lines = ["Written:"] + ["  " + str(p) for p in written] + [""] + report.summary
        lines += ["Errors: none" if not report.errors else "Errors: " + "; ".join(report.errors[:5])]
        if report.warnings:
            lines.append("Warnings: " + "; ".join(report.warnings[:5]))
        lines += ["", "Test with a new save file: a save that has been on the vanilla map remembers its point by index and "
                  "its path stops by name, which the new map may not have."]
        QtWidgets.QMessageBox.information(self, "Exported", "\n".join(lines))
        self.accept()

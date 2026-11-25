"""
kdx/map2d.py - the 2D map: the world seen from straight above, Koopatlas-style.

Scene coordinates are world X (right) and world Z (down). The terrain picture comes from WorldScene.render_topdown();
points, waypoints and routes are items on top. Markers and labels keep their size on screen while you zoom.
Selecting and moving use the same signals as the 3D view, so the main window treats both views alike.
"""
from __future__ import annotations

import math

from PyQt5 import QtCore, QtGui, QtWidgets

from .diagnostics import guarded
from .viewport import KIND_STYLE, ROUTE_COLORS
from .world import point_kind


def qcolor(rgb, alpha=255):
    return QtGui.QColor(int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255), alpha)


class PointItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, view, pid, kind, x, z):
        col, size = KIND_STYLE.get(kind, KIND_STYLE["Other"])
        r = max(3.5, size / 2.0 - 1.0)
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.view, self.pid, self.kind, self.radius, self.base_color = view, pid, kind, r, col
        self.setPos(x, z)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(20 if kind != "Waypoint" else 10)
        self.label = QtWidgets.QGraphicsSimpleTextItem(pid, self)
        f = self.label.font(); f.setPointSize(8); f.setBold(True); self.label.setFont(f)
        self.label.setBrush(QtGui.QBrush(QtGui.QColor("#fff3c4")))
        self.label.setPos(r + 2, -r - 12)
        self.set_selected(False)

    def set_selected(self, on):
        col = (1.0, 0.25, 0.85) if on else self.base_color
        self.setBrush(QtGui.QBrush(qcolor(col)))
        self.setPen(QtGui.QPen(QtGui.QColor(10, 10, 14), 2.0 if on else 1.0))
        scale = 1.6 if on else 1.0
        self.setRect(-self.radius * scale, -self.radius * scale, 2 * self.radius * scale, 2 * self.radius * scale)
        self.setZValue(30 if on else (20 if self.kind != "Waypoint" else 10))
        self.label.setBrush(QtGui.QBrush(QtGui.QColor("#ff8adf" if on else "#fff3c4")))

    @guarded
    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            self.view._item_moved(self)
        return super().itemChange(change, value)


class Map2D(QtWidgets.QGraphicsView):
    pointClicked = QtCore.pyqtSignal(str)
    pointDragged = QtCore.pyqtSignal(str, tuple)
    cameraDragged = QtCore.pyqtSignal(str, tuple)
    statusMessage = QtCore.pyqtSignal(str)
    routeClicked = QtCore.pyqtSignal(str)
    routeMenu = QtCore.pyqtSignal(str, QtCore.QPoint)

    def __init__(self, parent=None):
        self._scene = QtWidgets.QGraphicsScene()
        super().__init__(self._scene, parent)
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QtGui.QColor(14, 20, 30))
        self.world = None
        self.world_scene = None
        self.items = {}            # id -> PointItem
        self.routes = {}           # route id -> QGraphicsLineItem
        self.background = None
        self.hidden = set()
        self.selected = ""
        self.move_mode = False          # kept for compatibility; dragging no longer needs a mode
        self.locked = False
        self.pick_handler = None
        self.place_handler = None       # + Level / + Stop: called with the clicked world position
        self.highlight = {}
        self.camera_for = None
        self.show_all_cameras = False
        self.cameras = {}               # point id -> (marker item, line item)
        self._candidate = None
        self._cam_drag = None
        self.snap_to_terrain = True
        self.show_labels = True
        self.show_waypoints = True
        self.show_routes = True
        self.show_grid = True
        self._pan = None
        self._press = None
        self._drag_item = None
        self._drag_start = None

    # ---- building ----------------------------------------------------------------------------------------------------
    def set_world(self, world, world_scene):
        self.world, self.world_scene = world, world_scene
        self.selected = ""
        self.hidden = set()
        self._rebuild()
        self.fit_all()

    def _rebuild(self):
        self._scene.clear()
        self.items, self.routes, self.background, self.cameras = {}, {}, None, {}
        if not self.world:
            return
        self._set_background()
        from .routeui import RouteItem
        for r in self.world.routes:
            line = RouteItem(r["id"], (0, 0), (0, 0), r["type"])
            line.WIDTH = 6.0
            line.setZValue(5)
            self._scene.addItem(line)
            self.routes[r["id"]] = line
        for pid in self.world.point_ids:
            self._add_point(pid, point_kind(pid))
        for k in self.world.waypoint_ids:
            self._add_point(k, "Waypoint")
        self._update_routes()
        self._apply_visibility()
        self._update_cameras()
        x0, z0, x1, z1 = self.world_scene.bounds_xz()
        pad = max(x1 - x0, z1 - z0) * 0.6 + 200
        self._scene.setSceneRect(x0 - pad, z0 - pad, (x1 - x0) + 2 * pad, (z1 - z0) + 2 * pad)

    def _add_point(self, pid, kind):
        p = self.world.position(pid)
        if p is None:
            return
        item = PointItem(self, pid, kind, p[0], p[2])
        item.setToolTip("%s · %s" % (pid, kind))
        self._scene.addItem(item)
        self.items[pid] = item

    def _set_background(self):
        if self.background is not None:
            self._scene.removeItem(self.background)
            self.background = None
        if not self.world_scene:
            return
        img, (x0, z0, x1, z1) = self.world_scene.render_topdown(hidden=self.hidden)
        if getattr(self.world_scene, "render_error", None):
            self.statusMessage.emit("The terrain picture could not be drawn (%s); points and routes still work." % self.world_scene.render_error)
        pix = QtWidgets.QGraphicsPixmapItem(QtGui.QPixmap.fromImage(img))
        pix.setTransformationMode(QtCore.Qt.SmoothTransformation)
        pix.setScale((x1 - x0) / img.width())
        pix.setPos(x0, z0)
        pix.setZValue(-10)
        self._scene.addItem(pix)
        self.background = pix

    # ---- public ------------------------------------------------------------------------------------------------------
    def refresh_markers(self):
        """Positions may have changed (edit, undo, redo)."""
        if not self.world:
            return
        current = set(self.world.point_ids) | set(self.world.waypoint_ids)
        if set(self.items) != current or set(self.routes) != {r["id"] for r in self.world.routes}:
            sel = self.selected
            self._rebuild()
            self.select(sel)
            return
        for pid, item in self.items.items():
            p = self.world.position(pid)
            if p is not None and (abs(item.x() - p[0]) > 1e-4 or abs(item.y() - p[2]) > 1e-4):
                item.setPos(p[0], p[2])
        self._style_routes()
        self._update_routes()
        self._update_cameras()

    def set_model_visible(self, name, visible):
        (self.hidden.discard if visible else self.hidden.add)(name)
        self._set_background()

    def select(self, pid):
        if self.selected in self.items:
            self.items[self.selected].set_selected(False)
        self.selected = pid or ""
        if self.selected in self.items:
            self.items[self.selected].set_selected(True)
        self._apply_visibility()
        self._update_cameras()

    def set_highlight(self, colors):
        self.highlight = dict(colors or {})
        self._style_routes()

    def _style_routes(self):
        for r in (self.world.routes if self.world else []):
            line = self.routes.get(r["id"])
            if line is None:
                continue
            if line.rtype != r["type"]:                      # the type was edited
                line.rtype = r["type"]
            line.set_highlight(qcolor(self.highlight[r["id"]]) if r["id"] in self.highlight else None,
                               dim=bool(self.highlight))
            line.setZValue(8 if r["id"] in self.highlight else 5)

    # ---- camera markers ----------------------------------------------------------------------------------------------
    def _camera_xz(self, pid):
        item = self.items.get(pid)
        off = self.camera_for(pid) if (self.camera_for and item is not None) else None
        if off is None:
            return None
        return (item.x() + off[0], item.y() + off[2])

    def _update_cameras(self):
        for marker, line in self.cameras.values():
            self._scene.removeItem(marker); self._scene.removeItem(line)
        self.cameras = {}
        if not self.world or not self.camera_for:
            return
        wanted = [pid for pid, it in self.items.items() if it.kind != "Waypoint" and
                  (pid == self.selected or (self.show_all_cameras and any(abs(v) > 1e-6 for v in (self.camera_for(pid) or (0, 0, 0)))))]
        for pid in wanted:
            xz = self._camera_xz(pid)
            if xz is None:
                continue
            sel = pid == self.selected
            r = 9 if sel else 5
            marker = QtWidgets.QGraphicsPolygonItem(QtGui.QPolygonF([QtCore.QPointF(0, -r), QtCore.QPointF(r, 0), QtCore.QPointF(0, r), QtCore.QPointF(-r, 0)]))
            marker.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            marker.setBrush(QtGui.QBrush(QtGui.QColor(50, 225, 255, 255 if sel else 150)))
            marker.setPen(QtGui.QPen(QtGui.QColor(10, 10, 14), 1.0))
            marker.setPos(*xz)
            marker.setZValue(40 if sel else 15)
            marker.setToolTip("Camera of %s (drag to move it)" % pid)
            line = QtWidgets.QGraphicsLineItem(QtCore.QLineF(self.items[pid].pos(), QtCore.QPointF(*xz)))
            pen = QtGui.QPen(QtGui.QColor(50, 225, 255, 200 if sel else 90), 1.5, QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            line.setPen(pen)
            line.setZValue(39 if sel else 14)
            self._scene.addItem(line); self._scene.addItem(marker)
            self.cameras[pid] = (marker, line)

    def focus_on(self, pid):
        item = self.items.get(pid)
        if item:
            self.centerOn(item)

    def fit_all(self):
        if not self.world_scene:
            return
        x0, z0, x1, z1 = self.world_scene.bounds_xz()
        self.resetTransform()
        self.fitInView(QtCore.QRectF(x0, z0, max(1.0, x1 - x0), max(1.0, z1 - z0)), QtCore.Qt.KeepAspectRatio)

    def set_option(self, name, value):
        setattr(self, name, value)
        self._apply_visibility()
        self.viewport().update()

    def scene_pos_of(self, pid):
        item = self.items.get(pid)
        return None if item is None else self.mapFromScene(item.pos())

    # ---- helpers -----------------------------------------------------------------------------------------------------
    def _apply_visibility(self):
        for pid, item in self.items.items():
            visible = self.show_waypoints or item.kind != "Waypoint" or pid == self.selected
            item.setVisible(visible)
            item.label.setVisible(pid == self.selected or (self.show_labels and item.kind not in ("Waypoint", "Path stop")))
            item.setCursor(QtCore.Qt.PointingHandCursor if (self.locked or self.pick_handler) else QtCore.Qt.SizeAllCursor)
        for line in self.routes.values():
            line.setVisible(self.show_routes)

    def _update_routes(self, only=None):
        for r in self.world.routes:
            if only and only not in (r["from"], r["to"]):
                continue
            a, b = self.items.get(r["from"]), self.items.get(r["to"])
            line = self.routes.get(r["id"])
            if line is None:
                continue
            if a is None or b is None:
                line.setVisible(False)
                continue
            line.setLine(QtCore.QLineF(a.pos(), b.pos()))

    def _item_moved(self, item):
        self._update_routes(only=item.pid)
        if self._drag_item is item:
            self.statusMessage.emit("%s → X %.1f, Z %.1f" % (item.pid, item.x(), item.y()))

    def _point_item_at(self, pos, radius=10):
        """The visible point whose marker centre is nearest to pos (in pixels), within radius; real points win ties
        over waypoints. (Qt's own lookup returns the topmost item, which is wrong when markers overlap.)"""
        best, best_d = None, None
        for item in self.items.values():
            if not item.isVisible():
                continue
            c = self.mapFromScene(item.pos())
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y()) + (3.0 if item.kind == "Waypoint" else 0.0)
            if d <= radius + 3.0 and (best_d is None or d < best_d):
                best, best_d = item, d
        return best

    # ---- mouse -------------------------------------------------------------------------------------------------------
    @guarded
    def wheelEvent(self, e):
        factor = 1.18 if e.angleDelta().y() > 0 else 1 / 1.18
        current = self.transform().m11()
        if 0.01 < current * factor < 60:
            self.scale(factor, factor)

    def _camera_hit(self, pos, radius=11):
        if not self.selected or self.selected not in self.cameras:
            return False
        c = self.mapFromScene(self.cameras[self.selected][0].pos())
        return math.hypot(c.x() - pos.x(), c.y() - pos.y()) <= radius

    @guarded
    def mousePressEvent(self, e):
        if e.button() in (QtCore.Qt.RightButton, QtCore.Qt.MiddleButton):
            self._pan = e.pos()
            self._pan_start = e.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if e.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(e)
        self._press = e.pos()
        self._candidate = None
        if self.pick_handler is None and not self.locked and self._camera_hit(e.pos()):
            self._cam_drag = self.selected
            grab = self.mapToScene(e.pos())
            marker = self.cameras[self.selected][0]
            self._cam_offset = (marker.x() - grab.x(), marker.y() - grab.y())   # keep where it was grabbed
            return
        item = self._point_item_at(e.pos())
        if item and self.pick_handler is None and not self.locked:
            self._candidate = item
            grab = self.mapToScene(e.pos())
            self._drag_offset = (item.x() - grab.x(), item.y() - grab.y())

    @guarded
    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.pos() - self._pan
            self._pan = e.pos()
            self.translate_view(d.x(), d.y())
            return
        if self._cam_drag and e.buttons() & QtCore.Qt.LeftButton:
            g = self.mapToScene(e.pos())
            sp = QtCore.QPointF(g.x() + self._cam_offset[0], g.y() + self._cam_offset[1])
            marker, line = self.cameras[self._cam_drag]
            marker.setPos(sp)
            line.setLine(QtCore.QLineF(self.items[self._cam_drag].pos(), sp))
            item = self.items[self._cam_drag]
            self.statusMessage.emit("Camera of %s: X offset %.1f, Z offset %.1f" % (self._cam_drag, sp.x() - item.x(), sp.y() - item.y()))
            return
        if self._candidate is not None and e.buttons() & QtCore.Qt.LeftButton:
            if self._drag_item is None and (e.pos() - self._press).manhattanLength() >= 5:
                self._drag_item = self._candidate
                self._drag_start = (self._candidate.x(), self._candidate.y())
                self.select(self._candidate.pid)
            if self._drag_item is not None:
                sp = self.mapToScene(e.pos())
                self._drag_item.setPos(sp.x() + self._drag_offset[0], sp.y() + self._drag_offset[1])
                self._update_cameras()
            return
        super().mouseMoveEvent(e)

    @guarded
    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() in (QtCore.Qt.RightButton, QtCore.Qt.MiddleButton):
            self._pan = None
            self.unsetCursor()
            start = getattr(self, "_pan_start", None)
            if e.button() == QtCore.Qt.RightButton and start is not None and (e.pos() - start).manhattanLength() < 4:
                rid = self.route_at(e.pos())
                if rid:
                    self.routeMenu.emit(rid, e.globalPos())
            return
        if e.button() != QtCore.Qt.LeftButton:
            return super().mouseReleaseEvent(e)
        if self._cam_drag:
            pid, self._cam_drag = self._cam_drag, None
            marker = self.cameras[pid][0]
            item = self.items[pid]
            old = self.camera_for(pid)
            self.cameraDragged.emit(pid, (float(marker.x() - item.x()), float(old[1]), float(marker.y() - item.y())))
            return
        if self._drag_item is not None:
            item, start = self._drag_item, self._drag_start
            self._drag_item = self._drag_start = self._candidate = None
            if abs(item.x() - start[0]) + abs(item.y() - start[1]) > 1e-3:
                x, z = item.x(), item.y()
                y = self.world.position(item.pid)[1]
                if self.snap_to_terrain and self.world_scene:
                    h = self.world_scene.height_at(x, z)
                    if h is not None:
                        y = h
                self.pointDragged.emit(item.pid, (float(x), float(y), float(z)))
            return
        self._candidate = None
        if self._press is not None and (e.pos() - self._press).manhattanLength() < 5:
            item = self._point_item_at(e.pos())
            pid = item.pid if item else ""
            rid = None if (pid or self.pick_handler is not None) else self.route_at(e.pos())
            if self.place_handler is not None and not pid:
                sp = self.mapToScene(e.pos())
                h = self.world_scene.height_at(sp.x(), sp.y()) if self.world_scene else None
                self.place_handler((float(sp.x()), float(h if h is not None else 0.0), float(sp.y())))
            elif self.pick_handler is not None:
                if pid:
                    self.pick_handler(pid)
            elif rid:
                self.select("")
                self.select_route(rid)
                self.routeClicked.emit(rid)
            else:
                self.select(pid)
                self.select_route("")
                self.pointClicked.emit(pid)
        self._press = None

    def route_at(self, pos):
        """The route under a view position (its band or badge), or None."""
        from .routeui import pick_route_screen
        segs = []
        for rid, it in self.routes.items():
            if not (it.isVisible() and self.show_routes):
                continue
            l = it.line()
            if l.length() < 1e-6:
                continue
            a, b = self.mapFromScene(l.p1()), self.mapFromScene(l.p2())
            segs.append((rid, (a.x(), a.y()), (b.x(), b.y())))
        return pick_route_screen(segs, pos.x(), pos.y(), 7.0)

    def select_route(self, rid):
        for k, it in self.routes.items():
            if it.selected != (k == rid):
                it.selected = k == rid
                it.setZValue(7 if it.selected else 5)
                it.update()

    def translate_view(self, dx, dy):
        """Pan by a mouse movement in pixels (right- or middle-drag)."""
        c = self.mapToScene(self.viewport().rect().center())
        s = self.transform().m11() or 1.0
        self.centerOn(c.x() - dx / s, c.y() - dy / s)

    @guarded
    def mouseDoubleClickEvent(self, e):
        item = self._point_item_at(e.pos())
        if item:
            self.centerOn(item)

    # ---- overlays ----------------------------------------------------------------------------------------------------
    @guarded
    def drawForeground(self, painter, rect):
        if self.show_grid and self.world:  # grid
            s = self.transform().m11() or 1.0
            step = 100.0
            while step * s < 40:
                step *= 2
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 22), 0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            x = math.floor(rect.left() / step) * step
            while x < rect.right():
                painter.drawLine(QtCore.QLineF(x, rect.top(), x, rect.bottom())); x += step
            z = math.floor(rect.top() / step) * step
            while z < rect.bottom():
                painter.drawLine(QtCore.QLineF(rect.left(), z, rect.right(), z)); z += step
        painter.save()
        painter.resetTransform()
        painter.setPen(QtGui.QColor("#cfe3ff"))
        f = painter.font(); f.setPointSize(9); f.setBold(True); painter.setFont(f)
        if self.world:
            text = "2D map · %s · %s · wheel: zoom · right-drag: pan" % (
                self.world.id, "PICK a point" if self.pick_handler else ("points locked" if self.locked else "drag points to move them"))
        else:
            text = "Open a world to see its 2D map"
        painter.drawText(10, 18, text)
        painter.restore()

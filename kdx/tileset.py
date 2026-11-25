"""
kdx/tileset.py - tile sheets for the World Maker.

The starter tileset is original art drawn by code (no Nintendo graphics): ground, water and hazards, a full set of path
pieces, cliffs and bridges, and decorations with transparent backgrounds (for a Decoration layer on top of the ground).
Any PNG sheet can be used as well: tiles are read left to right, top to bottom.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PyQt5 import QtCore, QtGui

TILE = 32

STARTER_NAMES = [
    # row 0: ground
    "Grass", "Grass (dark)", "Grass with flowers", "Sand", "Dirt", "Snow", "Stone floor", "Meadow",
    # row 1: water and hazards
    "Water", "Deep water", "Water (sparkle)", "Ice", "Lava", "Poison", "Cloud floor", "Night sky",
    # row 2: paths
    "Path ─", "Path │", "Path └ (N-E)", "Path ┘ (N-W)", "Path ┌ (S-E)", "Path ┐ (S-W)", "Path ┼", "Path stop",
    # row 3: paths
    "Path ┴ (no S)", "Path ┬ (no N)", "Path ├ (no W)", "Path ┤ (no E)", "Path end (N)", "Path end (S)", "Path end (E)", "Path end (W)",
    # row 4: cliffs and crossings
    "Cliff edge (N)", "Cliff edge (S)", "Cliff edge (E)", "Cliff edge (W)", "Cliff face", "Bridge ─", "Bridge │", "Stairs",
    # row 5: decoration
    "Round tree", "Pine tree", "Palm tree", "Bush", "Rock", "Boulder", "Flower patch", "Mushroom",
    # row 6: decoration
    "House", "Tower", "Castle", "Pipe", "Fence ─", "Fence │", "Signpost", "Cloud",
    # row 7: decoration
    "Mountain", "Hill", "Snowy pine", "Cactus", "Crystal", "Lantern", "Tent", "Well",
]
STARTER_CATEGORIES = ["Ground"] * 8 + ["Water and hazards"] * 8 + ["Paths"] * 16 + ["Cliffs and crossings"] * 8 + ["Decoration"] * 24


class Tileset:
    def __init__(self, name, image, tile_px=TILE, names=None, categories=None, source="starter"):
        self.name = name
        self.image = image
        self.tile_px = tile_px
        self.columns = max(1, image.width() // tile_px)
        self.rows = max(1, image.height() // tile_px)
        self.count = self.columns * self.rows
        self.names = names or ["Tile %d" % i for i in range(self.count)]
        self.categories = categories or ["Tiles"] * self.count
        self.source = source
        self._cache = {}

    def rect(self, i):
        return QtCore.QRect((i % self.columns) * self.tile_px, (i // self.columns) * self.tile_px, self.tile_px, self.tile_px)

    def pixmap(self, i):
        pm = self._cache.get(i)
        if pm is None:
            pm = self._cache[i] = QtGui.QPixmap.fromImage(self.image.copy(self.rect(i)))
        return pm

    @classmethod
    def from_png(cls, path, tile_px=TILE):
        img = QtGui.QImage(str(path))
        if img.isNull():
            raise ValueError("%s could not be read as an image." % Path(path).name)
        if img.width() < tile_px or img.height() < tile_px:
            raise ValueError("The image is smaller than one tile (%d px)." % tile_px)
        return cls(Path(path).stem, img.convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied), tile_px, source=str(path))

    @classmethod
    def starter(cls):
        return cls("Starter (original art)", _draw_starter(), TILE, STARTER_NAMES, STARTER_CATEGORIES, "starter")


def load(ref, tile_px=TILE):
    if not ref or ref == "starter":
        return Tileset.starter()
    return Tileset.from_png(ref, tile_px)


# --------------------------------------------------------------------------------------------- drawing the starter set
def _c(h):
    return QtGui.QColor(h)


def _noise(p, rect, base, var, rnd, dots=60, size=2):
    p.fillRect(rect, _c(base))
    for _ in range(dots):
        c = _c(var[rnd.randrange(len(var))])
        p.fillRect(rect.x() + rnd.randrange(rect.width() - size + 1), rect.y() + rnd.randrange(rect.height() - size + 1), size, size, c)


def _path(p, r, n=False, s=False, e=False, w=False, stop=False):
    _noise(p, r, "#5fb548", ["#55a840", "#6cc254", "#4f9c3b"], random.Random(7), 40)
    road, edge = _c("#d9b77a"), _c("#b48a50")
    cx, cy, half = r.x() + 16, r.y() + 16, 7
    p.setPen(QtCore.Qt.NoPen)
    for flag, rect in ((n, QtCore.QRect(cx - half, r.y(), 2 * half, 16)), (s, QtCore.QRect(cx - half, cy, 2 * half, 16)),
                       (e, QtCore.QRect(cx, cy - half, 16, 2 * half)), (w, QtCore.QRect(r.x(), cy - half, 16, 2 * half))):
        if flag:
            p.setBrush(edge); p.drawRect(rect.adjusted(-1, -1, 1, 1))
    p.setBrush(edge); p.drawEllipse(QtCore.QPoint(cx, cy), half + 1, half + 1)
    for flag, rect in ((n, QtCore.QRect(cx - half + 1, r.y(), 2 * half - 2, 16)), (s, QtCore.QRect(cx - half + 1, cy, 2 * half - 2, 16)),
                       (e, QtCore.QRect(cx, cy - half + 1, 16, 2 * half - 2)), (w, QtCore.QRect(r.x(), cy - half + 1, 16, 2 * half - 2))):
        if flag:
            p.setBrush(road); p.drawRect(rect)
    p.setBrush(road); p.drawEllipse(QtCore.QPoint(cx, cy), half - 1, half - 1)
    if stop:
        p.setBrush(_c("#fff4d6")); p.setPen(QtGui.QPen(_c("#8a6a3a"), 1.5)); p.drawEllipse(QtCore.QPoint(cx, cy), 5, 5)


def _cliff(p, r, side):
    _noise(p, r, "#5fb548", ["#55a840", "#6cc254"], random.Random(11), 40)
    rock = QtCore.QRect(r)
    if side == "N":
        rock.setHeight(10)
    elif side == "S":
        rock.setTop(r.bottom() - 9)
    elif side == "E":
        rock.setLeft(r.right() - 9)
    else:
        rock.setWidth(10)
    _noise(p, rock, "#8d7b68", ["#7a6a59", "#a08d78", "#6b5c4d"], random.Random(13), 30)


def _tree(p, r, crown, trunk="#7a4f2a", shape="round"):
    cx, cy = r.x() + 16, r.y() + 16
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(_c("#00000040")); p.drawEllipse(QtCore.QPoint(cx + 2, r.y() + 28), 10, 3)
    p.setBrush(_c(trunk)); p.drawRect(cx - 2, cy + 4, 4, 10)
    p.setBrush(_c(crown))
    if shape == "round":
        p.drawEllipse(QtCore.QPoint(cx, cy - 2), 11, 10)
        p.setBrush(_c("#ffffff30")); p.drawEllipse(QtCore.QPoint(cx - 4, cy - 6), 4, 3)
    else:
        for i, w in enumerate((5, 8, 11)):
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx, r.y() + 2 + i * 6), QtCore.QPointF(cx - w, r.y() + 12 + i * 6),
                                           QtCore.QPointF(cx + w, r.y() + 12 + i * 6)]))


def _draw_starter():
    img = QtGui.QImage(8 * TILE, 8 * TILE, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)

    def r(i):
        return QtCore.QRect((i % 8) * TILE, (i // 8) * TILE, TILE, TILE)

    # row 0: ground
    _noise(p, r(0), "#5fb548", ["#55a840", "#6cc254", "#4f9c3b"], random.Random(1), 70)
    _noise(p, r(1), "#3f8f35", ["#367f2d", "#4a9c3f", "#2f7428"], random.Random(2), 70)
    _noise(p, r(2), "#5fb548", ["#55a840", "#6cc254"], random.Random(3), 50)
    rnd = random.Random(4)
    for _ in range(7):
        x, y = r(2).x() + rnd.randrange(4, 28), r(2).y() + rnd.randrange(4, 28)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(["#ffe14d", "#ff7aa8", "#ffffff"][rnd.randrange(3)])); p.drawEllipse(QtCore.QPoint(x, y), 2, 2)
    _noise(p, r(3), "#ecd9a0", ["#e2cc8d", "#f5e6b8", "#d8c07d"], random.Random(5), 80, 1)
    _noise(p, r(4), "#a87b4f", ["#996d43", "#b98b5d", "#8c623b"], random.Random(6), 80)
    _noise(p, r(5), "#f4f8ff", ["#e2ebf7", "#ffffff", "#d6e2f2"], random.Random(7), 60)
    p.fillRect(r(6), _c("#9a9aa2"))
    p.setPen(QtGui.QPen(_c("#7d7d86"), 1))
    for k in (0, 16):
        p.drawLine(r(6).x(), r(6).y() + k, r(6).right(), r(6).y() + k)
        p.drawLine(r(6).x() + k + (8 if k else 0), r(6).y(), r(6).x() + k + (8 if k else 0), r(6).bottom())
    _noise(p, r(7), "#7cc95e", ["#71bd53", "#8ad46b", "#68b04b"], random.Random(8), 70)

    # row 1: water and hazards
    for i, (base, var) in enumerate((("#3d8fe0", ["#357fcf", "#4c9cec"]), ("#2a64b8", ["#2457a3", "#3170c7"]),
                                     ("#3d8fe0", ["#357fcf", "#4c9cec"]), ("#bfe6f5", ["#aedbee", "#d4f0fa"]),
                                     ("#d9480f", ["#f76707", "#c92a2a", "#ffa94d"]), ("#7b3fa6", ["#6a3291", "#8d4fba"]),
                                     ("#f8f9fb", ["#e9edf3", "#ffffff"]), ("#1b2440", ["#232e52", "#141b33"]))):
        _noise(p, r(8 + i), base, var, random.Random(20 + i), 50)
    p.setPen(QtGui.QPen(_c("#ffffffaa"), 1.5))
    for i in (8, 10):
        for k in range(3):
            y = r(i).y() + 8 + k * 9
            p.drawArc(r(i).x() + 4 + k * 5, y, 10, 5, 0, 180 * 16)
    p.setPen(QtCore.Qt.NoPen); p.setBrush(_c("#ffffff"))
    for (dx, dy) in ((8, 8), (22, 14), (14, 24)):
        p.drawEllipse(QtCore.QPoint(r(10).x() + dx, r(10).y() + dy), 1, 1)
    for (dx, dy) in ((6, 6), (20, 10), (12, 22), (26, 26)):
        p.drawEllipse(QtCore.QPoint(r(15).x() + dx, r(15).y() + dy), 1, 1)

    # rows 2-3: paths
    combos = [(0, 0, 1, 1), (1, 1, 0, 0), (1, 0, 1, 0), (1, 0, 0, 1), (0, 1, 1, 0), (0, 1, 0, 1), (1, 1, 1, 1), (0, 0, 0, 0),
              (1, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 0), (1, 1, 0, 1), (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)]
    for k, (n, s, e, w) in enumerate(combos):
        _path(p, r(16 + k), n, s, e, w, stop=(k == 7))

    # row 4: cliffs and crossings
    for k, side in enumerate("NSEW"):
        _cliff(p, r(32 + k), side)
    _noise(p, r(36), "#8d7b68", ["#7a6a59", "#a08d78", "#6b5c4d"], random.Random(40), 70)
    for k, horizontal in ((37, True), (38, False)):
        rr = r(k)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(_c("#9c6b3c"))
        for j in range(4):
            if horizontal:
                p.drawRect(rr.x() + j * 8 + 1, rr.y() + 7, 6, 18)
            else:
                p.drawRect(rr.x() + 7, rr.y() + j * 8 + 1, 18, 6)
        p.setBrush(_c("#6b4524"))
        if horizontal:
            p.drawRect(rr.x(), rr.y() + 6, 32, 2); p.drawRect(rr.x(), rr.y() + 24, 32, 2)
        else:
            p.drawRect(rr.x() + 6, rr.y(), 2, 32); p.drawRect(rr.x() + 24, rr.y(), 2, 32)
    rr = r(39)
    for j in range(4):
        p.fillRect(rr.x() + 4, rr.y() + j * 8, 24, 7, _c("#b0a08e" if j % 2 else "#c9b9a6"))

    # rows 5-7: decoration (transparent background)
    _tree(p, r(40), "#2f9e44")
    _tree(p, r(41), "#1f6f3a", shape="pine")
    rr = r(42); p.setPen(QtGui.QPen(_c("#8b5a2b"), 3)); p.drawLine(rr.x() + 16, rr.y() + 28, rr.x() + 18, rr.y() + 10)
    p.setPen(QtCore.Qt.NoPen); p.setBrush(_c("#37b24d"))
    for ang in range(0, 360, 60):
        p.drawEllipse(QtCore.QPointF(rr.x() + 18 + 7 * math.cos(math.radians(ang)), rr.y() + 9 + 4 * math.sin(math.radians(ang))), 6, 3)
    p.setBrush(_c("#2b8a3e")); p.drawEllipse(QtCore.QPoint(r(43).x() + 16, r(43).y() + 20), 11, 8)
    p.setBrush(_c("#868e96")); p.drawEllipse(QtCore.QPoint(r(44).x() + 16, r(44).y() + 21), 8, 6)
    p.setBrush(_c("#6c757d")); p.drawEllipse(QtCore.QPoint(r(45).x() + 16, r(45).y() + 18), 12, 10)
    rnd = random.Random(46)
    for _ in range(9):
        p.setBrush(_c(["#ffe14d", "#ff7aa8", "#ffffff", "#b197fc"][rnd.randrange(4)]))
        p.drawEllipse(QtCore.QPoint(r(46).x() + rnd.randrange(5, 27), r(46).y() + rnd.randrange(8, 27)), 2, 2)
    rr = r(47); p.setBrush(_c("#f1e3c6")); p.drawRect(rr.x() + 13, rr.y() + 16, 6, 12)
    p.setBrush(_c("#e8590c")); p.drawChord(rr.x() + 5, rr.y() + 4, 22, 22, 0, 180 * 16)
    rr = r(48); p.setBrush(_c("#f8f0e3")); p.drawRect(rr.x() + 7, rr.y() + 14, 18, 14)
    p.setBrush(_c("#c2410c")); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(rr.x() + 4, rr.y() + 15), QtCore.QPointF(rr.x() + 16, rr.y() + 4), QtCore.QPointF(rr.x() + 28, rr.y() + 15)]))
    p.setBrush(_c("#7a4f2a")); p.drawRect(rr.x() + 14, rr.y() + 20, 5, 8)
    rr = r(49); p.setBrush(_c("#adb5bd")); p.drawRect(rr.x() + 10, rr.y() + 8, 12, 21)
    p.setBrush(_c("#495057")); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(rr.x() + 8, rr.y() + 9), QtCore.QPointF(rr.x() + 16, rr.y() + 1), QtCore.QPointF(rr.x() + 24, rr.y() + 9)]))
    rr = r(50); p.setBrush(_c("#ced4da")); p.drawRect(rr.x() + 4, rr.y() + 12, 24, 17)
    p.setBrush(_c("#868e96"))
    for x in (4, 13, 22):
        p.drawRect(rr.x() + x, rr.y() + 6, 6, 8)
    p.setBrush(_c("#343a40")); p.drawRect(rr.x() + 13, rr.y() + 20, 6, 9)
    rr = r(51); p.setBrush(_c("#2f9e44")); p.drawRect(rr.x() + 8, rr.y() + 12, 16, 17)
    p.setBrush(_c("#40c057")); p.drawRect(rr.x() + 5, rr.y() + 6, 22, 8)
    for k, horizontal in ((52, True), (53, False)):
        rr = r(k); p.setBrush(_c("#a9764a"))
        if horizontal:
            p.drawRect(rr.x(), rr.y() + 14, 32, 3); p.drawRect(rr.x(), rr.y() + 21, 32, 3)
            for x in (3, 15, 27):
                p.drawRect(rr.x() + x, rr.y() + 10, 3, 18)
        else:
            p.drawRect(rr.x() + 14, rr.y(), 3, 32); p.drawRect(rr.x() + 21, rr.y(), 3, 32)
            for y in (3, 15, 27):
                p.drawRect(rr.x() + 10, rr.y() + y, 18, 3)
    rr = r(54); p.setBrush(_c("#8b5a2b")); p.drawRect(rr.x() + 15, rr.y() + 14, 3, 15)
    p.setBrush(_c("#d9a066")); p.drawRect(rr.x() + 6, rr.y() + 6, 20, 10)
    rr = r(55); p.setBrush(_c("#ffffff"))
    for (dx, dy, rad) in ((10, 18, 7), (18, 14, 9), (25, 19, 6)):
        p.drawEllipse(QtCore.QPoint(rr.x() + dx, rr.y() + dy), rad, rad - 1)
    rr = r(56); p.setBrush(_c("#868e96")); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(rr.x() + 2, rr.y() + 30), QtCore.QPointF(rr.x() + 16, rr.y() + 3), QtCore.QPointF(rr.x() + 30, rr.y() + 30)]))
    p.setBrush(_c("#f8f9fa")); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(rr.x() + 11, rr.y() + 13), QtCore.QPointF(rr.x() + 16, rr.y() + 3), QtCore.QPointF(rr.x() + 21, rr.y() + 13)]))
    rr = r(57); p.setBrush(_c("#69db7c")); p.drawChord(rr.x() + 1, rr.y() + 10, 30, 36, 0, 180 * 16)
    _tree(p, r(58), "#e7f5ff", shape="pine")
    rr = r(59); p.setBrush(_c("#2f9e44")); p.drawRoundedRect(rr.x() + 13, rr.y() + 5, 6, 24, 3, 3)
    p.drawRoundedRect(rr.x() + 6, rr.y() + 12, 5, 10, 2, 2); p.drawRoundedRect(rr.x() + 21, rr.y() + 9, 5, 10, 2, 2)
    rr = r(60); p.setBrush(_c("#74c0fc")); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(rr.x() + 16, rr.y() + 3), QtCore.QPointF(rr.x() + 24, rr.y() + 16), QtCore.QPointF(rr.x() + 16, rr.y() + 29), QtCore.QPointF(rr.x() + 8, rr.y() + 16)]))
    rr = r(61); p.setBrush(_c("#495057")); p.drawRect(rr.x() + 15, rr.y() + 12, 3, 17)
    p.setBrush(_c("#ffd43b")); p.drawEllipse(QtCore.QPoint(rr.x() + 16, rr.y() + 9), 5, 5)
    rr = r(62); p.setBrush(_c("#e67700")); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(rr.x() + 3, rr.y() + 28), QtCore.QPointF(rr.x() + 16, rr.y() + 5), QtCore.QPointF(rr.x() + 29, rr.y() + 28)]))
    rr = r(63); p.setBrush(_c("#adb5bd")); p.drawEllipse(QtCore.QPoint(rr.x() + 16, rr.y() + 20), 11, 7)
    p.setBrush(_c("#1c7ed6")); p.drawEllipse(QtCore.QPoint(rr.x() + 16, rr.y() + 20), 7, 4)
    p.end()
    return img

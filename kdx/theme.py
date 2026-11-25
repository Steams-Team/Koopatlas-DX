"""
kdx/theme.py - the look of Koopatlas DX: Fusion with a dark or light palette and one style sheet for both.

    theme.apply(app, "dark")        # or "light"; the choice is kept in QSettings ("theme")
    theme.icon("open")              # small drawn icons for the toolbar (no image files needed)
"""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

THEMES = {
    "dark": {"window": "#1b2230", "base": "#121822", "alt": "#1f2736", "button": "#273041", "text": "#e5e9f0",
             "muted": "#8b95a7", "accent": "#d946ef", "accent2": "#a855f7", "border": "#323c50", "tip": "#273041"},
    "light": {"window": "#eef1f6", "base": "#ffffff", "alt": "#f5f7fb", "button": "#e2e7ef", "text": "#1d2433",
              "muted": "#5f6b7d", "accent": "#b026c9", "accent2": "#7c3aed", "border": "#c7cfdb", "tip": "#ffffff"},
}
NAMES = {"dark": "Dark", "light": "Light"}


def current():
    v = str(QtCore.QSettings("KoopatlasDX", "KoopatlasDX").value("theme", "dark") or "dark")
    return v if v in THEMES else "dark"


def colour(dark, light):
    """A QColor that reads on the current theme: dark-theme colour, light-theme colour."""
    return QtGui.QColor(light if current() == "light" else dark)


def palette(name):
    c = THEMES[name]
    pal = QtGui.QPalette()
    P = QtGui.QPalette
    for role, key in ((P.Window, "window"), (P.WindowText, "text"), (P.Base, "base"), (P.AlternateBase, "alt"),
                      (P.Text, "text"), (P.Button, "button"), (P.ButtonText, "text"), (P.Highlight, "accent"),
                      (P.ToolTipBase, "tip"), (P.ToolTipText, "text"), (P.PlaceholderText, "muted"),
                      (P.BrightText, "accent"), (P.Link, "accent2")):
        pal.setColor(role, QtGui.QColor(c[key]))
    pal.setColor(P.HighlightedText, QtGui.QColor("#ffffff"))
    for role in (P.WindowText, P.Text, P.ButtonText):
        pal.setColor(P.Disabled, role, QtGui.QColor(c["muted"]))
    return pal


def style_sheet(name):
    c = THEMES[name]
    return """
QMainWindow::separator {{ background: {border}; width: 3px; height: 3px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {alt}; padding: 5px 8px; border-bottom: 1px solid {border}; font-weight: 600; }}
QToolBar {{ background: {window}; border: none; border-bottom: 1px solid {border}; spacing: 4px; padding: 3px; }}
QToolBar QToolButton {{ padding: 4px 8px; border-radius: 5px; }}
QToolButton:hover {{ background: {button}; }}
QToolButton:checked {{ background: {accent}; color: white; }}
QPushButton {{ background: {button}; border: 1px solid {border}; border-radius: 5px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:pressed {{ background: {accent}; color: white; }}
QPushButton:default {{ border-color: {accent2}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {base}; border: 1px solid {border}; border-radius: 4px; padding: 3px 5px;
    selection-background-color: {accent}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QTreeView, QListView, QTableView {{ background: {base}; border: 1px solid {border}; border-radius: 4px;
    alternate-background-color: {alt}; }}
QTreeView::item, QListView::item {{ padding: 2px 0; }}
QHeaderView::section {{ background: {alt}; border: none; border-right: 1px solid {border};
    border-bottom: 1px solid {border}; padding: 4px 6px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 4px; top: -1px; }}
QTabBar::tab {{ background: {alt}; border: 1px solid {border}; padding: 6px 14px; margin-right: 2px;
    border-top-left-radius: 5px; border-top-right-radius: 5px; }}
QTabBar::tab:selected {{ background: {window}; border-bottom-color: {window}; color: {accent}; }}
QTabBar::tab:hover {{ color: {accent}; }}
QGroupBox {{ border: 1px solid {border}; border-radius: 6px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {muted}; font-weight: 600; }}
QStatusBar {{ background: {alt}; border-top: 1px solid {border}; }}
QStatusBar QLabel {{ color: {muted}; padding: 0 6px; }}
QProgressBar {{ background: {base}; border: 1px solid {border}; border-radius: 4px; text-align: center;
    min-height: 14px; max-height: 16px; }}
QProgressBar::chunk {{ border-radius: 3px; background: {accent2}; }}
QProgressBar[verdict="tight"]::chunk {{ background: #e3a008; }}
QProgressBar[verdict="over"]::chunk {{ background: #e02424; }}
QMenu {{ background: {alt}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {accent}; color: white; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {button}; }}
QToolTip {{ background: {tip}; color: {text}; border: 1px solid {border}; padding: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {border}; }}
QLabel#hint {{ color: {muted}; }}
""".format(**c)


def apply(app, name=None):
    """Set the palette and style sheet ('dark' or 'light'; None: the saved choice)."""
    name = name if name in THEMES else current()
    app.setStyle("Fusion")
    app.setPalette(palette(name))
    app.setStyleSheet(style_sheet(name))
    QtCore.QSettings("KoopatlasDX", "KoopatlasDX").setValue("theme", name)
    _ICONS.clear()
    return name


# ---- icons, drawn ---------------------------------------------------------------------------------------------------
_ICONS = {}


def icon(kind, size=20):
    """A small line icon in the theme's text colour: open, export, undo, redo, map2d, view3d, maker, frame, check,
    models, play, music, gear."""
    key = (kind, size, current())
    if key in _ICONS:
        return _ICONS[key]
    c = THEMES[current()]
    pm = QtGui.QPixmap(size * 2, size * 2)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.scale(2 * size / 20.0, 2 * size / 20.0)
    pen = QtGui.QPen(QtGui.QColor(c["text"]), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    acc = QtGui.QPen(QtGui.QColor(c["accent"]), 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    p.setPen(pen)
    P = QtCore.QPointF
    if kind == "open":
        p.drawPolyline(QtGui.QPolygonF([P(2, 6), P(2, 16), P(16, 16), P(18, 8), P(6, 8), P(4, 16)]))
        p.drawPolyline(QtGui.QPolygonF([P(2, 6), P(2, 4), P(7, 4), P(9, 6), P(15, 6), P(15, 8)]))
    elif kind == "export":
        p.drawRoundedRect(QtCore.QRectF(3, 9, 14, 8), 2, 2)
        p.setPen(acc)
        p.drawLine(P(10, 2), P(10, 12)); p.drawPolyline(QtGui.QPolygonF([P(6, 6), P(10, 2), P(14, 6)]))
    elif kind in ("undo", "redo"):
        if kind == "redo":
            p.translate(20, 0); p.scale(-1, 1)
        path = QtGui.QPainterPath(P(5, 8)); path.cubicTo(P(10, 3), P(18, 6), P(16, 14))
        p.drawPath(path); p.drawPolyline(QtGui.QPolygonF([P(5, 3), P(5, 8), P(10, 8)]))
    elif kind == "map2d":
        p.drawPolygon(QtGui.QPolygonF([P(2, 5), P(7, 3), P(13, 5), P(18, 3), P(18, 15), P(13, 17), P(7, 15), P(2, 17)]))
        p.drawLine(P(7, 3), P(7, 15)); p.drawLine(P(13, 5), P(13, 17))
    elif kind == "view3d":
        p.drawPolygon(QtGui.QPolygonF([P(10, 2), P(17, 6), P(17, 14), P(10, 18), P(3, 14), P(3, 6)]))
        p.drawPolyline(QtGui.QPolygonF([P(3, 6), P(10, 10), P(17, 6)])); p.drawLine(P(10, 10), P(10, 18))
    elif kind == "maker":
        p.drawRoundedRect(QtCore.QRectF(2, 2, 16, 16), 2, 2)
        p.setPen(acc)
        p.drawPolyline(QtGui.QPolygonF([P(5, 14), P(9, 8), P(12, 11), P(15, 5)]))
        p.drawEllipse(P(5, 14), 1.5, 1.5); p.drawEllipse(P(15, 5), 1.5, 1.5)
    elif kind == "frame":
        for x, y, dx, dy in ((2, 2, 1, 1), (18, 2, -1, 1), (2, 18, 1, -1), (18, 18, -1, -1)):
            p.drawPolyline(QtGui.QPolygonF([P(x, y + 5 * dy), P(x, y), P(x + 5 * dx, y)]))
        p.drawEllipse(P(10, 10), 3, 3)
    elif kind == "check":
        p.drawEllipse(P(10, 10), 8, 8)
        p.setPen(acc); p.drawPolyline(QtGui.QPolygonF([P(6, 10), P(9, 13), P(14, 7)]))
    elif kind == "models":
        p.drawRect(QtCore.QRectF(3, 8, 7, 9)); p.drawEllipse(P(14, 13), 4, 4)
        p.setPen(acc); p.drawPolygon(QtGui.QPolygonF([P(6.5, 2), P(10, 7), P(3, 7)]))
    elif kind == "play":
        p.drawEllipse(P(10, 10), 8, 8)
        p.setBrush(QtGui.QColor(c["accent"])); p.setPen(QtCore.Qt.NoPen)
        p.drawPolygon(QtGui.QPolygonF([P(8, 6), P(14, 10), P(8, 14)]))
    else:                                                    # gear
        p.drawEllipse(P(10, 10), 3, 3)
        import math
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(P(10 + 5.5 * math.cos(a), 10 + 5.5 * math.sin(a)), P(10 + 8 * math.cos(a), 10 + 8 * math.sin(a)))
        p.drawEllipse(P(10, 10), 5.5, 5.5)
    p.end()
    ic = QtGui.QIcon(pm)
    _ICONS[key] = ic
    return ic

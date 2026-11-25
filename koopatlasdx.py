#!/usr/bin/env python3
"""Koopatlas DX - world editor for New Super Mario Bros. U Deluxe (Switch). Run: python koopatlasdx.py [world.szs]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    from kdx import diagnostics
    diagnostics.install()                 # errors are logged and shown instead of closing the program
    from PyQt5 import QtCore, QtGui, QtWidgets
    fmt = QtGui.QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Koopatlas DX")
    from kdx import theme
    theme.apply(app)                      # dark or light, as last chosen (View > Theme)
    from kdx.mainwindow import MainWindow
    win = MainWindow()
    win.show()
    QtCore.QTimer.singleShot(0, lambda: win.startup(sys.argv[1] if len(sys.argv) > 1 else None))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

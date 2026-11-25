"""
kdx/diagnostics.py - never let an error silently close Koopatlas DX.

PyQt5 aborts the program when Python code called from Qt (painting, mouse handling) raises an error, unless
sys.excepthook has been replaced. install() replaces it: the error is written to the log and shown in a dialog, and the
program keeps running. faulthandler also writes native crashes (for example in a graphics driver) to the same log.
"""
from __future__ import annotations

import datetime
import faulthandler
import functools
import os
import platform
import sys
import traceback
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "KoopatlasDX"
LOG_FILE = LOG_DIR / "koopatlasdx.log"
_fault_file = None
_showing = False


listeners = []                            # callables(line): the main window's Log panel


def log(text):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), text)
    for fn in list(listeners):
        try:
            fn(line)
        except Exception:
            pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
    except OSError:
        pass


def _hook(etype, value, tb):
    text = "".join(traceback.format_exception(etype, value, tb))
    sys.stderr.write(text)
    log("Unhandled error:\n" + text)
    _show(text)


def _show(text):
    global _showing
    if _showing:
        return
    try:
        from PyQt5 import QtWidgets
        if QtWidgets.QApplication.instance() is None:
            return
        _showing = True
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Koopatlas DX: something went wrong")
        box.setText("An error happened, but Koopatlas DX is still running. If it keeps happening, please send the log "
                    "file:\n%s" % LOG_FILE)
        box.setDetailedText(text)
        box.exec_()
    except Exception:
        pass
    finally:
        _showing = False


def install():
    """Call once, before the window is created."""
    global _fault_file
    sys.excepthook = _hook
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _fault_file = open(LOG_DIR / "native-crash.log", "a", encoding="utf-8")
        faulthandler.enable(_fault_file)
    except (OSError, RuntimeError):
        pass
    try:
        from PyQt5 import QtCore
        qt = QtCore.QT_VERSION_STR
    except Exception:
        qt = "?"
    log("Start: Python %s, Qt %s, %s" % (platform.python_version(), qt, platform.platform()))


def guarded(fn):
    """Decorator for Qt event handlers: report errors instead of letting PyQt5 abort the program."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            _hook(*sys.exc_info())
            return None
    return wrapper

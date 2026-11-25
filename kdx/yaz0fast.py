"""
kdx/yaz0fast.py - FastYZ (MIT, AboodXD; based on FastLZ) through ctypes.

Koopatlas DX ships FastYZ as a small native library in kdx/fastyz/ (fastyz_win64.dll for Windows x64, libfastyz.so for
Linux). It needs no Cython and no compiler, and works with any Python version. If the library cannot be loaded, the
built-in pure-Python Yaz0 code is used instead (slower, same results).
"""
from __future__ import annotations

import ctypes
import struct
import sys
from pathlib import Path

_lib = None
load_error = None


def _candidates():
    if sys.platform.startswith("win"):
        names = ["fastyz_win64.dll"] if struct.calcsize("P") == 8 else []
    elif sys.platform == "darwin":
        names = ["libfastyz.dylib"]
    else:
        names = ["libfastyz.so"]
    folders = [Path(__file__).resolve().parent / "fastyz"]
    if hasattr(sys, "_MEIPASS"):                                   # inside a PyInstaller build
        folders.append(Path(sys._MEIPASS) / "kdx" / "fastyz")
    return [f / n for f in folders for n in names]


def _load():
    global _lib, load_error
    for path in _candidates():
        if not path.exists():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.yaz0_compress.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            lib.yaz0_compress.restype = ctypes.c_int
            lib.yaz0_decompress.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            lib.yaz0_decompress.restype = ctypes.c_int
            lib.yaz0_get_decompressed_size.argtypes = [ctypes.c_void_p]
            lib.yaz0_get_decompressed_size.restype = ctypes.c_uint32
            lib.yaz0_is_valid.argtypes = [ctypes.c_void_p]
            lib.yaz0_is_valid.restype = ctypes.c_int
            _lib = lib
            return
        except (OSError, AttributeError) as e:
            load_error = "%s: %s" % (path.name, e)
    if load_error is None:
        load_error = "no FastYZ library for this system in kdx/fastyz"


_load()


def available():
    return _lib is not None


def compress(data):
    """Yaz0-compress bytes with FastYZ. Raises RuntimeError if FastYZ is not available or fails."""
    if _lib is None:
        raise RuntimeError("FastYZ is not available (%s)." % load_error)
    data = bytes(data)
    if len(data) < 16:
        raise RuntimeError("FastYZ needs at least 16 bytes of input.")
    bound = 16 + len(data) + len(data) // 8 + 1
    src = ctypes.create_string_buffer(data, len(data))
    out = ctypes.create_string_buffer(bound)
    n = _lib.yaz0_compress(src, len(data), out)
    if n <= 0 or n > bound:
        raise RuntimeError("FastYZ compression failed.")
    return out.raw[:n]


def decompress(data):
    """Decompress a Yaz0 stream with FastYZ. Raises RuntimeError if FastYZ is not available or the data is bad."""
    if _lib is None:
        raise RuntimeError("FastYZ is not available (%s)." % load_error)
    data = bytes(data)
    if len(data) < 16:
        raise RuntimeError("Yaz0 data is shorter than its header.")
    src = ctypes.create_string_buffer(data, len(data))
    if not _lib.yaz0_is_valid(src):
        raise RuntimeError("Not valid Yaz0 data.")
    size = _lib.yaz0_get_decompressed_size(src)
    out = ctypes.create_string_buffer(max(1, size))
    n = _lib.yaz0_decompress(src, len(data), out, size)
    if n != size:
        raise RuntimeError("FastYZ decompression failed (%d of %d bytes)." % (n, size))
    return out.raw[:size]

#!/usr/bin/env python3
"""
wmdx_libs.py - find the optional helper libraries Koopatlas DX can use, without ever requiring them.

Koopatlas DX runs on the standard library alone. If you keep helper libraries in a folder (for example your
"Yaz0loom" project folder), this module adds that folder to Python's import path and reports what it found:

    SarcLib      SARC archives (Koopatlas DX has its own reader/writer, so this is only a cross-check)
    fastyz       fast Yaz0 compression (used when saving a world; otherwise a slower built-in compressor is used)
    bntxlib      BNTX texture decoding (for textured previews)

Folders searched, in order: the KDX_LIBS environment variable (several folders separated like PATH),
"kdx_libs.txt" next to the program (one folder per line), the default Yaz0loom folder below, and ./libs.
Only folders that exist are used. Nothing here can break startup: every import is guarded.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

DEFAULT_LIB_DIRS = [str(Path(__file__).resolve().parents[3] / "Yaz0loom")]  # a Yaz0loom folder next to KoopatlasDX/
SUBFOLDERS = ("", "libs", "lib", "Yaz0moto", "Yaz0moto-main", "Yaz0moto-deluxe-support", "src")


def candidate_dirs(program_dir=None):
    program_dir = Path(program_dir or Path(__file__).resolve().parent)
    dirs = []
    env = os.environ.get("KDX_LIBS", "")
    dirs += [d for d in env.split(os.pathsep) if d]
    cfg = program_dir / "kdx_libs.txt"
    if cfg.exists():
        dirs += [l.strip() for l in cfg.read_text(encoding="utf-8", errors="replace").splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
    dirs += DEFAULT_LIB_DIRS
    dirs.append(str(program_dir / "libs"))
    seen, out = set(), []
    for d in dirs:
        for sub in SUBFOLDERS:
            p = Path(d) / sub if sub else Path(d)
            try:
                if p.is_dir() and str(p) not in seen:
                    seen.add(str(p))
                    out.append(p)
            except OSError:
                pass
    return out


def setup(program_dir=None):
    """Add existing library folders to sys.path and report what can be imported. Never raises."""
    added = []
    for p in candidate_dirs(program_dir):
        s = str(p)
        if s not in sys.path:
            sys.path.append(s)                # appended: never shadows the standard library or Koopatlas DX itself
            added.append(s)
    report = {"folders": added, "modules": {}, "compressor": None, "notes": []}

    for name in ("SarcLib", "bntxlib"):
        try:
            importlib.import_module(name)
            report["modules"][name] = True
        except Exception as e:                # ImportError, or the library failing while importing its own dependencies
            report["modules"][name] = False
            report["notes"].append("%s not available (%s: %s)" % (name, type(e).__name__, str(e)[:80]))

    report["compressor"] = _find_fast_compressor(report)
    report["modules"]["fastyz"] = report["compressor"] is not None
    return report


def _find_fast_compressor(report):
    """Return a callable bytes -> Yaz0 bytes using FastYZ if it can be imported, else None."""
    import tempfile
    failures = []
    for source in ("fastyz", "miyamoto.yaz0"):
        try:
            if source == "fastyz":
                mod = importlib.import_module("fastyz")
                comp = getattr(mod, "compressFASTYZ", None)
            else:
                try:                                          # Yaz0moto builds fastyz on the fly through pyximport
                    import pyximport
                    pyximport.install()
                except Exception:
                    pass
                mod = importlib.import_module("miyamoto.yaz0")
                comp = getattr(mod, "compressFASTYZ", None)
            if comp is None:
                continue

            def compress(data, _comp=comp):
                fd, path = tempfile.mkstemp(suffix=".szs")
                os.close(fd)
                try:
                    if not _comp(data, path):
                        raise RuntimeError("FastYZ refused the data")
                    with open(path, "rb") as f:
                        return f.read()
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            return compress
        except Exception as e:
            failures.append("%s not usable (%s: %s)" % (source, type(e).__name__, str(e)[:80]))
    report["notes"].extend(failures)          # only reported when no compressor could be found at all
    return None


def summary(report):
    """One short line for the status panel."""
    mods = report["modules"]
    parts = ["%s %s" % (n, "yes" if mods.get(n) else "no") for n in ("fastyz", "bntxlib", "SarcLib")]
    where = ("searched %d folder(s)" % len(report["folders"])) if report["folders"] else "no library folder found (built-in code is used)"
    return "Optional libraries: " + ", ".join(parts) + " (" + where + ")"


if __name__ == "__main__":
    r = setup()
    print(summary(r))
    for n in r["notes"]:
        print("  -", n)
    for f in r["folders"]:
        print("  folder:", f)

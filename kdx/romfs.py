"""
kdx/romfs.py - find things in a dumped romfs folder.

The user may point at the romfs folder itself, the folder that contains it, or its Model folder; normalize() turns any
of these into the romfs root (the folder that contains Model/).
"""
from __future__ import annotations

import re
from pathlib import Path

WORLD_FILE = re.compile(r"^CS_(W(\d)([ab]?))[._]Nin_NX_NVN\.szs$", re.IGNORECASE)


def _has_worlds(model_dir):
    try:
        return model_dir.is_dir() and any(WORLD_FILE.match(p.name) for p in model_dir.iterdir())
    except OSError:
        return False


def normalize(path):
    """The romfs root for a folder the user picked, or None if no world archives can be found there."""
    if not path:
        return None
    p = Path(path)
    candidates = [p, p / "romfs"]
    if p.name.lower() == "model":
        candidates.insert(0, p.parent)
    for c in candidates:
        for model_name in ("Model", "model"):
            if _has_worlds(c / model_name):
                return c
    return None


def model_dir(root):
    root = Path(root)
    return root / "Model" if (root / "Model").is_dir() else root / "model"


def worlds(root):
    """[(world id, path)] for every world archive with points (CS_W1 … CS_W9b), in world order."""
    out = []
    md = model_dir(root)
    for p in md.iterdir():
        m = WORLD_FILE.match(p.name)
        if m:
            wid = "W" + m.group(2) + m.group(3).lower()
            out.append((wid, p))
    return sorted(out, key=lambda t: (int(t[0][1]), t[0][2:]))


def languages(root):
    msg = Path(root) / "Message"
    if not msg.is_dir():
        return []
    return sorted(p.name[len("CommonMsg_"):-len(".szs")] for p in msg.glob("CommonMsg_*.szs"))


def names_archive(root, language="USen"):
    p = Path(root) / "Message" / ("CommonMsg_%s.szs" % language)
    return p if p.exists() else None


def describe(root):
    """One-paragraph summary of what Koopatlas DX found in a romfs folder."""
    ws = worlds(root)
    md = model_dir(root)
    mario = sum((md / ("MarioOffsetDataW%d.bin" % n)).exists() for n in range(1, 10))
    look = sum((md / ("LookOffsetDataW%d.bin" % n)).exists() for n in range(1, 10))
    langs = languages(root)
    return "%d world archives, MarioOffset tables %d/9, LookOffset tables %d/9, text: %s" % (
        len(ws), mario, look, ", ".join(langs) if langs else "none found (names will not be shown)")

"""
kdx/modelref.py - a model placed in a world: the file, where it stands, and the edits made to it in Koopatlas DX.

A reference is a plain dict saved in the .kdxmap, so the model file itself is never changed:

    {"path": "models/castle.dae", "z_up": False,
     "position": [x, y, z], "rotation": [rx, ry, rz], "scale": [sx, sy, sz],     # degrees; scale may be one number
     "hidden": ["mtShadow"],                                                       # materials left out
     "edits": {"mtWater": {"mode": "translucent", "texture": "water.png", "color": [r, g, b],
                           "flip": True, "double_sided": False}}}

The World Maker's ground model uses the same keys, with "offset" for its position (older maps have only "scale" and
"offset"). build() loads the file once per session (kdx.model3d.load) and returns the edited, placed ObjModel.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

from . import model3d

_cache = {}           # (resolved path, z_up, mtime) -> loaded ObjModel (model space, unedited)


def resolve(ref, base=None):
    """The reference's file as a Path (relative paths are relative to base, the map's folder)."""
    p = Path(ref["path"])
    if not p.is_absolute() and base is not None:
        p = Path(base) / p
    return p


def load_raw(path, z_up=False):
    path = Path(path)
    try:
        key = (str(path.resolve()), bool(z_up), path.stat().st_mtime)
    except OSError:
        key = (str(path), bool(z_up), None)
    if key not in _cache:
        _cache[key] = model3d.load(path, z_up=z_up)
    return _cache[key]


def forget(path=None):
    """Drop cached models (all, or one file's) so they are read again."""
    for k in list(_cache):
        if path is None or k[0] == str(Path(path).resolve()):
            del _cache[k]


def euler_matrix(rotation):
    """4x4 rotation from degrees (x, y, z), applied x first, then y, then z."""
    rx, ry, rz = (math.radians(float(a)) for a in rotation)
    X = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Y = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Z = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    m = np.identity(4)
    m[:3, :3] = Z @ Y @ X
    return m


def scale3(ref):
    s = ref.get("scale", 1.0)
    return [float(s)] * 3 if isinstance(s, (int, float)) else [float(c) for c in s]


def matrix(ref):
    """Model space -> world: translate(position or offset) * rotate * scale."""
    pos = ref.get("position", ref.get("offset", [0.0, 0.0, 0.0]))
    m = euler_matrix(ref.get("rotation", [0.0, 0.0, 0.0]))
    m[:3, :3] = m[:3, :3] @ np.diag(scale3(ref))
    m[:3, 3] = [float(c) for c in pos]
    return m


def apply_edits(model, ref):
    """A copy of a model-space ObjModel with the reference's hidden materials removed and its edits applied."""
    hidden = set(ref.get("hidden", ()))
    edits = ref.get("edits", {})
    parts = []
    for p in model.parts:
        if p.material in hidden:
            continue
        e = edits.get(p.material)
        q = p.copy()
        if e:
            if "mode" in e:
                q.mode = e["mode"]
            if "texture" in e:
                q.texture = e["texture"] or None
            if "color" in e:
                q.color = tuple(float(c) for c in e["color"][:3])
            if "double_sided" in e:
                q.double_sided = bool(e["double_sided"])
            if e.get("flip"):
                order = np.arange(len(q.positions)).reshape(-1, 3)[:, [0, 2, 1]].ravel()
                q.reorder(order)
                q.normals = -q.normals
        parts.append(q)
    out = type(model)(parts, model.path)
    out.flipped = model.flipped
    return out


def build(ref, base=None):
    """The reference's model: loaded, edited and placed in the world (an ObjModel; may have no parts)."""
    raw = load_raw(resolve(ref, base), ref.get("z_up", False))
    return apply_edits(raw, ref).transformed(matrix(ref))


def relative(ref, base):
    """A copy of the reference with its path relative to base when it can be (so map and models can move together)."""
    r = dict(ref)
    if base is not None:
        try:
            r["path"] = os.path.relpath(str(Path(ref["path"]).resolve()), str(Path(base).resolve()))
        except ValueError:                                   # another drive: keep the full path
            pass
    return r

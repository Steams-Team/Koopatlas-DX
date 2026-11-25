"""
kdx/anim.py - play a world's animations: texture scrolling (material animations) and moving scenery (skeletal).

    anims = Animations.load(world)                  # reads them through the model writer (kdx-bfres "anims")
    anims.uv_matrix("CS_W1_World", "mtWater", 37.0)  # 3x3 UV matrix of the material's texmtx0 at frame 37, or None
    anims.bone_matrices(model, 37.0)                 # {bone index: 3x4 world matrix} of the posed skeleton

The model writer (BfresLibrary) decodes the binary animations and hands over each curve raw: keys, frames, type,
scale and offset. Values are offset + scale * key polynomial (cubic: k0 + k1 t + k2 t^2 + k3 t^3, linear: k0 + k1 t,
t = 0..1 across the key), step curves are offset + key. Texture SRT values follow the NintendoWare TexSrt layout
(mode +0, scale +4/+8, rotation +12, translation +16/+20) in the "Maya" convention; bones animate scale +0x04..0x0C,
translation +0x10..0x18 and rotation +0x20..0x2C (Euler XYZ).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import tempfile
from pathlib import Path

from . import bfres

CUBIC, LINEAR = "Cubic", "Linear"
STEPS = ("StepInt", "BakedInt", "BakedFloat", "StepBool", "BakedBool")
SRT_FIELDS = {4: "sx", 8: "sy", 12: "rot", 16: "tx", 20: "ty"}


def evaluate(curve, frame):
    """Value of one curve at a frame (clamped to its keys)."""
    frames, keys = curve["frames"], curve["keys"]
    if not keys:
        return curve.get("offset", 0.0)
    kind, scale, offset = curve["type"], curve.get("scale", 1.0) or 1.0, curve.get("offset", 0.0)
    if kind.startswith("Baked"):
        i = max(0, min(len(keys) - 1, int(frame - curve.get("start", 0.0))))
        return offset + scale * keys[i][0]
    i = max(0, bisect.bisect_right(frames, frame) - 1)
    i = min(i, len(keys) - 1)
    k = keys[i]
    if kind in STEPS:
        return curve.get("offset_int", 0) + k[0] if kind.endswith("Int") else offset + scale * k[0]
    span = (frames[i + 1] - frames[i]) if i + 1 < len(frames) else 0.0
    t = max(0.0, min(1.0, (frame - frames[i]) / span)) if span > 0 else 0.0
    if kind == CUBIC and len(k) >= 4:
        poly = k[0] + t * (k[1] + t * (k[2] + t * k[3]))
    elif len(k) >= 2:
        poly = k[0] + k[1] * t
    else:
        poly = k[0]
    return offset + scale * poly


def srt_matrix(sx=1.0, sy=1.0, rot=0.0, tx=0.0, ty=0.0):
    """3x3 UV matrix of a texture SRT in the Maya convention (rotation and scale about the texture's centre)."""
    c, s = math.cos(rot), math.sin(rot)
    return [[sx * c, sx * s, sx * (-0.5 * c - 0.5 * s + 0.5 - tx)],
            [-sy * s, sy * c, sy * (0.5 * s - 0.5 * c + 0.5 + ty)],
            [0.0, 0.0, 1.0]]


def euler_matrix(rx, ry, rz, scale, trans):
    r = bfres._euler_xyz_to_rot(rx, ry, rz)
    return (r[0][0] * scale[0], r[0][1] * scale[1], r[0][2] * scale[2], trans[0],
            r[1][0] * scale[0], r[1][1] * scale[1], r[1][2] * scale[2], trans[1],
            r[2][0] * scale[0], r[2][1] * scale[1], r[2][2] * scale[2], trans[2])


class Animations:
    def __init__(self, data):
        self.material_anims = data.get("material_anims", [])
        self.skeletal_anims = data.get("skeletal_anims", [])
        # material name -> [(anim, entry)] for texture matrices; model name -> skeletal anim of the same name
        self._srt = {}
        for a in self.material_anims:
            for m in a["materials"]:
                if any(p["param"].startswith(("texmtx", "tex_mtx", "texsrt")) for p in m["params"]):
                    self._srt.setdefault(m["material"], []).append((a, m))
        self._skel = {a["name"]: a for a in self.skeletal_anims}

    @classmethod
    def load(cls, world, run_engine=None):
        """Read a world's animations through the model writer (cached by content). Needs the .NET runtime."""
        return cls.load_bytes(bytes(world.bfres_bytes), run_engine)

    @classmethod
    def load_bytes(cls, data, run_engine=None):
        """The animations of a BFRES given as bytes (e.g. a temp build with pending appear animations)."""
        from . import export2d
        cache = Path(tempfile.gettempdir()) / "kdx_anims"
        cache.mkdir(exist_ok=True)
        out = cache / (hashlib.sha1(data).hexdigest() + ".json")
        if not out.exists():
            src = cache / (out.stem + ".bfres")
            src.write_bytes(data)
            try:
                (run_engine or export2d.run_engine)("anims", src, out)
            finally:
                src.unlink(missing_ok=True)
        return cls(json.loads(out.read_text(encoding="utf-8")))

    # ---- material animations ---------------------------------------------------------------------------------------
    @staticmethod
    def local_frame(anim, frame):
        n = max(1, int(anim.get("frames") or 1))
        return frame % n if anim.get("loop", True) else min(frame, n)

    def animated_materials(self):
        return set(self._srt)

    def srt(self, material, frame, param="texmtx0"):
        """{sx, sy, rot, tx, ty} of a material's texture matrix at a frame, or None if no animation moves it."""
        for anim, entry in self._srt.get(material, []):
            for p in entry["params"]:
                if p["param"].replace("tex_mtx", "texmtx").replace("texsrt", "texmtx") != param:
                    continue
                f = self.local_frame(anim, frame)
                v = {"sx": 1.0, "sy": 1.0, "rot": 0.0, "tx": 0.0, "ty": 0.0}
                for c in p["constants"]:
                    if c["target"] in SRT_FIELDS:
                        v[SRT_FIELDS[c["target"]]] = c["value"]
                for c in p["curves"]:
                    if c["target"] in SRT_FIELDS:
                        v[SRT_FIELDS[c["target"]]] = evaluate(c, f)
                return v
        return None

    def uv_matrix(self, material, frame):
        v = self.srt(material, frame)
        return None if v is None else srt_matrix(**v)

    # ---- skeletal animations --------------------------------------------------------------------------------------
    def skeletal_for(self, model_name):
        return self._skel.get(model_name)

    def bone_matrices(self, model, frame, anim=None):
        """World matrices (3x4 tuples) of every bone of a model posed at a frame by its skeletal animation (the one
        named like the model, or `anim`). Bones the animation does not move keep their rest pose."""
        anim = anim or self._skel.get(model.name)
        by_name = {b["bone"]: b for b in anim["bones"]} if anim else {}
        f = self.local_frame(anim, frame) if anim else 0.0
        world = []
        for i, bone in enumerate(model.bones):
            ba = by_name.get(bone.name)
            if ba is None:
                local = bone.local_matrix()
            else:
                s, r, t = list(bone.scale), list(bone.rotation[:3]), list(bone.position)
                if ba["flags_base"] & (1 << 3):
                    s = list(ba["base_scale"])
                if ba["flags_base"] & (1 << 4):
                    r = list(ba["base_rotate"][:3])
                if ba["flags_base"] & (1 << 5):
                    t = list(ba["base_translate"])
                for c in ba["curves"]:
                    tgt, val = c["target"], evaluate(c, f)
                    if 0x04 <= tgt <= 0x0C:
                        s[(tgt - 0x04) // 4] = val
                    elif 0x10 <= tgt <= 0x18:
                        t[(tgt - 0x10) // 4] = val
                    elif 0x20 <= tgt <= 0x28:
                        r[(tgt - 0x20) // 4] = val
                local = euler_matrix(r[0], r[1], r[2], s, t)
            parent = bone.parent
            world.append(bfres._mat_mul(world[parent], local) if 0 <= parent < i else local)
        return world

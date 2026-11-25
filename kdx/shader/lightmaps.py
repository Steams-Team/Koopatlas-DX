"""
kdx/shader/lightmaps.py - the light maps the game makes at run time (agl light maps), ported from Yaz0loom-DX.

The world-map materials sample two sphere maps by view-space normal: lmDifCSW1a (diffuse) and lmSpcCSW1a (specular).
The copies inside the BFRES are 16x16 grey placeholders; the game renders the real ones from Env/env_pack.szs:
CS_All.bagllmap lists, per light map, which lights it sums (Lambert / Half-Lambert, effect, power), and the
CS_W*.baglenv environments hold those lights (named like the light maps they feed).

    defs = world_lightmap_defs(romfs_root)       {light map name: (LightmapDef, lights)}  ({} without env_pack)
    rgba = bake_lightmap(defn, lights, view_rot)  float32 RGBA sphere map for the camera's rotation (world -> view)

The maps are view dependent (agl renders them every frame with the lights turned into view space), so the renderer
bakes them again when the camera turns.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import aamp


@dataclass
class Light:
    kind: str
    name: str
    enable: bool
    diffuse: tuple
    backside: tuple
    intensity: float
    direction: tuple
    sky: tuple = (1, 1, 1, 1)
    ground: tuple = (0, 0, 0, 1)


@dataclass
class LightmapEntry:
    kind: str
    name: str
    lut: str
    effect: float
    power: float
    mip0: bool


@dataclass
class LightmapDef:
    name: str
    settings: dict
    entries: list


def parse_env(data):
    pl = aamp.parse(data)
    lights = []
    for kind in ("AmbientLight", "DirectionalLight", "HemisphereLight"):
        lst = pl.list(kind)
        if lst is None:
            continue
        for o in lst.objs:
            lights.append(Light(kind, o.get("name", ""), o.get("enable", True),
                                tuple(o.get("DiffuseColor", o.get("Color", (1, 1, 1, 1)))),
                                tuple(o.get("BacksideColor", (0, 0, 0, 1))), o.get("Intensity", 1.0),
                                tuple(o.get("Direction", (0, -1, 0))),
                                tuple(o.get("SkyColor", (1, 1, 1, 1))), tuple(o.get("GroundColor", (0, 0, 0, 1)))))
    return lights


def parse_lightmaps(data):
    pl = aamp.parse(data)
    out = []
    for lst in pl.lists:
        st, arr = lst.obj("setting"), lst.list("env_obj_ref_array")
        if st is None or arr is None:
            continue
        entries = [LightmapEntry(o.get("type", ""), o.get("name", ""), o.get("lut_name", "Lambert"),
                                 o.get("effect", 1.0), o.get("pow", 1.0), o.get("enable_mip0", True)) for o in arr.objs]
        keys = ("name", "hdr_enable", "rim_enable", "rim_light_ref", "rim_effect", "rim_width", "rim_pow")
        settings = {k: st.get(k) for k in keys if st.get(k) is not None}      # looked up by CRC32 of the name
        out.append(LightmapDef(st.get("name", ""), settings, entries))
    return out


def bake_lightmap(defn, lights, view_rot=None, size=32):
    """A sphere map holding the summed light for every view-space normal. Lights are matched by name; their world
    directions are turned into view space by view_rot. LDR (0..1) unless the map's hdr_enable is set."""
    R = np.eye(3) if view_rot is None else np.asarray(view_rot, float)[:3, :3]
    by_name = {l.name: l for l in lights if l.enable and l.name}
    ys, xs = np.mgrid[0:size, 0:size]
    nx = (xs + 0.5) / size * 2 - 1
    ny = 1 - (ys + 0.5) / size * 2
    nz = np.sqrt(np.clip(1 - nx * nx - ny * ny, 0, 1))
    n = np.stack([nx, ny, nz], -1)
    rgb = np.zeros((size, size, 3))
    for e in defn.entries:
        if not e.effect or not e.mip0:
            continue
        l = by_name.get(e.name)
        if l is None:
            continue
        if e.kind == "AmbientLight":
            rgb += np.array(l.diffuse[:3]) * l.intensity * e.effect
            continue
        d = R @ np.array(l.direction, float)
        to_light = -d / (np.linalg.norm(d) or 1)
        if e.kind == "HemisphereLight":
            t = n @ to_light * 0.5 + 0.5
            rgb += (np.array(l.sky[:3]) * t[..., None] + np.array(l.ground[:3]) * (1 - t[..., None])) * \
                l.intensity * e.effect
            continue
        t = n @ to_light
        f = t * 0.5 + 0.5 if e.lut == "Half-Lambert" else np.maximum(t, 0)
        f = np.power(np.clip(f, 0, 1), max(e.power, 1e-3))
        col = np.array(l.diffuse[:3]) * l.intensity * e.effect
        back = np.array(l.backside[:3]) * e.effect
        rgb += col * f[..., None] + back * (1 - f[..., None])
    st = defn.settings
    if st.get("rim_enable") and st.get("rim_light_ref"):
        l = by_name.get(st["rim_light_ref"])
        if l is not None:
            rim = np.power(np.clip(1 - nz, 0, 1) / max(st.get("rim_width", 1.0), 1e-3), st.get("rim_pow", 2.0))
            rgb += np.array(l.diffuse[:3]) * np.clip(rim, 0, 1)[..., None] * st.get("rim_effect", 1.0)
    a = np.ones((size, size, 4), np.float32)
    a[..., :3] = np.clip(rgb, 0, 4 if st.get("hdr_enable") else 1)
    return a


_CACHE = {}


def find_env_pack(*roots):
    """Path of Env/env_pack.szs under the first romfs root (or a folder inside one) that has it, or None."""
    for r in roots:
        if not r:
            continue
        p = Path(r)
        for cand in [p] + list(p.parents)[:4]:
            f = cand / "Env" / "env_pack.szs"
            if f.is_file():
                return f
    return None


def world_lightmap_defs(*roots):
    """{name: (LightmapDef, lights)} for the lmDif* / lmSpc* maps of CS_All.bagllmap. lmDifCSW1a is lit by CS_W1a's
    lights first (names repeat between environments), then by every other CS_* environment's."""
    path = find_env_pack(*roots)
    if path is None:
        return {}
    key = (str(path), path.stat().st_mtime)
    if key in _CACHE:
        return _CACHE[key]
    from .. import archive
    files = {Path(n).name: d for n, d in archive.read_szs(path)[0]}
    envs = {}
    for name, data in sorted(files.items()):
        if name.endswith(".baglenv") and name.lower().startswith("cs_"):
            try:
                envs[name[:-8].replace("_", "").lower()] = parse_env(data)
            except Exception:
                continue
    every = [l for ls in envs.values() for l in ls]
    out = {}
    defs = files.get("CS_All.bagllmap")
    if defs is not None:
        for d in parse_lightmaps(defs):
            if d.name.startswith(("lmDif", "lmSpc")):
                out[d.name] = (d, every + envs.get(d.name[5:].lower(), []))     # later entries win
    _CACHE[key] = out
    return out

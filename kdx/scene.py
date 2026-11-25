"""
kdx/scene.py - what both views need from a world's models, built once per world.

    scene = WorldScene(world)
    scene.meshes            {model name: (n*3, 9) float32 array: position, normal, colour per vertex}
    scene.height_at(x, z)   highest terrain surface at (x, z) or None
    scene.render_topdown()  QImage of the world seen from straight above, and the world rectangle it covers

The top-down picture is drawn with QPainter (no OpenGL needed): triangles facing up, lowest first, so higher ground
covers lower ground, with the same flat lighting as the 3D view.
"""
from __future__ import annotations

import hashlib

import numpy as np

from . import bfres

LIGHT = np.array([0.35, 0.85, 0.4]) / np.linalg.norm([0.35, 0.85, 0.4])


def material_color(model_name, mat_name):
    if model_name.startswith("R"):
        return (0.98, 0.50, 0.15)
    if "Synchro" in model_name:
        base = [(0.42, 0.68, 0.38), (0.55, 0.75, 0.42), (0.75, 0.62, 0.40)]
    elif model_name.lower().startswith("cs_w") and model_name.count("_") == 1:
        return (0.85, 0.85, 0.90)                      # the point markers
    else:
        base = [(0.55, 0.72, 0.45), (0.74, 0.66, 0.46), (0.52, 0.64, 0.80), (0.70, 0.56, 0.48),
                (0.62, 0.70, 0.55), (0.45, 0.66, 0.72), (0.80, 0.76, 0.56), (0.62, 0.60, 0.64)]
    return base[int(hashlib.md5(mat_name.encode("utf-8", "replace")).hexdigest(), 16) % len(base)]


def mesh_arrays(model):
    """Flat-shaded triangle soup for one model: float32 (n*3, 9) array, or None if it has no triangles."""
    m = bfres.build_mesh(model)
    if not m.faces:
        return None
    V = np.asarray(m.vertices, np.float32)
    F = np.asarray([f[:3] for f in m.faces], np.int64)
    tri = V[F]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    cache = {}
    cols = np.empty((len(F), 3), np.float32)
    for i, f in enumerate(m.faces):
        c = cache.get(f[3])
        if c is None:
            c = cache[f[3]] = material_color(model.name, f[3])
        cols[i] = c
    data = np.empty((len(F), 3, 9), np.float32)
    data[:, :, 0:3] = tri
    data[:, :, 3:6] = n[:, None, :]
    data[:, :, 6:9] = cols[:, None, :]
    return data.reshape(-1, 9)


class Batch:
    """One material's triangles in a model's textured vertex array."""

    def __init__(self, material, first, count):
        self.material = material.name if material else "Default"
        self.first, self.count = first, count
        self.albedo = material.albedo if material else None
        self.mode = material.blend_mode if material else "opaque"
        self.cull = material.cull if material else "back"
        self.alpha_ref = material.alpha_ref if material else 0.5
        self.samplers = list(material.samplers) if material else []
        self.shadow = False       # a ground-shadow decal (set by the viewport once it has seen the texture: is_shadow_decal)


def is_shadow_decal(batch, tex):
    """Whether a material is a shadow cast on the ground, like World 1's mtLeafDonguriShadow under the acorn trees: alpha
    tested, an albedo sampler and nothing else (no lightmaps, no bake), and a one-channel mask texture (BC4 with every
    channel reading red). Drawn as stored, the mask is white (the white rings under the trees); the game's shader lights
    it with nothing, so it shows as a dark patch. The water-edge foam uses the same kind of texture but is translucent
    (and white in the game), so it does not match."""
    return (batch.mode == "mask" and batch.samplers == ["_a0"] and tex is not None
            and len(set(tex.channels)) == 1)


ORDER = {"opaque": 0, "mask": 1, "translucent": 2}


def textured_arrays(model):
    """(float32 (n, 11) array: position, normal, colour, uv per vertex; [Batch] sorted opaque, mask, translucent;
    int32 (n,) bone index per vertex, -1 for model space), or None. Normals are the model's own (_n0) where it has
    them, else the face normal."""
    m = bfres.build_mesh(model)
    if not m.faces:
        return None
    mats = {mat.name: mat for mat in model.materials}
    V = np.asarray(m.vertices, np.float32)
    UV = np.asarray(m.uvs, np.float32) if len(m.uvs) == len(m.vertices) else np.zeros((len(V), 2), np.float32)
    have = np.array([n is not None for n in m.normals]) if len(m.normals) == len(m.vertices) else np.zeros(len(V), bool)
    N = np.zeros((len(V), 3), np.float32)
    if have.any():
        N[have] = np.asarray([n for n in m.normals if n is not None], np.float32)
    groups = {}
    for f in m.faces:
        groups.setdefault(f[3], []).append(f[:3])
    order = sorted(groups, key=lambda k: (ORDER.get(mats[k].blend_mode if k in mats else "opaque", 0), k))
    B = np.asarray(m.bone_of, np.int32) if len(m.bone_of) == len(m.vertices) else np.full(len(V), -1, np.int32)
    parts, bones, batches, first = [], [], [], 0
    for name in order:
        F = np.asarray(groups[name], np.int64)
        tri = V[F]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        nrm = np.where(have[F][..., None], N[F], fn[:, None, :])
        d = np.empty((len(F), 3, 11), np.float32)
        d[..., 0:3], d[..., 3:6] = tri, nrm
        d[..., 6:9] = material_color(model.name, name)
        d[..., 9:11] = UV[F]
        parts.append(d.reshape(-1, 11))
        bones.append(B[F].reshape(-1))
        batches.append(Batch(mats.get(name), first, len(F) * 3))
        first += len(F) * 3
    return np.concatenate(parts), batches, np.concatenate(bones)


class WorldScene:
    def __init__(self, world):
        self.world = world
        self.meshes = {}
        self.errors = {}
        self._textured = {}
        self._textures = None
        for model in world.model.models:
            try:
                data = mesh_arrays(model)
            except Exception as e:                          # one damaged model must not hide the rest
                self.errors[model.name] = "%s: %s" % (type(e).__name__, e)
                continue
            if data is not None:
                self.meshes[model.name] = data
        terrain = [d[:, 0:3].reshape(-1, 3, 3) for name, d in self.meshes.items() if name.endswith("_World")]
        self.terrain = np.concatenate(terrain).astype(np.float64) if terrain else None
        self._topdown = {}

    def textured(self, name):
        """textured_arrays() of a model (cached), or None."""
        if name not in self._textured:
            model = next((m for m in self.world.model.models if m.name == name), None)
            try:
                self._textured[name] = textured_arrays(model) if model else None
            except Exception as e:
                self.errors[name] = "%s: %s" % (type(e).__name__, e)
                self._textured[name] = None
        return self._textured[name]

    def textures(self):
        """{name: kdx.texture.Texture} of the world's BFRES (decoded on first use of each)."""
        if self._textures is None:
            from . import texture
            try:
                self._textures = texture.read_bntx(self.world.model.external_bytes("textures.bntx"))
            except Exception as e:
                self.errors["textures"] = "%s: %s" % (type(e).__name__, e)
                self._textures = {}
        return self._textures

    def triangle_count(self, name):
        d = self.meshes.get(name)
        return 0 if d is None else len(d) // 3

    def height_at(self, x, z):
        """Highest terrain surface at (x, z), or None (vertical ray cast against the *_World models)."""
        T = self.terrain
        if T is None:
            return None
        a, b, c = T[:, 0], T[:, 1], T[:, 2]
        v0x, v0z = c[:, 0] - a[:, 0], c[:, 2] - a[:, 2]
        v1x, v1z = b[:, 0] - a[:, 0], b[:, 2] - a[:, 2]
        v2x, v2z = x - a[:, 0], z - a[:, 2]
        den = v0x * v1z - v1x * v0z
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (v2x * v1z - v1x * v2z) / den
            v = (v0x * v2z - v2x * v0z) / den
            inside = (np.abs(den) > 1e-9) & (u >= 0) & (v >= 0) & (u + v <= 1)
        if not inside.any():
            return None
        hy = a[inside, 1] + u[inside] * (c[inside, 1] - a[inside, 1]) + v[inside] * (b[inside, 1] - a[inside, 1])
        return float(hy.max())

    def bounds_xz(self, include_points=True):
        pts = []
        for d in self.meshes.values():
            pts.append(d[:, [0, 2]].min(axis=0)); pts.append(d[:, [0, 2]].max(axis=0))
        if include_points:
            for name in list(self.world.point_ids) + list(self.world.waypoint_ids):
                p = self.world.position(name)
                if p:
                    pts.append(np.array([p[0], p[2]]))
        if not pts:
            return (-100.0, -100.0, 100.0, 100.0)
        a = np.asarray(pts)
        lo, hi = a.min(axis=0), a.max(axis=0)
        return (float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1]))

    def render_topdown(self, hidden=(), max_px=3072):
        """(QImage, (x0, z0, x1, z1)) of the visible models seen from above. Cached per set of hidden models."""
        from PyQt5 import QtCore, QtGui
        key = (tuple(sorted(hidden)), max_px)
        if key in self._topdown:
            return self._topdown[key]
        x0, z0, x1, z1 = self.bounds_xz(include_points=False)
        pad = max(x1 - x0, z1 - z0) * 0.04 + 10
        x0, z0, x1, z1 = x0 - pad, z0 - pad, x1 + pad, z1 + pad
        scale = max_px / max(x1 - x0, z1 - z0)
        w, h = max(1, int((x1 - x0) * scale)), max(1, int((z1 - z0) * scale))
        img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtGui.QColor(18, 26, 38))
        tris = [d.reshape(-1, 3, 9) for name, d in self.meshes.items() if name not in hidden]
        self.render_error = None
        try:
            self._paint_topdown(img, tris, x0, z0, scale)
        except Exception as e:                                   # a blank picture is better than no 2D map
            import traceback
            self.render_error = "%s: %s" % (type(e).__name__, e)
            traceback.print_exc()
        result = (img, (x0, z0, x1, z1))
        self._topdown[key] = result
        return result

    def _paint_topdown(self, img, tris, x0, z0, scale):
        from PyQt5 import QtCore, QtGui
        if tris:
            T = np.concatenate(tris)
            up = T[:, 0, 4] > 0.02                                   # only triangles facing up are seen from above
            T = T[up]
            order = np.argsort(T[:, :, 1].max(axis=1))               # low first, high last
            T = T[order]
            shade = 0.38 + 0.62 * np.abs(T[:, 0, 3:6] @ LIGHT)
            rgb = np.clip(T[:, 0, 6:9] * shade[:, None] * 255, 0, 255).astype(np.int32).tolist()
            # plain Python numbers: Qt is not given numpy types (their conversion differs between PyQt builds)
            px = ((T[:, :, 0].astype(np.float64) - x0) * scale).tolist()
            pz = ((T[:, :, 2].astype(np.float64) - z0) * scale).tolist()
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            p.setPen(QtCore.Qt.NoPen)
            last = None
            try:
                for i in range(len(px)):
                    c = rgb[i]
                    if c != last:
                        p.setBrush(QtGui.QColor(c[0], c[1], c[2]))
                        last = c
                    a, b = px[i], pz[i]
                    p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(a[0], b[0]), QtCore.QPointF(a[1], b[1]), QtCore.QPointF(a[2], b[2])]))
            finally:
                p.end()

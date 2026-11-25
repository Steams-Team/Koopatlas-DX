"""
kdx/objmodel.py - 3D models for custom worlds, from Wavefront OBJ files (with their MTL materials and textures).

    model = ObjModel.load("island.obj")                 # parts per material, in the file's units
    placed = model.placed(scale=2.0, offset=(0, 0, 0))   # the same, scaled and moved into world units
    placed.height_at(x, z)                               # surface height under a point (highest hit), or None

OBJ is what almost every 3D program exports (Blender, Maya, 3ds Max, SketchUp...). Faces of any size are split into
triangles (fan), negative indices are honoured, and a file with no normals gets face normals. OBJ texture coordinates
start at the bottom left; the game's start at the top left, so v is flipped. Files made "Z up" can be turned to Y up.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


class ObjError(ValueError):
    pass


class Part:
    """The triangles of one material: per-corner arrays (3 rows per triangle)."""

    def __init__(self, material, positions, normals, uvs, texture=None, color=(0.8, 0.8, 0.8), mode="opaque",
                 double_sided=False, colors=None):
        self.material = material
        self.positions, self.normals, self.uvs = positions, normals, uvs       # float32 (n*3, 3/3/2)
        self.texture = texture                    # path of the albedo image (map_Kd) or None
        self.color = color                        # Kd, used when there is no texture
        self.mode = mode                          # "opaque", "mask" (alpha cut-out) or "translucent" (alpha blend)
        self.double_sided = double_sided          # the source drew both faces (the game draws front faces only)
        self.colors = colors                      # float32 (n*3, 4) vertex colours, or None (white)
        self.wrap = ("repeat", "repeat")          # texture wrap across u and v: repeat, clamp or mirror
        # NSMBW paths: the reveal mask's UVs (float32 (n*3, 2)) and its hidden offset (the mask SRT's v translate). The
        # game hides a material mt<route><a-h> at that offset and sweeps its mask open when the route opens.
        self.uvs2 = None
        self.mask_offset = None

    def reorder(self, order):
        """Corners in a new order (a winding flip): every per-corner array follows."""
        self.positions, self.normals, self.uvs = self.positions[order], self.normals[order], self.uvs[order]
        if self.colors is not None:
            self.colors = self.colors[order]
        if self.uvs2 is not None:
            self.uvs2 = self.uvs2[order]

    @property
    def triangle_count(self):
        return len(self.positions) // 3

    def copy(self, positions=None, normals=None):
        p = Part(self.material, self.positions if positions is None else positions,
                 self.normals if normals is None else normals, self.uvs, self.texture, self.color, self.mode,
                 self.double_sided, self.colors)
        p.wrap, p.uvs2, p.mask_offset = self.wrap, self.uvs2, self.mask_offset
        return p


class ObjModel:
    def __init__(self, parts, path=None):
        self.parts = parts
        self.path = path
        self.flipped = 0          # triangles turned round on loading because they faced inwards (_face_outwards)

    # ---- reading ---------------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path, z_up=False):
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise ObjError("%s cannot be read: %s" % (path.name, e))
        v, vt, vn = [], [], []
        faces = {}                                # material -> [(vi, ti, ni) * 3]
        mats = {}
        current = "default"
        for line in text.splitlines():
            tok = line.split()
            if not tok or tok[0].startswith("#"):
                continue
            key = tok[0]
            if key == "v" and len(tok) >= 4:
                v.append(tuple(float(x) for x in tok[1:4]))
            elif key == "vt" and len(tok) >= 3:
                vt.append((float(tok[1]), float(tok[2])))
            elif key == "vn" and len(tok) >= 4:
                vn.append(tuple(float(x) for x in tok[1:4]))
            elif key == "usemtl":
                current = " ".join(tok[1:]) or "default"
            elif key == "mtllib":
                for lib in tok[1:]:
                    mats.update(read_mtl(path.parent / lib))
            elif key == "f" and len(tok) >= 4:
                corners = []
                for c in tok[1:]:
                    parts = (c.split("/") + ["", ""])[:3]
                    idx = []
                    for s, n in zip(parts, (len(v), len(vt), len(vn))):
                        if not s:
                            idx.append(None)
                        else:
                            i = int(s)
                            idx.append(i - 1 if i > 0 else n + i)
                    corners.append(tuple(idx))
                tri = faces.setdefault(current, [])
                for k in range(1, len(corners) - 1):                 # fan triangulation
                    tri.extend((corners[0], corners[k], corners[k + 1]))
        if not v or not faces:
            raise ObjError("%s has no faces (is it a Wavefront OBJ file?)." % path.name)
        V = np.asarray(v, np.float32)
        T = np.asarray(vt, np.float32) if vt else np.zeros((1, 2), np.float32)
        N = np.asarray(vn, np.float32) if vn else None
        if z_up:                                                     # (x, y, z) Z up -> (x, z, -y) Y up
            V = V[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32)
            if N is not None:
                N = N[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32)
        parts = []
        for name, corners in faces.items():
            ci = np.asarray([[c[0], c[1] if c[1] is not None else -1, c[2] if c[2] is not None else -1]
                             for c in corners], np.int64)
            if (ci[:, 0] < 0).any() or (ci[:, 0] >= len(V)).any():
                raise ObjError("%s: a face of material %s uses a vertex that does not exist." % (path.name, name))
            pos = V[ci[:, 0]]
            uv = np.where((ci[:, 1:2] >= 0) & (ci[:, 1:2] < len(T)), T[np.clip(ci[:, 1], 0, len(T) - 1)], 0.0)
            uv = np.c_[uv[:, 0], 1.0 - uv[:, 1]].astype(np.float32)          # OBJ v runs upwards, the game's downwards
            given = np.zeros(len(ci), bool)
            nrm = np.zeros((len(ci), 3), np.float32)
            if N is not None:
                given = (ci[:, 2] >= 0) & (ci[:, 2] < len(N))
                nrm[given] = N[ci[given, 2]]
            m = mats.get(name, {})
            parts.append(Part(name, pos.astype(np.float32), nrm, uv, m.get("texture"), m.get("color", (0.8, 0.8, 0.8))))
            parts[-1]._given = given
        model = cls(parts, path)
        model.flipped = model._face_outwards()
        return model

    def _face_outwards(self):
        """Make every triangle wind counter-clockwise seen from its front, as the game draws them: world materials draw
        front faces only (gsys_render_state_display_face "front"), so an inside-out model shows its far walls from the
        inside and none of its top (test mod 9). With normals in the file, a triangle whose winding disagrees with them
        is turned round; without, the whole model is turned round when it is inside out (negative volume, measured from
        below it so that an open sheet of ground counts as well). Face normals are filled in afterwards. Returns the
        number of triangles turned round."""
        allp = np.concatenate([p.positions for p in self.parts])
        lo, hi = allp.min(axis=0), allp.max(axis=0)
        ref = np.array([(lo[0] + hi[0]) / 2, lo[1] - max(float(hi[1] - lo[1]), 1.0), (lo[2] + hi[2]) / 2], np.float32)
        faces = [np.cross(p.positions.reshape(-1, 3, 3)[:, 1] - p.positions.reshape(-1, 3, 3)[:, 0],
                          p.positions.reshape(-1, 3, 3)[:, 2] - p.positions.reshape(-1, 3, 3)[:, 0]) for p in self.parts]
        def volume(t):
            return float(np.einsum("ij,ij->i", t[:, 0] - ref, np.cross(t[:, 1] - ref, t[:, 2] - ref)).sum())
        flips = []
        if any(p._given.any() for p in self.parts):
            for p, fn in zip(self.parts, faces):
                g = p._given.reshape(-1, 3)
                avg = (p.normals.reshape(-1, 3, 3) * g[:, :, None]).sum(axis=1)
                flip = g.any(axis=1) & ((fn * avg).sum(axis=1) < 0)
                if not g.any():
                    # a part without normals among parts with them (NSMBW's water plane): the volume rule for it alone,
                    # so a sheet facing down (the Wii's clockwise winding) is turned up instead of culled from above
                    flip = np.full(len(fn), volume(p.positions.reshape(-1, 3, 3)) < 0)
                flips.append(flip)
        else:
            vol = sum(volume(p.positions.reshape(-1, 3, 3)) for p in self.parts)
            flips = [np.full(len(fn), vol < 0) for fn in faces]
        turned = 0
        for p, fn, flip in zip(self.parts, faces, flips):
            order = np.arange(len(p.positions)).reshape(-1, 3)
            order[flip] = order[flip][:, [0, 2, 1]]
            order = order.ravel()
            p.reorder(order)
            p._given = p._given[order]
            fn = np.where(flip[:, None], -fn, fn)
            fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
            face = np.repeat(fn, 3, axis=0).astype(np.float32)
            p.normals = np.where(p._given[:, None], p.normals, face).astype(np.float32)
            turned += int(flip.sum())
        return turned

    # ---- placing ---------------------------------------------------------------------------------------------------
    def bounds(self):
        allp = np.concatenate([p.positions for p in self.parts])
        return allp.min(axis=0), allp.max(axis=0)

    def placed(self, scale=1.0, offset=(0.0, 0.0, 0.0)):
        """A copy in world units: positions * scale + offset."""
        off = np.asarray(offset, np.float32)
        placed = ObjModel([p.copy(positions=p.positions * np.float32(scale) + off) for p in self.parts], self.path)
        placed.flipped = self.flipped
        return placed

    def transformed(self, matrix):
        """A copy with every part moved by a 4x4 matrix (normals by its inverse transpose). A mirroring matrix
        (negative determinant) would turn the faces inside out, so their winding is reversed to keep them facing out."""
        m = np.asarray(matrix, np.float64)
        lin, t = m[:3, :3], m[:3, 3]
        nm = np.linalg.inv(lin).T if abs(np.linalg.det(lin)) > 1e-12 else lin
        mirror = np.linalg.det(lin) < 0
        parts = []
        for p in self.parts:
            pos = (p.positions.astype(np.float64) @ lin.T + t).astype(np.float32)
            nrm = p.normals.astype(np.float64) @ nm.T
            nrm = (nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)).astype(np.float32)
            q = p.copy(positions=pos, normals=nrm)
            if mirror:
                order = np.arange(len(pos)).reshape(-1, 3)[:, [0, 2, 1]].ravel()
                q.reorder(order)
            parts.append(q)
        out = ObjModel(parts, self.path)
        out.flipped = self.flipped
        return out

    def merged(self, other):
        """A model with the parts of both (other's after self's)."""
        out = ObjModel(list(self.parts) + list(other.parts), self.path)
        out.flipped = self.flipped + other.flipped
        return out

    def fit(self, width, depth):
        """(scale, offset) that put the model's x/z extent inside width x depth world units, its corner at (0, 0) and
        its lowest point at height 0."""
        lo, hi = self.bounds()
        size = max(float(hi[0] - lo[0]) / max(width, 1e-6), float(hi[2] - lo[2]) / max(depth, 1e-6), 1e-9)
        s = 1.0 / size
        return s, (-float(lo[0]) * s, -float(lo[1]) * s, -float(lo[2]) * s)

    def height_at(self, x, z):
        """Highest surface under (x, z), or None (vertical ray against every triangle; numpy)."""
        best = None
        for p in self.parts:
            t = p.positions.reshape(-1, 3, 3)
            a, b, c = t[:, 0], t[:, 1], t[:, 2]
            v0x, v0z = c[:, 0] - a[:, 0], c[:, 2] - a[:, 2]
            v1x, v1z = b[:, 0] - a[:, 0], b[:, 2] - a[:, 2]
            v2x, v2z = x - a[:, 0], z - a[:, 2]
            den = v0x * v1z - v1x * v0z
            ok = np.abs(den) > 1e-9
            den = np.where(ok, den, 1.0)
            u = (v2x * v1z - v1x * v2z) / den
            w = (v0x * v2z - v2x * v0z) / den
            hit = ok & (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6)
            if hit.any():
                y = a[hit, 1] + u[hit] * (c[hit, 1] - a[hit, 1]) + w[hit] * (b[hit, 1] - a[hit, 1])
                top = float(y.max())
                best = top if best is None or top > best else best
        return best


def read_mtl(path):
    """{material: {"texture": absolute path or None, "color": (r, g, b)}} from an MTL file (missing file: {})."""
    out, cur = {}, None
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "newmtl":
            cur = " ".join(tok[1:])
            out[cur] = {"texture": None, "color": (0.8, 0.8, 0.8)}
        elif cur and tok[0] == "Kd" and len(tok) >= 4:
            out[cur]["color"] = tuple(float(x) for x in tok[1:4])
        elif cur and tok[0] == "map_Kd" and len(tok) >= 2:
            name = tok[-1]                                    # options (-s, -o ...) come before the file name
            tex = (Path(path).parent / name)
            out[cur]["texture"] = str(tex) if tex.exists() else str(Path(path).parent / Path(name).name)
    return out


def part_color(part):
    """Average colour of a part: its texture's mean, or Kd."""
    if part.texture:
        from PyQt5 import QtCore, QtGui
        img = QtGui.QImage(part.texture)
        if not img.isNull():
            c = QtGui.QColor(img.scaled(1, 1, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation).pixel(0, 0))
            return (c.redF(), c.greenF(), c.blueF())
    return tuple(part.color)


def topdown_image(model, width_px, height_px, world_w, world_d):
    """QImage of a placed model seen from straight above over (0..world_w, 0..world_d): every triangle in its part's
    average colour, darker where it slopes, lowest first so higher ground covers lower ground."""
    from PyQt5 import QtCore, QtGui
    img = QtGui.QImage(int(width_px), int(height_px), QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.transparent)
    sx, sz = width_px / max(world_w, 1e-6), height_px / max(world_d, 1e-6)
    tris = []
    for part in model.parts:
        base = np.asarray(part_color(part))
        t = part.positions.reshape(-1, 3, 3)
        n = np.abs(part.normals.reshape(-1, 3, 3)[:, 0, 1])
        shade = np.clip(0.55 + 0.45 * n, 0, 1)
        for tri, sh, top in zip(t, shade, t[:, :, 1].max(axis=1)):
            tris.append((float(top), tri, base * sh))
    tris.sort(key=lambda x: x[0])
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, False)
    for _, tri, col in tris:
        c = QtGui.QColor.fromRgbF(*[float(v) for v in col])
        p.setPen(QtGui.QPen(c, 1.0))
        p.setBrush(c)
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(v[0] * sx, v[2] * sz) for v in tri]))
    p.end()
    return img

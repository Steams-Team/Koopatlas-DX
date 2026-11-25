"""
kdx/editor3d.py - 3D editing of a World Maker map: the ground model and scenery objects, drawn with their textures,
plus the points and routes; objects are picked, moved, turned and scaled with the mouse, points dragged over the ground.

SceneView is also the preview of the model editor (kdx/modeleditor.py), with a single item and no points.

Items are drawn in their model space with a model matrix, so dragging an object only changes a uniform; geometry is
uploaded again only when the model or its material edits change. Faces are culled as the game culls them (front faces
only, counter-clockwise) unless a material is double-sided; View culling can be switched off.

Mouse: left click selects (an object, or a point); left drag moves the selection (Move mode: over the ground, Shift up
and down; Turn mode: around Y, Shift around X, Ctrl around Z; Scale mode: up and down). Right drag orbits, middle drag
(or Shift + right drag) pans, the wheel zooms. F frames the selection, Delete removes an object, Ctrl+D duplicates.

The gizmo on the selection works as in Blender, Maya or Switch Toolbox: W move (arrows), E turn (rings), R scale
(handles; the centre square scales evenly). Drag a handle to work along that axis only. X switches world/local axes
(scaling is always along the object's own axes). With snapping on, moves go in steps of 10 units, turns of 15 degrees
and scales of 0.1; holding Ctrl while dragging a handle does the opposite of the snapping setting.
"""
from __future__ import annotations

import ctypes
import math

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from OpenGL import GL

from . import rules
from .diagnostics import guarded
from .viewport import KIND_STYLE, LINE_FS, LINE_VS, POINT_FS, POINT_VS, look_at, perspective

ITEM_VS = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec3 nrm; layout(location=2) in vec3 col; layout(location=3) in vec2 uv;
uniform mat4 mvp; uniform mat3 nmat; out vec3 vcol; out vec2 vuv; out float vlight;
void main(){ gl_Position = mvp*vec4(pos,1.0); vec3 L = normalize(vec3(0.35,0.85,0.4));
  vlight = 0.45 + 0.55*abs(dot(normalize(nmat*nrm), L)); vcol = col; vuv = uv; }"""
ITEM_FS = """#version 330 core
in vec3 vcol; in vec2 vuv; in float vlight; uniform sampler2D tex; uniform int has_tex; uniform int mode;
uniform vec4 tint; out vec4 o;
void main(){ vec4 c = has_tex == 1 ? texture(tex, vuv) : vec4(vcol, 1.0);
  if(mode == 1 && c.a < 0.5) discard;
  if(mode == 0 || mode == 1) c.a = 1.0;
  if(mode == 2 && c.a < 0.004) discard;
  vec3 rgb = mix(c.rgb*vlight, tint.rgb, tint.a);
  o = vec4(rgb, c.a); }"""
MODES = {"opaque": 0, "mask": 1, "translucent": 2}
GL_WRAPS = {"repeat": GL.GL_REPEAT, "clamp": GL.GL_CLAMP_TO_EDGE, "mirror": GL.GL_MIRRORED_REPEAT}


class GpuItem:
    """One model's triangles (model space) in a vertex buffer, with a batch per part."""

    def __init__(self, model, textures):
        data, self.batches = [], []
        first = 0
        for p in model.parts:
            n = len(p.positions)
            if not n:
                continue
            col = np.tile(np.asarray(p.color[:3], np.float32), (n, 1)) if not p.texture else np.ones((n, 3), np.float32)
            data.append(np.hstack([p.positions, p.normals, col, p.uvs]).astype(np.float32))
            self.batches.append({"first": first, "count": n, "tex": textures(p.texture), "mode": p.mode,
                                 "double": p.double_sided, "material": p.material,
                                 "wrap": [GL_WRAPS.get(w, GL.GL_REPEAT) for w in getattr(p, "wrap", ("repeat", "repeat"))]})
            first += n
        arr = np.ascontiguousarray(np.vstack(data) if data else np.zeros((0, 11), np.float32))
        self.count = len(arr)
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, max(arr.nbytes, 4), arr if arr.nbytes else None, GL.GL_STATIC_DRAW)
        for loc, (k, off) in enumerate(((3, 0), (3, 12), (3, 24), (2, 36))):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, k, GL.GL_FLOAT, False, 44, ctypes.c_void_p(off))
        GL.glBindVertexArray(0)

    def delete(self):
        GL.glDeleteBuffers(1, [self.vbo])
        GL.glDeleteVertexArrays(1, [self.vao])


class Item:
    """What SceneView draws: a key ("ground", ("object", i), "model"), a model-space ObjModel, a 4x4 matrix."""

    def __init__(self, key, model, matrix, version=0, pickable=True):
        self.key, self.model, self.matrix = key, model, np.asarray(matrix, np.float64)
        self.version = version            # changes when the model or its edits change (the GPU copy is remade)
        self.pickable = pickable
        allp = np.concatenate([p.positions for p in model.parts]) if model.parts else np.zeros((1, 3), np.float32)
        self.lo, self.hi = allp.min(axis=0).astype(np.float64), allp.max(axis=0).astype(np.float64)
        self._tris = None

    def triangles(self):
        if self._tris is None:
            self._tris = (np.concatenate([p.positions for p in self.model.parts]).reshape(-1, 3, 3).astype(np.float64)
                          if self.model.parts else np.zeros((0, 3, 3)))
        return self._tris

    def world_bounds(self):
        c = np.array([[x, y, z, 1.0] for x in (self.lo[0], self.hi[0]) for y in (self.lo[1], self.hi[1])
                      for z in (self.lo[2], self.hi[2])])
        w = c @ self.matrix.T
        return w[:, :3].min(axis=0), w[:, :3].max(axis=0)


def ray_triangles(o, d, tris):
    """Distances along the ray (o + t d) to every triangle hit (Möller-Trumbore, both faces), or an empty array."""
    if not len(tris):
        return np.zeros(0)
    e1, e2 = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    p = np.cross(d, e2)
    det = (e1 * p).sum(axis=1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = o - tris[:, 0]
    u = (s * p).sum(axis=1) * inv
    q = np.cross(s, e1)
    v = (q * d).sum(axis=1) * inv
    t = (e2 * q).sum(axis=1) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
    return t[hit]


class SceneView(QtWidgets.QOpenGLWidget):
    picked = QtCore.pyqtSignal(object)               # item key, ("point", id), or None
    objectEdited = QtCore.pyqtSignal(object, object)  # item key, new 4x4 matrix (on release)
    pointDragged = QtCore.pyqtSignal(str, tuple)     # point id, new world position (on release)
    statusMessage = QtCore.pyqtSignal(str)
    modeChanged = QtCore.pyqtSignal(str)              # "move" / "turn" / "scale" (W / E / R)
    routeMenu = QtCore.pyqtSignal(str, QtCore.QPoint)  # route id, global position (right click on a route)
    spaceChanged = QtCore.pyqtSignal(str)             # "world" / "local" (X)

    GIZMO_COLOURS = ((0.93, 0.27, 0.27), (0.45, 0.85, 0.3), (0.3, 0.55, 1.0))
    MODE_KEYS = {QtCore.Qt.Key_W: "move", QtCore.Qt.Key_E: "turn", QtCore.Qt.Key_R: "scale"}

    def __init__(self, parent=None, editable=True):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.editable = editable
        self.items = []                  # [Item]
        self.points = {}                 # id -> ((x, y, z), kind)
        self.routes = []                 # [((x, y, z), (x, y, z))] or [(a, b, route type, route id)]
        self.route_problems = {}         # route id -> text (drawn with a red badge)
        self.route_width = 12.0          # world units
        self.area = None                 # (w, d): the map's rectangle, drawn as an outline
        self.selected = None             # item key or ("point", id)
        self.mode = "move"               # move / turn / scale
        self.snap = True                 # moved objects and points stand on the ground
        self.cull = True                 # front faces only, as the game
        self.show_points = True
        self.highlight_material = None   # a material drawn tinted (the model editor's selected row)
        self.select_first = False        # True: a click on an unselected item selects it without dragging it
        self.ground_height = None        # callable(x, z) -> height or None
        self.yaw, self.pitch, self.distance = -0.6, 0.75, 3000.0
        self.target = np.zeros(3)
        self._gpu = {}                   # (key, version) -> GpuItem
        self._textures = {}              # path -> GL texture id
        self._ready = False
        self._press = None               # (button, pos, modifiers)
        self._drag = None                # {"key", "start", "matrix", "plane_y", "anchor"} while dragging
        self._last = None
        self.space = "world"             # gizmo axes: world / local
        self.snapping = False
        self.snap_steps = {"move": 10.0, "turn": 15.0, "scale": 0.1}
        self.show_gizmo = True
        self._hover_axis = None
        self.setMouseTracking(True)

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self.modeChanged.emit(mode)
            self.update()

    def set_space(self, space):
        if space != self.space:
            self.space = space
            self.spaceChanged.emit(space)
            self.update()

    # ---- the gizmo -----------------------------------------------------------------------------------------------
    def _gizmo_item(self):
        if not (self.show_gizmo and self.editable) or self.selected is None or \
                (isinstance(self.selected, tuple) and self.selected[:1] == ("point",)):
            return None
        it = self.item(self.selected)
        if it is None or (it.key == "ground" and self._ground_locked()):
            return None
        return it

    def _gizmo_frame(self, it, matrix=None):
        """(origin, [unit axis x, y, z] in world space, handle length)."""
        m = it.matrix if matrix is None else matrix
        o = np.asarray(m[:3, 3], np.float64)
        if self.space == "local" or self.mode == "scale":
            axes = [m[:3, k] / max(np.linalg.norm(m[:3, k]), 1e-12) for k in range(3)]
        else:
            axes = [np.eye(3)[k] for k in range(3)]
        return o, axes, self.distance * 0.12

    def _gizmo_handles(self, it):
        """[(axis 0-2, or 3 for the centre square, [world points of the polyline])] of the current mode's handles."""
        o, axes, L = self._gizmo_frame(it)
        out = []
        if self.mode == "turn":
            ang = np.linspace(0, 2 * math.pi, 49)
            for k in range(3):
                u, v = axes[(k + 1) % 3], axes[(k + 2) % 3]
                out.append((k, [o + 0.8 * L * (math.cos(a) * u + math.sin(a) * v) for a in ang]))
            return out
        for k in range(3):
            out.append((k, [o + axes[k] * 0.18 * L, o + axes[k] * L]))
        if self.mode == "scale":
            _, view, _ = self.matrices()
            r, up = view[0, :3] * 0.07 * L, view[1, :3] * 0.07 * L
            out.append((3, [o - r - up, o + r - up, o + r + up, o - r + up, o - r - up]))
        return out

    def _pick_gizmo(self, x, y, radius=9.0):
        it = self._gizmo_item()
        if it is None:
            return None
        vp = self.mvp()
        best, bd = None, radius
        for k, pts in self._gizmo_handles(it):
            sp = [self.project(p, vp) for p in pts]
            if k == 3 and None not in sp:                     # the centre square: inside counts
                xs, ys = [p[0] for p in sp], [p[1] for p in sp]
                if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys):
                    return 3
            for a, b in zip(sp, sp[1:]):
                if a is None or b is None:
                    continue
                d = _seg_dist(x, y, a, b)
                if d < bd:
                    best, bd = k, d
        return best

    def _gizmo_lines(self, it):
        lines = []
        o, axes, L = self._gizmo_frame(it)
        _, view, _ = self.matrices()
        active = self._drag.get("axis") if self._drag else self._hover_axis
        for k, pts in self._gizmo_handles(it):
            col = (1.0, 0.9, 0.2) if k == active else (self.GIZMO_COLOURS[k] if k < 3 else (0.9, 0.9, 0.9))
            c = list(col) + [1.0]
            if self.mode != "turn" and k < 3:
                pts = [o, pts[-1]]
            for a, b in zip(pts, pts[1:]):
                lines += [list(a) + c, list(b) + c]
            if k < 3 and self.mode != "turn":                 # an arrow head (move) or a small square (scale)
                tip, u = pts[-1], axes[k]
                side = np.cross(u, view[2, :3])
                side = side / max(np.linalg.norm(side), 1e-9) * 0.06 * L
                if self.mode == "move":
                    base = tip - u * 0.16 * L
                    for q in (base + side, base - side):
                        lines += [list(tip) + c, list(q) + c]
                else:
                    up = np.cross(side, view[2, :3])
                    up = up / max(np.linalg.norm(up), 1e-9) * 0.06 * L
                    sq = [tip - side - up, tip + side - up, tip + side + up, tip - side + up, tip - side - up]
                    for a, b in zip(sq, sq[1:]):
                        lines += [list(a) + c, list(b) + c]
        return lines

    def _gizmo_drag(self, e, drag):
        """The new matrix while a handle is dragged."""
        m0, k = drag["matrix"], drag["axis"]
        o, axes, _L = drag["frame"]
        snap = self.snapping != bool(e.modifiers() & QtCore.Qt.ControlModifier)
        step = self.snap_steps[self.mode]
        m = m0.copy()
        ro, rd = self.ray(e.x(), e.y())
        if self.mode == "move":
            t = _line_param(ro, rd, o, axes[k]) if k < 3 else None
            if t is None or drag["t0"] is None:
                return m0
            delta = t - drag["t0"]
            if snap:
                delta = round(delta / step) * step
            m[:3, 3] = m0[:3, 3] + axes[k] * delta
        elif self.mode == "turn":
            u = axes[k]
            p = _plane_point(ro, rd, o, u)
            if p is None or drag["p0"] is None:
                return m0
            v0, v1 = drag["p0"] - o, p - o
            ang = math.degrees(math.atan2(float(np.dot(u, np.cross(v0, v1))), float(np.dot(v0, v1))))
            if snap:
                ang = round(ang / step) * step
            m[:3, :3] = _rotation(u, math.radians(ang)) @ m0[:3, :3]
        else:
            if k == 3:
                c = drag["centre_px"]
                if c is None:
                    return m0
                r0 = max(math.hypot(drag["start"].x() - c[0], drag["start"].y() - c[1]), 4.0)
                f = math.hypot(e.x() - c[0], e.y() - c[1]) / r0
                s0 = float(np.linalg.norm(m0[:3, 0]))
            else:
                t = _line_param(ro, rd, o, axes[k])
                if t is None or not drag["t0"]:
                    return m0
                f = t / drag["t0"]
                s0 = float(np.linalg.norm(m0[:3, k]))
            f = max(f, 1e-3)
            if snap and s0 > 0:
                f = max(round(s0 * f / step) * step, step) / s0
            if k == 3:
                m[:3, :3] = m0[:3, :3] * f
            else:
                m[:3, k] = m0[:3, k] * f
        return m

    def event(self, e):
        # W/E/R/X belong to the view while it has the focus (the main window uses some of them for its own menus)
        if e.type() == QtCore.QEvent.ShortcutOverride and self.editable and not e.modifiers() and \
                (e.key() in self.MODE_KEYS or e.key() == QtCore.Qt.Key_X):
            e.accept()
            return True
        return super().event(e)

    # ---- content -----------------------------------------------------------------------------------------------------
    def set_items(self, items, frame=False):
        self.items = list(items)
        if self._ready:
            self.makeCurrent()
            live = {(it.key, it.version) for it in self.items}
            for k in [k for k in self._gpu if k not in live]:
                self._gpu.pop(k).delete()
            self.doneCurrent()
        if frame:
            self.frame_all()
        self.update()

    def set_points(self, points, routes):
        self.points, self.routes = dict(points), list(routes)
        self.update()

    def item(self, key):
        return next((it for it in self.items if it.key == key), None)

    def frame_all(self):
        boxes = [it.world_bounds() for it in self.items if it.model.parts]
        pts = [p for p, _k in self.points.values()]
        if not boxes and not pts:
            return
        lo = np.min([b[0] for b in boxes] + [np.asarray(p) for p in pts], axis=0)
        hi = np.max([b[1] for b in boxes] + [np.asarray(p) for p in pts], axis=0)
        self.target = (lo + hi) / 2
        self.distance = max(float(np.linalg.norm(hi - lo)) * 0.85, 100.0)
        self.update()

    def frame_selection(self):
        if isinstance(self.selected, tuple) and self.selected[:1] == ("point",) and self.selected[1] in self.points:
            self.target = np.asarray(self.points[self.selected[1]][0], np.float64)
            self.distance = min(self.distance, 800.0)
        else:
            it = self.item(self.selected)
            if it is None:
                return self.frame_all()
            lo, hi = it.world_bounds()
            self.target = (lo + hi) / 2
            self.distance = max(float(np.linalg.norm(hi - lo)) * 1.4, 60.0)
        self.update()

    # ---- GL ----------------------------------------------------------------------------------------------------------
    def initializeGL(self):
        from .viewport import Viewport
        self.item_prog = Viewport._program(self, ITEM_VS, ITEM_FS)
        self.point_prog = Viewport._program(self, POINT_VS, POINT_FS)
        self.line_prog = Viewport._program(self, LINE_VS, LINE_FS)
        self.overlay_vao = GL.glGenVertexArrays(1)
        self.overlay_vbo = GL.glGenBuffers(1)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
        self._ready = True

    def _texture(self, path):
        if not path:
            return 0
        if path in self._textures:
            return self._textures[path]
        img = QtGui.QImage(path)
        tid = 0
        if not img.isNull():
            img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
            ptr = img.constBits()
            ptr.setsize(img.byteCount())
            raw = np.frombuffer(bytes(ptr), np.uint8).reshape(img.height(), img.bytesPerLine())[:, :img.width() * 4]
            tid = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, img.width(), img.height(), 0, GL.GL_RGBA,
                            GL.GL_UNSIGNED_BYTE, np.ascontiguousarray(raw))
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            for k in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
                GL.glTexParameteri(GL.GL_TEXTURE_2D, k, GL.GL_REPEAT)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)          # Qt's glyph cache (the labels) expects the default
        self._textures[path] = tid
        return tid

    def _gpu_item(self, it):
        k = (it.key, it.version)
        if k not in self._gpu:
            for old in [o for o in self._gpu if o[0] == it.key]:
                self._gpu.pop(old).delete()
            self._gpu[k] = GpuItem(it.model, self._texture)
        return self._gpu[k]

    def matrices(self):
        w, h = max(self.width(), 1), max(self.height(), 1)
        eye = self.target + self.distance * np.array([math.cos(self.pitch) * math.sin(self.yaw), math.sin(self.pitch),
                                                       math.cos(self.pitch) * math.cos(self.yaw)])
        near = max(self.distance / 500.0, 0.5)
        return perspective(45.0, w / h, near, self.distance * 20 + 20000), look_at(eye, self.target), eye

    def mvp(self):
        proj, view, _ = self.matrices()
        return proj @ view

    @guarded
    def paintGL(self):
        # QPainter (the labels, last frame) leaves depth testing off and blending on: set the state every frame
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glDepthMask(True)
        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
        GL.glClearColor(0.55, 0.72, 0.88, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        vp = self.mvp()
        GL.glUseProgram(self.item_prog)
        loc = {n: GL.glGetUniformLocation(self.item_prog, n) for n in ("mvp", "nmat", "tex", "has_tex", "mode", "tint")}
        GL.glUniform1i(loc["tex"], 0)
        drafts = []
        for pass_modes in (("opaque", "mask"), ("translucent",)):
            if pass_modes == ("translucent",):
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glDepthMask(False)
            for it in self.items:
                if not it.model.parts:
                    continue
                g = self._gpu_item(it)
                m = it.matrix
                mvp = (vp @ m).astype(np.float32)
                GL.glUniformMatrix4fv(loc["mvp"], 1, True, mvp)
                lin = m[:3, :3]
                nm = np.linalg.inv(lin).T if abs(np.linalg.det(lin)) > 1e-12 else lin
                GL.glUniformMatrix3fv(loc["nmat"], 1, True, nm.astype(np.float32))
                sel = it.key == self.selected
                GL.glUniform4f(loc["tint"], 1.0, 0.85, 0.2, 0.25 if sel else 0.0)
                GL.glFrontFace(GL.GL_CW if np.linalg.det(lin) < 0 else GL.GL_CCW)
                GL.glBindVertexArray(g.vao)
                for b in g.batches:
                    if b["mode"] not in pass_modes:
                        continue
                    if self.cull and not b["double"]:
                        GL.glEnable(GL.GL_CULL_FACE)
                        GL.glCullFace(GL.GL_BACK)
                    else:
                        GL.glDisable(GL.GL_CULL_FACE)
                    hl = sel or (self.highlight_material is not None and b["material"] == self.highlight_material)
                    GL.glUniform4f(loc["tint"], 1.0, 0.85, 0.2, (0.25 if sel else 0.45) if hl else 0.0)
                    GL.glUniform1i(loc["mode"], MODES.get(b["mode"], 0))
                    GL.glUniform1i(loc["has_tex"], 1 if b["tex"] else 0)
                    GL.glActiveTexture(GL.GL_TEXTURE0)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, b["tex"])
                    if b["tex"]:                               # a texture can be shared by parts that wrap differently
                        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, b["wrap"][0])
                        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, b["wrap"][1])
                    GL.glDrawArrays(GL.GL_TRIANGLES, b["first"], b["count"])
                if sel:
                    drafts.append(it)
        GL.glDepthMask(True)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glFrontFace(GL.GL_CCW)
        GL.glBindVertexArray(0)
        self._draw_overlays(vp, drafts)
        GL.glUseProgram(0)
        self._paint_labels(vp)

    def _upload(self, data, comps):
        data = np.ascontiguousarray(data, np.float32)
        GL.glBindVertexArray(self.overlay_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.overlay_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_DYNAMIC_DRAW)
        stride, off = sum(comps) * 4, 0
        for loc, n in enumerate(comps):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, False, stride, ctypes.c_void_p(off))
            off += n * 4
        for loc in range(len(comps), 3):
            GL.glDisableVertexAttribArray(loc)

    def _draw_overlays(self, vp, selected_items):
        lines = []
        if self.area:
            w, d = self.area
            c = (1.0, 1.0, 1.0, 0.6)
            for a, b in (((0, 0, 0), (w, 0, 0)), ((w, 0, 0), (w, 0, d)), ((w, 0, d), (0, 0, d)), ((0, 0, d), (0, 0, 0))):
                lines += [list(a) + list(c), list(b) + list(c)]
        styled = []
        if self.show_points:
            from .routeui import route_lines_3d
            for r in self.routes:
                if len(r) >= 4:
                    sel = self.selected == ("route", r[3])
                    styled += route_lines_3d(r[0], r[1], r[2], self.route_width,
                                             glow=(0.85, 0.27, 0.94) if sel else None)
                else:
                    a, b = r[0], r[1]
                    lines += [list(np.add(a, (0, 3, 0))) + [0.95, 0.85, 0.55, 1.0], list(np.add(b, (0, 3, 0))) + [0.95, 0.85, 0.55, 1.0]]
        g = self._gizmo_item()
        gizmo = self._gizmo_lines(g) if g is not None else []
        for it in selected_items:                   # the selection's box
            lo, hi = it.lo, it.hi
            corners = [np.array([x, y, z, 1.0]) @ it.matrix.T for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            for i, j in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)):
                lines += [list(corners[i][:3]) + [1.0, 0.85, 0.1, 1.0], list(corners[j][:3]) + [1.0, 0.85, 0.1, 1.0]]
        GL.glDisable(GL.GL_DEPTH_TEST)
        if lines:
            GL.glUseProgram(self.line_prog)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.line_prog, "mvp"), 1, True, vp.astype(np.float32))
            self._upload(np.asarray(lines, np.float32), (3, 4))
            GL.glDrawArrays(GL.GL_LINES, 0, len(lines))
        if self.show_points and getattr(self, "warp_rows", None):
            from .routeui import warp_lines_3d
            styled += warp_lines_3d(self.warp_rows, lambda q: self.points[q][0] if q in self.points else None,
                                    getattr(self, "warp_slot", None))
        if styled:                                  # routes: their action's look, 2 px wide
            from .routeui import thick_lines
            GL.glEnable(GL.GL_BLEND); GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glUseProgram(self.line_prog)
            self._upload(np.asarray(styled, np.float32), (3, 4))
            thick_lines(self.line_prog, vp, len(styled), self.width(), self.height(), px=2)
            GL.glDisable(GL.GL_BLEND)
        if gizmo:                                   # 3 px wide: drawn nine times, a pixel apart (core GL lines are 1 px)
            GL.glUseProgram(self.line_prog)
            self._upload(np.asarray(gizmo, np.float32), (3, 4))
            loc = GL.glGetUniformLocation(self.line_prog, "mvp")
            px, py = 2.0 / max(self.width(), 1), 2.0 / max(self.height(), 1)
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    m = vp.copy()
                    m[0] += ox * px * vp[3]
                    m[1] += oy * py * vp[3]
                    GL.glUniformMatrix4fv(loc, 1, True, m.astype(np.float32))
                    GL.glDrawArrays(GL.GL_LINES, 0, len(gizmo))
        if self.show_points and self.points:
            pts = []
            for pid, (pos, kind) in self.points.items():
                col, size = KIND_STYLE.get(kind, KIND_STYLE["Other"])
                if self.selected == ("point", pid):
                    col, size = (1.0, 0.3, 0.9), size + 6
                pts.append(list(np.add(pos, (0, 4, 0))) + list(col) + [1.0, float(size)])
            GL.glUseProgram(self.point_prog)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.point_prog, "mvp"), 1, True, vp.astype(np.float32))
            self._upload(np.asarray(pts, np.float32), (3, 4, 1))
            GL.glDrawArrays(GL.GL_POINTS, 0, len(pts))
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glBindVertexArray(0)

    def _route_badges(self, vp):
        from .routeui import route_badge_3d
        out = []
        for r in self.routes:
            if len(r) >= 4:
                sel = self.selected == ("route", r[3])
                e0, e1 = self.project(r[0], vp), self.project(r[1], vp)
                if not sel and (e0 is None or e1 is None or math.hypot(e1[0] - e0[0], e1[1] - e0[1]) < 70):
                    continue
                s = self.project(route_badge_3d(r[0], r[1], r[2]), vp)
                if s is not None:
                    out.append((s[0], s[1], r[2], self.selected == ("route", r[3]), r[3] in self.route_problems))
        return out

    def _paint_labels(self, vp):
        if not (self.show_points and self.points):
            return
        p = QtGui.QPainter(self)
        from .routeui import paint_badges, point_icon
        paint_badges(p, self._route_badges(vp), 20)
        for pid, keys in getattr(self, "point_actions", {}).items():   # pipes, cannons, links beside their points
            if pid in self.points and keys:
                s = self.project(self.points[pid][0], vp)
                if s is not None:
                    for k, key in enumerate(keys):
                        point_icon(key, 22).paint(p, QtCore.QRect(int(s[0] - 34 - 24 * k), int(s[1] - 11), 22, 22))
        p.setPen(QtGui.QColor(20, 20, 30))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        for pid, (pos, _kind) in self.points.items():
            if pid[0] == "K":
                continue
            s = self.project(pos)
            if s is not None:
                p.drawText(QtCore.QPointF(s[0] + 7, s[1] - 7), rules.point_label(pid))
        p.end()

    # ---- picking -----------------------------------------------------------------------------------------------------
    def project(self, pos, vp=None):
        vp = self.mvp() if vp is None else vp
        c = vp @ np.array([pos[0], pos[1], pos[2], 1.0])
        if c[3] <= 1e-6:
            return None
        n = c[:3] / c[3]
        return ((n[0] + 1) / 2 * self.width(), (1 - n[1]) / 2 * self.height())

    def ray(self, x, y):
        inv = np.linalg.inv(self.mvp().astype(np.float64))
        nx, ny = 2 * x / max(self.width(), 1) - 1, 1 - 2 * y / max(self.height(), 1)
        a = inv @ np.array([nx, ny, -1.0, 1.0]); b = inv @ np.array([nx, ny, 1.0, 1.0])
        a, b = a[:3] / a[3], b[:3] / b[3]
        d = b - a
        return a, d / np.linalg.norm(d)

    def pick_point(self, x, y, radius=12):
        best, bd = None, radius * radius
        for pid, (pos, _k) in self.points.items():
            s = self.project(np.add(pos, (0, 4, 0)))
            if s is None:
                continue
            d = (s[0] - x) ** 2 + (s[1] - y) ** 2
            if d < bd:
                best, bd = pid, d
        return best

    def pick_route(self, x, y, radius=8.0):
        """The route id whose line passes within radius pixels of (x, y), or None."""
        if not self.show_points:
            return None
        from .routeui import pick_route_screen, route_badge_3d
        vp = self.mvp()
        segs = [(r[3], self.project(r[0], vp), self.project(r[1], vp)) for r in self.routes if len(r) >= 4]
        for r in self.routes:                               # the badge counts too (a jump's badge is up in the air)
            if len(r) >= 4:
                s = self.project(route_badge_3d(r[0], r[1], r[2]), vp)
                if s is not None and math.hypot(s[0] - x, s[1] - y) <= 12:
                    return r[3]
        return pick_route_screen(segs, x, y, radius)

    def pick_item(self, x, y):
        """(item key, distance) of the nearest pickable item under the pixel, or (None, None)."""
        o, d = self.ray(x, y)
        best = (None, None)
        for it in self.items:
            if not it.pickable or not it.model.parts:
                continue
            inv = np.linalg.inv(it.matrix)
            lo_ = inv @ np.r_[o, 1.0]
            ld = inv[:3, :3] @ d
            t = ray_triangles(lo_[:3], ld, it.triangles())
            if len(t):
                hit = it.matrix @ np.r_[lo_[:3] + ld * t.min(), 1.0]
                dist = float(np.linalg.norm(hit[:3] - o))
                if best[1] is None or dist < best[1]:
                    best = (it.key, dist)
        return best

    def ground_hit(self, x, y, plane_y=0.0, ground_key="ground"):
        """Where the ray under the pixel meets the ground item (or the horizontal plane at plane_y)."""
        o, d = self.ray(x, y)
        g = self.item(ground_key)
        if g is not None and g.model.parts:
            inv = np.linalg.inv(g.matrix)
            lo_ = inv @ np.r_[o, 1.0]
            ld = inv[:3, :3] @ d
            t = ray_triangles(lo_[:3], ld, g.triangles())
            if len(t):
                return tuple((g.matrix @ np.r_[lo_[:3] + ld * t.min(), 1.0])[:3])
        return self.plane_hit(o, d, plane_y)

    @staticmethod
    def plane_hit(o, d, y):
        if abs(d[1]) < 1e-9:
            return None
        t = (y - o[1]) / d[1]
        return None if t < 0 else tuple(o + d * t)

    # ---- mouse -------------------------------------------------------------------------------------------------------
    @guarded
    def mousePressEvent(self, e):
        self.setFocus()
        self._press = (e.button(), e.pos(), e.modifiers())
        self._last = e.pos()
        if e.button() != QtCore.Qt.LeftButton:
            return
        x, y = e.x(), e.y()
        axis = self._pick_gizmo(x, y)
        if axis is not None and not (self.mode == "move" and axis == 3):   # a gizmo handle: along that axis only
            it = self._gizmo_item()
            frame = self._gizmo_frame(it)
            o, axes, _L = frame
            ro, rd = self.ray(x, y)
            self._drag = {"key": it.key, "start": e.pos(), "matrix": it.matrix.copy(), "moved": False, "axis": axis,
                          "frame": frame, "t0": _line_param(ro, rd, o, axes[axis]) if axis < 3 else None,
                          "p0": _plane_point(ro, rd, o, axes[axis]) if axis < 3 else None,
                          "centre_px": self.project(o)}
            self.update()
            return
        pid = self.pick_point(x, y) if self.show_points else None
        rid = None if pid else self.pick_route(x, y)
        if rid:                                            # a route: select it (it is not dragged)
            self.selected = ("route", rid)
            self.picked.emit(self.selected)
            self.update()
            return
        key = ("point", pid) if pid else self.pick_item(x, y)[0]
        if key == "ground" and self.editable and self._ground_locked():
            key = None                                     # a locked ground is not selected (nor dragged)
        was = self.selected
        self.selected = key
        self.picked.emit(key)
        # select_first: the first click only selects (the world model editor: every game shape is pickable, and a
        # click on the grass must not drag the ground away)
        if key is not None and self.editable and (not self.select_first or key == was or key[:1] == ("point",)):
            if key[:1] == ("point",):
                self._drag = {"key": key, "start": e.pos(), "moved": False}
            else:
                it = self.item(key)
                if it is not None:
                    o, d = self.ray(x, y)
                    plane_y = float(it.matrix[1, 3])
                    self._drag = {"key": key, "start": e.pos(), "matrix": it.matrix.copy(), "moved": False,
                                  "anchor": self.plane_hit(o, d, plane_y), "plane_y": plane_y}
        self.update()

    def _ground_locked(self):
        return getattr(self, "lock_ground", False)

    @guarded
    def mouseMoveEvent(self, e):
        if self._last is None:
            hover = self._pick_gizmo(e.x(), e.y()) if not e.buttons() else None
            if hover != self._hover_axis:
                self._hover_axis = hover
                self.update()
            return
        dx, dy = e.x() - self._last.x(), e.y() - self._last.y()
        self._last = e.pos()
        buttons = e.buttons()
        if buttons & QtCore.Qt.RightButton and not (e.modifiers() & QtCore.Qt.ShiftModifier):
            self.yaw -= dx * 0.008
            self.pitch = max(-1.5, min(1.5, self.pitch + dy * 0.008))
            self.update()
            return
        if buttons & QtCore.Qt.MiddleButton or (buttons & QtCore.Qt.RightButton and e.modifiers() & QtCore.Qt.ShiftModifier):
            _, view, _ = self.matrices()
            right, up = view[0, :3], view[1, :3]
            k = self.distance * 0.0015
            self.target = self.target - right * dx * k + up * dy * k
            self.update()
            return
        if not (buttons & QtCore.Qt.LeftButton) or self._drag is None:
            return
        drag = self._drag
        if not drag["moved"] and (e.pos() - drag["start"]).manhattanLength() < 4:
            return
        drag["moved"] = True
        if "axis" in drag:
            it = self.item(drag["key"])
            if it is not None:
                it.matrix = self._gizmo_drag(e, drag)
                self.statusMessage.emit(describe_matrix(it.matrix))
                self.update()
            return
        if drag["key"][:1] == ("point",):
            pid = drag["key"][1]
            pos = self.points[pid][0]
            hit = self.ground_hit(e.x(), e.y(), pos[1]) if self.snap else self.plane_hit(*self.ray(e.x(), e.y()), pos[1])
            if hit is not None:
                self.points[pid] = (tuple(hit), self.points[pid][1])
                self.statusMessage.emit("%s: %.0f, %.0f, %.0f" % ((pid,) + tuple(hit)))
            self.update()
            return
        it = self.item(drag["key"])
        if it is None:
            return
        m0 = drag["matrix"]
        total_x, total_y = e.x() - drag["start"].x(), e.y() - drag["start"].y()
        mods = e.modifiers()
        if self.mode == "move":
            m = m0.copy()
            if mods & QtCore.Qt.ShiftModifier:                # up and down
                m[1, 3] = m0[1, 3] - total_y * self.distance * 0.0015
            else:
                o, d = self.ray(e.x(), e.y())
                hit = self.plane_hit(o, d, drag["plane_y"])
                if hit is not None and drag["anchor"] is not None:
                    m[0, 3] = m0[0, 3] + hit[0] - drag["anchor"][0]
                    m[2, 3] = m0[2, 3] + hit[2] - drag["anchor"][2]
                    if self.snap and self.ground_height and it.key != "ground":
                        h = self.ground_height(m[0, 3], m[2, 3])
                        if h is not None:
                            m[1, 3] = h
        elif self.mode == "turn":
            axis = (1, 0, 0) if mods & QtCore.Qt.ShiftModifier else (0, 0, 1) if mods & QtCore.Qt.ControlModifier else (0, 1, 0)
            ang = math.radians(total_x * 0.5)
            r = _rotation(axis, ang)
            m = m0.copy()
            m[:3, :3] = r @ m0[:3, :3]
        else:                                                # scale
            f = math.exp(-total_y * 0.005)
            m = m0.copy()
            m[:3, :3] = m0[:3, :3] * f
        it.matrix = m
        self.statusMessage.emit(describe_matrix(m))
        self.update()

    @guarded
    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.RightButton and self._press is not None and \
                (e.pos() - self._press[1]).manhattanLength() < 4:
            rid = self.pick_route(e.x(), e.y())
            if rid:
                self.routeMenu.emit(rid, e.globalPos())
        drag, self._drag = self._drag, None
        self._last = None
        if drag is None or not drag["moved"]:
            return
        if drag["key"][:1] == ("point",):
            pid = drag["key"][1]
            self.pointDragged.emit(pid, tuple(self.points[pid][0]))
        else:
            it = self.item(drag["key"])
            if it is not None:
                self.objectEdited.emit(drag["key"], it.matrix.copy())

    def wheelEvent(self, e):
        self.distance = max(10.0, self.distance * (0.85 if e.angleDelta().y() > 0 else 1 / 0.85))
        self.update()

    def keyPressEvent(self, e):
        if self.editable and not e.modifiers() and e.key() in self.MODE_KEYS:
            self.set_mode(self.MODE_KEYS[e.key()])
        elif self.editable and not e.modifiers() and e.key() == QtCore.Qt.Key_X:
            self.set_space("local" if self.space == "world" else "world")
            self.statusMessage.emit("Gizmo axes: %s" % self.space)
        elif e.key() == QtCore.Qt.Key_F:
            self.frame_selection()
        elif e.key() == QtCore.Qt.Key_Escape and self._drag is not None:
            it = self.item(self._drag["key"])
            if it is not None and "matrix" in self._drag:
                it.matrix = self._drag["matrix"]
            self._drag = None
            self.update()
        else:
            super().keyPressEvent(e)


def gizmo_options(view, settings_key="gizmo"):
    """Check boxes 'Snap' and 'Local axes' bound to a SceneView (remembered between runs)."""
    st = QtCore.QSettings("KoopatlasDX", "KoopatlasDX")
    snap = QtWidgets.QCheckBox("Snap")
    snap.setToolTip("Moves in steps of %g units, turns of %g degrees, scales of %g (Ctrl while dragging a handle: the "
                    "opposite)" % (view.snap_steps["move"], view.snap_steps["turn"], view.snap_steps["scale"]))
    local = QtWidgets.QCheckBox("Local axes")
    local.setToolTip("The gizmo follows the object's own axes (X)")
    snap.setChecked(str(st.value(settings_key + "_snap", "false")).lower() == "true")
    view.snapping = snap.isChecked()
    snap.toggled.connect(lambda on: (setattr(view, "snapping", on), st.setValue(settings_key + "_snap", on)))
    local.toggled.connect(lambda on: view.set_space("local" if on else "world"))
    view.spaceChanged.connect(lambda sp: (local.blockSignals(True), local.setChecked(sp == "local"),
                                          local.blockSignals(False)))
    return snap, local


def _seg_dist(x, y, a, b):
    """Pixel distance from (x, y) to the screen segment a-b."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def _line_param(ro, rd, p, u):
    """Where on the line p + t u the ray ro + s rd passes closest: t (None when they are parallel)."""
    w0 = np.asarray(ro, np.float64) - p
    b = float(np.dot(rd, u))
    denom = float(np.dot(rd, rd)) * float(np.dot(u, u)) - b * b
    if denom < 1e-6:
        return None
    return (float(np.dot(rd, rd)) * float(np.dot(u, w0)) - b * float(np.dot(rd, w0))) / denom


def _plane_point(ro, rd, p, n):
    """Where the ray meets the plane through p with normal n (None when edge-on or behind)."""
    den = float(np.dot(rd, n))
    if abs(den) < 1e-6:
        return None
    t = float(np.dot(np.asarray(p) - ro, n)) / den
    return None if t < 0 else np.asarray(ro, np.float64) + rd * t


def _rotation(axis, ang):
    x, y, z = axis
    c, s = math.cos(ang), math.sin(ang)
    C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def decompose(m):
    """(position, rotation degrees (x, y, z) as kdx.modelref.euler_matrix, scale (x, y, z)) of a 4x4."""
    m = np.asarray(m, np.float64)
    s = np.linalg.norm(m[:3, :3], axis=0)
    r = m[:3, :3] / np.maximum(s, 1e-12)
    if np.linalg.det(r) < 0:                                 # a mirror: put it in the x scale
        s[0] = -s[0]
        r[:, 0] = -r[:, 0]
    ry = math.asin(max(-1.0, min(1.0, -r[2, 0])))
    if abs(r[2, 0]) < 0.9999:
        rx, rz = math.atan2(r[2, 1], r[2, 2]), math.atan2(r[1, 0], r[0, 0])
    else:
        rx, rz = math.atan2(-r[1, 2], r[1, 1]), 0.0
    return list(m[:3, 3]), [math.degrees(rx), math.degrees(ry), math.degrees(rz)], list(s)


def describe_matrix(m):
    pos, rot, sc = decompose(m)
    return "Position %.1f, %.1f, %.1f · turn %.1f°, %.1f°, %.1f° · scale %.3g, %.3g, %.3g" % (tuple(pos) + tuple(rot) + tuple(sc))

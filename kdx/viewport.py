"""
kdx/viewport.py - the 3D world view (OpenGL 3.3 core through PyQt5's QOpenGLWidget).

Draws every model of the world (one vertex buffer per model, one draw per material, so models can be hidden), the
points and waypoints as round markers, and the routes as lines. Picking and dragging are done on the CPU with numpy.

Materials are drawn with their albedo texture (sampler _a0, decoded by kdx.texture) and the game's render states:
opaque, mask (alpha test against gsys_alpha_test_value) and translucent (alpha blend, drawn after everything else
without writing depth). Lighting is one directional light. View > Textures switches back to flat colours from the
material names.

View > Shading > Game shaders draws the materials with the game's own programs instead (kdx.shader: the world's
Wii_UBER bfsha translated from Maxwell to GLSL 4.50, light maps and bake maps included). It needs an OpenGL 4.5
context, which the view asks for when the driver offers one; without it, or without a shader archive, the simple
renderer above is used. Materials whose program cannot be translated are drawn by the simple renderer too.
"""
from __future__ import annotations

import ctypes
import math

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from OpenGL import GL

from . import rules
from .diagnostics import guarded
from .scene import is_shadow_decal
from .world import point_kind

MESH_VS = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec3 nrm; layout(location=2) in vec3 col;
uniform mat4 mvp; out vec3 vcol; out float vlight;
void main(){ gl_Position = mvp*vec4(pos,1.0); vec3 L = normalize(vec3(0.35,0.85,0.4));
  vlight = 0.38 + 0.62*abs(dot(normalize(nrm), L)); vcol = col; }"""
MESH_FS = """#version 330 core
in vec3 vcol; in float vlight; uniform float alpha; out vec4 o; void main(){ o = vec4(vcol*vlight, alpha); }"""
POINT_VS = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec4 col; layout(location=2) in float size;
uniform mat4 mvp; out vec4 vcol; void main(){ gl_Position = mvp*vec4(pos,1.0); gl_PointSize = size; vcol = col; }"""
POINT_FS = """#version 330 core
in vec4 vcol; out vec4 o; void main(){ vec2 d = gl_PointCoord*2.0-1.0; float r = dot(d,d); if(r>1.0) discard;
  o = vec4(mix(vcol.rgb, vec3(0.05), smoothstep(0.55,1.0,r)), vcol.a); }"""
TEX_VS = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec3 nrm; layout(location=2) in vec3 col; layout(location=3) in vec2 uv;
uniform mat4 mvp; uniform mat3 uvmtx; out vec3 vcol; out vec2 vuv; out float vlight;
void main(){ gl_Position = mvp*vec4(pos,1.0); vec3 L = normalize(vec3(0.35,0.85,0.4));
  vlight = 0.45 + 0.55*abs(dot(normalize(nrm), L)); vcol = col; vuv = (uvmtx*vec3(uv,1.0)).xy; }"""
TEX_FS = """#version 330 core
in vec3 vcol; in vec2 vuv; in float vlight; uniform sampler2D tex; uniform int has_tex; uniform int mode;
uniform float alpha_ref; out vec4 o;
void main(){ vec4 c = has_tex == 1 ? texture(tex, vuv) : vec4(vcol, 1.0);
  if(mode == 1 && c.a < alpha_ref) discard;
  if(mode == 0 && has_tex == 1 && c.a < 0.05) discard;     // cut-outs in "opaque" textures (course markers)
  if(mode == 0 || mode == 1) c.a = 1.0;
  if(mode == 2 && c.a < 0.004) discard;
  if(mode == 3){ if(c.a < alpha_ref) discard; o = vec4(0.0, 0.0, 0.0, 0.35); return; }   // ground shadow decal
  o = vec4(c.rgb*vlight, c.a); }"""
LINE_VS = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec4 col; uniform mat4 mvp; out vec4 vcol;
void main(){ gl_Position = mvp*vec4(pos,1.0); vcol = col; }"""
LINE_FS = """#version 330 core
in vec4 vcol; out vec4 o; void main(){ o = vcol; }"""

KIND_STYLE = {   # colour (r, g, b), marker size in pixels
    "Level": ((1.0, 0.80, 0.15), 15), "Tower": ((0.95, 0.35, 0.25), 17), "Castle": ((0.90, 0.15, 0.15), 19),
    "Toad house": ((0.30, 0.85, 0.40), 15), "World link": ((0.30, 0.80, 1.00), 15), "Ghost house": ((0.70, 0.55, 1.0), 16),
    "Airship": ((0.85, 0.55, 0.30), 17), "Special": ((0.95, 0.60, 0.95), 14), "Pipe": ((0.20, 0.70, 0.30), 12),
    "Path stop": ((0.92, 0.92, 0.95), 10), "Waypoint": ((0.55, 0.60, 0.68), 7), "Other": ((0.8, 0.8, 0.8), 9),
}
ROUTE_COLORS = {"道": (0.95, 0.85, 0.55), "坂": (0.95, 0.65, 0.35), "ジャンプ": (0.40, 0.85, 1.0), "雲": (0.92, 0.95, 1.0), "石": (0.70, 0.70, 0.72)}


def perspective(fovy, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy) / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]], np.float32)


def look_at(eye, target, up=(0, 1, 0)):
    eye, target, up = (np.asarray(v, np.float64) for v in (eye, target, up))
    f = target - eye; f /= np.linalg.norm(f)
    s = np.cross(f, up); s /= (np.linalg.norm(s) or 1.0)
    u = np.cross(s, f)
    m = np.identity(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m.astype(np.float32)


class GpuMesh:
    def __init__(self, name, data, count):
        self.name, self.count = name, count
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        stride = 9 * 4
        for loc in range(3):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, 3, GL.GL_FLOAT, False, stride, ctypes.c_void_p(loc * 12))
        GL.glBindVertexArray(0)

    def delete(self):
        GL.glDeleteBuffers(1, [self.vbo])
        GL.glDeleteVertexArrays(1, [self.vao])


class GpuTexturedMesh:
    """A model's textured vertex array (kdx.scene.textured_arrays: 11 floats per vertex) and its material batches."""

    def __init__(self, name, data, batches, bones=None):
        self.name, self.batches, self.count = name, batches, len(data)
        self.rest, self.bones = data, bones           # kept for re-posing by a skeletal animation
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        stride = 11 * 4
        for loc, (n, off) in enumerate(((3, 0), (3, 12), (3, 24), (2, 36))):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, False, stride, ctypes.c_void_p(off))
        GL.glBindVertexArray(0)

    def delete(self):
        GL.glDeleteBuffers(1, [self.vbo])
        GL.glDeleteVertexArrays(1, [self.vao])


MODES = {"opaque": 0, "mask": 1, "translucent": 2}
IDENT3 = np.identity(3, np.float32)

_GL45 = None


def gl45_available():
    """Whether the driver gives an OpenGL 4.5 core context (tried once, on a hidden surface)."""
    global _GL45
    if _GL45 is None:
        _GL45 = False
        try:
            fmt = QtGui.QSurfaceFormat.defaultFormat()
            fmt.setVersion(4, 5)
            fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile)
            ctx = QtGui.QOpenGLContext()
            ctx.setFormat(fmt)
            if ctx.create():
                f = ctx.format()
                _GL45 = (f.majorVersion(), f.minorVersion()) >= (4, 5) and f.profile() == QtGui.QSurfaceFormat.CoreProfile
        except Exception:
            _GL45 = False
    return _GL45


def _settings():
    return QtCore.QSettings("KoopatlasDX", "KoopatlasDX")


class Viewport(QtWidgets.QOpenGLWidget):
    pointClicked = QtCore.pyqtSignal(str)            # point/waypoint ID, or "" for empty space
    pointDragged = QtCore.pyqtSignal(str, tuple)     # ID, new world position (on release)
    cameraDragged = QtCore.pyqtSignal(str, tuple)    # point ID, new camera offset (on release)
    statusMessage = QtCore.pyqtSignal(str)
    routeClicked = QtCore.pyqtSignal(str)            # route id (a click on a route's line or badge)
    routeMenu = QtCore.pyqtSignal(str, QtCore.QPoint)  # route id, global position (right click on a route)
    groundMenu = QtCore.pyqtSignal(tuple, QtCore.QPoint)  # world position under the mouse, global position (right click)
    pointMenu = QtCore.pyqtSignal(str, QtCore.QPoint)     # point id (right click on a point)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.world = None
        self.scene = None           # WorldScene shared with the 2D map
        self.meshes = {}            # model name -> GpuMesh
        self.hidden = set()
        self.show_labels = True
        self.show_waypoints = True
        self.show_routes = True
        self.locked = False             # True: points cannot be dragged
        self.snap_to_terrain = True
        self.pick_handler = None        # callable(point id) while the main window waits for a point to be picked
        self.highlight = {}             # route id -> (r, g, b)
        self.sinking = {}               # route id -> (depth, place): roads Mario would walk inside the ground on
        self.secret_routes = set()      # segments only a secret exit opens (pink)
        self.camera_for = None          # callable(point id) -> camera offset (x, y, z) or None
        self.show_all_cameras = False
        self._candidate = None          # point under the press, not yet dragged
        self._cam_drag = None           # (point id, start offset, press y) while dragging the camera marker
        self._cam_pos = None
        self.selected = ""
        self.yaw, self.pitch, self.distance = -0.6, 0.85, 3000.0
        self.target = np.zeros(3)
        self._pending = None        # (world) waiting for a GL context
        self._mouse = None
        self._press = None
        self._drag_id = None
        self._axis_drag = None
        self._axis_hover = None
        self.place_handler = None        # set by the window's + Level / + Stop ...: called with the ground position
        self.place_label = ""
        self._ghost = None
        self.setMouseTracking(True)
        self._drag_pos = None
        self._ready = False
        self.move_mode = False          # kept for compatibility; dragging no longer needs a mode
        self.markers = []           # [(id, kind, (x, y, z))]
        self.mesh_stats = {}
        self.show_textures = True       # View > Textures: draw materials with their textures (else flat colours)
        self.textured = {}              # model name -> GpuTexturedMesh
        self.gl_textures = {}           # texture name -> GL texture id
        self.uv_matrix = None           # callable(model name, material name) -> 3x3 UV matrix (texture animations)
        self.texture_stats = {"decoded": 0, "failed": []}
        self.anims = None               # kdx.anim.Animations while animations play
        self.anim_frame = 0.0
        self.anim_timer = QtCore.QTimer(self)
        self.anim_timer.timeout.connect(self._anim_tick)
        self._rest_inv = {}             # model name -> (n_bones, 4, 4) inverse rest world matrices
        self.ride = None                # ride-along preview: {"ride": kdx.ridealong.Ride, "dist", "speed", "reading"}
        self._rider = None              # Mario's position during a ride
        self.ride_timer = QtCore.QTimer(self)
        self.ride_timer.timeout.connect(self._ride_tick)
        # View > Shading: the game's own shaders (kdx.shader) when the GL context is 4.5
        st = _settings()
        self.game_shaders = str(st.value("render/game_shaders", "false")).lower() == "true"
        self.shader_backend = str(st.value("render/shader_backend", "builtin") or "builtin")
        self.shadertools_path = str(st.value("render/shadertools", "") or "")
        self.game = None                # kdx.shader.gamerender.GameRenderer of the current world
        self.game_status = ""
        self._shading_menu = None
        QtCore.QTimer.singleShot(0, self.install_shading_menu)      # once the window has put the view in place
        self.gl45 = gl45_available()
        if self.gl45:
            fmt = QtGui.QSurfaceFormat.defaultFormat()
            fmt.setVersion(4, 5)
            fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile)
            self.setFormat(fmt)

    # ---- public ------------------------------------------------------------------------------------------------------
    def set_world(self, world, scene):
        self.world = world
        self.scene = scene
        self.selected = ""
        self.hidden = set()
        self._build_markers()                      # markers need no GL context (the 2D tab may be shown first)
        if self._ready:
            self.makeCurrent()
            self._upload_world()
            self.doneCurrent()
        else:
            self._pending = world
        self.frame_all()
        self.update()

    def refresh_markers(self):
        """Call after points move or the point table changes."""
        self._build_markers()
        self.update()

    def set_model_visible(self, name, visible):
        (self.hidden.discard if visible else self.hidden.add)(name)
        self.update()

    def select(self, pid):
        self.selected = pid or ""
        self.update()

    def frame_all(self):
        if not self.world:
            return
        pts = [p for _i, _k, p in self.markers] if self.markers else []
        if not pts:
            return
        a = np.asarray(pts)
        lo, hi = a.min(axis=0), a.max(axis=0)
        self.target = (lo + hi) / 2
        self.distance = max(float(np.linalg.norm(hi - lo)) * 0.9, 300.0)
        self.update()

    def camera_position(self, pid):
        """Where the camera marker for a point is (point + offset), or None."""
        if self._cam_drag and self._cam_drag[0] == pid and self._cam_pos is not None:
            return self._cam_pos
        p = self._marker_pos(pid)
        off = self.camera_for(pid) if (self.camera_for and p is not None) else None
        if off is None:
            return None
        return (p[0] + off[0], p[1] + off[1], p[2] + off[2])

    def view_from_camera(self, pid):
        """Look at the point from its camera offset (what the game camera sees there). False if it has no offset."""
        p = self._marker_pos(pid)
        off = self.camera_for(pid) if (self.camera_for and p is not None) else None
        if off is None:
            return False
        d = math.sqrt(off[0] ** 2 + off[1] ** 2 + off[2] ** 2)
        if d < 1e-3:
            return False
        self.target = np.asarray(p, np.float64)
        self.distance = d
        self.pitch = math.asin(max(-1.0, min(1.0, off[1] / d)))
        self.yaw = math.atan2(off[0], off[2])
        self.update()
        return True

    def focus_on(self, pid):
        p = self._marker_pos(pid)
        if p is not None:
            self.target = np.asarray(p, np.float64)
            self.distance = min(self.distance, 1400.0)
            self.update()

    # ---- GL lifecycle ------------------------------------------------------------------------------------------------
    def initializeGL(self):
        self.mesh_prog = self._program(MESH_VS, MESH_FS)
        self.point_prog = self._program(POINT_VS, POINT_FS)
        self.line_prog = self._program(LINE_VS, LINE_FS)
        self.tex_prog = self._program(TEX_VS, TEX_FS)
        self.overlay_vao = GL.glGenVertexArrays(1)
        self.overlay_vbo = GL.glGenBuffers(1)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
        self._ready = True
        if self._pending is not None:
            self._upload_world()
            self._pending = None
            self.frame_all()

    def _program(self, vs_src, fs_src):
        prog = GL.glCreateProgram()
        for kind, src in ((GL.GL_VERTEX_SHADER, vs_src), (GL.GL_FRAGMENT_SHADER, fs_src)):
            sh = GL.glCreateShader(kind)
            GL.glShaderSource(sh, src)
            GL.glCompileShader(sh)
            if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(sh).decode())
            GL.glAttachShader(prog, sh)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(prog).decode())
        return prog

    def _upload_world(self):
        ov, self._overlay = getattr(self, "_overlay", None), None
        for m in (ov or {}).get("saved", {}).values():          # a pending preview's stand-ins go too
            if m is not None:
                m.delete()
        for m in list(self.meshes.values()) + list(self.textured.values()):
            m.delete()
        if self.gl_textures:
            GL.glDeleteTextures(list(self.gl_textures.values()))
        self.meshes, self.mesh_stats, self.textured, self.gl_textures = {}, {}, {}, {}
        self.texture_stats = {"decoded": 0, "failed": []}
        self._drop_game()
        if not self.world or not self.scene:
            return
        for name, data in self.scene.meshes.items():
            self.meshes[name] = GpuMesh(name, data, len(data))
            self.mesh_stats[name] = len(data) // 3
        if self.show_textures:
            self._upload_textured()
        for name, err in self.scene.errors.items():
            self.statusMessage.emit("Model %s could not be drawn: %s" % (name, err))
        self._build_markers()

    def _upload_textured(self):
        """Textured vertex arrays for every model, and a GL texture for every albedo they use."""
        textures = self.scene.textures()
        for name in self.scene.meshes:
            arrays = self.scene.textured(name)
            if not arrays:
                continue
            data, batches, bones = arrays
            self.textured[name] = GpuTexturedMesh(name, data, batches, bones)
            for b in batches:
                if b.albedo and b.albedo not in self.gl_textures and b.albedo in textures:
                    self.gl_textures[b.albedo] = self._upload_texture(textures[b.albedo])
                if is_shadow_decal(b, textures.get(b.albedo)):
                    b.shadow, b.mode = True, "translucent"         # blended over the ground, after the opaque pass

    def _upload_texture(self, tex):
        try:
            rgba = tex.rgba()
        except Exception as e:                            # an unsupported format draws flat instead
            self.texture_stats["failed"].append("%s: %s" % (tex.name, e))
            return 0
        tid = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, rgba.shape[1], rgba.shape[0], 0, GL.GL_RGBA,
                        GL.GL_UNSIGNED_BYTE, np.ascontiguousarray(rgba))
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        self.texture_stats["decoded"] += 1
        return tid

    # ---- animations ------------------------------------------------------------------------------------------------
    def play_animations(self, anims):
        """Start (anims: kdx.anim.Animations) or stop (None) the world's texture and skeletal animations."""
        self.anims = anims
        if anims is None:
            self.end_preview_models()
            self._preview = None
            self.anim_timer.stop()
            self.uv_matrix = None
            self._repose(reset=True)
        else:
            self.uv_matrix = lambda model, material: anims.uv_matrix(material, self.anim_frame)
            self.anim_timer.start(33)
        self.update()

    def preview_anim(self, anims, model_name, frames):
        """Play one model's skeletal animation once from frame 0 (a path's appear animation), then hold the end."""
        self._preview_token = getattr(self, "_preview_token", 0) + 1
        ov = getattr(self, "_overlay", None)
        if ov and model_name not in ov["models"]:
            self.end_preview_models()
        self._preview = (model_name, float(frames or 0))
        self.anim_frame = 0.0
        self.play_animations(anims)

    def preview_models(self, bf, names):
        """Draw these models from another build of the world (kdx.bfres.BfresFile: a pending appear animation built in a
        temp copy) in place of the world's own, until end_preview_models(). Models it lacks are left out."""
        self.end_preview_models()
        if not self._ready or not self.show_textures:
            return False
        from .scene import textured_arrays
        textures = self.scene.textures() if self.scene else {}
        self.makeCurrent()
        saved, models = {}, {}
        for name in names:
            model = next((m for m in bf.models if m.name == name), None)
            arrays = textured_arrays(model) if model is not None else None
            saved[name] = self.textured.pop(name, None)
            self._rest_inv.pop(name, None)
            if not arrays:
                continue
            data, batches, bones = arrays
            self.textured[name] = GpuTexturedMesh(name, data, batches, bones)
            models[name] = model
            for b in batches:
                if b.albedo and b.albedo not in self.gl_textures and b.albedo in textures:
                    self.gl_textures[b.albedo] = self._upload_texture(textures[b.albedo])
                if is_shadow_decal(b, textures.get(b.albedo)):
                    b.shadow, b.mode = True, "translucent"
        self.doneCurrent()
        self._overlay = {"saved": saved, "models": models}
        self.update()
        return True

    def end_preview_models(self):
        ov = getattr(self, "_overlay", None)
        self._overlay = None
        if not ov:
            return
        self.makeCurrent()
        for name in list(ov["saved"]):
            mesh = self.textured.pop(name, None)
            if mesh is not None:
                mesh.delete()
            self._rest_inv.pop(name, None)
            if ov["saved"][name] is not None:
                self.textured[name] = ov["saved"][name]
        self.doneCurrent()
        self.update()

    def _finish_pending(self, token):
        if token == getattr(self, "_preview_token", 0) and getattr(self, "_overlay", None):
            self.play_animations(None)

    def _model_named(self, name):
        ov = getattr(self, "_overlay", None)
        if ov and name in ov["saved"]:
            return ov["models"].get(name)
        return next((m for m in self.world.model.models if m.name == name), None)

    @guarded
    def _anim_tick(self):
        self.anim_frame += 2.0                             # the game runs at 60 frames a second, this timer at 30
        pv = getattr(self, "_preview", None)
        if pv and self.anim_frame >= pv[1]:
            self.anim_frame = pv[1]
            self._repose()
            self.anim_timer.stop()
            self.update()
            if getattr(self, "_overlay", None):            # a pending build: show the end a moment, then the world
                tok = self._preview_token
                QtCore.QTimer.singleShot(1500, lambda: self._finish_pending(tok))
            return
        self._repose()
        self.update()

    def _repose(self, reset=False):
        """Move the vertices of every model that has a skeletal animation to the current frame (CPU, numpy)."""
        if not self._ready or not self.world:
            return
        self.makeCurrent()
        for name, mesh in self.textured.items():
            if mesh.bones is None:
                continue
            model = self._model_named(name)
            if model is None or not model.bones:
                continue
            anim = self.anims.skeletal_for(name) if self.anims else None
            pv = getattr(self, "_preview", None)
            if pv and name != pv[0] and not reset:
                continue                                   # previewing one path's animation: the rest stay still
            if anim is None and not reset:
                continue
            data = mesh.rest
            if not reset:
                if name not in self._rest_inv:
                    rest = np.array([np.r_[np.reshape(b.world, (3, 4)), [[0, 0, 0, 1]]] for b in model.bones])
                    self._rest_inv[name] = np.linalg.inv(rest)
                pose = np.array([np.r_[np.reshape(m, (3, 4)), [[0, 0, 0, 1]]]
                                 for m in self.anims.bone_matrices(model, self.anim_frame, anim)])
                delta = pose @ self._rest_inv[name]
                idx = mesh.bones
                moving = idx >= 0
                d = delta[idx[moving]]
                data = mesh.rest.copy()
                p, n = data[moving, 0:3], data[moving, 3:6]
                data[moving, 0:3] = np.einsum("nij,nj->ni", d[:, :3, :3], p) + d[:, :3, 3]
                data[moving, 3:6] = np.einsum("nij,nj->ni", d[:, :3, :3], n)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, mesh.vbo)
            GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, data.nbytes, np.ascontiguousarray(data, np.float32))
        self.doneCurrent()

    # ---- ride-along camera preview ---------------------------------------------------------------------------------
    GAME_PITCH, GAME_DISTANCE = 0.85, 1100.0     # the "look-at" reading: a fixed map camera looking at Mario + offset

    def start_ride(self, ride, reading="look", speed=250.0):
        """Mario walks the ride's paths and the view follows the map camera. reading: "look" (the camera looks at
        Mario + offset from a fixed map angle) or "position" (the camera sits at Mario + offset). Esc or a click stops."""
        self.stop_ride()
        if not ride.samples:
            self.statusMessage.emit("Ride along: no path with points to walk.")
            return
        self.ride = {"ride": ride, "dist": 0.0, "speed": speed, "reading": reading}
        self.ride_timer.start(33)
        self.setFocus()

    def stop_ride(self):
        if self.ride:
            self.ride_timer.stop()
            self.ride, self._rider = None, None
            self.update()

    @guarded
    def _ride_tick(self):
        r = self.ride
        if r is None:
            return
        r["dist"] += r["speed"] * 0.033
        pos, off, label = r["ride"].sample(r["dist"])
        self._rider = pos
        self.set_ride_camera(pos, off, r["reading"])
        self.statusMessage.emit("Ride along (%s): %s" % (r["reading"], label))
        if r["dist"] >= r["ride"].length:
            self.ride_timer.stop()
            self.ride = None
            self.statusMessage.emit("Ride along finished.")
        self.update()

    def set_ride_camera(self, pos, off, reading):
        if reading == "position" and math.sqrt(sum(v * v for v in off)) > 1.0:
            d = math.sqrt(sum(v * v for v in off))
            self.target = np.asarray(pos, np.float64)
            self.distance = d
            self.pitch = math.asin(max(-1.0, min(1.0, off[1] / d)))
            self.yaw = math.atan2(off[0], off[2])
        else:
            self.target = np.asarray(pos, np.float64) + np.asarray(off, np.float64)
            self.pitch, self.yaw, self.distance = self.GAME_PITCH, 0.0, self.GAME_DISTANCE

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape and self.ride:
            self.stop_ride()
            self.statusMessage.emit("Ride along stopped.")
            return
        super().keyPressEvent(e)

    def set_textures(self, on):
        """View > Textures."""
        self.show_textures = on
        if self._ready and self.world and on and not self.textured:
            self.makeCurrent()
            self._upload_textured()
            self.doneCurrent()
        self.update()

    # ---- game shaders (View > Shading) -------------------------------------------------------------------------------
    def set_game_shaders(self, on):
        """View > Shading > Game shaders (True) or the simple renderer (False). Remembered between sessions."""
        self.game_shaders = bool(on)
        _settings().setValue("render/game_shaders", "true" if on else "false")
        self._sync_shading_menu()
        if on and self._ready and self.world and self.game is None:
            self.makeCurrent()
            self._ensure_game()
            self.doneCurrent()
        elif not on and self.game_status:
            self.statusMessage.emit("Shading: simple renderer.")
        self.update()

    def set_shader_backend(self, backend, path=None):
        """The translator for the game's programs: "builtin" (kdx.shader) or "ryujinx" (Ryujinx.ShaderTools at path)."""
        if backend not in ("builtin", "ryujinx"):
            return
        if path is not None:
            self.shadertools_path = path
            _settings().setValue("render/shadertools", path)
        self.shader_backend = backend
        _settings().setValue("render/shader_backend", backend)
        self._sync_shading_menu()
        if self._ready and self.game is not None:
            self.makeCurrent()
            self._drop_game()
            if self.game_shaders:
                self._ensure_game()
            self.doneCurrent()
        self.update()

    def _backend(self):
        """(backend, tool path) actually used: Ryujinx only when its executable exists."""
        from .shader import exact
        if self.shader_backend == "ryujinx":
            tool = exact.find_shadertools(self.shadertools_path)
            if tool:
                return "ryujinx", tool
        return "builtin", None

    def _drop_game(self):
        if self.game is not None:
            try:
                self.game.delete()
            except Exception:
                pass
        self.game, self.game_status = None, ""

    def _ensure_game(self):
        """Build the game-shader renderer for the current world (GL current). Sets game_status."""
        if self.game is not None or not self.world:
            return self.game
        if not self.gl45:
            self.game_status = "Game shaders need OpenGL 4.5, which this graphics driver does not offer: " \
                               "drawing with the simple renderer."
            self.statusMessage.emit(self.game_status)
            return None
        from .shader.gamerender import GameRenderer
        backend, tool = self._backend()
        try:
            g = GameRenderer(self.world, self.scene.textures() if self.scene else {}, backend, tool,
                             romfs=str(_settings().value("romfs_root", "") or "") or None)
            g.build()
        except Exception as e:
            self.game_status = "Game shaders could not be set up (%s): drawing with the simple renderer." % e
            self.statusMessage.emit(self.game_status)
            return None
        self.game = g
        if not g.drawn:
            reason = g.errors[0] if g.errors else "no material uses a shading model of the archive"
            self.game_status = "Game shaders: %s. Drawing with the simple renderer." % reason
        else:
            total = sum(len(m.materials) for m in self.world.model.models)
            progs = sum(1 for p in g.programs.values() if p is not None)
            self.game_status = "Game shaders (%s translator): %d of %d materials, %d programs%s." % (
                "Ryujinx.ShaderTools" if backend == "ryujinx" else "built-in", len(g.drawn), total, progs,
                "; %d problems (the rest uses the simple renderer)" % len(g.errors) if g.errors else "")
        self.statusMessage.emit(self.game_status)
        for e in g.errors[:20]:
            print("game shaders:", e)
        return g

    def _game_active(self):
        if getattr(self, "_overlay", None):                 # a pending build plays with the simple renderer
            return False
        if not self.game_shaders or not self.show_textures or not self.world:
            return False
        g = self.game if self.game is not None else self._ensure_game()
        return bool(g is not None and g.drawn)

    def _pose_for(self, model):
        """Bone world matrices of a model at the current animation frame, or None (rest pose)."""
        if not self.anims:
            return None
        pv = getattr(self, "_preview", None)
        if pv and model.name != pv[0]:
            return None
        anim = self.anims.skeletal_for(model.name)
        return self.anims.bone_matrices(model, self.anim_frame, anim) if anim else None

    def _srt_for(self, model, material, param):
        return self.anims.srt(material, self.anim_frame, param) if self.anims else None

    def _draw_game(self, translucent):
        w, h = max(1, self.width()), max(1, self.height())
        eye = self.target + self.distance * np.array([math.cos(self.pitch) * math.sin(self.yaw), math.sin(self.pitch),
                                                      math.cos(self.pitch) * math.cos(self.yaw)])
        view = look_at(eye, self.target)
        proj = perspective(45.0, w / h, max(1.0, self.distance * 0.01), self.distance * 20 + 20000)
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self.game.draw(view, proj, (int(w * dpr), int(h * dpr)), self.hidden, self._pose_for,
                       self._srt_for if self.anims else None, translucent)

    def install_shading_menu(self, view_menu=None):
        """Add View > Shading (simple / game shaders, translator) to the window's View menu. Called by itself when the
        view is first shown; the window may call it with its menu instead."""
        if self._shading_menu is not None:
            return self._shading_menu
        if view_menu is None:
            win = self.window()
            bar = win.menuBar() if isinstance(win, QtWidgets.QMainWindow) else None
            if bar is None:
                return None
            for a in bar.actions():
                if a.menu() is not None and a.text().replace("&", "") == "View":
                    view_menu = a.menu()
                    break
            if view_menu is None:
                return None
        m = QtWidgets.QMenu("Sha&ding", view_menu)
        g = QtWidgets.QActionGroup(m)
        self._act_simple = m.addAction("&Simple (one light)")
        self._act_game = m.addAction("&Game shaders (the game's own programs)")
        for a in (self._act_simple, self._act_game):
            a.setCheckable(True)
            g.addAction(a)
        self._act_game.setToolTip("Needs OpenGL 4.5. Translates the world's Wii_UBER programs to GLSL.")
        self._act_simple.triggered.connect(lambda: self.set_game_shaders(False))
        self._act_game.triggered.connect(lambda: self.set_game_shaders(True))
        m.addSeparator()
        t = m.addMenu("&Translator")
        tg = QtWidgets.QActionGroup(t)
        self._act_builtin = t.addAction("&Built-in (Yaz0loom's, from Ryujinx's tables)")
        self._act_ryujinx = t.addAction("&Ryujinx.ShaderTools (emulator)")
        for a in (self._act_builtin, self._act_ryujinx):
            a.setCheckable(True)
            tg.addAction(a)
        self._act_builtin.triggered.connect(lambda: self.set_shader_backend("builtin"))
        self._act_ryujinx.triggered.connect(self._choose_ryujinx)
        t.addSeparator()
        t.addAction("Set Ryujinx.ShaderTools &path…", lambda: self._choose_ryujinx(force=True))
        m.addSeparator()
        m.addAction("Shader &status", lambda: QtWidgets.QMessageBox.information(
            self, "Game shaders", self._shading_report()))
        before = next((a for a in view_menu.actions() if a.text().replace("&", "").startswith("Textures")), None)
        idx = view_menu.actions().index(before) + 1 if before is not None else -1
        acts = view_menu.actions()
        if 0 <= idx < len(acts):
            view_menu.insertMenu(acts[idx], m)
        else:
            view_menu.addMenu(m)
        self._shading_menu = m
        self._sync_shading_menu()
        return m

    def _sync_shading_menu(self):
        if self._shading_menu is None:
            return
        self._act_game.setChecked(self.game_shaders)
        self._act_simple.setChecked(not self.game_shaders)
        self._act_game.setEnabled(self.gl45)
        self._act_ryujinx.setChecked(self.shader_backend == "ryujinx")
        self._act_builtin.setChecked(self.shader_backend != "ryujinx")

    def _choose_ryujinx(self, force=False):
        from .shader import exact
        tool = exact.find_shadertools(self.shadertools_path)
        if force or not tool:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Ryujinx.ShaderTools executable (from your own Ryujinx / Ryubing build)",
                self.shadertools_path or "", "Ryujinx.ShaderTools (Ryujinx.ShaderTools*);;All files (*)")
            if not path:
                self._sync_shading_menu()
                return
            tool = path
        self.set_shader_backend("ryujinx", tool)

    def _shading_report(self):
        lines = ["OpenGL 4.5: %s" % ("yes" if self.gl45 else "no (game shaders are off)"),
                 "Mode: %s" % ("game shaders" if self.game_shaders else "simple"),
                 "Translator: %s" % ("Ryujinx.ShaderTools (%s)" % self._backend()[1] if self._backend()[0] == "ryujinx"
                                     else "built-in" + (" (Ryujinx.ShaderTools not found)"
                                                        if self.shader_backend == "ryujinx" else ""))]
        if self.game_status:
            lines.append(self.game_status)
        if self.game is not None and self.game.errors:
            lines.append("")
            lines += self.game.errors[:15]
        return "\n".join(lines)

    def showEvent(self, e):
        super().showEvent(e)
        self.install_shading_menu()

    def _draw_textured(self, mvp, translucent):
        prog = self.tex_prog
        GL.glUseProgram(prog)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(prog, "mvp"), 1, GL.GL_TRUE, mvp)
        GL.glUniform1i(GL.glGetUniformLocation(prog, "tex"), 0)
        loc_has, loc_mode = GL.glGetUniformLocation(prog, "has_tex"), GL.glGetUniformLocation(prog, "mode")
        loc_ref, loc_uv = GL.glGetUniformLocation(prog, "alpha_ref"), GL.glGetUniformLocation(prog, "uvmtx")
        GL.glActiveTexture(GL.GL_TEXTURE0)
        game = self.game if self._game_active() else None
        for name, mesh in self.textured.items():
            if name in self.hidden:
                continue
            GL.glBindVertexArray(mesh.vao)
            for b in mesh.batches:
                if (b.mode == "translucent") != translucent:
                    continue
                if game is not None and game.handles(name, b.material):
                    continue                                      # drawn with the game's shader
                tid = self.gl_textures.get(b.albedo, 0)
                GL.glUniform1i(loc_has, 1 if tid else 0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
                GL.glUniform1i(loc_mode, 3 if b.shadow else MODES.get(b.mode, 0))
                GL.glUniform1f(loc_ref, b.alpha_ref)
                if b.cull == "none":                              # as the game draws it: an inside-out model shows
                    GL.glDisable(GL.GL_CULL_FACE)                 # its far walls here too, not a solid surface
                else:
                    GL.glEnable(GL.GL_CULL_FACE)
                    GL.glCullFace(GL.GL_FRONT if b.cull == "front" else GL.GL_BACK)
                m = self.uv_matrix(name, b.material) if self.uv_matrix else None
                GL.glUniformMatrix3fv(loc_uv, 1, GL.GL_TRUE, IDENT3 if m is None else np.asarray(m, np.float32))
                GL.glDrawArrays(GL.GL_TRIANGLES, b.first, b.count)
        GL.glDisable(GL.GL_CULL_FACE)

    def _build_markers(self):
        self.markers = []
        if not self.world:
            return
        w = self.world
        for pid in w.point_ids:
            pos = w.position(pid)
            if pos is not None:
                self.markers.append((pid, point_kind(pid), pos))
        for k in w.waypoint_ids:
            self.markers.append((k, "Waypoint", w.position(k)))

    def _marker_pos(self, pid):
        if self._drag_id == pid and self._drag_pos is not None:
            return self._drag_pos
        for i, _k, p in self.markers:
            if i == pid:
                return p
        return None

    # ---- camera ------------------------------------------------------------------------------------------------------
    def matrices(self):
        w, h = max(1, self.width()), max(1, self.height())
        eye = self.target + self.distance * np.array([math.cos(self.pitch) * math.sin(self.yaw), math.sin(self.pitch),
                                                      math.cos(self.pitch) * math.cos(self.yaw)])
        view = look_at(eye, self.target)
        proj = perspective(45.0, w / h, max(1.0, self.distance * 0.01), self.distance * 20 + 20000)
        return proj @ view, eye

    def project(self, pts, mvp=None):
        mvp = self.matrices()[0] if mvp is None else mvp
        a = np.c_[np.asarray(pts, np.float64), np.ones(len(pts))] @ mvp.T
        w = a[:, 3:4]
        ok = w[:, 0] > 1e-6
        ndc = a[:, :3] / np.where(np.abs(w) < 1e-9, 1e-9, w)
        sx = (ndc[:, 0] + 1) * 0.5 * self.width()
        sy = (1 - ndc[:, 1]) * 0.5 * self.height()
        return np.c_[sx, sy], ok

    def ray(self, x, y):
        mvp, eye = self.matrices()
        inv = np.linalg.inv(mvp.astype(np.float64))
        nx, ny = 2 * x / max(1, self.width()) - 1, 1 - 2 * y / max(1, self.height())
        far = inv @ np.array([nx, ny, 1.0, 1.0]); far = far[:3] / far[3]
        d = far - eye
        return eye, d / np.linalg.norm(d)

    def terrain_height(self, x, z):
        """Highest terrain surface at (x, z), or None."""
        return self.scene.height_at(x, z) if self.scene else None

    # ---- drawing -----------------------------------------------------------------------------------------------------
    def paintGL(self):
        try:
            self._paint()
        except Exception as e:                            # never let a drawing error take the program down
            if not getattr(self, "_paint_error_shown", False):
                self._paint_error_shown = True
                import traceback
                traceback.print_exc()
                self.statusMessage.emit("Drawing problem: %s" % e)

    def _paint(self):
        GL.glClearColor(0.07, 0.10, 0.15, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if not self.world:
            self._paint_text([], "Open a world archive (File > Open world…)")
            return
        mvp, _eye = self.matrices()
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        if self.show_textures and self.textured:
            game = self._game_active()
            if game:
                self._draw_game(translucent=False)
            self._draw_textured(mvp, translucent=False)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glDepthMask(GL.GL_FALSE)
            GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)          # decals (paths, shadows) lie on the ground: no flicker
            GL.glPolygonOffset(-1.0, -4.0)
            self._draw_textured(mvp, translucent=True)
            GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glDisable(GL.GL_BLEND)
            if game:
                self._draw_game(translucent=True)
        else:
            GL.glUseProgram(self.mesh_prog)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.mesh_prog, "mvp"), 1, GL.GL_TRUE, mvp)
            GL.glUniform1f(GL.glGetUniformLocation(self.mesh_prog, "alpha"), 1.0)
            for name, mesh in self.meshes.items():
                if name in self.hidden:
                    continue
                GL.glBindVertexArray(mesh.vao)
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, mesh.count)
        # overlays drawn on top
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        if self.show_routes:
            self._draw_routes(mvp)
        self._draw_markers(mvp)
        self._draw_gizmo(mvp)
        GL.glBindVertexArray(0)
        self._paint_labels(mvp)

    def _upload_overlay(self, data, comps):
        GL.glBindVertexArray(self.overlay_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.overlay_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_DYNAMIC_DRAW)
        stride = sum(comps) * 4
        off = 0
        for loc, n in enumerate(comps):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, False, stride, ctypes.c_void_p(off))
            off += n * 4
        for loc in range(len(comps), 3):
            GL.glDisableVertexAttribArray(loc)

    def _draw_routes(self, mvp):
        """Each route in its action's look (kdx.routeui): bands, ladders, arcs, chevrons; shown paths glow."""
        from .routeui import route_lines_3d, thick_lines
        verts, top = [], []
        width = max(8.0, min(30.0, self.distance * 0.006))
        for r in self.world.routes:
            a, b = self._marker_pos(r["from"]), self._marker_pos(r["to"])
            if a is None or b is None:
                continue
            sel = r["id"] == getattr(self, "selected_route", "")
            if r["id"] in self.highlight or sel:
                top += route_lines_3d(a, b, r["type"], width, 1.0,
                                      glow=self.highlight.get(r["id"], (0.85, 0.27, 0.94)))
            elif r["id"] in self.sinking:                 # through the ground in game: red, on top
                top += route_lines_3d(a, b, r["type"], width, 1.0, glow=(0.95, 0.15, 0.15))
            elif r["id"] in self.secret_routes:           # opened only by a secret exit: pink outline
                verts += route_lines_3d(a, b, r["type"], width, 0.95 if not self.highlight else 0.35,
                                        glow=(1.0, 0.35, 0.85))
            else:
                verts += route_lines_3d(a, b, r["type"], width, 0.95 if not self.highlight else 0.35)
        from .routeui import warp_lines_3d
        def pos_of(q):
            try:
                return self._marker_pos(q)
            except (KeyError, ValueError, TypeError):
                return None
        verts += warp_lines_3d(self.world.point_rows, pos_of, "W%d" % getattr(self.world, "number", 0))
        sel_pos = self._marker_pos(self.selected) if self.selected else None
        if sel_pos is not None:                          # stick compass of the selected point: one arrow per road
            from . import directions
            table = directions.compass(self.world, self.selected)
            if sum(len(v) for v in table.values()) >= 2:
                p = np.asarray(sel_pos, float) + np.array([0.0, 6.0, 0.0])
                arm = max(110.0, width * 10)
                for dname in directions.DIRS:
                    vx, vz = directions.VECTORS[dname]
                    n = len(table[dname])
                    rgb = (1.0, 0.45, 0.1) if n > 1 else (0.35, 0.85, 1.0) if n == 1 else (0.45, 0.5, 0.58)
                    q = p + np.array([vx, 0.0, vz]) * (arm if n else arm * 0.45)
                    top += route_lines_3d(tuple(p), tuple(q), "道", width * (0.55 if n else 0.3), 1.0 if n else 0.5,
                                          glow=rgb)
        verts += top                                     # highlighted routes drawn last, on top
        if not verts:
            return
        data = np.asarray(verts, np.float32)
        GL.glUseProgram(self.line_prog)
        self._upload_overlay(data, (3, 4))
        thick_lines(self.line_prog, mvp, len(data), self.width(), self.height(), px=2)

    # ---- move arrows (X red, Y green, Z blue) on the selected point --------------------------------------------------
    AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    AXIS_COLORS = ((0.95, 0.3, 0.3), (0.35, 0.9, 0.4), (0.35, 0.55, 1.0))

    def gizmo(self):
        """(origin, length) of the selected point's move arrows, or None."""
        if not self.world or not self.selected or self.locked or self.pick_handler is not None:
            return None
        o = self._marker_pos(self.selected)
        if o is None:
            return None
        return np.asarray(o, np.float64), self.distance * 0.085

    def camera_gizmo(self):
        """(origin, arm length) of the X/Y/Z arrows of the selected point's camera (where it looks: point + offset), or
        None. When that is on or near the point (offset 0, as on many vanilla points: the camera just looks at Mario),
        the arrows are lifted above it so they can be grabbed apart from the point's own."""
        if not self.selected or self.camera_for is None:
            return None
        c = self.camera_position(self.selected)
        p = self._marker_pos(self.selected)
        if c is None or p is None:
            return None
        L = self.distance * 0.07
        ca = getattr(self, "_cam_axis", None)
        lift = ca[3] if ca else (np.array([0.0, L * 1.8, 0.0]) if math.dist(c, p) < L * 1.3 else np.zeros(3))
        return np.asarray(c, np.float64) + lift, L

    def camera_lift(self):
        g = self.camera_gizmo()
        c = self.camera_position(self.selected) if g else None
        return (g[0] - np.asarray(c, np.float64)) if g is not None and c is not None else np.zeros(3)

    def _cam_gizmo_hit(self, x, y, radius=9):
        return self._gizmo_hit(x, y, radius, self.camera_gizmo())

    def _gizmo_hit(self, x, y, radius=9, g=False, with_dist=False):
        g = self.gizmo() if g is False else g
        if g is None:
            return (None, 99.0) if with_dist else None
        o, L = g
        pts = [o] + [o + np.asarray(a) * L for a in self.AXES]
        scr, ok = self.project(pts)
        if not ok[0]:
            return (None, 99.0) if with_dist else None
        if math.hypot(x - scr[0][0], y - scr[0][1]) < 12:          # on the point itself: a free drag, not an arrow
            return (None, 99.0) if with_dist else None
        best, bd = None, radius
        for i in range(3):
            if not ok[i + 1]:
                continue
            ax, ay = scr[0]; bx, by = scr[i + 1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 < 1e-9 else max(0.15, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
            d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
            if d < bd:
                best, bd = i, d
        return (best, bd) if with_dist else best

    def _axis_param(self, x, y, origin, axis):
        """Where the mouse ray passes closest to the line origin + t*axis: t."""
        ro, rd = self.ray(x, y)
        a = np.asarray(axis, np.float64)
        w0 = origin - ro
        b = float(a @ rd)
        d, e = float(a @ w0), float(rd @ w0)
        den = 1.0 - b * b
        if abs(den) < 1e-6:
            return None
        return (b * e - d) / den

    def _draw_ghost(self, mvp):
        """Where + Level / + Stop will put the point: a ring on the ground and a line from the selected point."""
        if self.place_handler is None or self._ghost is None:
            return
        g = np.asarray(self._ghost, np.float64)
        r = self.distance * 0.02
        col = (1.0, 0.85, 0.2)
        rows = []
        for k in range(24):
            a0, a1 = 2 * math.pi * k / 24, 2 * math.pi * (k + 1) / 24
            rows += [(g[0] + r * math.cos(a0), g[1] + 2, g[2] + r * math.sin(a0), *col, 1.0),
                     (g[0] + r * math.cos(a1), g[1] + 2, g[2] + r * math.sin(a1), *col, 1.0)]
        src = self._marker_pos(self.selected) if self.selected else None
        if src is not None:
            for k in range(0, 20, 2):
                t0, t1 = k / 20, (k + 1) / 20
                rows += [(*(np.asarray(src) + (g - np.asarray(src)) * t0), *col, 0.9),
                         (*(np.asarray(src) + (g - np.asarray(src)) * t1), *col, 0.9)]
        from .routeui import thick_lines
        data = np.asarray(rows, np.float32)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glUseProgram(self.line_prog)
        self._upload_overlay(data, (3, 4))
        thick_lines(self.line_prog, mvp, len(data), self.width(), self.height(), px=3)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def _draw_gizmo(self, mvp):
        self._draw_ghost(mvp)
        cg = self.camera_gizmo()
        if cg is not None:                               # the camera marker's own arrows
            ca = getattr(self, "_cam_axis", None)
            self._draw_axes(mvp, cg[0], cg[1], ca[0] if ca else getattr(self, "_cam_hover", None))
            lift = self.camera_lift()
            if np.linalg.norm(lift) > 1e-6:                  # lifted off the point: a stem down to where it looks
                c = cg[0] - lift
                rows = []
                for k in range(0, 12, 2):
                    rows += [(*(c + lift * k / 12), 0.85, 0.9, 1.0, 0.9), (*(c + lift * (k + 1) / 12), 0.85, 0.9, 1.0, 0.9)]
                from .routeui import thick_lines
                data = np.asarray(rows, np.float32)
                GL.glDisable(GL.GL_DEPTH_TEST)
                GL.glUseProgram(self.line_prog)
                self._upload_overlay(data, (3, 4))
                thick_lines(self.line_prog, mvp, len(data), self.width(), self.height(), px=2)
                GL.glEnable(GL.GL_DEPTH_TEST)
        g = self.gizmo()
        if g is None:
            return
        o, L = g
        active = self._axis_drag[1] if getattr(self, "_axis_drag", None) else getattr(self, "_axis_hover", None)
        self._draw_axes(mvp, o, L, active)

    def _draw_axes(self, mvp, o, L, active):
        rows = []
        for i, (a, col) in enumerate(zip(self.AXES, self.AXIS_COLORS)):
            a = np.asarray(a)
            c = (1.0, 0.95, 0.4) if i == active else col
            tip = o + a * L
            rows += [(*o, *c, 1.0), (*tip, *c, 1.0)]
            side = np.cross(a, (0, 1, 0) if i != 1 else (1, 0, 0))
            side = side / (np.linalg.norm(side) or 1)
            up = np.cross(a, side)
            for v in (side, -side, up, -up):                      # arrow head
                rows += [(*tip, *c, 1.0), (*(tip - a * L * 0.2 + v * L * 0.08), *c, 1.0)]
        from .routeui import thick_lines
        data = np.asarray(rows, np.float32)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glUseProgram(self.line_prog)
        self._upload_overlay(data, (3, 4))
        thick_lines(self.line_prog, mvp, len(data), self.width(), self.height(), px=3)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def ground_at(self, x, y):
        """World position of the terrain under the mouse (or the plane at the view's target height)."""
        o, d = self.ray(x, y)
        far = self.distance * 6
        prev = None
        for k in range(1, 400):
            t = far * k / 400
            q = o + d * t
            h = self.terrain_height(q[0], q[2])
            if h is not None and q[1] <= h:
                return (float(q[0]), float(h), float(q[2]))
            prev = q
        if abs(d[1]) > 1e-6:
            t = (self.target[1] - o[1]) / d[1]
            if t > 0:
                q = o + d * t
                return (float(q[0]), float(q[1]), float(q[2]))
        return None

    def _draw_markers(self, mvp):
        rows = []
        for pid, kind, _p in self.markers:
            if kind == "Waypoint" and not self.show_waypoints and pid != self.selected:
                continue
            p = self._marker_pos(pid)
            col, size = KIND_STYLE.get(kind, KIND_STYLE["Other"])
            if pid == self.selected:
                col, size = (1.0, 0.25, 0.85), size + 8
            rows.append((*p, *col, 1.0, size))
        cams = []
        for pid, kind, _p in self.markers:
            if kind == "Waypoint":
                continue
            if pid == self.selected or self.show_all_cameras:
                c = self.camera_position(pid)
                if c is not None and (pid == self.selected or any(abs(v) > 1e-6 for v in self.camera_for(pid))):
                    cams.append((pid, c))
        if cams:
            lines = []
            for pid, c in cams:
                p = self._marker_pos(pid)
                sel = pid == self.selected
                rows.append((*c, 0.2, 0.9, 1.0, 1.0, 18 if sel else 9))
                lines += [(*p, 0.2, 0.9, 1.0, 0.9 if sel else 0.4), (*c, 0.2, 0.9, 1.0, 0.9 if sel else 0.4)]
            data = np.asarray(lines, np.float32)
            GL.glUseProgram(self.line_prog)
            GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.line_prog, "mvp"), 1, GL.GL_TRUE, mvp)
            self._upload_overlay(data, (3, 4))
            GL.glDrawArrays(GL.GL_LINES, 0, len(data))
        if self._rider is not None:
            rows.append((*self._rider, 1.0, 0.12, 0.12, 1.0, 24))      # Mario during a ride-along
        if not rows:
            return
        data = np.asarray(rows, np.float32)
        GL.glUseProgram(self.point_prog)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.point_prog, "mvp"), 1, GL.GL_TRUE, mvp)
        self._upload_overlay(data, (3, 4, 1))
        GL.glDrawArrays(GL.GL_POINTS, 0, len(data))

    def _paint_labels(self, mvp):
        labels = []
        if self.markers:
            ids = [m[0] for m in self.markers]
            pos = [self._marker_pos(i) for i in ids]
            scr, ok = self.project(pos, mvp)
            for (pid, kind, _p), (sx, sy), visible in zip(self.markers, scr, ok):
                if not visible:
                    continue
                if pid == self.selected or (self.show_labels and kind not in ("Waypoint", "Path stop")):
                    labels.append((sx, sy, rules.point_label(pid), pid == self.selected))
        self._badges = []
        if self.show_routes and self.world.routes:
            from .routeui import route_badge_3d
            for r in self.world.routes:
                a, b = self._marker_pos(r["from"]), self._marker_pos(r["to"])
                if a is None or b is None:
                    continue
                sel = r["id"] == getattr(self, "selected_route", "")
                ends, eok = self.project([a, b], mvp)
                if not sel and (not eok.all() or math.hypot(*(ends[1] - ends[0])) < 70):
                    continue                                  # short bend segments: no badge (it would crowd the path)
                scr, ok = self.project([route_badge_3d(a, b, r["type"])], mvp)
                if ok[0]:
                    self._badges.append((float(scr[0][0]), float(scr[0][1]), r["type"],
                                         r["id"] == getattr(self, "selected_route", ""), False, r["id"]))
        self._point_badges = []
        if self.markers:
            from . import pointactions as PA
            from . import rules as _rules
            rows = {r[1]: r for r in self.world.point_rows if len(r) > 2}
            for pid, kind, _p in self.markers:
                keys = [a.key for a in PA.actions_of(pid, _rules.split(rows[pid][2]))] if pid in rows else []
                if keys:
                    scr, ok = self.project([self._marker_pos(pid)], mvp)
                    if ok[0]:
                        self._point_badges.append((float(scr[0][0]), float(scr[0][1]), keys))
        mode = ("PICK a point" if self.pick_handler else ("points locked" if self.locked else "drag points to move them"))
        self._paint_text(labels, "%s · %d models · %d points · %d waypoints · %d routes" % (
            mode, len(self.meshes) - len(self.hidden & set(self.meshes)), len(self.world.point_ids),
            len(self.world.waypoint_ids), len(self.world.routes)))

    def _paint_text(self, labels, status):
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)             # Qt's glyph cache expects the default
        p = QtGui.QPainter(self)
        if getattr(self, "_badges", None) and self.distance < 9000:
            from .routeui import paint_badges
            paint_badges(p, [b[:5] for b in self._badges], 18)
        for sx, sy, keys in getattr(self, "_point_badges", []):
            from .routeui import point_icon
            for k, key in enumerate(keys):
                point_icon(key, 22).paint(p, QtCore.QRect(int(sx - 34 - 24 * k), int(sy - 11), 22, 22))
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        font = p.font(); font.setPointSize(8); font.setBold(True); p.setFont(font)
        for sx, sy, text, sel in labels:
            p.setPen(QtGui.QColor(0, 0, 0, 200))
            p.drawText(QtCore.QPointF(sx + 9, sy - 6), text)
            p.setPen(QtGui.QColor("#ff8adf" if sel else "#fff3c4"))
            p.drawText(QtCore.QPointF(sx + 8, sy - 7), text)
        p.setPen(QtGui.QColor("#cfe3ff"))
        p.drawText(QtCore.QPointF(10, 18), status)
        p.end()

    # ---- interaction -------------------------------------------------------------------------------------------------
    def pick(self, x, y, radius=12):
        if not self.markers:
            return ""
        ids = [m for m in self.markers if self.show_waypoints or m[1] != "Waypoint"]
        scr, ok = self.project([self._marker_pos(m[0]) for m in ids])
        d = np.hypot(scr[:, 0] - x, scr[:, 1] - y)
        d[~ok] = np.inf
        # prefer real points over waypoints when both are close
        penalty = np.array([4.0 if m[1] == "Waypoint" else 0.0 for m in ids])
        i = int(np.argmin(d + penalty))
        return ids[i][0] if d[i] <= radius else ""

    def pick_route(self, x, y, radius=8.0):
        """The route id under (x, y): its badge or within radius pixels of its line."""
        if not (self.world and self.show_routes):
            return None
        for bx, by, _t, _s, _p, rid in getattr(self, "_badges", []):
            if math.hypot(bx - x, by - y) <= 11:
                return rid
        from .routeui import pick_route_screen
        segs = []
        for r in self.world.routes:
            a, b = self._marker_pos(r["from"]), self._marker_pos(r["to"])
            if a is None or b is None:
                continue
            scr, ok = self.project([a, b])
            segs.append((r["id"], tuple(scr[0]) if ok[0] else None, tuple(scr[1]) if ok[1] else None))
        return pick_route_screen(segs, x, y, radius)

    def _camera_marker_hit(self, x, y, radius=12):
        if not self.selected or not self.camera_for:
            return False
        c = self.camera_position(self.selected)
        if c is None:
            return False
        scr, ok = self.project([c])
        return bool(ok[0]) and math.hypot(scr[0][0] - x, scr[0][1] - y) <= radius

    @guarded
    def mousePressEvent(self, e):
        if self.ride:
            self.stop_ride()
            return
        self._mouse = (e.x(), e.y())
        self._press = (e.x(), e.y(), e.button())
        self._candidate = None
        if e.button() != QtCore.Qt.LeftButton or not self.world:
            return
        cam_axis = None
        if self.pick_handler is None and not self.locked and not self.pick(e.x(), e.y()):
            ca, cd = self._gizmo_hit(e.x(), e.y(), 9, self.camera_gizmo(), True) if self.camera_gizmo() else (None, 99)
            pa, pd = self._gizmo_hit(e.x(), e.y(), 9, False, True) if self.gizmo() else (None, 99)
            if ca is not None and (pa is None or cd < pd):          # the nearer arrow wins
                cam_axis = ca
        if cam_axis is not None:                         # an arrow of the camera marker: move it along X, Y or Z
            c = self.camera_gizmo()[0]
            lift = self.camera_lift()
            t0 = self._axis_param(e.x(), e.y(), c, self.AXES[cam_axis])
            if t0 is not None:
                self._cam_drag = (self.selected, tuple(self.camera_for(self.selected)), e.y())
                self._cam_pos = tuple(c - lift)
                self._cam_axis = (cam_axis, tuple(c), t0, lift)
                return
        if self.pick_handler is None and not self.locked and self._camera_marker_hit(e.x(), e.y()):
            off = self.camera_for(self.selected)
            self._cam_drag = (self.selected, tuple(off), e.y())
            self._cam_pos = self.camera_position(self.selected)
            return
        other = self.pick(e.x(), e.y())
        axis = self._gizmo_hit(e.x(), e.y()) if (not other or other == self.selected) else None
        if axis is not None:
            o = np.asarray(self._marker_pos(self.selected), np.float64)
            t0 = self._axis_param(e.x(), e.y(), o, self.AXES[axis])
            if t0 is not None:
                self._axis_drag = (self.selected, axis, tuple(o), t0)
                return
        hit = self.pick(e.x(), e.y())
        if hit and self.pick_handler is None and not self.locked:
            self._candidate = hit

    @guarded
    def mouseMoveEvent(self, e):
        if self._mouse is None and self.place_handler is not None:
            self._ghost = self.ground_at(e.x(), e.y())
            self.update()
            return
        if self._mouse is None:
            hov = self._gizmo_hit(e.x(), e.y())
            chov = self._cam_gizmo_hit(e.x(), e.y())
            if hov != getattr(self, "_axis_hover", None) or chov != getattr(self, "_cam_hover", None):
                self._axis_hover, self._cam_hover = hov, chov
                self.update()
            return
        if getattr(self, "_axis_drag", None) and e.buttons() & QtCore.Qt.LeftButton:
            pid, axis, start, t0 = self._axis_drag
            t = self._axis_param(e.x(), e.y(), np.asarray(start), self.AXES[axis])
            if t is not None:
                step = t - t0
                if e.modifiers() & QtCore.Qt.ControlModifier:     # Ctrl: snap to 10 units
                    step = round(step / 10.0) * 10.0
                p = list(start); p[axis] += step
                self._drag_id, self._drag_pos = pid, tuple(p)
                self.statusMessage.emit("%s → %.1f, %.1f, %.1f  (%s %+.1f; Ctrl snaps to 10)" % (
                    pid, *p, "XYZ"[axis], step))
                self.update()
            self._mouse = (e.x(), e.y())
            return
        dx, dy = e.x() - self._mouse[0], e.y() - self._mouse[1]
        self._mouse = (e.x(), e.y())
        buttons = e.buttons()
        if self._cam_drag and buttons & QtCore.Qt.LeftButton and getattr(self, "_cam_axis", None):
            pid = self._cam_drag[0]
            axis, start, t0, lift = self._cam_axis
            t = self._axis_param(e.x(), e.y(), np.asarray(start), self.AXES[axis])
            if t is not None:
                step = t - t0
                if e.modifiers() & QtCore.Qt.ControlModifier:
                    step = round(step / 10.0) * 10.0
                c = list(np.asarray(start) - lift); c[axis] += step
                self._cam_pos = tuple(c)
                p = self._marker_pos(pid)
                off = tuple(self._cam_pos[i] - p[i] for i in range(3))
                self.statusMessage.emit("Camera of %s: offset %.1f, %.1f, %.1f  (%s %+.1f; Ctrl snaps to 10)" % (
                    pid, *off, "XYZ"[axis], step))
                self.update()
            return
        if self._cam_drag and buttons & QtCore.Qt.LeftButton:
            pid, start_off, _y0 = self._cam_drag
            p = self._marker_pos(pid)
            cur = self._cam_pos
            if e.modifiers() & QtCore.Qt.ShiftModifier:          # Shift: change the height
                k = self.distance * 0.0016
                self._cam_pos = (cur[0], cur[1] - dy * k, cur[2])
            else:
                origin, direction = self.ray(e.x(), e.y())
                if abs(direction[1]) > 1e-6:
                    t = (cur[1] - origin[1]) / direction[1]
                    if t > 0:
                        hit = origin + t * direction
                        self._cam_pos = (float(hit[0]), cur[1], float(hit[2]))
            off = tuple(self._cam_pos[i] - p[i] for i in range(3))
            self.statusMessage.emit("Camera of %s: offset %.1f, %.1f, %.1f" % (pid, *off))
            self.update()
            return
        if self._candidate and not self._drag_id and buttons & QtCore.Qt.LeftButton:
            if abs(e.x() - self._press[0]) + abs(e.y() - self._press[1]) >= 5:
                self._drag_id = self._candidate
                self._drag_pos = tuple(self._marker_pos(self._candidate))
                self.selected = self._candidate
        if self._drag_id and buttons & QtCore.Qt.LeftButton:
            origin, direction = self.ray(e.x(), e.y())
            y0 = self._drag_pos[1]
            if abs(direction[1]) > 1e-6:
                t = (y0 - origin[1]) / direction[1]
                if t > 0:
                    hit = origin + t * direction
                    y = y0
                    if self.snap_to_terrain:
                        h = self.terrain_height(hit[0], hit[2])
                        if h is not None:
                            y = h
                    self._drag_pos = (float(hit[0]), float(y), float(hit[2]))
                    self.statusMessage.emit("%s → %.1f, %.1f, %.1f" % (self._drag_id, *self._drag_pos))
                    self.update()
            return
        if self._candidate:
            return                                              # pressed on a point: no orbit until it is a drag
        if buttons & QtCore.Qt.LeftButton:
            self.yaw -= dx * 0.007
            self.pitch = max(-1.45, min(1.52, self.pitch + dy * 0.007))
        elif buttons & (QtCore.Qt.RightButton | QtCore.Qt.MiddleButton):
            mvp, eye = self.matrices()
            fwd = self.target - eye; fwd /= np.linalg.norm(fwd)
            right = np.cross(fwd, (0, 1, 0)); right /= (np.linalg.norm(right) or 1)
            up = np.cross(right, fwd)
            k = self.distance * 0.0016
            self.target = self.target - right * dx * k + up * dy * k
        self.update()

    @guarded
    def mouseReleaseEvent(self, e):
        if getattr(self, "_axis_drag", None):
            self._axis_drag = None
            pid, pos = self._drag_id, self._drag_pos
            self._drag_id, self._drag_pos = None, None
            if pid and pos is not None:
                self.pointDragged.emit(pid, pos)
            self._mouse = self._press = None
            self.update()
            return
        if self._cam_drag:
            pid, _start, _y = self._cam_drag
            p = self._marker_pos(pid)
            new = tuple(float(self._cam_pos[i] - p[i]) for i in range(3))
            self._cam_drag, self._cam_pos, self._cam_axis = None, None, None
            self.cameraDragged.emit(pid, new)
        elif self._drag_id:
            pid, pos = self._drag_id, self._drag_pos
            self._drag_id, self._drag_pos = None, None
            if pos is not None:
                self.pointDragged.emit(pid, pos)
        elif self._press and e.button() == QtCore.Qt.LeftButton and abs(e.x() - self._press[0]) + abs(e.y() - self._press[1]) < 5:
            hit = self.pick(e.x(), e.y())
            rid = None if (hit or self.pick_handler is not None) else self.pick_route(e.x(), e.y())
            if self.place_handler is not None and not hit:
                g = self.ground_at(e.x(), e.y())
                if g is not None:
                    self.place_handler(g)
            elif self.pick_handler is not None:
                if hit:
                    self.pick_handler(hit)
            elif rid:
                self.selected, self.selected_route = "", rid
                self.routeClicked.emit(rid)
            else:
                self.selected, self.selected_route = hit, ""
                self.pointClicked.emit(hit)
        elif self._press and e.button() == QtCore.Qt.RightButton and abs(e.x() - self._press[0]) + abs(e.y() - self._press[1]) < 5:
            hit = self.pick(e.x(), e.y())
            rid = None if hit else self.pick_route(e.x(), e.y())
            if hit:
                self.pointMenu.emit(hit, e.globalPos())
            elif rid:
                self.routeMenu.emit(rid, e.globalPos())
            else:
                g = self.ground_at(e.x(), e.y())
                if g is not None:
                    self.groundMenu.emit(g, e.globalPos())
        self._mouse = self._press = None
        self._candidate = None
        self.update()

    @guarded
    def wheelEvent(self, e):
        self.distance = max(50.0, min(40000.0, self.distance * (0.88 if e.angleDelta().y() > 0 else 1 / 0.88)))
        self.update()

    @guarded
    def mouseDoubleClickEvent(self, e):
        hit = self.pick(e.x(), e.y())
        if hit:
            self.focus_on(hit)

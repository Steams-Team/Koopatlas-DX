"""
kdx/shader/gamerender.py - draws a world's models with the game's own shaders (View > Shading > Game shaders).

    r = GameRenderer(world, textures, backend="builtin", tool=None)   # no GL needed yet
    r.build()                       # GL 4.5 context current: translate, link, upload (errors in r.errors)
    r.draw(view, proj, size, hidden=set(), pose_for=None, srt_for=None, translucent=False)
    r.handles(model, material)      # True when that material is drawn here (the viewport skips it)
    r.delete()

Each shape becomes one draw: its material's program (picked from the shading model by the material's options),
a vertex array laid out on the program's attribute locations, the material's textures through the program's
reflection, and the gsys render state (blend, depth, cull, alpha test). Shapes skinned to one bone (rigid) or
smoothly are moved to their bind pose on the CPU; shapes on a bone (all of Deluxe's world maps) get the bone's
world matrix in MdlMtx, so skeletal animations move them on the GPU.
"""
from __future__ import annotations

import ctypes

import numpy as np
from OpenGL import GL

from . import bfsha, exact, fmat

BLEND_FACTORS = {
    "zero": GL.GL_ZERO, "one": GL.GL_ONE, "src_color": GL.GL_SRC_COLOR, "one_minus_src_color": GL.GL_ONE_MINUS_SRC_COLOR,
    "src_alpha": GL.GL_SRC_ALPHA, "one_minus_src_alpha": GL.GL_ONE_MINUS_SRC_ALPHA, "dst_alpha": GL.GL_DST_ALPHA,
    "one_minus_dst_alpha": GL.GL_ONE_MINUS_DST_ALPHA, "dst_color": GL.GL_DST_COLOR,
    "one_minus_dst_color": GL.GL_ONE_MINUS_DST_COLOR, "const_color": GL.GL_CONSTANT_COLOR,
    "one_minus_const_color": GL.GL_ONE_MINUS_CONSTANT_COLOR, "const_alpha": GL.GL_CONSTANT_ALPHA,
    "one_minus_const_alpha": GL.GL_ONE_MINUS_CONSTANT_ALPHA, "src_alpha_saturate": GL.GL_SRC_ALPHA_SATURATE,
}
BLEND_OPS = {"add": GL.GL_FUNC_ADD, "src_minus_dst": GL.GL_FUNC_SUBTRACT, "sub": GL.GL_FUNC_SUBTRACT,
             "dst_minus_src": GL.GL_FUNC_REVERSE_SUBTRACT, "rev_sub": GL.GL_FUNC_REVERSE_SUBTRACT,
             "min": GL.GL_MIN, "max": GL.GL_MAX}
BLEND_PRESETS = {"blend": ("add", "src_alpha", "one_minus_src_alpha"), "add": ("add", "src_alpha", "one"),
                 "sub": ("rev_sub", "src_alpha", "one"), "mul": ("add", "zero", "src_color")}
COMPARE = {"never": GL.GL_NEVER, "less": GL.GL_LESS, "equal": GL.GL_EQUAL, "lequal": GL.GL_LEQUAL,
           "greater": GL.GL_GREATER, "notequal": GL.GL_NOTEQUAL, "gequal": GL.GL_GEQUAL, "always": GL.GL_ALWAYS}
ALPHA_FUNC_IDS = {"never": 0, "less": 1, "equal": 2, "lequal": 3, "greater": 4, "notequal": 5, "gequal": 6,
                  "always": 7}
IDENT34 = np.eye(4, dtype=np.float32)[:3].reshape(-1)
# The route mask is the game's route-reveal ramp: a texture-matrix animation slides it along a route as the route
# opens. An editor shows every route open, so it reads white.
OPEN_SYMBOLS = {"cTexMapRouteMask"}
MIRROR_SYMBOLS = {"cReflectionMap"}


def gl_version():
    """(major, minor) of the current context."""
    try:
        return int(GL.glGetIntegerv(GL.GL_MAJOR_VERSION)), int(GL.glGetIntegerv(GL.GL_MINOR_VERSION))
    except Exception:
        return 0, 0


def find_bfsha(world):
    """The world archive's shader archive bytes (Wii_UBER.Nin_NX_NVN.bfsha), or None."""
    for name, data in getattr(world, "entries", ()):
        if str(name).lower().endswith(".bfsha"):
            return bytes(data)
    return None


class RenderState:
    """The gsys render state of a material (render infos + alpha-test options)."""

    def __init__(self, mi):
        def info(name, default):
            v = mi.render_info.get(name)
            return v[0] if v else default
        self.mode = str(info("gsys_render_state_mode", "opaque"))
        self.face = str(info("gsys_render_state_display_face", "front"))
        blend_mode = str(info("gsys_render_state_blend_mode", "none"))
        self.blend = self.mode in ("translucent", "custom") and blend_mode == "color"
        self.rgb_op = BLEND_OPS.get(info("gsys_color_blend_rgb_op", "add"), GL.GL_FUNC_ADD)
        self.rgb_src = BLEND_FACTORS.get(info("gsys_color_blend_rgb_src_func", "src_alpha"), GL.GL_SRC_ALPHA)
        self.rgb_dst = BLEND_FACTORS.get(info("gsys_color_blend_rgb_dst_func", "one_minus_src_alpha"),
                                         GL.GL_ONE_MINUS_SRC_ALPHA)
        self.a_op = BLEND_OPS.get(info("gsys_color_blend_alpha_op", "add"), GL.GL_FUNC_ADD)
        self.a_src = BLEND_FACTORS.get(info("gsys_color_blend_alpha_src_func", "one"), GL.GL_ONE)
        self.a_dst = BLEND_FACTORS.get(info("gsys_color_blend_alpha_dst_func", "zero"), GL.GL_ZERO)
        preset = info("gsys_render_state_blend_preset", None)
        if self.blend and "gsys_color_blend_rgb_src_func" not in mi.render_info and preset in BLEND_PRESETS:
            op, s, d = BLEND_PRESETS[preset]
            self.rgb_op, self.rgb_src, self.rgb_dst = BLEND_OPS[op], BLEND_FACTORS[s], BLEND_FACTORS[d]
        cc = mi.render_info.get("gsys_color_blend_const_color")
        self.blend_const = tuple(float(x) for x in cc[:4]) if cc and len(cc) >= 4 else (0.0, 0.0, 0.0, 0.0)
        self.depth_test = str(info("gsys_depth_test_enable", "true")) == "true"
        self.depth_func = COMPARE.get(info("gsys_depth_test_func", "lequal"), GL.GL_LEQUAL)
        self.depth_write = str(info("gsys_depth_test_write", "true")) == "true"
        # the engine sets the alpha test from the render state; the stored options are often just the defaults
        opt = mi.shader_options
        self.alpha_test = (opt.get("gsys_alpha_test_enable") in ("1", "true")
                           or str(info("gsys_alpha_test_enable", "false")) == "true" or self.mode == "mask")
        func = info("gsys_alpha_test_func", None)
        if func in ALPHA_FUNC_IDS:
            self.alpha_func = ALPHA_FUNC_IDS[func]
        else:
            func = opt.get("gsys_alpha_test_func", "6")
            self.alpha_func = int(func) if func.isdigit() and int(func) < 8 else 6
        ref = next((p.value for p in mi.params if p.name == "gsys_alpha_test_ref_value" and p.value), None)
        try:
            self.alpha_ref = float(ref[0]) if ref else float(info("gsys_alpha_test_value", 0.5) or 0.5)
        except (TypeError, ValueError):
            self.alpha_ref = 0.5
        try:
            self.priority = int(info("priority", 0) or 0)
        except (TypeError, ValueError):
            self.priority = 0

    RENDERSTATE = {"opaque": "0", "mask": "1", "translucent": "2", "custom": "3"}

    def options(self, mi, sm):
        """The material's shader options as the engine sets them: gsys_renderstate and the alpha test come from the
        render state (the stored options are often the defaults), e.g. leaves are "mask" with alpha test on."""
        opts = dict(mi.shader_options)
        names = {o.name: o for o in sm.options}

        def put(name, value):
            o = names.get(name)
            if o is not None and value in o.choices:
                opts[name] = value
        put("gsys_renderstate", self.RENDERSTATE.get(self.mode, "0"))
        put("gsys_alpha_test_enable", "1" if self.alpha_test else "0")
        if self.alpha_test:
            put("gsys_alpha_test_func", str(self.alpha_func))
        return opts

    def apply(self):
        if self.blend:
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendEquationSeparate(self.rgb_op, self.a_op)
            GL.glBlendFuncSeparate(self.rgb_src, self.rgb_dst, self.a_src, self.a_dst)
            GL.glBlendColor(*self.blend_const)
        else:
            GL.glDisable(GL.GL_BLEND)
        if self.depth_test:
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glDepthFunc(self.depth_func)
        else:
            GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_TRUE if self.depth_write and not self.blend else GL.GL_FALSE)
        if self.face in ("both", "none"):
            GL.glDisable(GL.GL_CULL_FACE)
        else:
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_BACK if self.face == "front" else GL.GL_FRONT)


class Item:
    """One shape drawn with one program."""
    __slots__ = ("model", "material", "shape", "info", "program", "state", "vao", "vbo", "ibo", "count",
                 "bone", "center", "option_data")


def _attr_array(vb, name, n):
    vals = vb.read(name)
    a = np.zeros((n, 4), np.float32)
    if not vals:
        return None
    arr = np.asarray(vals, np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    k = min(4, arr.shape[1])
    m = min(n, len(arr))
    a[:m, :k] = arr[:m, :k]
    return a


class GameRenderer:
    def __init__(self, world, textures, backend="builtin", tool=None, romfs=None):
        self.world, self.textures_src = world, textures or {}
        self.romfs = romfs
        self.backend, self.tool = backend, tool
        self.errors = []                        # problems the user may want to read (status line / log)
        self.items = []
        self.programs = {}                      # (shading model, program index) -> GameProgram or None
        self.gl_textures = {}                   # texture name -> GL id (0 = undecodable)
        self.samplers = {}                      # (wrap s, wrap t) -> GL sampler object
        self.buffers = None
        self.white = self.shadow_tex = self.shadow_sampler = 0
        self.built = False
        self.lightmaps = None                   # {name: (LightmapDef, lights)} from env_pack, on first use
        self.lm_live = {}                       # light map name -> GL texture baked for lm_rot
        self.lm_rot = np.eye(3)
        self.drawn = set()                      # (model, material) drawn by this renderer
        sh = find_bfsha(world)
        self.models = {}
        if sh is None:
            self.errors.append("The world archive has no shader archive (.bfsha).")
        else:
            try:
                if bfsha.version(sh) >= 8:
                    self.errors.append("Shader archive version %d is not supported (Deluxe's is 7)." % bfsha.version(sh))
                else:
                    self.models = bfsha.parse(sh)
            except Exception as e:
                self.errors.append("The shader archive could not be read: %s" % e)
        try:
            self.mat_info = fmat.read_materials(world.bfres_bytes)
        except Exception as e:
            self.mat_info = {}
            self.errors.append("Material shader assigns could not be read: %s" % e)

    @property
    def usable(self):
        return bool(self.models) and bool(self.mat_info)

    def handles(self, model, material):
        return (model, material) in self.drawn

    # ---- building (GL current) ------------------------------------------------------------------------------------
    def program(self, smn, pi):
        key = (smn, pi)
        if key in self.programs:
            return self.programs[key]
        sm = self.models[smn]
        prog = None
        try:
            stages = sm.program(pi).stages
            if "vertex" not in stages or "pixel" not in stages:
                raise RuntimeError("program %d has no vertex/pixel stage" % pi)
            src = {st: exact.translate_stage(stages[st].code, st, smn, self.backend, self.tool)
                   for st in ("vertex", "pixel")}
            prog = exact.GameProgram(sm, pi, src)
        except Exception as e:
            self.errors.append("%s program %d: %s" % (smn, pi, str(e).strip().splitlines()[0][:300] if str(e) else e))
        self.programs[key] = prog
        return prog

    def build(self):
        if self.built:
            return
        self.built = True
        if not self.usable:
            return
        self.buffers = exact.Buffers()
        self.white = self._upload_rgba(np.full((1, 1, 4), 255, np.uint8), False)
        self.shadow_tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.shadow_tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_DEPTH_COMPONENT32F, 1, 1, 0, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT,
                        np.ones(1, np.float32))
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self.shadow_sampler = GL.glGenSamplers(1)
        for k, v in ((GL.GL_TEXTURE_COMPARE_MODE, GL.GL_COMPARE_REF_TO_TEXTURE), (GL.GL_TEXTURE_COMPARE_FUNC, GL.GL_LEQUAL),
                     (GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
                     (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glSamplerParameteri(self.shadow_sampler, k, v)
        for model in self.world.model.models:
            for si, shape in enumerate(model.shapes):
                try:
                    self._build_item(model, shape)
                except Exception as e:
                    self.errors.append("%s / %s: %s" % (model.name, shape.name, e))
        mats = {}
        for it in self.items:
            mats.setdefault((it.model, it.material), []).append(it)
        # a material is drawn here only when every one of its shapes is (otherwise the viewport draws it all)
        for key, its in mats.items():
            model = next(m for m in self.world.model.models if m.name == key[0])
            total = sum(1 for s in model.shapes if s.material_index < len(model.materials)
                        and model.materials[s.material_index].name == key[1])
            if len(its) == total:
                self.drawn.add(key)
        self.items = [it for it in self.items if (it.model, it.material) in self.drawn]

    def _build_item(self, model, shape):
        if shape.material_index >= len(model.materials) or shape.vertex_buffer_index >= len(model.vertex_buffers):
            return
        mat_name = model.materials[shape.material_index].name
        mi = self.mat_info.get((model.name, mat_name))
        if mi is None or mi.shading_model not in self.models or not shape.meshes:
            return
        sm = self.models[mi.shading_model]
        state = RenderState(mi)
        options = state.options(mi, sm)
        pi = sm.find_program(options)
        prog = self.program(mi.shading_model, pi)
        if prog is None:
            return
        vb = model.vertex_buffers[shape.vertex_buffer_index]
        n = vb.vertex_count
        bones = model.bones
        # where the vertices are: on a bone (MdlMtx[0] = its world matrix), or moved to the bind pose here
        per_vertex = None
        bone = shape.bone_index if 0 <= shape.bone_index < len(bones) else -1
        if shape.skin_count >= 1:
            idx = vb.read("_i0")
            if shape.skin_count == 1 and idx:
                per_vertex = []
                for t in idx[:n]:
                    slot = int(t[0])
                    b = shape.skin_bone_indices[slot] if slot < len(shape.skin_bone_indices) else shape.bone_index
                    per_vertex.append(b if 0 <= b < len(bones) else -1)
                per_vertex += [-1] * (n - len(per_vertex))
            bone = -1                            # smooth skins are already in model space at the bind pose
        cols = []
        for shader_attr, model_attr in mi.attrib_assign.items():
            loc = sm.attributes.get(shader_attr)
            if loc is None or loc < 0 or loc not in prog.inputs:
                continue
            if model_attr.startswith("_i"):
                vals = vb.read(model_attr)
                bits = np.zeros((n, 4), np.int32)
                if vals:
                    arr = np.round(np.asarray(vals, np.float64)).astype(np.int32)
                    arr = arr[:, None] if arr.ndim == 1 else arr
                    bits[:min(n, len(arr)), :min(4, arr.shape[1])] = arr[:n, :4]
                a = bits.view(np.float32)
            else:
                a = _attr_array(vb, model_attr, n)
                if a is None:
                    a = np.zeros((n, 4), np.float32)
                    if model_attr.startswith("_c"):
                        a[:] = 1.0
                elif model_attr.startswith(("_p", "_c")) and len(vb.read(model_attr)[0]) < 4:
                    a[:, 3] = 1.0
                if per_vertex is not None and model_attr[:2] in ("_p", "_n", "_t", "_b"):
                    a = self._pretransform(a, per_vertex, bones, model_attr.startswith("_p"))
            cols.append((loc, a))
        for loc in prog.inputs:                   # inputs the material does not feed read zero
            if not any(l == loc for l, _ in cols):
                cols.append((loc, np.zeros((n, 4), np.float32)))
        tris = shape.meshes[0].triangles()            # LOD 0
        if not tris:
            return
        ind = np.asarray(tris, np.uint32).reshape(-1)
        if int(ind.max()) >= n:
            ind = np.asarray([t for t in tris if max(t) < n], np.uint32).reshape(-1)
        data = np.ascontiguousarray(np.concatenate([a for _, a in cols], 1) if cols else np.zeros((n, 4), np.float32))
        it = Item()
        it.model, it.material, it.shape, it.info, it.program = model.name, mat_name, shape, mi, prog
        it.state = state
        it.bone = bone
        it.count = int(ind.size)
        p = vb.read("_p0")
        it.center = np.asarray(p, np.float64)[:, :3].mean(axis=0) if p else np.zeros(3)
        it.option_data = exact.option_block(sm, mi, options)
        it.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(it.vao)
        it.vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, it.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        stride = 16 * len(cols)
        for k, (loc, _) in enumerate(cols):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, 4, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(16 * k))
        it.ibo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, it.ibo)
        ind = np.ascontiguousarray(ind)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, ind.nbytes, ind, GL.GL_STATIC_DRAW)
        GL.glBindVertexArray(0)
        for unit, smp, _sym, _kind in prog.textures:
            name, _raw = mi.texture_for_sampler(smp) if smp else (None, None)
            if name:
                self.texture(name)
        self.items.append(it)

    @staticmethod
    def _pretransform(a, per_vertex, bones, is_point):
        out = a.copy()
        idx = np.asarray(per_vertex)
        for b in set(per_vertex):
            if b < 0:
                continue
            m = np.asarray(bones[b].world, np.float64).reshape(3, 4)
            sel = idx == b
            v = a[sel, :3].astype(np.float64) @ m[:, :3].T
            if is_point:
                v += m[:, 3]
            out[sel, :3] = v
        return out

    def texture(self, name):
        if name in self.gl_textures:
            return self.gl_textures[name]
        if self.lightmaps is None:
            from . import lightmaps
            try:
                self.lightmaps = lightmaps.world_lightmap_defs(getattr(self.world, "path", None), self.romfs)
            except Exception as e:
                self.lightmaps = {}
                self.errors.append("light maps (Env/env_pack.szs): %s" % e)
        tex = self.textures_src.get(name)
        tid = 0
        if name in self.lightmaps:                # made by the game at run time from env_pack, per camera angle
            from . import lightmaps
            d, lights = self.lightmaps[name]
            tid = self._upload_float(lightmaps.bake_lightmap(d, lights, self.lm_rot))
            self.lm_live[name] = tid
        elif tex is not None:
            try:
                if getattr(tex, "kind", "") == "snorm":
                    raw = tex.raw_rgba().astype(np.int16) - 128
                    out = np.zeros(raw.shape, np.int8)
                    for i, sel in enumerate(tex.channels):
                        out[..., i] = 0 if sel == 0 else 127 if sel == 1 else raw[..., sel - 2] if 2 <= sel <= 5 else 0
                    tid = self._upload_rgba(out, True)
                else:
                    tid = self._upload_rgba(tex.rgba(), False)
            except Exception as e:
                self.errors.append("texture %s: %s" % (name, e))
        self.gl_textures[name] = tid
        return tid

    @staticmethod
    def _upload_float(rgba):
        tid = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA16F, rgba.shape[1], rgba.shape[0], 0, GL.GL_RGBA, GL.GL_FLOAT,
                        np.ascontiguousarray(rgba, np.float32))
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return tid

    @staticmethod
    def _upload_rgba(rgba, snorm):
        tid = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8_SNORM if snorm else GL.GL_RGBA8, rgba.shape[1], rgba.shape[0],
                        0, GL.GL_RGBA, GL.GL_BYTE if snorm else GL.GL_UNSIGNED_BYTE, np.ascontiguousarray(rgba))
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return tid

    def sampler(self, wrap):
        s = self.samplers.get(wrap)
        if s is None:
            s = self.samplers[wrap] = GL.glGenSamplers(1)
            GL.glSamplerParameteri(s, GL.GL_TEXTURE_WRAP_S, wrap[0])
            GL.glSamplerParameteri(s, GL.GL_TEXTURE_WRAP_T, wrap[1])
            GL.glSamplerParameteri(s, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glSamplerParameteri(s, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            try:
                GL.glSamplerParameterf(s, 0x84FE, 4.0)            # GL_TEXTURE_MAX_ANISOTROPY
            except Exception:
                pass
        return s

    # ---- drawing ------------------------------------------------------------------------------------------------
    def draw(self, view, proj, size, hidden=(), pose_for=None, srt_for=None, translucent=False):
        """Draw the opaque/mask items (translucent False) or the blended ones, far to near."""
        if not self.items:
            return
        view = np.asarray(view, np.float64)
        vp = np.asarray(proj, np.float64) @ view
        bufs = self.buffers
        bufs.set(0, exact.support_block(*size))
        bufs.cbuf(5, exact.env_block(view[:3], vp))
        self._update_lightmaps(view[:3, :3])
        poses = {}
        items = [it for it in self.items if it.model not in hidden and it.state.blend == translucent]
        if translucent:
            def depth(it):
                return (view @ np.r_[it.center, 1.0])[2]
            items.sort(key=lambda it: (it.state.priority, depth(it)))
            GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)         # decals (paths, foam) lie on the ground: no flicker
            GL.glPolygonOffset(-1.0, -4.0)
        model_by_name = {m.name: m for m in self.world.model.models}
        GL.glColorMask(GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE, GL.GL_FALSE)   # the window stays opaque
        for it in items:
            prog, mi = it.program, it.info
            GL.glUseProgram(prog.id)
            bufs.cbuf(3, it.option_data)
            overrides = None
            if srt_for is not None:
                for p in mi.params:
                    if p.type in (30, 31) and p.value:
                        v = srt_for(it.model, it.material, p.name)
                        if v:
                            overrides = overrides or {}
                            overrides[p.name] = (int(p.value[0]), v["sx"], v["sy"], v["rot"], v["tx"], v["ty"])
            bufs.cbuf(4, exact.material_block(prog.sm, mi, overrides))
            mtx = IDENT34
            if it.bone >= 0:
                if it.model not in poses:
                    poses[it.model] = pose_for(model_by_name[it.model]) if pose_for else None
                pose = poses[it.model]
                src = pose[it.bone] if pose is not None and it.bone < len(pose) else \
                    model_by_name[it.model].bones[it.bone].world
                mtx = np.asarray(src, np.float32).reshape(-1)[:12]
            bufs.cbuf(6, exact.mtx_rows(mtx))
            for unit, smp, sym, kind in prog.textures:
                GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
                if sym in OPEN_SYMBOLS:
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self.white)
                    GL.glBindSampler(unit, self.sampler((GL.GL_REPEAT, GL.GL_REPEAT)))
                    continue
                if kind.endswith("Shadow"):          # the shadow map (not drawn here): everything lit
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self.shadow_tex)
                    GL.glBindSampler(unit, self.shadow_sampler)
                    continue
                name, raw = mi.texture_for_sampler(smp) if smp else (None, None)
                if name is None and sym in MIRROR_SYMBOLS:
                    # the water's reflection is a render target of the scene, not drawn here: reflect the albedo
                    name, raw = mi.texture_for_sampler(prog.albedo_sampler) if prog.albedo_sampler else (None, None)
                tid = self.texture(name) if name else 0
                GL.glBindTexture(GL.GL_TEXTURE_2D, tid or self.white)
                GL.glBindSampler(unit, self.sampler(mi.wrap(raw)))
            st = it.state
            host_test = st.alpha_test and not prog.has_discard
            GL.glUniform1i(prog.alpha_func_loc, int(st.alpha_func) if host_test else 7)
            GL.glUniform1f(prog.alpha_ref_loc, float(st.alpha_ref))
            st.apply()
            GL.glBindVertexArray(it.vao)
            GL.glDrawElements(GL.GL_TRIANGLES, it.count, GL.GL_UNSIGNED_INT, None)
        GL.glColorMask(GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE)
        for unit in range(16):
            GL.glBindSampler(unit, 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindVertexArray(0)
        GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_BLEND)
        GL.glBlendEquation(GL.GL_FUNC_ADD)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def _update_lightmaps(self, rot):
        """Bake the light maps again for a new camera rotation (they hold the light per view-space normal)."""
        if not self.lm_live or np.abs(rot - self.lm_rot).max() < 0.02:
            return
        from . import lightmaps
        self.lm_rot = np.array(rot, float)
        for name, tid in self.lm_live.items():
            d, lights = self.lightmaps[name]
            a = np.ascontiguousarray(lightmaps.bake_lightmap(d, lights, self.lm_rot), np.float32)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, a.shape[1], a.shape[0], GL.GL_RGBA, GL.GL_FLOAT, a)
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def delete(self):
        for it in self.items:
            GL.glDeleteVertexArrays(1, [it.vao])
            GL.glDeleteBuffers(2, [it.vbo, it.ibo])
        for p in self.programs.values():
            if p is not None:
                p.delete()
        ids = [t for t in self.gl_textures.values() if t] + [t for t in (self.white, self.shadow_tex) if t]
        if self.shadow_sampler:
            GL.glDeleteSamplers(1, [self.shadow_sampler])
        if ids:
            GL.glDeleteTextures(ids)
        if self.samplers:
            GL.glDeleteSamplers(len(self.samplers), list(self.samplers.values()))
        if self.buffers:
            self.buffers.delete()
        self.items, self.programs, self.gl_textures, self.samplers, self.buffers = [], {}, {}, {}, None
        self.lm_live = {}
        self.drawn = set()
        self.white = self.shadow_tex = self.shadow_sampler = 0
        self.built = False

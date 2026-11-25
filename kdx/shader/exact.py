"""
kdx/shader/exact.py - the game's own NVN (Maxwell) programs as OpenGL 4.5 programs, bound the way NVN binds them.

Ported from Yaz0loom-DX (dvdx/render/exact.py). Pipeline:

  1. kdx.shader.bfsha pulls a shading model's program (SPH + code per stage) out of the world's Wii_UBER bfsha;
     the program is the one whose option key matches the material's shader options (find_program).
  2. A translator turns each stage into GLSL 4.50: the built-in one (kdx.shader.translator, pure Python, the
     default) or an external Ryujinx.ShaderTools(.exe) that you point the app at (nothing is downloaded). Results
     are cached in %LOCALAPPDATA%/KoopatlasDX/cache/shaders/<backend>/.
  3. Binding (the GLSL interface is the one Ryujinx.ShaderTools writes, so both backends bind the same way):

       NVN uniform slot s (BNSH reflection)  ->  Maxwell cbuf s + 3  ->  GLSL block _vp_c{n}/_fp_c{n}, binding n + 1
         0 ShaderOption   static options (int)
         1 Material       material params at the bfsha member offsets
         2 MdlEnvView     cView[3] cViewProj[4] lights, ambient, fog ...
         3 MdlMtx         3 x vec4 per matrix: the world matrix of the shape (entry 0) / skin palette
         4 Shp            (empty in the world-map shaders)
       textures: sampler {vp,fp}_t_tcb_{H}, H = 8 + 2 * reflection slot; the slot's symbol (cTexMap0, cBakeMap0 ...)
       names the shading model sampler (_a0, _b0 ...) through the bfsha symbol table.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
from OpenGL import GL

from . import bfsha

BLOCK_SIZE = 0x10000              # every cbuf is declared vec4[4096]
SUPPORT_SIZE = 1408
TRANSLATOR_VERSION = 2            # bump when the built-in translator's output changes (invalidates the cache)

BACKENDS = ("builtin", "ryujinx")


def cache_root():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "KoopatlasDX" / "cache" / "shaders"
    return base


def find_shadertools(path=""):
    """An existing Ryujinx.ShaderTools executable: `path`, $KDX_SHADERTOOLS, or None."""
    for c in (path, os.environ.get("KDX_SHADERTOOLS", "")):
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


_LOCK = threading.Lock()


def translate_stage(code: bytes, stage: str, name="", backend="builtin", tool=None):
    """One stage's NVN code (SPH + instructions) -> GLSL, from the cache when it was translated before."""
    if backend == "ryujinx" and not tool:
        raise RuntimeError("Ryujinx.ShaderTools is not set")
    tag = "builtin%d" % TRANSLATOR_VERSION if backend == "builtin" else "ryujinx"
    h = hashlib.sha1(code + stage.encode()).hexdigest()
    path = cache_root() / tag / (h + ".glsl")
    with _LOCK:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            pass
        if backend == "builtin":
            from . import translator
            src, _warnings = translator.translate(code, stage, name)
        else:
            with tempfile.TemporaryDirectory() as d:
                binp, out = os.path.join(d, "in.bin"), os.path.join(d, "out.glsl")
                with open(binp, "wb") as f:
                    f.write(code)
                r = subprocess.run([tool, binp, out], capture_output=True, text=True, timeout=60,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0 or not os.path.isfile(out):
                    raise RuntimeError("Ryujinx.ShaderTools failed: %s" % (r.stderr or r.stdout)[-300:])
                src = open(out, encoding="utf-8").read()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(src, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass                                       # a read-only cache only costs time
        return src


# ---------------------------------------------------------------------------------------------------- GL program
_SAMPLER_RE = re.compile(r"uniform\s+(sampler\w*)\s+(\w+)\s*;")
_BLOCK_RE = re.compile(r"uniform\s+_(\w+)\s*\{")
_INPUT_RE = re.compile(r"layout \(location = (\d+)\) in vec4 in_attr(\d+);")


def _prep_pixel(src):
    """Keep colour output 0; add a host alpha test like NVN's colour state (7 = always: off)."""
    src = src.replace("void main()", "void main_nvn()", 1)
    src += """
uniform int kdx_alpha_func;
uniform float kdx_alpha_ref;
void main()
{
    main_nvn();
    float a = out_attr0.w;
    bool pass = true;
    if (kdx_alpha_func == 0) pass = false;
    else if (kdx_alpha_func == 1) pass = a < kdx_alpha_ref;
    else if (kdx_alpha_func == 2) pass = a == kdx_alpha_ref;
    else if (kdx_alpha_func == 3) pass = a <= kdx_alpha_ref;
    else if (kdx_alpha_func == 4) pass = a > kdx_alpha_ref;
    else if (kdx_alpha_func == 5) pass = a != kdx_alpha_ref;
    else if (kdx_alpha_func == 6) pass = a >= kdx_alpha_ref;
    if (!pass) discard;
}
"""
    return src


def _link(vs, fs):
    def sh(src, kind):
        s = GL.glCreateShader(kind)
        GL.glShaderSource(s, src)
        GL.glCompileShader(s)
        if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
            log = GL.glGetShaderInfoLog(s)
            GL.glDeleteShader(s)
            raise RuntimeError((log.decode(errors="replace") if isinstance(log, bytes) else str(log))[:1500])
        return s
    a = sh(vs, GL.GL_VERTEX_SHADER)
    try:
        b = sh(fs, GL.GL_FRAGMENT_SHADER)
    except RuntimeError:
        GL.glDeleteShader(a)
        raise
    p = GL.glCreateProgram()
    GL.glAttachShader(p, a)
    GL.glAttachShader(p, b)
    GL.glLinkProgram(p)
    GL.glDeleteShader(a)
    GL.glDeleteShader(b)
    if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
        log = GL.glGetProgramInfoLog(p)
        GL.glDeleteProgram(p)
        raise RuntimeError((log.decode(errors="replace") if isinstance(log, bytes) else str(log))[:1500])
    return p


def sampler_symbols(sm):
    """{reflection symbol (cTexMap0 ...): shading model sampler name (_a0 ...)}."""
    out = {}
    for name, syms in zip(sm.samplers, sm.symbols.get("samplers", []) if sm.symbols else []):
        for s in syms:
            if s:
                out[s] = name
    for name in sm.samplers:                    # Wii_UBER distant views: tex_mapN without a symbol table
        out.setdefault(bfsha.sampler_symbol(name), name)
    return out


class GameProgram:
    """One linked program of a shading model: its textures (GL location, unit, shader sampler) and inputs."""

    def __init__(self, sm, program_index, sources):
        self.sm, self.index = sm, program_index
        vs, fs = sources["vertex"], _prep_pixel(sources["pixel"])
        self.id = _link(vs, fs)
        GL.glUseProgram(self.id)
        self.cbufs = set()
        for src in (vs, fs):
            for name in _BLOCK_RE.findall(src):
                idx = GL.glGetUniformBlockIndex(self.id, "_" + name)
                if idx == GL.GL_INVALID_INDEX:
                    continue
                if name == "support_buffer":
                    GL.glUniformBlockBinding(self.id, idx, 0)
                    continue
                m = re.match(r"[vf]p_c(\d+)$", name)
                if m:
                    c = int(m.group(1))
                    GL.glUniformBlockBinding(self.id, idx, c + 1)
                    self.cbufs.add(c)
        prog = sm.program(program_index)
        sym_to_smp = sampler_symbols(sm)
        slot_symbol = {st: {v: k for k, v in prog.stages[st].reflection.samplers.items()}
                       for st in ("vertex", "pixel") if st in prog.stages}
        self.textures = []                     # (unit, shader sampler name or None, symbol, GLSL sampler type)
        unit = 0
        for stage, src in (("vertex", vs), ("pixel", fs)):
            for kind, name in _SAMPLER_RE.findall(src):
                m = re.match(r"[vf]p_t_tcb_([0-9A-Fa-f]+)$", name)
                if not m:
                    continue
                loc = GL.glGetUniformLocation(self.id, name)
                if loc < 0:
                    continue
                slot = (int(m.group(1), 16) - 8) // 2
                sym = slot_symbol.get(stage, {}).get(slot)
                GL.glUniform1i(loc, unit)
                self.textures.append((unit, sym_to_smp.get(sym), sym, kind))
                unit += 1
        self.albedo_sampler = sym_to_smp.get("cTexMap0")
        self.inputs = sorted({int(l) for l, _ in _INPUT_RE.findall(vs)})
        self.has_discard = "discard" in sources["pixel"]
        self.alpha_func_loc = GL.glGetUniformLocation(self.id, "kdx_alpha_func")
        self.alpha_ref_loc = GL.glGetUniformLocation(self.id, "kdx_alpha_ref")

    def delete(self):
        GL.glDeleteProgram(self.id)


# ---------------------------------------------------------------------------------------------- block contents
def tex_srt_matrix(value):
    """texsrt (mode, sx, sy, rot, tx, ty) -> 2x3 rows, nw::g3d conventions (0 Maya, 1 3ds Max, 2 Softimage)."""
    if not value or len(value) < 6:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    mode, sx, sy, r, tx, ty = value[:6]
    c, s = math.cos(r), math.sin(r)
    if mode == 1:
        return ((sx * c, sx * s, sx * (-c * (tx + 0.5) + s * (ty - 0.5)) + 0.5),
                (-sy * s, sy * c, sy * (s * (tx + 0.5) + c * (ty - 0.5)) + 0.5))
    if mode == 2:
        return ((sx * c, sx * s, -sx * (c * tx + s * ty)),
                (-sy * s, sy * c, sy * (s * tx - c * ty) + 1 - sy))
    return ((sx * c, sx * s, sx * ((-0.5 * c - 0.5 * s + 0.5) - tx)),
            (-sy * s, sy * c, sy * ((0.5 * s - 0.5 * c + 0.5) + ty) + 1.0 - sy))


def material_block(sm, mat, overrides=None):
    """The Material block (cbuf 4) from a material's params; overrides: {param name: value} (animations)."""
    blk = sm.block("material")
    if blk is None:
        return b""
    data = bytearray(blk.default)
    params = {p.name: p for p in mat.params}
    overrides = overrides or {}
    for m in blk.members:
        p = params.get(m.name)
        if p is None:
            continue
        v = overrides.get(m.name, p.value)
        t = p.type
        if not v:
            continue
        if t in (30, 31):                      # texsrt -> three float2 columns (m00 m10 m01 m11 tx ty)
            r0, r1 = tex_srt_matrix((int(v[0]),) + tuple(float(x) for x in v[1:6]))
            raw = struct.pack("<8f", r0[0], r1[0], r0[1], r1[1], r0[2], r1[2], 0.0, 0.0)
        elif t == 28:                          # srt2d: scale xy, rotate, translate xy
            sx, sy, r, tx, ty = (float(x) for x in v[:5])
            c, s = math.cos(r), math.sin(r)
            raw = struct.pack("<8f", sx * c, sx * s, -sy * s, sy * c, tx, ty, 0.0, 0.0)
        elif 4 <= t <= 7:
            raw = struct.pack("<%di" % len(v), *[int(x) for x in v])
        elif t <= 3 or 8 <= t <= 11:
            raw = struct.pack("<%dI" % len(v), *[int(x) for x in v])
        else:
            raw = struct.pack("<%df" % len(v), *[float(x) for x in v])
        end = min(len(data), m.offset + len(raw))
        if m.offset < end:
            data[m.offset:end] = raw[:end - m.offset]
    return bytes(data)


def option_block(sm, mat, options=None):
    """The ShaderOption block (cbuf 3); options: the effective option choices (default: the material's)."""
    blk = sm.block("option")
    if blk is None:
        return b""
    options = mat.shader_options if options is None else options
    data = bytearray(blk.default)
    for m in blk.members:
        v = options.get(m.name)
        if v is None or m.offset + 4 > len(data):
            continue
        try:
            iv = int(v)
        except ValueError:
            iv = 1 if v == "true" else 0
        struct.pack_into("<i", data, m.offset, iv)
    return bytes(data)


def env_block(view, viewproj, light_dir=(0.35, 0.85, 0.4), light_color=(1.0, 1.0, 1.0), ambient=(0.4, 0.4, 0.4)):
    """MdlEnvView (0x410, std140): cView[3] cViewProj[4] cLightDiffDir[8] cLightDiffColor[8] cAmbColor[2]
    cFogColor[8] cFogStart[8] cFogStartEndInv[8] cShadowMtx[4] cFogDir[8] cTexProjMtx[4]. The world-map programs
    read only the matrices (their light comes from the light-map and bake textures); one sun and an ambient colour
    are filled in for programs that read more, and fog is off."""
    d = bytearray(0x410)

    def v4(off, v):
        struct.pack_into("<4f", d, off, *(list(v) + [0.0] * 4)[:4])
    for i in range(3):
        v4(0x00 + i * 16, view[i])
    for i in range(4):
        v4(0x30 + i * 16, viewproj[i])
    L = np.asarray(light_dir, float)
    L = L / (np.linalg.norm(L) or 1.0)
    v4(0x70, tuple(-L) + (0.0,))
    v4(0xF0, tuple(light_color) + (1.0,))
    v4(0x170, tuple(ambient) + (1.0,))
    for i in range(8):
        struct.pack_into("<f", d, 0x210 + i * 16, 1000000.0)
        struct.pack_into("<f", d, 0x290 + i * 16, 1000000.0)
        v4(0x350 + i * 16, (0.0, 0.0, -1.0, 0.0))
    for i in range(4):
        v4(0x310 + i * 16, [1.0 if j == i else 0.0 for j in range(4)])
        v4(0x3D0 + i * 16, [1.0 if j == i else 0.0 for j in range(4)])
    return bytes(d)


def support_block(w, h):
    d = bytearray(SUPPORT_SIZE)
    struct.pack_into("<4f", d, 144, 2.0 / max(w, 1), 2.0 / max(h, 1), 1.0, 0.0)
    struct.pack_into("<4f", d, 160, float(w), float(h), 1.0, 1.0)
    for i in range(73):
        struct.pack_into("<f", d, 192 + i * 16, 1.0)
    return bytes(d)


def mtx_rows(m):
    """3x4 (12 floats, row-major) or 4x4 -> three vec4 rows."""
    a = np.asarray(m, np.float32).reshape(-1)
    return np.ascontiguousarray(a[:12]).tobytes()


class Buffers:
    """One uniform buffer per binding: support (0) and cbufs 3..7 (bindings 4..8). Uploads only on change."""

    def __init__(self):
        self.ids = {}
        for b in (0, 4, 5, 6, 7, 8):
            bid = GL.glGenBuffers(1)
            GL.glBindBuffer(GL.GL_UNIFORM_BUFFER, bid)
            GL.glBufferData(GL.GL_UNIFORM_BUFFER, BLOCK_SIZE, None, GL.GL_DYNAMIC_DRAW)
            self.ids[b] = bid
        GL.glBindBuffer(GL.GL_UNIFORM_BUFFER, 0)
        self.cache = {}

    def set(self, binding, data):
        if data and self.cache.get(binding) != data:
            self.cache[binding] = data
            GL.glBindBuffer(GL.GL_UNIFORM_BUFFER, self.ids[binding])
            GL.glBufferSubData(GL.GL_UNIFORM_BUFFER, 0, len(data), data)
        GL.glBindBufferBase(GL.GL_UNIFORM_BUFFER, binding, self.ids[binding])

    def cbuf(self, index, data):
        self.set(index + 1, data)

    def delete(self):
        GL.glDeleteBuffers(len(self.ids), list(self.ids.values()))
        self.ids, self.cache = {}, {}

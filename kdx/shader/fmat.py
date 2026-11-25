"""
kdx/shader/fmat.py - what the game-shader renderer needs from each BFRES material that kdx.bfres does not keep:
the shader assign (shading model, attribute / sampler assigns, static options), the shader parameters and the
sampler states. Read straight from the file bytes (Switch BFRES 0.8, as Deluxe's world maps and BfresLibrary's
output are), following Yaz0loom-DX's reader.

    infos = read_materials(bfres_bytes)     {(model name, material name): MaterialShader}
"""
from __future__ import annotations

import struct

# nn::gfx wrap modes in a SamplerInfo -> GL
# nn::gfx TextureAddressMode: repeat, mirror, clamp to edge, clamp to border, mirror clamp to edge
WRAP = {0: 0x2901, 1: 0x8370, 2: 0x812F, 3: 0x812D, 4: 0x8743}


class Param:
    __slots__ = ("name", "type", "value")

    def __init__(self, name, typ, value):
        self.name, self.type, self.value = name, typ, value


class MaterialShader:
    def __init__(self):
        self.name = ""
        self.shader_archive = ""
        self.shading_model = ""
        self.attrib_assign = {}       # shader attribute (_p0, _u1 ...) -> model attribute
        self.sampler_assign = {}      # shader sampler (_a0, _x0 ...) -> material sampler
        self.shader_options = {}      # option name -> choice string
        self.textures = []            # texture names, by slot
        self.sampler_names = []       # material sampler names, same order
        self.sampler_raw = []         # nn::gfx::SamplerInfo (0x20 bytes) per sampler
        self.params = []              # [Param]
        self.render_info = {}

    def texture_for_sampler(self, shader_sampler):
        """Texture name and SamplerInfo bound to a shader sampler (_a0 ...), or (None, None)."""
        model_smp = self.sampler_assign.get(shader_sampler, shader_sampler)
        if model_smp in self.sampler_names:
            k = self.sampler_names.index(model_smp)
            if k < len(self.textures):
                return self.textures[k], (self.sampler_raw[k] if k < len(self.sampler_raw) else None)
        return None, None

    def wrap(self, raw):
        if not raw or len(raw) < 2:
            return 0x2901, 0x2901
        return WRAP.get(raw[0], 0x2901), WRAP.get(raw[1], 0x2901)


class _R:
    def __init__(self, b):
        self.b = b

    def q(self, o):
        return struct.unpack_from("<Q", self.b, o)[0]

    def u(self, fmt, o):
        return struct.unpack_from("<" + fmt, self.b, o)

    def s(self, o):
        """String at an offset (u16 length + bytes), '' for 0."""
        if not o:
            return ""
        n = struct.unpack_from("<H", self.b, o)[0]
        return self.b[o + 2:o + 2 + n].decode("utf-8", "replace")

    def ps(self, o):
        return self.s(self.q(o))

    def keys(self, o):
        if not o:
            return []
        c = struct.unpack_from("<i", self.b, o + 4)[0]
        if not 0 <= c < 1 << 16:
            return []
        return [self.s(self.q(o + 8 + 16 * (i + 1) + 8)) for i in range(c)]


def param_value(typ, raw):
    size = len(raw)
    if size == 0:
        return ()
    if 0 <= typ <= 3:
        return tuple(bool(x) for x in struct.unpack("<%dI" % (size // 4), raw[:size // 4 * 4]))
    if 4 <= typ <= 7:
        return struct.unpack("<%di" % (size // 4), raw[:size // 4 * 4])
    if 8 <= typ <= 11:
        return struct.unpack("<%dI" % (size // 4), raw[:size // 4 * 4])
    if typ in (30, 31) and size >= 24:                # texsrt: mode + 5 floats
        return (struct.unpack("<I", raw[:4])[0],) + struct.unpack("<5f", raw[4:24])
    return struct.unpack("<%df" % (size // 4), raw[:size // 4 * 4])


def _material(r, o):
    m = MaterialShader()
    m.name = r.ps(o + 0x10)
    ri_p, sa_p = r.q(o + 0x18), r.q(o + 0x28)
    texn_p, smp_p, smpd_p = r.q(o + 0x38), r.q(o + 0x48), r.q(o + 0x50)
    sp_p, src_p = r.q(o + 0x58), r.q(o + 0x68)
    _idx, n_ri = r.u("2H", o + 0xA4)
    n_tex, n_smp = r.u("2B", o + 0xA8)
    n_sp, _n_vol, src_size = r.u("3H", o + 0xAA)
    for i in range(n_ri):
        e = ri_p + i * 0x18
        dp = r.q(e + 8)
        cnt, typ = r.u("HB", e + 0x10)
        if not dp or cnt > 64:
            vals = []
        elif typ == 2:
            vals = [r.ps(dp + 8 * k) for k in range(cnt)]
        elif typ == 1:
            vals = list(r.u("%df" % cnt, dp))
        else:
            vals = list(r.u("%di" % cnt, dp))
        m.render_info[r.ps(e)] = vals
    if sa_p:
        m.shader_archive, m.shading_model = r.ps(sa_p), r.ps(sa_p + 8)
        aa_v, aa_d = r.q(sa_p + 0x10), r.q(sa_p + 0x18)
        sm_v, sm_d = r.q(sa_p + 0x20), r.q(sa_p + 0x28)
        so_v, so_d = r.q(sa_p + 0x30), r.q(sa_p + 0x38)
        n_aa, n_sm, n_so = r.u("BBH", sa_p + 0x44)
        m.attrib_assign = dict(zip(r.keys(aa_d), [r.ps(aa_v + 8 * k) for k in range(n_aa)]))
        m.sampler_assign = dict(zip(r.keys(sm_d), [r.ps(sm_v + 8 * k) for k in range(n_sm)]))
        m.shader_options = dict(zip(r.keys(so_d), [r.ps(so_v + 8 * k) for k in range(n_so)]))
    m.textures = [r.ps(texn_p + 8 * k) for k in range(n_tex)] if texn_p else []
    names = r.keys(smpd_p)
    m.sampler_names = [names[k] if k < len(names) else "_s%d" % k for k in range(n_smp)]
    m.sampler_raw = [bytes(r.b[smp_p + k * 0x20:smp_p + k * 0x20 + 0x20]) for k in range(n_smp)] if smp_p else []
    src = r.b[src_p:src_p + src_size] if src_p else b""
    for i in range(n_sp):
        e = sp_p + i * 0x20
        typ, size, off = r.u("BBH", e + 0x10)
        m.params.append(Param(r.ps(e + 8), typ, param_value(typ, bytes(src[off:off + size]))))
    return m


def read_materials(data):
    """{(model name, material name): MaterialShader} for every material of a Switch BFRES. Materials that cannot be
    read are left out (the viewport then draws them with its own shader)."""
    data = bytes(data)
    r = _R(data)
    out = {}
    if data[:4] != b"FRES":
        return out
    model_array, num_models = r.q(0x28), r.u("H", 0xBC)[0]
    for i in range(num_models):
        mo = model_array + i * 0x78
        try:
            if data[mo:mo + 4] != b"FMDL":
                continue
            name = r.ps(mo + 0x10)
            mat_values, num_mat = r.q(mo + 0x40), r.u("H", mo + 0x6C)[0]
            for k in range(num_mat):
                try:
                    m = _material(r, mat_values + k * 0xB8)
                except (struct.error, IndexError, ValueError):
                    continue
                out[(name, m.name)] = m
        except (struct.error, IndexError):
            continue
    return out

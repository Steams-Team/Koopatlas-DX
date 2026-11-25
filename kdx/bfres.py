#!/usr/bin/env python3
"""
wmdx_bfres.py - native, read-only BFRES reader for WorldMapDX (Nintendo Switch "FRES", version 8).

Pure Python, standard library only. It reads what a viewer needs: models, skeleton (bones and their world
transforms), vertex buffers, shapes/meshes (indices), and material/texture names. It never writes anything.

The binary layout was taken from the MIT-licensed BfresLibrary by KillzXGaming / Syroot
(https://github.com/KillzXGaming/BfresLibrary), which is the library behind Switch-Toolbox. This is an
independent implementation, not a copy of that code.

Scope and limits
    - Switch BFRES version 8 only (that is the version of Deluxe's resources). Wii U BFRES (big-endian, v3) and
      newer Switch versions are refused with a clear message instead of being misread.
    - Geometry and skeleton only: no materials/shaders evaluation, textures or animations.
    - Smooth-skinned shapes are shown in bind pose (their vertices are already in model space).
"""
from __future__ import annotations

import math
import struct
from array import array

__all__ = ["BfresError", "BfresFile", "Model", "Shape", "Bone", "build_mesh"]


class BfresError(ValueError):
    """The data is not a BFRES this reader can handle, or it is damaged."""


# --------------------------------------------------------------------------------------------- attribute formats
# Vertex attribute formats are stored as a big-endian u16. Values come from BfresLibrary's SwitchAttribFormat.
_FORMAT_NAMES = {
    0x0102: "8_UNorm", 0x0302: "8_UInt", 0x0202: "8_SNorm", 0x0402: "8_SInt", 0x0802: "8_UIntToSingle", 0x0A02: "8_SIntToSingle",
    0x0001: "4_4_UNorm",
    0x010A: "16_UNorm", 0x020A: "16_UInt", 0x030A: "16_SNorm", 0x040A: "16_SInt", 0x050A: "16_Single",
    0x0803: "16_UIntToSingle", 0x0A03: "16_SIntToSingle",
    0x0109: "8_8_UNorm", 0x0309: "8_8_UInt", 0x0209: "8_8_SNorm", 0x0409: "8_8_SInt", 0x0804: "8_8_UIntToSingle", 0x0A04: "8_8_SIntToSingle",
    0x0112: "16_16_UNorm", 0x0212: "16_16_SNorm", 0x0312: "16_16_UInt", 0x0412: "16_16_SInt", 0x0512: "16_16_Single",
    0x0807: "16_16_UIntToSingle", 0x0A07: "16_16_SIntToSingle",
    0x0809: "10_11_11_Single",
    0x010B: "8_8_8_8_UNorm", 0x020B: "8_8_8_8_SNorm", 0x030B: "8_8_8_8_UInt", 0x040B: "8_8_8_8_SInt",
    0x080B: "8_8_8_8_UIntToSingle", 0x0A0B: "8_8_8_8_SIntToSingle",
    0x000B: "10_10_10_2_UNorm", 0x090B: "10_10_10_2_UInt", 0x020E: "10_10_10_2_SNorm", 0x099B: "10_10_10_2_SInt",
    0x0115: "16_16_16_16_UNorm", 0x0215: "16_16_16_16_SNorm", 0x0315: "16_16_16_16_UInt", 0x0415: "16_16_16_16_SInt",
    0x0515: "16_16_16_16_Single", 0x080E: "16_16_16_16_UIntToSingle", 0x0A0E: "16_16_16_16_SIntToSingle",
    0x0314: "32_UInt", 0x0416: "32_SInt", 0x0516: "32_Single",
    0x0317: "32_32_UInt", 0x0417: "32_32_SInt", 0x0517: "32_32_Single",
    0x0318: "32_32_32_UInt", 0x0418: "32_32_32_SInt", 0x0518: "32_32_32_Single",
    0x0319: "32_32_32_32_UInt", 0x0419: "32_32_32_32_SInt", 0x0519: "32_32_32_32_Single",
}


def _decode_format(value):
    """-> (struct format string for one vertex's attribute, converter) or None when unsupported."""
    name = _FORMAT_NAMES.get(value)
    if name is None:
        return None
    parts = name.split("_")
    kind = parts[-1]
    bits = [int(p) for p in parts[:-1]]
    if bits in ([10, 10, 10, 2], [10, 11, 11], [4, 4]):
        return ("packed", name)
    size = bits[0]
    if any(b != size for b in bits):
        return None
    if kind == "Single":
        code = {16: "e", 32: "f"}.get(size)
    elif kind in ("SNorm", "SInt", "SIntToSingle"):
        code = {8: "b", 16: "h", 32: "i"}.get(size)
    else:
        code = {8: "B", 16: "H", 32: "I"}.get(size)
    if code is None:
        return None
    n = len(bits)
    if kind == "UNorm":
        scale, signed = 1.0 / ((1 << size) - 1), False
    elif kind == "SNorm":
        scale, signed = 1.0 / ((1 << (size - 1)) - 1), True
    elif kind in ("UIntToSingle", "SIntToSingle"):
        scale, signed = 1.0, False
    else:
        scale, signed = None, False          # raw integers or floats
    return ("<%d%s" % (n, code), n, scale, signed, kind in ("Single", "UInt", "SInt"))


def _snorm10(v):
    v &= 0x3FF
    if v & 0x200:
        v -= 0x400
    return max(v / 511.0, -1.0)


def _decode_packed(name, word):
    if name == "10_10_10_2_SNorm":
        return (_snorm10(word), _snorm10(word >> 10), _snorm10(word >> 20), float(word >> 30))
    if name == "10_10_10_2_UNorm":
        return ((word & 0x3FF) / 1023.0, ((word >> 10) & 0x3FF) / 1023.0, ((word >> 20) & 0x3FF) / 1023.0, (word >> 30) / 3.0)
    return (0.0, 0.0, 0.0, 0.0)              # formats we do not decode (never used for positions)


# --------------------------------------------------------------------------------------------- tiny matrix helpers
_IDENT = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)     # 3x4, row-major


def _mat_mul(a, b):
    """a * b for 3x4 affine matrices (each row r0 r1 r2 followed by a translation column)."""
    out = []
    for r in range(3):
        a0, a1, a2, at = a[r * 4], a[r * 4 + 1], a[r * 4 + 2], a[r * 4 + 3]
        out.append(a0 * b[0] + a1 * b[4] + a2 * b[8])
        out.append(a0 * b[1] + a1 * b[5] + a2 * b[9])
        out.append(a0 * b[2] + a1 * b[6] + a2 * b[10])
        out.append(a0 * b[3] + a1 * b[7] + a2 * b[11] + at)
    return tuple(out)


def _quat_to_rot(x, y, z, w):
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def _euler_xyz_to_rot(x, y, z):
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    # R = Rz * Ry * Rx
    return ((cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
            (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
            (-sy, cy * sx, cy * cx))


def invert_affine(m):
    """Inverse of a 3x4 affine matrix (rotation/scale + translation)."""
    a, b, c, d, e, f, g, h, i = m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise BfresError("A bone transform cannot be inverted (zero scale).")
    inv = [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
           (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
           (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]
    tx, ty, tz = m[3], m[7], m[11]
    return (inv[0], inv[1], inv[2], -(inv[0] * tx + inv[1] * ty + inv[2] * tz),
            inv[3], inv[4], inv[5], -(inv[3] * tx + inv[4] * ty + inv[5] * tz),
            inv[6], inv[7], inv[8], -(inv[6] * tx + inv[7] * ty + inv[8] * tz))


def transform_point(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


# --------------------------------------------------------------------------------------------- low-level reader
class _Reader:
    def __init__(self, data):
        self.d = data

    def _check(self, off, size):
        if off < 0 or off + size > len(self.d):
            raise BfresError("A pointer in the file points outside it (offset 0x%X, file is 0x%X bytes). "
                             "The file may be damaged or a different BFRES version." % (off, len(self.d)))

    def u8(self, o):
        self._check(o, 1); return self.d[o]

    def u16(self, o):
        self._check(o, 2); return struct.unpack_from("<H", self.d, o)[0]

    def i16(self, o):
        self._check(o, 2); return struct.unpack_from("<h", self.d, o)[0]

    def u32(self, o):
        self._check(o, 4); return struct.unpack_from("<I", self.d, o)[0]

    def i32(self, o):
        self._check(o, 4); return struct.unpack_from("<i", self.d, o)[0]

    def u64(self, o):
        self._check(o, 8); return struct.unpack_from("<Q", self.d, o)[0]

    def f32s(self, o, n):
        self._check(o, 4 * n); return struct.unpack_from("<%df" % n, self.d, o)

    def sig(self, o, expected):
        self._check(o, 4)
        if self.d[o:o + 4] != expected:
            raise BfresError("Expected a %s block at offset 0x%X but found %r." % (expected.decode(), o, bytes(self.d[o:o + 4])))

    def string(self, ptr):
        if not ptr:
            return ""
        length = self.u16(ptr)
        self._check(ptr + 2, length)
        return bytes(self.d[ptr + 2:ptr + 2 + length]).decode("utf-8", "replace")

    def ptr_string(self, at):
        return self.string(self.u64(at))

    def dict_keys(self, off):
        """Names of a _DIC-style dictionary (the root node is skipped)."""
        if not off:
            return []
        count = self.i32(off + 4)
        if count < 0 or count > 100000:
            raise BfresError("A dictionary at 0x%X has an impossible entry count (%d)." % (off, count))
        return [self.ptr_string(off + 8 + i * 16 + 8) for i in range(1, count + 1)]


# --------------------------------------------------------------------------------------------- data classes
class Bone:
    def __init__(self):
        self.offset = None
        self.index = 0
        self.name = ""
        self.parent = -1
        self.scale = (1.0, 1.0, 1.0)
        self.rotation = (0.0, 0.0, 0.0, 1.0)
        self.position = (0.0, 0.0, 0.0)
        self.flags = 0
        self.world = _IDENT

    @property
    def rotation_mode(self):
        return "euler" if ((self.flags >> 12) & 7) == 1 else "quaternion"

    @property
    def world_position(self):
        return (self.world[3], self.world[7], self.world[11])

    def local_matrix(self):
        if self.rotation_mode == "euler":
            r = _euler_xyz_to_rot(*self.rotation[:3])
        else:
            r = _quat_to_rot(*self.rotation)
        sx, sy, sz = self.scale
        px, py, pz = self.position
        return (r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, px,
                r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, py,
                r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, pz)


class VertexBuffer:
    def __init__(self):
        self.vertex_count = 0
        self.attributes = {}      # name -> (format value, byte offset, buffer index)
        self.buffers = []         # (stride, bytes)
        self._cache = {}

    def has(self, name):
        return name in self.attributes

    def read(self, name):
        """All values of an attribute: a list of tuples of floats/ints (cached). [] if absent or undecodable."""
        if name in self._cache:
            return self._cache[name]
        out = []
        if name in self.attributes:
            value, offset, buf_i = self.attributes[name]
            spec = _decode_format(value)
            if spec is not None and buf_i < len(self.buffers):
                stride, blob = self.buffers[buf_i]
                if spec[0] == "packed":
                    pname = spec[1]
                    if pname.startswith("10_10_10_2"):
                        words = struct.Struct("<I")
                        usable = min(self.vertex_count, (len(blob) - offset - 4) // stride + 1 if stride else 0)
                        out = [_decode_packed(pname, words.unpack_from(blob, offset + i * stride)[0])
                               for i in range(max(0, usable))]
                else:
                    fmt, n, scale, signed, raw = spec
                    st = struct.Struct(fmt)
                    limit = min(self.vertex_count, (len(blob) - offset - st.size) // stride + 1 if stride else 0)
                    if scale is None:
                        out = [st.unpack_from(blob, offset + i * stride) for i in range(max(0, limit))]
                    else:
                        out = [tuple(c * scale for c in st.unpack_from(blob, offset + i * stride)) for i in range(max(0, limit))]
        self._cache[name] = out
        return out


class Mesh:
    def __init__(self):
        self.primitive = 3
        self.index_format = 1
        self.index_count = 0
        self.first_vertex = 0
        self.index_bytes = b""

    def indices(self):
        code = {0: "B", 1: "H", 2: "I"}.get(self.index_format)
        if code is None:
            return []
        width = struct.calcsize(code)
        usable = (len(self.index_bytes) // width) * width
        values = array(code)
        values.frombytes(bytes(self.index_bytes[:usable]))
        import sys
        if sys.byteorder != "little":
            values.byteswap()
        return values[:self.index_count] if self.index_count else values

    def triangles(self):
        """Triangle index triples (offset by first_vertex). Triangle lists and strips are supported."""
        idx = self.indices()
        base = self.first_vertex
        tris = []
        if self.primitive == 3:
            for i in range(0, len(idx) - 2, 3):
                tris.append((idx[i] + base, idx[i + 1] + base, idx[i + 2] + base))
        elif self.primitive == 4:
            restart = {0: 0xFF, 1: 0xFFFF, 2: 0xFFFFFFFF}.get(self.index_format)
            if restart is None:
                return []
            run = []
            for v in idx:
                if v == restart:
                    run = []
                    continue
                run.append(v)
                n = len(run)
                if n >= 3:
                    a, b, c = run[-3], run[-2], run[-1]
                    if a != b and b != c and a != c:
                        tris.append((a + base, b + base, c + base) if n % 2 else (b + base, a + base, c + base))
        return tris


class Shape:
    def __init__(self):
        self.name = ""
        self.material_index = 0
        self.bone_index = 0
        self.vertex_buffer_index = 0
        self.skin_count = 0
        self.skin_bone_indices = []
        self.meshes = []


class Material:
    def __init__(self):
        self.name = ""
        self.textures = []
        self.samplers = []        # sampler names, same order as textures: _a0 albedo, _n0 normal, _s0 specular, _b0 bake...
        self.render_info = {}     # name -> list of values (gsys_render_state_mode, gsys_alpha_test_enable, ...)

    def texture_for(self, sampler):
        """Name of the texture bound to a sampler (e.g. "_a0"), or None."""
        if sampler in self.samplers:
            i = self.samplers.index(sampler)
            return self.textures[i] if i < len(self.textures) else None
        return None

    @property
    def albedo(self):
        return self.texture_for("_a0") or (self.textures[0] if self.textures and not self.samplers else None)

    def info(self, name, default=None):
        v = self.render_info.get(name)
        return v[0] if v else default

    @property
    def blend_mode(self):
        """"opaque", "mask" (alpha test) or "translucent" (alpha blend), from the gsys render infos."""
        mode = str(self.info("gsys_render_state_mode", "opaque"))
        if mode == "translucent" or str(self.info("gsys_render_state_blend_mode", "none")) not in ("none", ""):
            return "translucent"
        if mode == "mask" or str(self.info("gsys_alpha_test_enable", "false")) == "true":
            return "mask"
        return "opaque"

    @property
    def alpha_ref(self):
        try:
            return float(self.info("gsys_alpha_test_value", 0.5))
        except (TypeError, ValueError):
            return 0.5

    @property
    def cull(self):
        """"back", "front" or "none" (gsys_render_state_display_face: front = draw the front faces only)."""
        face = str(self.info("gsys_render_state_display_face", "front"))
        return {"front": "back", "back": "front", "both": "none", "none": "none"}.get(face, "back")


class Model:
    def __init__(self):
        self.name = ""
        self.bones = []
        self.vertex_buffers = []
        self.shapes = []
        self.materials = []

    def bone_by_name(self):
        return {b.name: b for b in self.bones}

    def vertex_total(self):
        return sum(vb.vertex_count for vb in self.vertex_buffers)

    def triangle_total(self):
        """Triangles in all shapes (lists and strips), counted from the index counts without decoding them."""
        total = 0
        for s in self.shapes:
            for m in s.meshes:
                width = {0: 1, 1: 2, 2: 4}.get(m.index_format)
                if not width:
                    continue
                count = min(m.index_count, len(m.index_bytes) // width) if m.index_count else len(m.index_bytes) // width
                if m.primitive == 3:
                    total += count // 3
                elif m.primitive == 4:
                    total += max(0, count - 2)
        return total


class BfresFile:
    def __init__(self):
        self.version = (0, 0, 0)
        self.name = ""
        self.models = []
        self.external_files = []     # (name, offset, size)
        self._data = b""

    @property
    def version_text(self):
        return "%d.%d.%d" % self.version

    def external_bytes(self, name):
        for n, off, size in self.external_files:
            if n == name:
                return bytes(self._data[off:off + size])
        return None

    # ---- parsing -------------------------------------------------------------------------------------------
    @classmethod
    def parse(cls, data):
        data = bytes(data)
        if data[:4] != b"FRES":
            raise BfresError("This is not a BFRES file (it does not start with 'FRES').")
        if data[4:8] != b"    ":
            raise BfresError("This is a Wii U BFRES (big-endian, version 3). Deluxe uses the Switch format; "
                             "WorldMapDX reads Switch BFRES only.")
        if len(data) < 0xD0:
            raise BfresError("The file is too small to be a BFRES.")
        r = _Reader(data)
        if data[12:14] != b"\xff\xfe":
            raise BfresError("Unexpected byte-order mark in the BFRES header (expected little-endian).")
        version = r.u32(8)
        major, minor, micro = version >> 16, (version >> 8) & 0xFF, version & 0xFF
        if major not in (5, 6, 7, 8):
            raise BfresError("BFRES version %d.%d.%d is not supported yet (this reader handles versions 5 to 8; "
                             "Deluxe's world-map files are 8.1.1)." % (major, minor, micro))
        # Versions 5-8 share one layout in BfresLibrary; checked on real 8.1.1 files and on a 5.0.2 file's header
        # (Deluxe's Stage_Stadium00_Env, which holds textures only), so models in 5.x files are not yet verified.
        declared = r.u32(0x1C)
        if declared != len(data):
            raise BfresError("The header says the file is %d bytes but it is %d: it is truncated or was edited "
                             "by hand." % (declared, len(data)))
        f = cls()
        f._data = data
        f.version = (major, minor, micro)
        f.name = r.ptr_string(0x20)
        model_array, model_dict = r.u64(0x28), r.u64(0x30)
        buffer_info = r.u64(0x90)
        ext_values, ext_dict = r.u64(0x98), r.u64(0xA0)
        num_models = r.u16(0xBC)
        num_external = r.u16(0xC8)

        # The GPU buffer region is 8-byte aligned; the recorded offset can point just before the aligned start
        # (BfresLibrary writes small files that way), so round it up once. Real files are already aligned.
        buffer_base = r.u64(buffer_info + 8) if buffer_info else 0
        buffer_base = (buffer_base + 7) & ~7

        for i, key in enumerate(r.dict_keys(ext_dict)[:num_external]):
            ofs, size = r.u64(ext_values + i * 16), r.u64(ext_values + i * 16 + 8)
            f.external_files.append((key, ofs, size))

        names = r.dict_keys(model_dict)
        for i in range(num_models):
            model = cls._read_model(r, model_array + i * 0x78, buffer_base)
            if not model.name and i < len(names):
                model.name = names[i]
            f.models.append(model)
        return f

    @staticmethod
    def _read_model(r, m, buffer_base):
        r.sig(m, b"FMDL")
        model = Model()
        model.name = r.ptr_string(m + 0x10)
        skeleton_ptr, vertex_array = r.u64(m + 0x20), r.u64(m + 0x28)
        shape_values, mat_values = r.u64(m + 0x30), r.u64(m + 0x40)
        num_vb, num_shape, num_mat = r.u16(m + 0x68), r.u16(m + 0x6A), r.u16(m + 0x6C)

        if skeleton_ptr:
            model.bones = BfresFile._read_skeleton(r, skeleton_ptr)
        for i in range(num_vb):
            model.vertex_buffers.append(BfresFile._read_vertex_buffer(r, vertex_array + i * 0x60, buffer_base))
        for i in range(num_shape):
            model.shapes.append(BfresFile._read_shape(r, shape_values + i * 0x70, buffer_base))
        for i in range(num_mat):
            model.materials.append(BfresFile._read_material(r, mat_values + i * 0xB8))
        return model

    @staticmethod
    def _read_skeleton(r, s):
        r.sig(s, b"FSKL")
        bone_array, num_bone = r.u64(s + 0x18), r.u16(s + 0x4C)
        bones = []
        for i in range(num_bone):
            b0 = bone_array + i * 0x60
            bone = Bone()
            bone.offset = b0                     # file offset of this bone's struct (position floats at +0x54)
            bone.index = i
            bone.name = r.ptr_string(b0)
            bone.parent = r.i16(b0 + 0x2A)
            bone.flags = r.u32(b0 + 0x34)
            bone.scale = r.f32s(b0 + 0x38, 3)
            bone.rotation = r.f32s(b0 + 0x44, 4)
            bone.position = r.f32s(b0 + 0x54, 3)
            bones.append(bone)
        # World transforms; guard against cycles in damaged files.
        done = [False] * len(bones)
        for start in range(len(bones)):
            chain, i = [], start
            while 0 <= i < len(bones) and not done[i] and i not in chain and len(chain) <= len(bones):
                chain.append(i)
                i = bones[i].parent
            for j in reversed(chain):
                parent = bones[j].parent
                local = bones[j].local_matrix()
                bones[j].world = _mat_mul(bones[parent].world, local) if 0 <= parent < len(bones) and parent != j else local
                done[j] = True
        return bones

    @staticmethod
    def _read_vertex_buffer(r, v, buffer_base):
        r.sig(v, b"FVTX")
        vb = VertexBuffer()
        attr_values = r.u64(v + 0x10)
        size_array, stride_array = r.u64(v + 0x38), r.u64(v + 0x40)
        buffer_offset = r.i32(v + 0x50)
        num_attr, num_buffer = r.u8(v + 0x54), r.u8(v + 0x55)
        vb.vertex_count = r.u32(v + 0x58)
        for i in range(num_attr):
            a = attr_values + i * 0x10
            name = r.ptr_string(a)
            vb.attributes[name] = (struct.unpack_from(">H", r.d, a + 8)[0], r.u16(a + 0xC), r.u16(a + 0xE))
        pos = buffer_base + buffer_offset
        for i in range(num_buffer):
            stride = r.u32(stride_array + i * 16)
            size = r.u32(size_array + i * 16)
            pos = (pos + 7) & ~7                       # GPU buffers are aligned to 8 bytes
            r._check(pos, size)
            vb.buffers.append((stride, r.d[pos:pos + size]))
            pos += size
        return vb

    @staticmethod
    def _read_shape(r, s, buffer_base):
        r.sig(s, b"FSHP")
        shape = Shape()
        shape.name = r.ptr_string(s + 0x10)
        mesh_array, skin_list = r.u64(s + 0x20), r.u64(s + 0x28)
        shape.material_index = r.u16(s + 0x5E)
        shape.bone_index = r.u16(s + 0x60)
        shape.vertex_buffer_index = r.u16(s + 0x62)
        num_skin = r.u16(s + 0x64)
        shape.skin_count = r.u8(s + 0x66)
        num_mesh = r.u8(s + 0x67)
        if skin_list and num_skin:
            shape.skin_bone_indices = [r.u16(skin_list + i * 2) for i in range(num_skin)]
        for j in range(num_mesh):
            m = mesh_array + j * 0x38
            mesh = Mesh()
            size_ptr = r.u64(m + 0x18)
            face_offset = r.u32(m + 0x20)
            mesh.primitive = r.u32(m + 0x24)
            mesh.index_format = r.u32(m + 0x28)
            mesh.index_count = r.u32(m + 0x2C)
            mesh.first_vertex = r.u32(m + 0x30)
            size = r.u32(size_ptr) if size_ptr else 0
            start = buffer_base + face_offset
            r._check(start, size)
            mesh.index_bytes = r.d[start:start + size]
            shape.meshes.append(mesh)
        return shape

    @staticmethod
    def _read_material(r, m):
        """Material name and texture names. Materials are only labels for the viewer, so a damaged or odd one
        yields what could be read instead of failing the whole model.

        The texture-count and sampler-count bytes sit next to each other at 0xA8; BfresLibrary's loader and saver
        disagree about their order (real files have equal counts, so it rarely matters). The larger is used."""
        mat = Material()
        try:
            mat.name = r.ptr_string(m + 0x10)
            names_ptr = r.u64(m + 0x38)
            count = max(r.u8(m + 0xA8), r.u8(m + 0xA9))
            if names_ptr and count:
                for i in range(count):
                    try:
                        mat.textures.append(r.ptr_string(names_ptr + i * 8))
                    except BfresError:
                        break
            try:
                mat.samplers = r.dict_keys(r.u64(m + 0x50))
            except BfresError:
                mat.samplers = []
            try:
                values, keys = r.u64(m + 0x18), r.dict_keys(r.u64(m + 0x20))
                for i, key in enumerate(keys):
                    e = values + i * 24
                    data, count, kind = r.u64(e + 8), r.u16(e + 16), r.u8(e + 18)
                    if not data or count > 64:
                        mat.render_info[key] = []
                    elif kind == 0:
                        mat.render_info[key] = [r.i32(data + 4 * k) for k in range(count)]
                    elif kind == 1:
                        mat.render_info[key] = list(r.f32s(data, count))
                    else:
                        mat.render_info[key] = [r.ptr_string(data + 8 * k) for k in range(count)]
            except BfresError:
                pass
        except BfresError:
            pass
        return mat


# --------------------------------------------------------------------------------------------- mesh building
class Mesh3D:
    """Everything a viewer needs from one or more shapes of a model, already transformed into model space."""

    def __init__(self):
        self.vertices = []        # (x, y, z)
        self.uvs = []             # (u, v) per vertex (_u0), (0, 0) when the shape has none
        self.normals = []         # (x, y, z) per vertex (_n0, turned like the position), None when the shape has none
        self.bone_of = []         # bone index that places each vertex (-1: model space), for animating the skeleton
        self.faces = []           # (a, b, c, material_name)
        self.skipped = []         # human-readable reasons shapes were left out

    def bounds(self):
        if not self.vertices:
            return None
        return (tuple(min(v[i] for v in self.vertices) for i in range(3)),
                tuple(max(v[i] for v in self.vertices) for i in range(3)))


def build_mesh(model, shape_names=None):
    """Bake the model's shapes into one triangle mesh. shape_names: optional set of shape names to include.

    Shapes that share a vertex buffer and a transform share the baked vertices (they are stored once)."""
    out = Mesh3D()
    baked = {}                                   # (vertex buffer, transform key) -> index of its first vertex
    for shape in model.shapes:
        if shape_names is not None and shape.name not in shape_names:
            continue
        if shape.vertex_buffer_index >= len(model.vertex_buffers):
            out.skipped.append("%s: bad vertex buffer index" % shape.name)
            continue
        vb = model.vertex_buffers[shape.vertex_buffer_index]
        positions = vb.read("_p0")
        if not positions or len(positions[0]) < 3:
            out.skipped.append("%s: no readable position attribute (_p0)" % shape.name)
            continue

        bones = model.bones
        if shape.skin_count == 0 and 0 <= shape.bone_index < len(bones):
            key = (shape.vertex_buffer_index, "bone", shape.bone_index)
        elif shape.skin_count == 1 and vb.read("_i0"):
            key = (shape.vertex_buffer_index, "rigid", tuple(shape.skin_bone_indices), shape.bone_index)
        else:
            key = (shape.vertex_buffer_index, "model")      # smooth skin: already model-space at bind pose

        if key not in baked:
            if key[1] == "bone":
                m = bones[shape.bone_index].world
                world = [transform_point(m, p[:3]) for p in positions]
                out.bone_of.extend([shape.bone_index] * len(world))
            elif key[1] == "rigid":
                world = []
                for p, idx in zip(positions, vb.read("_i0")):
                    slot = int(idx[0])
                    bone_i = shape.skin_bone_indices[slot] if slot < len(shape.skin_bone_indices) else shape.bone_index
                    m = bones[bone_i].world if 0 <= bone_i < len(bones) else _IDENT
                    world.append(transform_point(m, p[:3]))
                    out.bone_of.append(bone_i if 0 <= bone_i < len(bones) else -1)
            else:
                world = [tuple(p[:3]) for p in positions]
                out.bone_of.extend([-1] * len(world))
            baked[key] = (len(out.vertices), len(world))
            out.vertices.extend(world)
            uv = vb.read("_u0")
            out.uvs.extend((float(t[0]), float(t[1])) for t in uv) if len(uv) == len(world) else                 out.uvs.extend([(0.0, 0.0)] * len(world))
            nrm = vb.read("_n0")
            if len(nrm) == len(world) and key[1] == "bone":
                m = bones[shape.bone_index].world
                out.normals.extend((m[0] * n[0] + m[1] * n[1] + m[2] * n[2], m[4] * n[0] + m[5] * n[1] + m[6] * n[2],
                                    m[8] * n[0] + m[9] * n[1] + m[10] * n[2]) for n in nrm)
            elif len(nrm) == len(world):
                out.normals.extend(tuple(n[:3]) for n in nrm)
            else:
                out.normals.extend([None] * len(world))

        base, n = baked[key]
        mat = model.materials[shape.material_index].name if shape.material_index < len(model.materials) else "Default"
        for mesh in shape.meshes:
            for a, b, c in mesh.triangles():
                if a < n and b < n and c < n:
                    out.faces.append((base + a, base + b, base + c, mat))
    return out


def describe(f):
    lines = ["BFRES %s, %d model(s)" % (f.version_text, len(f.models))]
    for m in f.models:
        lines.append("  %-24s %4d bone(s) %4d shape(s) %8d vertices %8d triangles" % (
            m.name, len(m.bones), len(m.shapes), m.vertex_total(), m.triangle_total()))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: python wmdx_bfres.py <file.bfres>")
    print(describe(BfresFile.parse(open(sys.argv[1], "rb").read())))

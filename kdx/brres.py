"""
kdx/brres.py - Wii models (BRRES: MDL0 models, TEX0 textures, PLT0 palettes), as used by New Super Mario Bros. Wii.

    arc = u8_files(open("CS_W1.arc", "rb").read())          # {"g3d/model.brres": bytes}
    res = Brres(arc["g3d/model.brres"])
    res.models["CS_W1"].bones / .polygons / .materials       # MDL0, decoded into world-space triangles
    res.textures["W1_grass"].rgba()                           # (h, w, 4) uint8

Only what a world map needs is read: bones (bind pose), vertex/normal/colour/uv arrays, polygons (GX display lists,
skinned by their bone or matrix table), materials (texture layers, cull mode, translucency, texture SRT), the draw
lists that pair polygons with materials, and textures in every GX format. Layouts follow BrawlBox and the NW4R
headers; everything is big-endian.
"""
from __future__ import annotations

import math
import struct

import numpy as np


class BrresError(ValueError):
    pass


# ----------------------------------------------------------------------------------------------------------------- U8
def u8_files(data):
    """{path: bytes} of a U8 archive (.arc)."""
    if data[:4] != b"\x55\xaa\x38\x2d":
        raise BrresError("not a U8 archive")
    root = struct.unpack_from(">I", data, 4)[0]
    count = struct.unpack_from(">I", data, root + 8)[0]
    strings = root + count * 12
    out, stack = {}, []                                 # stack: (end index, folder path)

    def name(o):
        return data[strings + o:data.index(b"\0", strings + o)].decode("ascii", "replace")
    for i in range(1, count):
        while stack and i >= stack[-1][0]:
            stack.pop()
        w, a, b = struct.unpack_from(">III", data, root + i * 12)
        folder = "/".join(s for _e, s in stack)
        path = (folder + "/" if folder else "") + name(w & 0xFFFFFF)
        if w >> 24:
            stack.append((b, path))
        else:
            out[path] = data[a:a + b]
    return out


# ------------------------------------------------------------------------------------------------------------- reader
def _str(data, off):
    return data[off:data.index(b"\0", off)].decode("ascii", "replace") if 0 < off < len(data) else ""


def _group(data, off):
    """{name: absolute data offset} of an NW4R index group at off."""
    out = {}
    if off <= 0:
        return out
    count = struct.unpack_from(">I", data, off + 4)[0]
    for i in range(1, count + 1):
        e = off + 8 + i * 16
        n, d = struct.unpack_from(">ii", data, e + 8)
        out[_str(data, off + n)] = off + d
    return out


# --------------------------------------------------------------------------------------------------------- textures
def _rgb565(v):
    v = v.astype(np.uint32)
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2), np.full_like(r, 255)], -1).astype(np.uint8)


def _rgb5a3(v):
    v = v.astype(np.uint32)
    opaque = (v & 0x8000) != 0
    r5, g5, b5 = (v >> 10) & 31, (v >> 5) & 31, v & 31
    r4, g4, b4, a3 = (v >> 8) & 15, (v >> 4) & 15, v & 15, (v >> 12) & 7
    r = np.where(opaque, (r5 << 3) | (r5 >> 2), r4 * 17)
    g = np.where(opaque, (g5 << 3) | (g5 >> 2), g4 * 17)
    b = np.where(opaque, (b5 << 3) | (b5 >> 2), b4 * 17)
    a = np.where(opaque, 255, (a3 << 5) | (a3 << 2) | (a3 >> 1))
    return np.stack([r, g, b, a], -1).astype(np.uint8)


def _ia8(v):
    v = v.astype(np.uint32)
    i, a = v & 0xFF, v >> 8
    return np.stack([i, i, i, a], -1).astype(np.uint8)


# format: (block w, block h, bits per pixel)
GX_FORMATS = {0: (8, 8, 4), 1: (8, 4, 8), 2: (8, 4, 8), 3: (4, 4, 16), 4: (4, 4, 16), 5: (4, 4, 16), 6: (4, 4, 32),
              8: (8, 8, 4), 9: (8, 4, 8), 10: (4, 4, 16), 14: (8, 8, 4)}
FORMAT_NAMES = {0: "I4", 1: "I8", 2: "IA4", 3: "IA8", 4: "RGB565", 5: "RGB5A3", 6: "RGBA8", 8: "C4", 9: "C8",
                10: "C14X2", 14: "CMPR"}


def texture_size(fmt, w, h):
    bw, bh, bpp = GX_FORMATS[fmt]
    return ((w + bw - 1) // bw) * ((h + bh - 1) // bh) * bw * bh * bpp // 8


def _untile(blocks, w, h, bw, bh):
    """(nblocks, bh, bw, c) blocks in row order -> (h, w, c) image (cropped)."""
    bx, by = (w + bw - 1) // bw, (h + bh - 1) // bh
    img = blocks.reshape(by, bx, bh, bw, -1).transpose(0, 2, 1, 3, 4).reshape(by * bh, bx * bw, -1)
    return img[:h, :w]


def _cmpr(raw, w, h):
    bx, by = (w + 7) // 8, (h + 7) // 8
    sub = np.frombuffer(raw, np.uint8)[:bx * by * 32].reshape(-1, 8)        # DXT1 sub-blocks, big-endian
    c0 = sub[:, 0].astype(np.uint16) << 8 | sub[:, 1]
    c1 = sub[:, 2].astype(np.uint16) << 8 | sub[:, 3]
    p0, p1 = _rgb565(c0).astype(np.int32), _rgb565(c1).astype(np.int32)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, 0)
    p3[:, 3] = np.where(four[:, 0], 255, 0)
    pal = np.stack([p0, p1, p2, p3], 1).astype(np.uint8)                    # (n, 4, 4)
    bits = sub[:, 4:8]
    idx = np.stack([(bits >> s) & 3 for s in (6, 4, 2, 0)], -1).reshape(-1, 16)     # MSB first
    px = np.take_along_axis(pal, idx[:, :, None].astype(np.int64).repeat(4, 2), 1).reshape(-1, 4, 4, 4)
    # 2x2 sub-blocks per 8x8 block
    px = px.reshape(by, bx, 2, 2, 4, 4, 4).transpose(0, 2, 4, 1, 3, 5, 6).reshape(by * 8, bx * 8, 4)
    return px[:h, :w]


def decode_texture(raw, fmt, w, h, palette=None):
    """(h, w, 4) uint8 RGBA of a GX texture (palette: (n, 4) uint8 for C4/C8/C14X2)."""
    if fmt == 14:
        return _cmpr(raw, w, h)
    bw, bh, bpp = GX_FORMATS[fmt]
    n = texture_size(fmt, w, h)
    b = np.frombuffer(raw, np.uint8)[:n]
    if fmt in (0, 8):
        v = np.stack([b >> 4, b & 15], -1).reshape(-1)
        if fmt == 0:
            i = (v * 17).astype(np.uint8)
            px = np.stack([i, i, i, np.full_like(i, 255)], -1)
        else:
            px = palette[np.minimum(v, len(palette) - 1)]
    elif fmt in (1, 9):
        if fmt == 1:
            px = np.stack([b, b, b, np.full_like(b, 255)], -1)
        else:
            px = palette[np.minimum(b, len(palette) - 1)]
    elif fmt == 2:
        i, a = (b & 15) * 17, (b >> 4) * 17
        px = np.stack([i, i, i, a], -1).astype(np.uint8)
    elif fmt == 6:
        blk = b.reshape(-1, 2, 16, 2)                                          # AR pairs, then GB pairs
        a, r, g, bb = blk[:, 0, :, 0], blk[:, 0, :, 1], blk[:, 1, :, 0], blk[:, 1, :, 1]
        px = np.stack([r, g, bb, a], -1).reshape(-1, 4)
    else:
        v = b.view(">u2")
        if fmt == 3:
            px = _ia8(v)
        elif fmt == 4:
            px = _rgb565(v)
        elif fmt == 5:
            px = _rgb5a3(v)
        else:                                                                  # C14X2
            px = palette[np.minimum(v & 0x3FFF, len(palette) - 1)]
    return _untile(px.reshape(-1, bh, bw, 4), w, h, bw, bh)


class Texture:
    def __init__(self, data, off):
        self.data, self.off = data, off
        (self.data_off, name, self.flags, self.width, self.height, self.format, self.mips) = struct.unpack_from(
            ">iiIHHII", data, off + 0x10)
        self.name = _str(data, off + name)
        self.palette = None                     # set by Brres for colour-indexed textures

    def rgba(self):
        fmt = self.format
        if fmt not in GX_FORMATS:
            raise BrresError("texture %s: unknown GX format %d" % (self.name, fmt))
        raw = self.data[self.off + self.data_off:self.off + self.data_off + texture_size(fmt, self.width, self.height)]
        pal = self.palette.colors() if self.palette is not None else np.full((256, 4), 255, np.uint8)
        return decode_texture(raw, fmt, self.width, self.height, pal)


class Palette:
    def __init__(self, data, off):
        self.data, self.off = data, off
        self.data_off, name, self.format, self.count = struct.unpack_from(">iiIH", data, off + 0x10)
        self.name = _str(data, off + name)

    def colors(self):
        v = np.frombuffer(self.data, ">u2", self.count, self.off + self.data_off)
        return {0: _ia8, 1: _rgb565, 2: _rgb5a3}.get(self.format, _rgb5a3)(v)


# ------------------------------------------------------------------------------------------------------------ models
def _srt_matrix(s, r, t):
    """4x4 from scale, rotation (degrees, XYZ order as NW4R: R = Rz * Ry * Rx) and translation."""
    rx, ry, rz = (math.radians(a) for a in r)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    R = (np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
         @ np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]))
    m = np.identity(4)
    m[:3, :3] = R * np.asarray(s)
    m[:3, 3] = t
    return m


class Bone:
    def __init__(self, data, off):
        name = struct.unpack_from(">i", data, off + 8)[0]
        self.name = _str(data, off + name)
        self.index, self.matrix_id, self.flags = struct.unpack_from(">III", data, off + 0x0C)
        self.scale = struct.unpack_from(">3f", data, off + 0x20)
        self.rotation = struct.unpack_from(">3f", data, off + 0x2C)
        self.translation = struct.unpack_from(">3f", data, off + 0x38)
        parent = struct.unpack_from(">i", data, off + 0x5C)[0]
        self.parent_off = off + parent if parent else None
        self.off = off
        m = np.identity(4)
        m[:3, :4] = np.asarray(struct.unpack_from(">12f", data, off + 0x70)).reshape(3, 4)
        self.world = m                          # bind pose, model space
        self.parent = None
        self.local = _srt_matrix(self.scale, self.rotation, self.translation)

    @property
    def position(self):
        return tuple(float(v) for v in self.world[:3, 3])


WRAPS = {0: "clamp", 1: "repeat", 2: "mirror"}                 # GX_CLAMP, GX_REPEAT, GX_MIRROR


class Layer:
    """A material's texture reference (BrawlCrate MDL0TextureRef: names, runtime pointers, two indices, then wrap
    u/v, min/mag filter...)."""

    def __init__(self, data, off):
        tname, pname = struct.unpack_from(">ii", data, off)
        self.texture = _str(data, off + tname)
        self.palette = _str(data, off + pname) if pname else ""
        self.wrap_s, self.wrap_t, self.min_filter, self.mag_filter = struct.unpack_from(">IIII", data, off + 0x18)
        self.uv_set = 0                      # the vertex UV set its texture coordinate is made from (Material sets it)

    @property
    def wrap(self):
        return WRAPS.get(self.wrap_s, "repeat"), WRAPS.get(self.wrap_t, "repeat")


class Material:
    def __init__(self, data, off, version):
        name = struct.unpack_from(">i", data, off + 8)[0]
        self.name = _str(data, off + name)
        self.index, self.flags = struct.unpack_from(">II", data, off + 0x0C)
        self.texgens, self.lightchans, self.stages, self.indirect = struct.unpack_from(">4B", data, off + 0x14)
        self.cull = struct.unpack_from(">I", data, off + 0x18)[0]      # GX: 0 none, 1 front, 2 back, 3 all
        self.translucent = bool(self.flags & 0x80000000)
        ntex, layers = struct.unpack_from(">Ii", data, off + 0x2C)
        self.layers = [Layer(data, off + layers + i * 0x34) for i in range(min(ntex, 8))] if layers else []
        # texture SRTs (BrawlCrate MDL0Material.TexMatrices): 0x1A0 + the texture-ref offset's low nibble (or 4 / 8
        # with no textures), then two u32 flag words and eight entries of 5 floats (scale u/v, rotation, trans u/v)
        srt = off + 0x1A0 + ((layers & 0xF) if layers else (4 if version < 10 else 8))
        self.uv_srts = [struct.unpack_from(">5f", data, srt + 8 + i * 20) for i in range(min(len(self.layers), 8))]
        self.uv_srt = self.uv_srts[0] if self.uv_srts else None
        # pixel and TEV colour display lists (MatModeBlock, MatTevColorBlock, MatTevKonstBlock): 32 + 64 + 64 bytes
        dl = struct.unpack_from(">i", data, off + (0x3C if version >= 10 else 0x38))[0]
        self.alpha_test, self.alpha_ref, self.tev_colors, self.konst_colors = False, 0.5, [], []
        self.blend = None               # pixel-engine blending (BP 0x41 bit 0), on in some materials not flagged XLU
        if dl > 0:
            d = off + dl
            self.blend = False
            if data[d] == 0x61 and data[d + 1] == 0xF3:                       # alpha function: comp0/1, ref0/1
                bits, _ref1, ref0 = data[d + 2], data[d + 3], data[d + 4]
                if (bits & 7) != 7 or (bits >> 3 & 7) != 7:
                    self.alpha_test, self.alpha_ref = True, ref0 / 255.0
            # blend mode (cmode0): NSMBW's roads blend by alpha without the XLU flag; its fruit and flowers do not blend
            # and have no alpha test, so the Wii draws them opaque whatever their texture's alpha holds
            for p in range(d, d + 30, 5):
                if data[p] == 0x61 and data[p + 1] == 0x41:
                    self.blend = bool(data[p + 4] & 1)
            self.tev_colors = self._colour_regs(data, d + 32, 3)
            self.konst_colors = self._colour_regs(data, d + 96, 4)
            # texture coordinate generators (BrawlCrate XFData.Parse at DisplayLists + 0xE0): XF writes to 0x1040 + i
            # (texgen i) and 0x1050 + i; bits 7-11 of the first are the source row, 5 = vertex UV set 0, 6 = set 1...
            # NSMBW's path materials put the reveal mask on texgen 0 (UV set 0) and the road on texgen 1 (UV set 1).
            gens = self._xf(data, d + 0xE0)
            for i, layer in enumerate(self.layers):
                row = (gens.get(0x1040 + i, 5 << 7) >> 7) & 0x1F
                layer.uv_set = row - 5 if row >= 5 else -1          # -1: made from positions/normals (env maps)

    @staticmethod
    def _xf(data, p):
        """{XF address: value} from a run of XF load commands (0x10, u16 count-1, u16 address, values)."""
        out = {}
        while p + 5 <= len(data) and data[p] == 0x10:
            count, addr = struct.unpack_from(">HH", data, p + 1)
            vals = struct.unpack_from(">%dI" % (count + 1), data, p + 5)
            for k, v in enumerate(vals):
                out[addr + k] = v
            p += 5 + 4 * (count + 1)
        return out

    @staticmethod
    def _colour_regs(data, p, count):
        """[(r, g, b, a)] 0..1 from pairs of BP colour-register writes (lo: red/alpha, hi: blue/green; 11 bits)."""
        out = []
        for i in range(count):
            lo = data[p + 2:p + 5]
            hi = data[p + 7:p + 10]
            if data[p] != 0x61 or len(hi) < 3:
                break

            def split(b):
                v = (b[0] << 16) | (b[1] << 8) | b[2]
                return v & 0x7FF, (v >> 12) & 0x7FF
            r, a = split(lo)
            b, g = split(hi)
            out.append(tuple(min(c, 255) / 255.0 for c in (r, g, b, a)))
            # colour registers repeat their hi write three times (20 bytes per register), konst ones once (10 bytes)
            p += 20 if count == 3 else 10
        return out


class Polygon:
    def __init__(self, data, off, version):
        self.off = off
        (self.bone_id, self.desc_lo, self.desc_hi) = struct.unpack_from(">iII", data, off + 8)
        decl_size, _decl_actual, decl_off = struct.unpack_from(">IIi", data, off + 0x18)
        prim_size, prim_actual, prim_off = struct.unpack_from(">IIi", data, off + 0x24)
        self.prims = data[off + 0x24 + prim_off:off + 0x24 + prim_off + prim_size]
        name = struct.unpack_from(">i", data, off + 0x38)[0]
        self.name = _str(data, off + name)
        self.index, self.vertex_count, self.face_count = struct.unpack_from(">III", data, off + 0x3C)
        n = 14 if version >= 10 else 12
        ids = struct.unpack_from(">%dh" % n, data, off + 0x48)
        self.pos_id, self.nrm_id = ids[0], ids[1]
        self.clr_ids = ids[2:4]
        self.uv_ids = ids[4:12]
        wt = struct.unpack_from(">i", data, off + 0x48 + n * 2)[0]
        self.matrix_ids = []                    # the polygon's matrix table (matrix ids used by PNMTXIDX / 3)
        if wt:
            cnt = struct.unpack_from(">I", data, off + wt)[0]
            if cnt < 64:
                self.matrix_ids = list(struct.unpack_from(">%dH" % cnt, data, off + wt + 4))

    def attributes(self):
        """[(attribute, kind)] in GX order: PNMTXIDX, TEX0-7 MTXIDX, POS, NRM, CLR0, CLR1, TEX0-7; kind 0 none,
        1 direct, 2 index8, 3 index16."""
        lo, hi = self.desc_lo, self.desc_hi
        out = [("pnmtx", 1 if lo & 1 else 0)]
        out += [("texmtx%d" % i, 1 if lo >> (1 + i) & 1 else 0) for i in range(8)]
        out += [("pos", lo >> 9 & 3), ("nrm", lo >> 11 & 3), ("clr0", lo >> 13 & 3), ("clr1", lo >> 15 & 3)]
        out += [("uv%d" % i, hi >> (i * 2) & 3) for i in range(8)]
        return out


def _array(data, off, kind):
    """(name, numpy array (count, comps) float32) of a vertex/normal/uv/colour array."""
    size, _mdl, doff, name, index = struct.unpack_from(">iiiiI", data, off)
    nm = _str(data, off + name)
    if kind == "clr":
        comp, fmt, stride, _scale, count = struct.unpack_from(">IIBBH", data, off + 0x14)
        raw = data[off + doff:off + doff + stride * count]
        b = np.frombuffer(raw, np.uint8).reshape(count, stride)
        if fmt == 0:
            c = _rgb565(b[:, :2].copy().view(">u2").ravel())
        elif fmt in (1, 2):
            c = np.c_[b[:, :3], np.full(count, 255, np.uint8)]
        elif fmt == 3:
            v = b[:, :2].copy().view(">u2").ravel().astype(np.uint32)
            c = np.stack([(v >> 12) * 17, (v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17], -1).astype(np.uint8)
        elif fmt == 4:
            v = (b[:, 0].astype(np.uint32) << 16) | (b[:, 1].astype(np.uint32) << 8) | b[:, 2]
            c = np.stack([(v >> 18 & 63) * 255 // 63, (v >> 12 & 63) * 255 // 63, (v >> 6 & 63) * 255 // 63,
                          (v & 63) * 255 // 63], -1).astype(np.uint8)
        else:
            c = b[:, :4]
        return nm, c.astype(np.float32) / 255.0
    comp, fmt, div, stride, count = struct.unpack_from(">IIBBH", data, off + 0x14)
    if kind == "pos":
        nc = 3 if comp == 1 else 2
    elif kind == "nrm":
        nc, div = (3 if comp == 0 else 9), {1: 6, 3: 14}.get(fmt, 0)      # normals: fixed point by type
    else:
        nc = 2 if comp == 1 else 1
    dt = {0: "u1", 1: "i1", 2: ">u2", 3: ">i2", 4: ">f4"}[fmt]
    es = np.dtype(dt).itemsize
    stride = stride or nc * es
    raw = np.frombuffer(data, np.uint8, stride * count, off + doff).reshape(count, stride)
    vals = raw[:, :nc * es].copy().view(dt).reshape(count, nc).astype(np.float32)
    if fmt != 4:
        vals /= float(1 << div)
    if kind == "nrm":
        vals = vals[:, :3]
    return nm, vals


class Model:
    """An MDL0 model."""

    def __init__(self, data, off, name):
        self.name = name
        self.data, self.off = data, off
        self.version = struct.unpack_from(">I", data, off + 8)[0]
        nsec = {8: 11, 9: 11, 10: 14, 11: 14}.get(self.version)
        if nsec is None:
            raise BrresError("MDL0 version %d is not supported" % self.version)
        secs = struct.unpack_from(">%di" % nsec, data, off + 0x10)
        g = [_group(data, off + s) if s else {} for s in secs]
        if nsec == 11:
            defs, bones, verts, nrms, clrs, uvs, mats, _shd, polys, _tex, _pal = g
        else:
            defs, bones, verts, nrms, clrs, uvs, _fv, _fp, mats, _shd, polys, _tex, _pal, *_ = g + [{}]
        self.bones = [Bone(data, o) for o in bones.values()]
        by_off = {b.off: b for b in self.bones}
        for b in self.bones:
            b.parent = by_off.get(b.parent_off)
        self.bones.sort(key=lambda b: b.index)
        self.positions = [_array(data, o, "pos") for o in verts.values()]
        self.normals = [_array(data, o, "nrm") for o in nrms.values()]
        self.colors = [_array(data, o, "clr") for o in clrs.values()]
        self.uvs = [_array(data, o, "uv") for o in uvs.values()]
        self._index_arrays(data, off, verts, nrms, clrs, uvs)
        self.materials = sorted((Material(data, o, self.version) for o in mats.values()), key=lambda m: m.index)
        self.polygons = sorted((Polygon(data, o, self.version) for o in polys.values()), key=lambda p: p.index)
        self.draws = []                           # [(material index, polygon index, bone index, translucent)]
        self.mixes = {}                           # matrix id -> [(matrix id, weight)]
        self.envelopes = {}                       # matrix id -> bone index
        for name, o in defs.items():
            self._definitions(data, o, name)
        self.matrix_bone = {b.matrix_id: b for b in self.bones}

    def _index_arrays(self, data, off, *groups):
        """Arrays are referenced from polygons by their index field, which may differ from the group order."""
        for attr, grp in zip(("positions", "normals", "colors", "uvs"), groups):
            arr = getattr(self, attr)
            ids = [struct.unpack_from(">I", data, o + 0x10)[0] for o in grp.values()]
            table = {}
            for i, a in zip(ids, arr):
                table[i] = a
            setattr(self, attr, table)

    def _definitions(self, data, o, name):
        p = o
        while p < len(data):
            op = data[p]
            if op == 1:
                break
            if op == 2:
                p += 5
            elif op == 3:
                mid, cnt = struct.unpack_from(">HB", data, p + 1)
                self.mixes[mid] = [struct.unpack_from(">Hf", data, p + 4 + i * 6) for i in range(cnt)]
                p += 4 + cnt * 6
            elif op == 4:
                mat, poly, bone, _pri = struct.unpack_from(">HHHB", data, p + 1)
                self.draws.append((mat, poly, bone, name.lower().startswith("drawxlu")))
                p += 8
            elif op == 5:
                mid, bone = struct.unpack_from(">HH", data, p + 1)
                self.envelopes[mid] = bone
                p += 5
            elif op == 6:
                p += 5
            else:
                break

    def apply_pose(self, pose):
        """Pose the skeleton: pose is {bone: {"scale"/"rotation"/"translation": [x, y, z]}} (chr0_end_pose). Posed
        bones and their descendants get new world matrices (parent world x local SRT); the rest keep the file's."""
        if not pose:
            return
        moved = set()
        for b in self.bones:                         # index order: parents come first
            if b.name in pose:
                p = pose[b.name]
                b.scale = tuple(p.get("scale", b.scale))
                b.rotation = tuple(p.get("rotation", b.rotation))
                b.translation = tuple(p.get("translation", b.translation))
                b.local = _srt_matrix(b.scale, b.rotation, b.translation)
            if b.name in pose or (b.parent is not None and b.parent.name in moved):
                b.world = (b.parent.world if b.parent is not None else np.identity(4)) @ b.local
                moved.add(b.name)

    def matrix(self, matrix_id):
        """Model-space 4x4 for a matrix id: a bone's bind matrix, or a weighted mix of them."""
        b = self.matrix_bone.get(matrix_id)
        if b is not None:
            return b.world
        mix = self.mixes.get(matrix_id)
        if mix:
            m = np.zeros((4, 4))
            for mid, w in mix:
                m += w * self.matrix(mid) if mid != matrix_id else 0
            m[3] = (0, 0, 0, 1)
            return m
        if matrix_id in self.envelopes and self.envelopes[matrix_id] < len(self.bones):
            return self.bones[self.envelopes[matrix_id]].world
        return np.identity(4)

    # ---- geometry ----------------------------------------------------------------------------------------------------
    def triangles(self, poly, bone_index=None, uv_set=0):
        """Model-space corner arrays of a polygon: (positions (n*3, 3), normals, uvs (n*3, 2) of UV set uv_set,
        colours (n*3, 4))."""
        data = poly.prims
        attrs = [(a, k) for a, k in poly.attributes() if k]
        pos_arr = self.positions.get(poly.pos_id, ("", np.zeros((1, 3), np.float32)))[1]
        nrm_arr = self.normals.get(poly.nrm_id, (None, None))[1] if poly.nrm_id >= 0 else None
        clr_arr = self.colors.get(poly.clr_ids[0], (None, None))[1] if poly.clr_ids[0] >= 0 else None
        uv_set = uv_set if 0 <= uv_set < 8 and poly.uv_ids[uv_set] >= 0 else 0
        uv_key = "uv%d" % uv_set
        uv_arr = self.uvs.get(poly.uv_ids[uv_set], (None, None))[1] if poly.uv_ids[uv_set] >= 0 else None
        if pos_arr.shape[1] == 2:
            pos_arr = np.c_[pos_arr, np.zeros(len(pos_arr), np.float32)]
        slots = {}                                   # matrix slot (PNMTXIDX / 3) -> matrix id
        corners = []                                 # [(slot, pos, nrm, clr, uv)]
        tris = []
        p = 0
        while p < len(data):
            op = data[p]
            if op == 0:
                p += 1
                continue
            if op in (0x20, 0x28, 0x30, 0x38):
                idx, addr = struct.unpack_from(">HH", data, p + 1)
                if op == 0x20:
                    slots[(addr & 0xFFF) // 12] = idx
                p += 5
                continue
            prim = op & 0xF8
            if prim < 0x80 or prim > 0xB8:
                break
            count = struct.unpack_from(">H", data, p + 1)[0]
            p += 3
            verts = []
            for _ in range(count):
                v = {}
                for a, k in attrs:
                    if k == 1:
                        if a == "pnmtx" or a.startswith("texmtx"):
                            v[a] = data[p]
                            p += 1
                        else:
                            raise BrresError("direct vertex data is not supported (%s in %s)" % (a, poly.name))
                    elif k == 2:
                        v[a] = data[p]
                        p += 1
                    else:
                        v[a] = struct.unpack_from(">H", data, p)[0]
                        p += 2
                verts.append(v)
            base = len(corners)
            corners.extend(verts)
            if prim == 0x90:
                tris += [(base + i, base + i + 1, base + i + 2) for i in range(0, count - 2, 3)]
            elif prim == 0x98:
                tris += [(base + i, base + i + 1, base + i + 2) if i % 2 == 0 else (base + i + 1, base + i, base + i + 2)
                         for i in range(count - 2)]
            elif prim == 0xA0:
                tris += [(base, base + i, base + i + 1) for i in range(1, count - 1)]
            elif prim == 0x80:
                for i in range(0, count - 3, 4):
                    tris += [(base + i, base + i + 1, base + i + 2), (base + i, base + i + 2, base + i + 3)]
            # lines and points are skipped
        if not tris:
            z = np.zeros((0, 3), np.float32)
            return z, z, np.zeros((0, 2), np.float32), np.zeros((0, 4), np.float32)
        order = np.asarray(tris, np.int64).ravel()
        pi = np.asarray([c.get("pos", 0) for c in corners], np.int64)
        pos = pos_arr[np.clip(pi, 0, len(pos_arr) - 1)][order].astype(np.float64)
        nrm = (nrm_arr[np.clip(np.asarray([c.get("nrm", 0) for c in corners]), 0, len(nrm_arr) - 1)][order]
               if nrm_arr is not None and "nrm" in dict(attrs) else None)
        uv = (uv_arr[np.clip(np.asarray([c.get(uv_key, 0) for c in corners]), 0, len(uv_arr) - 1)][order]
              if uv_arr is not None and uv_key in dict(attrs) else np.zeros((len(order), 2), np.float32))
        if uv.shape[1] == 1:
            uv = np.c_[uv, np.zeros(len(uv), np.float32)]
        clr = (clr_arr[np.clip(np.asarray([c.get("clr0", 0) for c in corners]), 0, len(clr_arr) - 1)][order]
               if clr_arr is not None and "clr0" in dict(attrs) else np.ones((len(order), 4), np.float32))
        # skinning: per-vertex matrix (PNMTXIDX) through the polygon's matrix table, or the polygon's bone
        if "pnmtx" in dict(attrs):
            mids = []
            for c in corners:
                slot = c["pnmtx"] // 3
                mid = slots.get(slot)
                if mid is None and slot < len(poly.matrix_ids):
                    mid = poly.matrix_ids[slot]
                mids.append(-1 if mid is None else mid)
            mids = np.asarray(mids)[order]
        else:
            # the polygon's node id is a matrix id (BrawlCrate MDL0Object._nodeId); without one, the draw's bone
            if poly.bone_id >= 0:
                mid = poly.bone_id
            elif bone_index is not None and 0 <= bone_index < len(self.bones):
                mid = self.bones[bone_index].matrix_id
            else:
                mid = -1
            mids = np.full(len(order), mid)
        out_p, out_n = np.empty_like(pos), None if nrm is None else np.empty((len(nrm), 3))
        for mid in np.unique(mids):
            sel = mids == mid
            m = self.matrix(int(mid)) if mid >= 0 else np.identity(4)
            out_p[sel] = pos[sel] @ m[:3, :3].T + m[:3, 3]
            if nrm is not None:
                nm = np.linalg.inv(m[:3, :3]).T
                out_n[sel] = nrm[sel] @ nm.T
        if out_n is not None:
            out_n /= np.maximum(np.linalg.norm(out_n, axis=1, keepdims=True), 1e-9)
        return (out_p.astype(np.float32), None if out_n is None else out_n.astype(np.float32),
                uv.astype(np.float32), clr.astype(np.float32))


# ------------------------------------------------------------------------------------------------- bone animations
def _track_end(data, p, fmt, frames):
    """The last key's value of a CHR0 track (BrawlCrate AnimationConverter.DecodeFrames / EncodingTypes)."""
    if fmt == 1:                                                     # I4: u16 count, pad, f32 scale, step, base
        n, _u, _fs, step, base = struct.unpack_from(">HHfff", data, p)
        keys = [struct.unpack_from(">I", data, p + 16 + 4 * i)[0] for i in range(n)]
        last = max(keys, key=lambda v: v >> 24) if keys else 0
        return base + ((last >> 12) & 0xFFF) * step
    if fmt == 2:                                                     # I6: entries u16 index<<5, u16 step, s16 tangent
        n, _u, _fs, step, base = struct.unpack_from(">HHfff", data, p)
        keys = [struct.unpack_from(">HHh", data, p + 16 + 6 * i) for i in range(n)]
        last = max(keys, key=lambda k: k[0] >> 5) if keys else (0, 0, 0)
        return base + last[1] * step
    if fmt == 3:                                                     # I12: entries f32 frame, value, tangent
        n = struct.unpack_from(">H", data, p)[0]
        keys = [struct.unpack_from(">fff", data, p + 8 + 12 * i) for i in range(n)]
        return max(keys, key=lambda k: k[0])[1] if keys else 0.0
    if fmt in (4, 5):                                                # L1 / L2: f32 step, base, then a value per frame
        step, base = struct.unpack_from(">ff", data, p)
        size = 1 if fmt == 4 else 2
        v = struct.unpack_from(">B" if fmt == 4 else ">H", data, p + 8 + size * (frames - 1))[0]
        return base + v * step
    if fmt == 6:                                                     # L4: a float per frame
        return struct.unpack_from(">f", data, p + 4 * (frames - 1))[0]
    return None


def chr0_end_pose(data, off):
    """{bone name: {"scale": [..], "rotation": [..], "translation": [..]}} at the last frame of a CHR0 animation (only
    the channels it animates). NW4R CHR0 v3-5 (BrawlCrate CHR0v4_3/CHR0v5, AnimationCode)."""
    version = struct.unpack_from(">I", data, off + 8)[0]
    if version >= 5:
        data_off, frames = struct.unpack_from(">i", data, off + 0x10)[0], struct.unpack_from(">H", data, off + 0x20)[0]
    else:
        data_off, frames = struct.unpack_from(">i", data, off + 0x10)[0], struct.unpack_from(">H", data, off + 0x1C)[0]
    frames = max(int(frames), 1)
    pose = {}
    for name, e in _group(data, off + data_off).items():
        code = struct.unpack_from(">I", data, e + 4)[0]
        p = e + 8
        out = {}
        for key, has, iso, fixed0, fmt_shift, fmt_bits in (("scale", 22, 4, 13, 25, 2), ("rotation", 23, 5, 16, 27, 3),
                                                           ("translation", 24, 6, 19, 30, 2)):
            if not code >> has & 1:
                continue
            fmt = code >> fmt_shift & ((1 << fmt_bits) - 1)
            vals = []
            for axis in range(1 if code >> iso & 1 else 3):
                fixed = code >> (fixed0 + (2 if code >> iso & 1 else axis)) & 1
                word = struct.unpack_from(">f" if fixed else ">I", data, p)[0]
                p += 4
                vals.append(word if fixed else _track_end(data, e + word, fmt, frames))
            out[key] = vals * 3 if len(vals) == 1 else vals
        if out:
            pose[name] = out
    return pose


class Brres:
    def __init__(self, data):
        if data[:4] != b"bres":
            raise BrresError("not a BRRES file")
        root = struct.unpack_from(">H", data, 0x0C)[0]
        self.data = data
        self.models, self.textures, self.palettes, self.other = {}, {}, {}, {}
        self.chr0 = {}                           # bone animation name -> offset (chr0_end_pose)
        for folder, off in _group(data, root + 8).items():
            for name, o in _group(data, off).items():
                magic = data[o:o + 4]
                if magic == b"MDL0":
                    self.models[name] = Model(data, o, name)
                elif magic == b"TEX0":
                    self.textures[name] = Texture(data, o)
                elif magic == b"PLT0":
                    self.palettes[name] = Palette(data, o)
                else:
                    if magic == b"CHR0":
                        self.chr0[name] = o
                    self.other.setdefault(folder, []).append(name)

    def end_pose(self, name):
        return chr0_end_pose(self.data, self.chr0[name]) if name in self.chr0 else {}
        for t in self.textures.values():
            if t.flags & 1 or t.format in (8, 9, 10):
                t.palette = self.palettes.get(t.name)

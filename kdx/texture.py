"""
kdx/texture.py - read the textures of a BFRES (its embedded textures.bntx) as RGBA8 pixels.

    textures = read_bntx(bfres.external_bytes("textures.bntx"))   # {name: Texture}
    rgba = textures["W1_grass_alb"].rgba()                         # numpy uint8 array (height, width, 4)

Covers every format the world maps use (all 12 surveyed: BC1, BC2, BC3, BC4, BC5 unorm/snorm, RGBA8). Pixels are
stored block-linear (Tegra X1 "GOB" swizzle): 64-byte x 8-row GOBs stacked (1 << block_height_log2) high. Both the
deswizzle and the BC decoders are vectorised with numpy, so a world's 50-150 textures decode in about a second.

Written for Koopatlas DX from the public format descriptions; checked pixel for pixel against an independent decoder
(tests/test_textures.py).
"""
from __future__ import annotations

import struct

import numpy as np

# NX format: high byte = channel layout, low byte = type (1 unorm, 2 snorm, 6 srgb).
LAYOUTS = {0x0B: ("rgba8", 4, 1), 0x0C: ("bgra8", 4, 1), 0x02: ("r8", 1, 1), 0x09: ("rg8", 2, 1),
           0x1A: ("bc1", 8, 4), 0x1B: ("bc2", 16, 4), 0x1C: ("bc3", 16, 4), 0x1D: ("bc4", 8, 4), 0x1E: ("bc5", 16, 4)}
TYPES = {1: "unorm", 2: "snorm", 6: "srgb"}


class TextureError(ValueError):
    pass


class Texture:
    def __init__(self):
        self.name = ""
        self.width = self.height = 0
        self.format = 0                       # NX format value
        self.block_height_log2 = 0
        self.channels = (2, 3, 4, 5)           # component selectors for r, g, b, a: 0 zero, 1 one, 2-5 = R, G, B, A
        self.mip_count = 1
        self.data = b""                        # every mip level, swizzled
        self._rgba = None

    @property
    def layout(self):
        return LAYOUTS.get(self.format >> 8, ("nx_0x%02X" % (self.format >> 8), 0, 0))[0]

    @property
    def kind(self):
        return TYPES.get(self.format & 0xFF, "type%d" % (self.format & 0xFF))

    @property
    def format_name(self):
        return "%s_%s" % (self.layout, self.kind)

    def supported(self):
        return (self.format >> 8) in LAYOUTS

    def raw_rgba(self):
        """Mip 0 decoded to RGBA8 without the channel selectors (what the format stores)."""
        if not self.supported():
            raise TextureError("%s: format %s is not supported." % (self.name, self.format_name))
        name, bpp, blk = LAYOUTS[self.format >> 8]
        bw, bh = -(-self.width // blk), -(-self.height // blk)
        lin = deswizzle(bw, bh, bpp, self.block_height_log2, self.data)
        if name in ("rgba8", "bgra8", "r8", "rg8"):
            px = np.frombuffer(lin, np.uint8).reshape(bh, bw, bpp)
            out = np.zeros((bh, bw, 4), np.uint8)
            out[..., 3] = 255
            if name == "bgra8":
                out[...] = px[..., [2, 1, 0, 3]]
            else:
                out[..., :bpp] = px
            return out
        blocks = np.frombuffer(lin, np.uint8).reshape(-1, bpp)
        snorm = self.kind == "snorm"
        if name == "bc1":
            px = decode_bc1(blocks)
        elif name == "bc2":
            px = decode_bc2(blocks)
        elif name == "bc3":
            px = decode_bc3(blocks)
        elif name == "bc4":
            r = decode_bc4(blocks, snorm)
            px = np.stack([r, np.zeros_like(r), np.zeros_like(r), np.full_like(r, 255)], -1)
        else:
            r, g = decode_bc4(blocks[:, :8], snorm), decode_bc4(blocks[:, 8:], snorm)
            px = np.stack([r, g, np.zeros_like(r), np.full_like(r, 255)], -1)
        img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
        return np.ascontiguousarray(img[:self.height, :self.width])

    def rgba(self):
        """Mip 0 as the shader sees it: RGBA8 with the texture's channel selectors applied (cached)."""
        if self._rgba is None:
            raw = self.raw_rgba()
            out = np.empty_like(raw)
            for i, sel in enumerate(self.channels):
                out[..., i] = 0 if sel == 0 else 255 if sel == 1 else raw[..., sel - 2] if 2 <= sel <= 5 else 0
            self._rgba = out
        return self._rgba


# --------------------------------------------------------------------------------------------- BNTX
def read_bntx(data):
    """{name: Texture} from a BNTX file (the textures.bntx inside a Switch BFRES)."""
    if not data or data[:4] != b"BNTX":
        return {}
    u16 = lambda o: struct.unpack_from("<H", data, o)[0]
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]
    u64 = lambda o: struct.unpack_from("<Q", data, o)[0]

    def string(off):
        n = u16(off)
        return data[off + 2:off + 2 + n].decode("utf-8", "replace")
    if data[0x20:0x24] != b"NX  ":
        raise TextureError("Unknown BNTX target %r." % data[0x20:0x24])
    count, infos = u32(0x24), u64(0x28)
    out = {}
    for i in range(count):
        o = u64(infos + 8 * i)
        if data[o:o + 4] != b"BRTI":
            raise TextureError("Texture %d has no BRTI block." % i)
        t = Texture()
        t.mip_count = u16(o + 0x16)
        t.format, t.width, t.height = u32(o + 0x1C), u32(o + 0x24), u32(o + 0x28)
        t.block_height_log2 = u32(o + 0x34) & 7
        size = u32(o + 0x50)
        comp = u32(o + 0x58)
        t.channels = tuple((comp >> (8 * k)) & 0xFF for k in range(4))
        t.name = string(u64(o + 0x60))
        first = u64(u64(o + 0x70))
        t.data = data[first:first + size]
        out[t.name] = t
    return out


# --------------------------------------------------------------------------------------------- Tegra X1 swizzle
def deswizzle(width, height, bpp, block_height_log2, data):
    """Block-linear -> linear for mip 0. width/height in blocks (pixels for uncompressed formats), bpp bytes per block.

    The surface is made of GOBs (64 bytes x 8 rows); (1 << block_height_log2) GOBs stack into a block, blocks run left
    to right, then down. Inside a GOB, a byte at (x, y) sits at (x%64//32)*256 + (y%8//2)*64 + (x%32//16)*32 +
    (y%2)*16 + x%16."""
    gob_h = 1 << block_height_log2
    row = width * bpp
    gobs_x = -(-row // 64)
    block_bytes = 512 * gob_h
    x = np.arange(row, dtype=np.int64)
    y = np.arange(height, dtype=np.int64)[:, None]
    addr = ((y // (8 * gob_h)) * gobs_x * block_bytes + (x // 64) * block_bytes + ((y % (8 * gob_h)) // 8) * 512
            + ((x % 64) // 32) * 256 + ((y % 8) // 2) * 64 + ((x % 32) // 16) * 32 + (y % 2) * 16 + x % 16)
    src = np.frombuffer(data, np.uint8)
    if addr.max() >= len(src):
        src = np.concatenate([src, np.zeros(int(addr.max()) + 1 - len(src), np.uint8)])
    return src[addr].tobytes()


# --------------------------------------------------------------------------------------------- BC decoders
def _rgb565(c):
    c = c.astype(np.uint32)
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.int32)


def _color_block(blocks, four_colour):
    """Colour part of BC1/2/3 (8 bytes per block) -> (n, 16, 4) uint8. four_colour forces the 4-colour mode."""
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    bits = (blocks[:, 4].astype(np.uint32) | (blocks[:, 5].astype(np.uint32) << 8) |
            (blocks[:, 6].astype(np.uint32) << 16) | (blocks[:, 7].astype(np.uint32) << 24))
    a, b = _rgb565(c0), _rgb565(c1)
    four = np.ones(len(blocks), bool) if four_colour else (c0 > c1)
    pal = np.empty((len(blocks), 4, 4), np.int32)
    pal[:, 0, :3], pal[:, 1, :3] = a, b
    pal[:, 2, :3] = np.where(four[:, None], (2 * a + b) // 3, (a + b) // 2)
    pal[:, 3, :3] = np.where(four[:, None], (a + 2 * b) // 3, 0)
    pal[..., 3] = 255
    pal[:, 3, 3] = np.where(four, 255, 0)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    return np.take_along_axis(pal, idx[..., None].astype(np.int64).repeat(4, -1), 1).astype(np.uint8)


def decode_bc1(blocks):
    return _color_block(blocks, False)


def encode_bc1(rgba):
    """(h, w, 3+) uint8 image, sides multiples of 4 -> BC1 blocks (linear, row of blocks after row; 8 bytes each), the
    format the game's ground textures use (8 times smaller than RGBA8). Opaque, 4-colour mode: endpoints are the
    block's extremes along its main colour axis (a few power-iteration steps), each pixel takes the nearest of the four
    palette colours."""
    img = np.asarray(rgba)[..., :3].astype(np.float32)
    h, w = img.shape[:2]
    if h % 4 or w % 4:
        raise TextureError("BC1 needs sides that are multiples of 4 (got %dx%d)." % (w, h))
    px = img.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3)
    mean = px.mean(axis=1, keepdims=True)
    c = px - mean
    cov = np.einsum("nki,nkj->nij", c, c)
    axis = np.tile(np.array([0.577, 0.577, 0.577], np.float32), (len(px), 1))
    for _ in range(6):
        axis = np.einsum("nij,nj->ni", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-6)
    t = np.einsum("nki,ni->nk", c, axis)
    lo = mean[:, 0] + axis * t.min(axis=1, keepdims=True)
    hi = mean[:, 0] + axis * t.max(axis=1, keepdims=True)

    def q565(col):
        r = np.clip(np.rint(col[:, 0] * 31 / 255), 0, 31).astype(np.uint32)
        g = np.clip(np.rint(col[:, 1] * 63 / 255), 0, 63).astype(np.uint32)
        b = np.clip(np.rint(col[:, 2] * 31 / 255), 0, 31).astype(np.uint32)
        return (r << 11) | (g << 5) | b
    c0, c1 = q565(hi), q565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    a, b = _rgb565(c0).astype(np.float32), _rgb565(c1).astype(np.float32)
    pal = np.stack([a, b, (2 * a + b) // 3, (a + 2 * b) // 3], axis=1)         # as decode_bc1 builds it
    d = ((px[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = d.argmin(axis=2).astype(np.uint32)
    idx[c0 == c1] = 0                                                         # flat block: 3-colour mode, colour 0
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(axis=1, dtype=np.uint64).astype(np.uint32)
    out = np.empty((len(px), 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    for k in range(4):
        out[:, 4 + k] = (bits >> (8 * k)) & 255
    return out.tobytes()


def decode_bc2(blocks):
    px = _color_block(blocks[:, 8:], True)
    a = blocks[:, :8].astype(np.uint8)
    nib = np.stack([a & 15, a >> 4], -1).reshape(len(blocks), 16)
    px[..., 3] = (nib * 17).astype(np.uint8)
    return px


def decode_bc4(blocks, snorm=False):
    """8-byte BC4 blocks -> (n, 16) uint8 (snorm values -128..127 are shifted to 0-255)."""
    if snorm:
        e0 = blocks[:, 0].view(np.int8).astype(np.int32)
        e1 = blocks[:, 1].view(np.int8).astype(np.int32)
        e0, e1 = np.maximum(e0, -127), np.maximum(e1, -127)
    else:
        e0, e1 = blocks[:, 0].astype(np.int32), blocks[:, 1].astype(np.int32)
    bits = np.zeros(len(blocks), np.uint64)
    for k in range(6):
        bits |= blocks[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    idx = ((bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
    pal = np.empty((len(blocks), 8), np.float64)
    pal[:, 0], pal[:, 1] = e0, e1
    eight = e0 > e1
    for i in range(1, 7):
        pal[:, 1 + i] = np.where(eight, ((7 - i) * e0 + i * e1) / 7.0, 0)
    for i in range(1, 5):
        pal[:, 1 + i] = np.where(eight, pal[:, 1 + i], ((5 - i) * e0 + i * e1) / 5.0)
    lo, hi = (-127.0, 127.0) if snorm else (0.0, 255.0)
    pal[:, 6] = np.where(eight, pal[:, 6], lo)
    pal[:, 7] = np.where(eight, pal[:, 7], hi)
    v = np.take_along_axis(pal, idx, 1)
    if snorm:
        v = np.floor(v) + 128                   # -128..127 -> 0..255 (as Pillow's decoder maps signed values)
    return np.clip(np.floor(v), 0, 255).astype(np.uint8)            # truncating, like Pillow's decoder


def decode_bc3(blocks):
    px = _color_block(blocks[:, 8:], True)
    px[..., 3] = decode_bc4(blocks[:, :8])
    return px

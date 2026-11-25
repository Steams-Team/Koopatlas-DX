#!/usr/bin/env python3
"""
Texture decoding tests: kdx.texture against two independent decoders, on every texture of every world map.

    python tests/test_textures.py <romfs folder>

- BC1-BC5 pixels are compared with Pillow's DDS decoder (BcnDecode, C), fed the same deswizzled blocks.
- The Tegra X1 deswizzle is compared with Yaz0loom's (KDX_YAZ0LOOM=<Yaz0loom.Python/src>; skipped if unset).
  Note: Yaz0loom's own BC4/BC3-alpha decoder weights its palette (6-i, i)/7 instead of (7-i, i)/7, so only its
  deswizzle is used here.
"""
import io
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kdx import romfs, texture
from kdx.world import World

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if (detail and not ok) else ""))


root = romfs.normalize(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KDX_ROMFS", ""))
if root is None:
    raise SystemExit("Pass the romfs folder (with Model/CS_W1.Nin_NX_NVN.szs).")
worlds = [(wid, World(p)) for wid, p in romfs.worlds(root)]

FOURCC = {"bc1": b"DXT1", "bc2": b"DXT3", "bc3": b"DXT5", "bc4": b"ATI1", "bc5": b"ATI2"}
DXGI = {("bc4", "snorm"): 81, ("bc5", "snorm"): 84}


def dds(name, kind, w, h, blocks):
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    struct.pack_into("<IIIIIII", hdr, 4, 124, 0x1007, h, w, len(blocks), 0, 1)
    struct.pack_into("<II4s", hdr, 76, 32, 4, FOURCC[name])
    struct.pack_into("<I", hdr, 108, 0x1000)
    if (name, kind) in DXGI:
        struct.pack_into("<4s", hdr, 84, b"DX10")
        hdr += struct.pack("<IIIII", DXGI[(name, kind)], 3, 0, 1, 0)
    return bytes(hdr) + blocks


all_tex = []
t0 = time.time()
for wid, w in worlds:
    data = bytes(w.bfres_bytes)
    texs = texture.read_bntx(data[data.find(b"BNTX"):])
    for t in texs.values():
        t.raw_rgba()
    all_tex += [(wid, t) for t in texs.values()]
elapsed = time.time() - t0
formats = {t.format_name for _, t in all_tex}
check("every texture of the 12 world maps decodes (%d textures, %d formats)" % (len(all_tex), len(formats)),
      len(worlds) == 12 and all(t.supported() for _, t in all_tex), sorted(formats))
check("decoding all of them takes under 30 s (%.1f s)" % elapsed, elapsed < 30, elapsed)

try:
    from PIL import Image
except ImportError:
    Image = None
if Image:
    worst, n = 0, 0
    for wid, t in all_tex:
        name, bpp, blk = texture.LAYOUTS[t.format >> 8]
        if name not in FOURCC:
            continue
        bw, bh = -(-t.width // 4), -(-t.height // 4)
        lin = texture.deswizzle(bw, bh, bpp, t.block_height_log2, t.data)
        ref = Image.open(io.BytesIO(dds(name, t.kind, bw * 4, bh * 4, lin)))
        ref = np.asarray(ref.convert("RGBA") if name in ("bc1", "bc2", "bc3") else ref)
        ours = t.raw_rgba()
        if name == "bc4":
            ours, ref = ours[..., 0], (ref if ref.ndim == 2 else ref[..., 0])
        elif name == "bc5":
            ours, ref = ours[..., :2], ref[..., :2]
        worst = max(worst, int(np.abs(ours.astype(int) - ref[:t.height, :t.width].astype(int)).max()))
        n += 1
    check("BC1-BC5 pixels are identical to Pillow's decoder (%d textures)" % n, worst == 0, worst)
else:
    print("skip: Pillow is not installed")

yl = os.environ.get("KDX_YAZ0LOOM")
if yl and Path(yl).is_dir():
    sys.path.insert(0, yl)
    from yaz0loom.bntx import Bntx
    same = total = 0
    for wid, w in worlds:
        data = bytes(w.bfres_bytes)
        raw = data[data.find(b"BNTX"):]
        ref = Bntx.from_bytes(raw)
        mine = texture.read_bntx(raw)
        for name in ref.names():
            t = mine[name]
            _, bpp, blk = texture.LAYOUTS[t.format >> 8]
            bw, bh = -(-t.width // blk), -(-t.height // blk)
            ours = texture.deswizzle(bw, bh, bpp, t.block_height_log2, t.data)
            same += ours == bytes(ref[name].mips()[0])[:len(ours)]
            total += 1
    check("the Tegra X1 deswizzle is identical to Yaz0loom's (%d textures)" % total, same == total, (same, total))
else:
    print("skip: set KDX_YAZ0LOOM to Yaz0loom.Python/src to compare the deswizzle")

t = next(t for _, t in all_tex if t.layout == "bc4")
rgba = t.rgba()
check("channel selectors apply (a BC4 texture's selectors are honoured in rgba())",
      rgba.shape == (t.height, t.width, 4) and all(
          (rgba[..., i] == (0 if s == 0 else 255 if s == 1 else t.raw_rgba()[..., s - 2])).all() for i, s in enumerate(t.channels)),
      t.channels)

passed = sum(ok for _, ok in results)
print("\n%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)

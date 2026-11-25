"""
kdx/levels.py - level archives (Course/<world>-<n>.sarc) and the tilesets they use (Unit/<name>.szs).

    tilesets(sarc)                                  {area file: [tileset names]} (block 1 of each courseN.bin)
    copy_level(src, dst, game_root, mod_root)       a copy of a level under another number with its own tilesets

A level names up to four tilesets per area in block 1 of course/courseN.bin (32 bytes each): Pa0 is the common set
every level shares (Pa0_jyotyu...), Pa1-Pa3 are the level's own (Pa1_1-1_1 for 1-1 area 1). The game opens
Unit/<name>.szs and reads BG_unt/<name>.bin, BG_unt/<name>_hd.bin, BG_chk/d_bgchk_<name>.bin and the model and
textures named <name> in its output.bfres, so a tileset copied for another level is renamed in all of those.
"""
from __future__ import annotations

import shutil
import struct
import tempfile
from pathlib import Path

from . import archive

COURSE_BIN = "course/course%d.bin"


def _read_sarc(path):
    d = Path(path).read_bytes()
    if d[:4] == b"Yaz0":
        d = archive.yaz0_decompress(d)
    ents = archive.parse_sarc(d)
    return list(ents[0] if isinstance(ents, tuple) else ents), (ents[1] if isinstance(ents, tuple) and len(ents) > 1 else None)


def _names(b):
    off, size = struct.unpack_from("<II", b, 0)
    if size < 128 or off + 128 > len(b):
        return None, []
    return off, [b[off + 32 * i:off + 32 * i + 32].split(b"\0")[0].decode("ascii", "replace") for i in range(4)]


def tilesets(sarc):
    out = {}
    for name, b in _read_sarc(sarc)[0]:
        if name.startswith("course/course") and name.endswith(".bin") and "_bgdat" not in name and len(b) >= 8:
            _off, ns = _names(b)
            out[name] = [n for n in ns if n]
    return out


def find_unit(name, dirs):
    return next((Path(d) / (name + ".szs") for d in dirs if (Path(d) / (name + ".szs")).exists()), None)


def own_name(tileset, src_id, dst_id, k):
    """Pa1_1-1_1 for 1-1 -> Pa1_1-6_1 for 1-6; a set not named after the level gets Pa<n>_<dst>_<k>."""
    if src_id in tileset:
        return tileset.replace(src_id, dst_id)
    return "%s_%s_%d" % (tileset[:3], dst_id, k)


def copy_tileset(src_szs, old, new, out_szs):
    """Unit/<old>.szs as Unit/<new>.szs: its BG_unt/BG_chk files renamed and output.bfres renamed by the model writer."""
    from . import export2d
    entries, info, _ = archive.read_szs(src_szs)
    tmp = Path(tempfile.mkdtemp(prefix="kdx_tileset_"))
    try:
        out = []
        for name, data in entries:
            if name.endswith(".bfres") and old.encode() in data:
                (tmp / "in.bfres").write_bytes(data)
                export2d.run_engine("rename", tmp / "in.bfres", old, new, tmp / "out.bfres")
                data = (tmp / "out.bfres").read_bytes()
            out.append((name.replace(old, new), data))
        blob = archive.build_sarc(out, info)
        Path(out_szs).parent.mkdir(parents=True, exist_ok=True)
        Path(out_szs).write_bytes(archive.yaz0_compress(blob))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def copy_level(src_sarc, dst_sarc, unit_dirs, out_unit, src_id, dst_id, own_tilesets=True):
    """Copy a level archive to dst_sarc (e.g. 1-1 -> 1-6). With own_tilesets, each Pa1-Pa3 tileset it uses is copied
    into out_unit under the new level's name and the level points to the copies (Pa0, the shared common set, stays).
    Returns {old tileset: new tileset}."""
    entries, info = _read_sarc(src_sarc)
    renames = {}
    if own_tilesets:
        k = 0
        for name, b in entries:
            if name.startswith("course/course") and name.endswith(".bin") and "_bgdat" not in name:
                for t in _names(b)[1]:
                    if t and not t.startswith("Pa0") and t not in renames:
                        k += 1
                        renames[t] = own_name(t, src_id, dst_id, k)
        for old, new in renames.items():
            src = find_unit(old, unit_dirs)
            if src is None:
                raise FileNotFoundError("Tileset %s (used by %s) is in no Unit folder." % (old, Path(src_sarc).name))
            copy_tileset(src, old, new, Path(out_unit) / (new + ".szs"))
    out = []
    for name, b in entries:
        if renames and name.startswith("course/course") and name.endswith(".bin") and "_bgdat" not in name:
            off, ns = _names(b)
            if off is not None:
                b = bytearray(b)
                for i, t in enumerate(ns):
                    if t in renames:
                        nb = renames[t].encode("ascii")
                        if len(nb) >= 32:
                            raise ValueError("Tileset name %s is too long (31 characters at most)." % renames[t])
                        b[off + 32 * i:off + 32 * i + 32] = nb + bytes(32 - len(nb))
                b = bytes(b)
        out.append((name, b))
    Path(dst_sarc).parent.mkdir(parents=True, exist_ok=True)
    Path(dst_sarc).write_bytes(archive.build_sarc(out, info))
    return renames

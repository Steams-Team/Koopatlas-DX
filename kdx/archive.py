#!/usr/bin/env python3
"""
wmdx_archive.py - Yaz0 and SARC reading/writing for WorldMapDX (pure Python, standard library only).

    entries, info, was_yaz0 = read_szs(path)         # entries: list of (name, bytes)
    write_szs(path, entries, info, compress="auto")  # rebuilds a SARC with the same byte order/alignment

Writing never touches the source archive: callers choose a separate output path.
"""
from __future__ import annotations

import struct

try:                                    # FastYZ through ctypes when the native library is present
    from . import yaz0fast as _fast
except Exception:                       # pragma: no cover - e.g. used outside the kdx package
    _fast = None

__all__ = ["yaz0_decompress", "yaz0_compress", "parse_sarc", "build_sarc", "read_szs", "write_szs", "ArchiveError"]


class ArchiveError(ValueError):
    pass


# --------------------------------------------------------------------------------------------- Yaz0
def yaz0_decompress(data):
    data = bytes(data)
    if data[:4] != b"Yaz0":
        raise ArchiveError("Not Yaz0 data (missing 'Yaz0' header).")
    if _fast is not None and _fast.available():
        try:
            return _fast.decompress(data)
        except RuntimeError:
            pass                        # fall through to the careful pure-Python decoder for its error messages
    return _yaz0_decompress_py(data)


def _yaz0_decompress_py(data):
    size = struct.unpack_from(">I", data, 4)[0]
    out = bytearray()
    src = 16
    n = len(data)
    while len(out) < size and src < n:
        code = data[src]
        src += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if code & (0x80 >> bit):
                if src >= n:
                    raise ArchiveError("Yaz0 stream ends in the middle of a literal.")
                out.append(data[src])
                src += 1
            else:
                if src + 1 >= n:
                    raise ArchiveError("Yaz0 stream ends in the middle of a copy.")
                b1, b2 = data[src], data[src + 1]
                src += 2
                dist = (((b1 & 0x0F) << 8) | b2) + 1
                length = b1 >> 4
                if length == 0:
                    if src >= n:
                        raise ArchiveError("Yaz0 stream ends in the middle of a long copy.")
                    length = data[src] + 0x12
                    src += 1
                else:
                    length += 2
                start = len(out) - dist
                if start < 0:
                    raise ArchiveError("Yaz0 copy points before the start of the data (corrupt stream).")
                for k in range(length):
                    out.append(out[start + k])
    if len(out) != size:
        raise ArchiveError("Yaz0 data decompressed to %d bytes but the header says %d." % (len(out), size))
    return bytes(out)


def _yaz0_header(size):
    return b"Yaz0" + struct.pack(">I", size) + bytes(8)


def yaz0_compress_stored(data):
    """A valid Yaz0 stream that stores everything as literals (no size saving, instant)."""
    data = bytes(data)
    out = bytearray(_yaz0_header(len(data)))
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        out.append((0xFF << (8 - len(chunk))) & 0xFF)
        out += chunk
    return bytes(out)


def yaz0_compress_lz(data, chain=12):
    """Greedy LZ77 Yaz0 compressor. Slow in pure Python on large data; `chain` trades speed for size."""
    data = bytes(data)
    n = len(data)
    out = bytearray(_yaz0_header(n))
    table = {}
    pos = 0
    group = bytearray()
    code = 0
    bits = 0

    def flush():
        nonlocal group, code, bits
        if bits:
            out.append(code << (8 - bits) & 0xFF if bits < 8 else code)
            out.extend(group)
        group = bytearray()
        code = 0
        bits = 0

    def insert(p):
        if p + 3 <= n:
            key = data[p:p + 3]
            lst = table.get(key)
            if lst is None:
                table[key] = [p]
            else:
                lst.append(p)
                if len(lst) > 64:
                    del lst[:32]

    while pos < n:
        best_len, best_dist = 0, 0
        if pos + 3 <= n:
            cands = table.get(data[pos:pos + 3])
            if cands:
                limit = min(0x111, n - pos)
                for p in reversed(cands[-chain:]):
                    dist = pos - p
                    if dist > 0x1000:
                        break
                    length = 3
                    while length < limit and data[p + length] == data[pos + length]:
                        length += 1
                    if length > best_len:
                        best_len, best_dist = length, dist
                        if length == limit:
                            break
        if best_len >= 3:
            d = best_dist - 1
            if best_len < 0x12:
                group.append(((best_len - 2) << 4) | (d >> 8))
                group.append(d & 0xFF)
            else:
                group.append(d >> 8)
                group.append(d & 0xFF)
                group.append(best_len - 0x12)
            code = (code << 1)
            for k in range(best_len):
                insert(pos + k)
            pos += best_len
        else:
            group.append(data[pos])
            code = (code << 1) | 1
            insert(pos)
            pos += 1
        bits += 1
        if bits == 8:
            out.append(code)
            out.extend(group)
            group = bytearray()
            code = 0
            bits = 0
    flush()
    return bytes(out)


def yaz0_compress(data, mode="auto", compressor=None):
    """mode: 'stored' (instant, bigger), 'lz' (pure Python), 'auto' (use `compressor` if given, else lz).

    compressor: optional callable bytes -> bytes (for example a FastYZ-based one found by wmdx_libs)."""
    if mode == "stored":
        return yaz0_compress_stored(data)
    if mode == "auto" and compressor is None and _fast is not None and _fast.available() and len(data) >= 16:
        compressor = _fast.compress
    if mode == "auto" and compressor is not None:
        try:
            result = compressor(bytes(data))
            if result and result[:4] == b"Yaz0" and yaz0_decompress(result) == bytes(data):
                return result
        except Exception:
            pass                                    # fall back to the built-in compressor
    return yaz0_compress_lz(data)


# --------------------------------------------------------------------------------------------- SARC
def _sarc_hash(name, key):
    h = 0
    for ch in name.encode("utf-8"):
        h = (h * key + (ch if ch < 128 else ch - 256)) & 0xFFFFFFFF
    return h


def parse_sarc(data):
    """-> (entries [(name, bytes)], info {byte_order, hash_key, alignment, data_offset, version})."""
    data = bytes(data)
    if data[:4] != b"SARC" or len(data) < 0x14:
        raise ArchiveError("Not a SARC archive.")
    bom = data[6:8]
    if bom == b"\xff\xfe":
        e = "<"
    elif bom == b"\xfe\xff":
        e = ">"
    else:
        raise ArchiveError("Unknown SARC byte-order mark %r." % bom)
    header_size, _, file_size, data_offset, version = struct.unpack_from(e + "HHIIH", data, 4)
    if file_size != len(data):
        raise ArchiveError("SARC header says %d bytes but the archive is %d." % (file_size, len(data)))
    if data[header_size:header_size + 4] != b"SFAT":
        raise ArchiveError("SARC has no SFAT block.")
    node_count, hash_key = struct.unpack_from(e + "HI", data, header_size + 6)
    sfat_end = header_size + 12 + node_count * 16
    if data[sfat_end:sfat_end + 4] != b"SFNT":
        raise ArchiveError("SARC has no SFNT block.")
    names_start = sfat_end + 8
    entries, starts = [], []
    for i in range(node_count):
        base = header_size + 12 + i * 16
        _h, attrs, start, end = struct.unpack_from(e + "IIII", data, base)
        if attrs & 0x01000000:
            name_off = names_start + (attrs & 0x00FFFFFF) * 4
            end_name = data.index(b"\x00", name_off)
            name = data[name_off:end_name].decode("utf-8", "replace")
        else:
            name = "%08X" % _h
        entries.append((name, data[data_offset + start:data_offset + end]))
        starts.append(start)
    # smallest power-of-two alignment shared by all files (starts that are 0 impose nothing)
    shared = 0x2000
    for s in starts:
        if s:
            shared = min(shared, s & -s)
    info = {"byte_order": e, "hash_key": hash_key, "alignment": max(4, shared), "data_offset": data_offset, "version": version}
    return entries, info


def build_sarc(entries, info=None):
    """Build a SARC from [(name, bytes)]. `info` (from parse_sarc) keeps the original byte order and alignment."""
    info = info or {}
    e = info.get("byte_order", "<")
    key = info.get("hash_key", 0x65)
    align = info.get("alignment", 0x1000)
    version = info.get("version", 0x0100)
    rows = sorted(((_sarc_hash(n, key), n, bytes(d)) for n, d in entries), key=lambda r: r[0])
    count = len(rows)

    names = bytearray()
    name_offsets = []
    for _h, n, _d in rows:
        name_offsets.append(len(names) // 4)
        raw = n.encode("utf-8") + b"\x00"
        names += raw + bytes((-len(raw)) % 4)

    fat_size = 12 + count * 16
    header_end = 0x14 + fat_size + 8 + len(names)
    data_offset = (header_end + align - 1) // align * align

    data_blob = bytearray()
    nodes = bytearray()
    for (h, _n, d), noff in zip(rows, name_offsets):
        start = (len(data_blob) + align - 1) // align * align
        data_blob += bytes(start - len(data_blob))
        data_blob += d
        nodes += struct.pack(e + "IIII", h, 0x01000000 | noff, start, start + len(d))

    total = data_offset + len(data_blob)
    bom = b"\xff\xfe" if e == "<" else b"\xfe\xff"
    out = bytearray(b"SARC" + struct.pack(e + "H", 0x14) + bom + struct.pack(e + "IIHH", total, data_offset, version, 0))
    out += b"SFAT" + struct.pack(e + "HHI", 12, count, key) + nodes
    out += b"SFNT" + struct.pack(e + "HH", 8, 0) + names
    out += bytes(data_offset - len(out))
    out += data_blob
    return bytes(out)


# --------------------------------------------------------------------------------------------- SZS helpers
def read_szs(path):
    with open(path, "rb") as f:
        raw = f.read()
    was_yaz0 = raw[:4] == b"Yaz0"
    data = yaz0_decompress(raw) if was_yaz0 else raw
    entries, info = parse_sarc(data)
    info["yaz0"] = was_yaz0
    if was_yaz0:
        info["yaz0_align"] = struct.unpack_from(">I", raw, 8)[0]
    return entries, info, was_yaz0


GPU_FILES = (".bfres", ".bfsha", ".bntx", ".bnsh", ".bffnt")


def yaz0_alignment(entries, info=None):
    """The alignment the Yaz0 header asks the game to unpack into. The game allocates the unpacked SARC with it, and
    GPU resources inside (bfres, bfsha, bntx) need their 4 KB-aligned offsets to stay aligned in memory: every vanilla
    Model archive says 0x2000. A writer that leaves it 0 gets whatever alignment the heap gives (the ported Newer
    worlds crashed in shader setup, 2026-11-23)."""
    align = (info or {}).get("yaz0_align", 0)
    if not align and any(str(n).lower().endswith(GPU_FILES) for n, _d in entries):
        align = 0x2000
    return align


def set_yaz0_alignment(blob, align):
    """Yaz0 bytes with the header's alignment field (bytes 8-11) set."""
    if blob[:4] != b"Yaz0" or not align:
        return blob
    return blob[:8] + struct.pack(">I", align) + blob[12:]


def write_szs(path, entries, info=None, compress="auto", compressor=None):
    """Write entries as a SARC (Yaz0-compressed unless compress='none'). Returns bytes written."""
    info = dict(info or {})
    blob = build_sarc(entries, info)
    if compress != "none" and info.get("yaz0", True):
        blob = yaz0_compress(blob, "stored" if compress == "stored" else "auto", compressor)
        blob = set_yaz0_alignment(blob, yaz0_alignment(entries, info))
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)

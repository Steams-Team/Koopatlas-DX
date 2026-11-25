"""
kdx/msbt.py - read Nintendo MSBT message files (the game's text, e.g. world and level names).

Read-only: Koopatlas DX shows names from CommonMsg_*.szs; edit text with your MSBT editor (path set in Preferences).
Format: "MsgStdBn" header, then sections (LBL1 labels, ATR1 attributes, TXT2 strings), each padded to 16 bytes.
Text is UTF-16 (or UTF-8); in-text control tags (0x0E ... / 0x0F ...) are shown as [tag:group.type] markers.
"""
from __future__ import annotations

import struct


class MsbtError(ValueError):
    pass


def _decode_text(raw, encoding_utf16, e):
    out = []
    if encoding_utf16:
        units = struct.unpack(e + "%dH" % (len(raw) // 2), raw[:len(raw) // 2 * 2])
        i = 0
        while i < len(units):
            u = units[i]
            if u == 0:
                break
            if u == 0x0E and i + 3 < len(units):              # control tag: group, type, parameter size (bytes)
                group, typ, size = units[i + 1], units[i + 2], units[i + 3]
                out.append("[tag:%d.%d]" % (group, typ))
                i += 4 + (size + 1) // 2
                continue
            if u == 0x0F and i + 2 < len(units):              # closing tag
                out.append("[/tag:%d.%d]" % (units[i + 1], units[i + 2]))
                i += 3
                continue
            if 0xD800 <= u < 0xDC00 and i + 1 < len(units):   # surrogate pair
                out.append(chr(0x10000 + ((u - 0xD800) << 10) + (units[i + 1] - 0xDC00)))
                i += 2
                continue
            out.append(chr(u))
            i += 1
        return "".join(out)
    end = raw.find(b"\x00")
    return raw[:end if end >= 0 else len(raw)].decode("utf-8", "replace")


def parse(data):
    """-> {label: text}, in label order of the file's text index."""
    data = bytes(data)
    if data[:8] != b"MsgStdBn":
        raise MsbtError("Not an MSBT file.")
    e = "<" if data[8:10] == b"\xff\xfe" else ">"
    encoding = data[0x0C]
    utf16 = encoding != 0
    sections = struct.unpack_from(e + "H", data, 0x0E)[0]
    pos = 0x20
    labels, texts = {}, []
    for _ in range(sections):
        if pos + 16 > len(data):
            break
        magic = data[pos:pos + 4]
        size = struct.unpack_from(e + "I", data, pos + 4)[0]
        body = pos + 16
        if magic == b"LBL1":
            slots = struct.unpack_from(e + "I", data, body)[0]
            for s in range(slots):
                count, off = struct.unpack_from(e + "II", data, body + 4 + s * 8)
                p = body + off
                for _ in range(count):
                    n = data[p]
                    name = data[p + 1:p + 1 + n].decode("utf-8", "replace")
                    index = struct.unpack_from(e + "I", data, p + 1 + n)[0]
                    labels[name] = index
                    p += 1 + n + 4
        elif magic == b"TXT2":
            count = struct.unpack_from(e + "I", data, body)[0]
            offs = struct.unpack_from(e + "%dI" % count, data, body + 4)
            for i, off in enumerate(offs):
                end = offs[i + 1] if i + 1 < count else size
                texts.append(_decode_text(data[body + off:body + end], utf16, e))
        pos = body + size
        pos = (pos + 15) & ~15
    return {name: (texts[idx] if idx < len(texts) else "") for name, idx in sorted(labels.items(), key=lambda kv: kv[1])}

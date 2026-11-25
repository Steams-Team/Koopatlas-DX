"""
kdx/offsets.py - the world-map offset tables in romfs/Model.

MarioOffsetDataW<n>.bin (5,376 bytes): 256 records of 21 bytes, decoded from the game's files:

    bytes 0-4    point ID, 4 ASCII characters + a zero byte   (unused slots: "W000" and 17 zero bytes)
    bytes 5-16   three little-endian floats (x, y, z)         meaning not yet confirmed in game
    bytes 17-20  little-endian u32                            flags or a mode (0-3 common; 5, 8, 32, 64, 65, 72 seen)

Slots 0-127 belong to the world's first (or only) archive, slots 128-255 to its "b" archive (W5b, W8b, W9b), and within
a block the records are in the same order as that archive's pointW<n>.csv rows.

LookOffsetDataW<n>.bin (744 bytes): almost entirely zeros in the game; the layout is not known, so it is kept as 186
floats and edited as raw values.
"""
from __future__ import annotations

import struct

SLOTS = 256
BLOCK = 128
RECORD_SIZE = 21
MARIO_SIZE = SLOTS * RECORD_SIZE          # 5376
LOOK_SIZE = 744
PLACEHOLDER = "W000"


class OffsetError(ValueError):
    pass


class MarioRecord:
    __slots__ = ("name", "vector", "flags")

    def __init__(self, name=PLACEHOLDER, vector=(0.0, 0.0, 0.0), flags=0):
        self.name = name
        self.vector = tuple(float(v) for v in vector)
        self.flags = int(flags)

    @property
    def unused(self):
        return self.name == PLACEHOLDER

    def copy(self):
        return MarioRecord(self.name, self.vector, self.flags)

    def __eq__(self, other):
        return isinstance(other, MarioRecord) and (self.name, self.vector, self.flags) == (other.name, other.vector, other.flags)

    def __repr__(self):
        return "MarioRecord(%r, %r, %d)" % (self.name, self.vector, self.flags)


def _valid_name(name):
    return isinstance(name, str) and len(name) == 4 and all(32 < ord(c) < 127 for c in name)


class MarioOffsetTable:
    def __init__(self, records=None):
        self.records = records if records is not None else [MarioRecord() for _ in range(SLOTS)]
        if len(self.records) != SLOTS:
            raise OffsetError("A MarioOffset table has exactly %d slots." % SLOTS)

    # ---- file I/O ------------------------------------------------------------------------------------------------
    @classmethod
    def parse(cls, data):
        data = bytes(data)
        if len(data) != MARIO_SIZE:
            raise OffsetError("A MarioOffsetData file is %d bytes; this one is %d." % (MARIO_SIZE, len(data)))
        recs = []
        for i in range(SLOTS):
            r = data[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
            name = r[:4].decode("ascii", "replace")
            x, y, z = struct.unpack_from("<3f", r, 5)
            flags = struct.unpack_from("<I", r, 17)[0]
            recs.append(MarioRecord(name, (x, y, z), flags))
        return cls(recs)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls.parse(f.read())

    def to_bytes(self):
        out = bytearray()
        for rec in self.records:
            if not _valid_name(rec.name):
                raise OffsetError("Record name %r is not 4 printable ASCII characters." % (rec.name,))
            out += rec.name.encode("ascii") + b"\x00" + struct.pack("<3fI", *rec.vector, rec.flags & 0xFFFFFFFF)
        assert len(out) == MARIO_SIZE
        return bytes(out)

    # ---- blocks --------------------------------------------------------------------------------------------------
    @staticmethod
    def block_range(block):
        if block not in (0, 1):
            raise OffsetError("Block must be 0 (first archive) or 1 (the 'b' archive).")
        return range(block * BLOCK, (block + 1) * BLOCK)

    def block_records(self, block):
        """The used records of a block, in order (they are packed from the block's start)."""
        out = []
        for i in self.block_range(block):
            rec = self.records[i]
            if rec.unused:
                break
            out.append(rec)
        return out

    def find(self, block, name):
        for i in self.block_range(block):
            if self.records[i].name == name:
                return i
        return None

    def check(self, block, point_ids):
        """Problems between a block and its archive's point list (order matters). [] means they agree."""
        recs = [r.name for r in self.block_records(block)]
        problems = []
        if len(point_ids) > BLOCK:
            problems.append("%d points, but a block holds at most %d." % (len(point_ids), BLOCK))
        missing = [p for p in point_ids if p not in recs]
        extra = [r for r in recs if r not in point_ids]
        if missing:
            problems.append("Points without a record: " + ", ".join(missing[:8]) + (" …" if len(missing) > 8 else ""))
        if extra:
            problems.append("Records with no point: " + ", ".join(extra[:8]) + (" …" if len(extra) > 8 else ""))
        if not missing and not extra and recs != list(point_ids[:BLOCK]):
            problems.append("Records are not in the same order as the point table.")
        stray = [i for i in self.block_range(block) if i >= self.block_range(block).start + len(recs) and not self.records[i].unused]
        if stray:
            problems.append("Used records after the first unused slot (slots %s)." % ", ".join(map(str, stray[:6])))
        return problems

    def sync(self, block, point_ids):
        """Rebuild a block so it matches point_ids in order: existing records keep their values, new points get a zero
        vector and flags 0, removed points are dropped, the rest of the block is filled with unused slots."""
        point_ids = list(point_ids)
        if len(point_ids) > BLOCK:
            raise OffsetError("%d points: an archive can have at most %d (one block of the MarioOffset table)." % (len(point_ids), BLOCK))
        for p in point_ids:
            if not _valid_name(p):
                raise OffsetError("Point ID %r must be exactly 4 printable ASCII characters." % (p,))
        current = {}
        for r in self.block_records(block):
            current.setdefault(r.name, r)
        new = [current[p].copy() if p in current else MarioRecord(p) for p in point_ids]
        rng = self.block_range(block)
        for k, i in enumerate(rng):
            self.records[i] = new[k] if k < len(new) else MarioRecord()

    def set_vector(self, block, name, vector):
        i = self.find(block, name)
        if i is None:
            raise OffsetError("No record named %s in block %d." % (name, block))
        self.records[i] = MarioRecord(name, vector, self.records[i].flags)

    def set_flags(self, block, name, flags):
        i = self.find(block, name)
        if i is None:
            raise OffsetError("No record named %s in block %d." % (name, block))
        if not 0 <= int(flags) <= 0xFFFFFFFF:
            raise OffsetError("Flags must fit in 32 bits.")
        self.records[i] = MarioRecord(name, self.records[i].vector, flags)


class LookOffsetTable:
    COUNT = LOOK_SIZE // 4                 # 186 floats

    def __init__(self, values=None):
        self.values = list(values) if values is not None else [0.0] * self.COUNT

    @classmethod
    def parse(cls, data):
        data = bytes(data)
        if len(data) != LOOK_SIZE:
            raise OffsetError("A LookOffsetData file is %d bytes; this one is %d." % (LOOK_SIZE, len(data)))
        return cls(struct.unpack("<%df" % cls.COUNT, data))

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls.parse(f.read())

    def to_bytes(self):
        return struct.pack("<%df" % self.COUNT, *self.values)

    def nonzero(self):
        return [(i, v) for i, v in enumerate(self.values) if v != 0.0]


def world_number(world_id):
    """'W5a' -> 5, 'W1' -> 1."""
    return int(world_id[1])


def block_of(world_id):
    """'W5b' -> 1, everything else -> 0."""
    return 1 if world_id.lower().endswith("b") else 0

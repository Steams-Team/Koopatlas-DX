"""AAMP v2 parameter archives (agl .baglenv / .bagllmap / .bagldof): parser keyed by CRC32.

Names are stored only as CRC32 hashes. names.txt (next to this file) lists the names the
game's environment files use, so the debug views can show readable keys.
"""
import os
import struct
import zlib
from dataclasses import dataclass, field

TYPES = ['bool', 'f32', 'int', 'vec2', 'vec3', 'vec4', 'color', 'string32', 'string64', 'curve1',
         'curve2', 'curve3', 'curve4', 'buffer_int', 'buffer_f32', 'string256', 'quat', 'u32',
         'buffer_u32', 'buffer_binary', 'string_ref']

_NAMES = {}


def crc(name: str) -> int:
    return zlib.crc32(name.encode('utf-8')) & 0xFFFFFFFF


def _load_names():
    if _NAMES:
        return
    path = os.path.join(os.path.dirname(__file__), 'aamp_names.txt')
    if os.path.isfile(path):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line:
                _NAMES[crc(line)] = line
    # numbered names agl uses for list/object children
    for i in range(1024):
        _NAMES.setdefault(crc(str(i)), str(i))
    for base in ('param', 'obj', 'list', 'light', 'Light', 'fog', 'Fog', 'lut', 'LUT', 'entry', 'Entry',
                 'lightmap', 'LightMap', 'hemi', 'dir', 'amb'):
        for i in range(64):
            for fmt in ('%s%d', '%s_%d', '%s%02d', '%s_%02d'):
                s = fmt % (base, i)
                _NAMES.setdefault(crc(s), s)


def name_of(h: int) -> str:
    _load_names()
    return _NAMES.get(h, '0x%08X' % h)


@dataclass
class PList:
    crc: int
    lists: list = field(default_factory=list)   # PList
    objs: list = field(default_factory=list)    # PObj

    @property
    def name(self):
        return name_of(self.crc)

    def list(self, name):
        h = crc(name) if isinstance(name, str) else name
        for l in self.lists:
            if l.crc == h:
                return l
        return None

    def obj(self, name):
        h = crc(name) if isinstance(name, str) else name
        for o in self.objs:
            if o.crc == h:
                return o
        return None


@dataclass
class PObj:
    crc: int
    params: dict = field(default_factory=dict)  # crc -> (type, value)

    @property
    def name(self):
        return name_of(self.crc)

    def get(self, name, default=None):
        h = crc(name) if isinstance(name, str) else name
        v = self.params.get(h)
        return v[1] if v is not None else default

    def items(self):
        return [(name_of(h), TYPES[t] if t < len(TYPES) else str(t), v) for h, (t, v) in self.params.items()]


def parse(d: bytes) -> PList:
    if d[:4] != b'AAMP':
        raise ValueError('not an AAMP file')
    le = struct.unpack_from('<I', d, 8)[0] & 1
    e = '<' if le else '>'
    pio_off = struct.unpack_from(e + 'I', d, 0x14)[0]

    def value(pos, t):
        if t == 0:
            return bool(struct.unpack_from(e + 'I', d, pos)[0])
        if t == 1:
            return struct.unpack_from(e + 'f', d, pos)[0]
        if t == 2:
            return struct.unpack_from(e + 'i', d, pos)[0]
        if t in (3, 4, 5, 6, 16):
            n = {3: 2, 4: 3, 5: 4, 6: 4, 16: 4}[t]
            return struct.unpack_from(e + '%df' % n, d, pos)
        if t in (7, 8, 15, 20):
            end = d.index(b'\0', pos)
            return d[pos:end].decode('utf-8', 'replace')
        if t == 17:
            return struct.unpack_from(e + 'I', d, pos)[0]
        if 9 <= t <= 12:
            n = t - 8
            out = []
            for k in range(n):
                p = pos + k * 0x80
                a, b = struct.unpack_from(e + '2I', d, p)
                out.append((a, b, struct.unpack_from(e + '30f', d, p + 8)))
            return out
        if t in (13, 14, 18, 19):
            size = struct.unpack_from(e + 'I', d, pos - 4)[0]
            if t == 19:
                return d[pos:pos + size]
            ch = {13: 'i', 14: 'f', 18: 'I'}[t]
            return struct.unpack_from(e + '%d%s' % (size, ch), d, pos)
        return None

    def read_list(pos):
        h, lo, nl, oo, no = struct.unpack_from(e + 'IHHHH', d, pos)
        pl = PList(h)
        for i in range(nl):
            pl.lists.append(read_list(pos + lo * 4 + i * 12))
        for i in range(no):
            op = pos + oo * 4 + i * 8
            oh, po, npar = struct.unpack_from(e + 'IHH', d, op)
            obj = PObj(oh)
            for j in range(npar):
                pp = op + po * 4 + j * 8
                ph, word = struct.unpack_from(e + 'II', d, pp)
                t = word >> 24
                try:
                    obj.params[ph] = (t, value(pp + (word & 0xFFFFFF) * 4, t))
                except Exception:
                    obj.params[ph] = (t, None)
            pl.objs.append(obj)
        return pl

    return read_list(0x30 + pio_off)


def to_tree(pl: PList, depth=0):
    """Nested (name, value/children) for the debug property view."""
    out = []
    for o in pl.objs:
        out.append((o.name, [(n, '%s = %s' % (t, _fmt(v))) for n, t, v in o.items()]))
    for l in pl.lists:
        out.append((l.name + '/', to_tree(l, depth + 1)))
    return out


def _fmt(v):
    if isinstance(v, float):
        return '%.6g' % v
    if isinstance(v, tuple):
        return '(' + ', '.join(_fmt(x) for x in v) + ')'
    if isinstance(v, bytes):
        return '<%d bytes>' % len(v)
    return repr(v)


# --------------------------------------------------------------------------------------------
# writer

_STRING_TYPES = (7, 8, 15, 20)


def write(root: PList) -> bytes:
    """AAMP v2 little endian: lists, objects, parameters, data section, string section."""
    lists, objs, params = [], [], []

    def walk(pl):
        lists.append(pl)
    order = [root]
    i = 0
    while i < len(order):          # breadth-first, like Nintendo's writer
        pl = order[i]
        order.extend(pl.lists)
        i += 1
    lists = order
    for pl in lists:
        objs.extend(pl.objs)
    for o in objs:
        params.extend(o.params.items())
    type_str = b'xml\0'
    head = 0x30 + len(type_str)
    list_pos = {id(pl): head + k * 12 for k, pl in enumerate(lists)}
    obj_base = head + len(lists) * 12
    obj_pos = {id(o): obj_base + k * 8 for k, o in enumerate(objs)}
    par_base = obj_base + len(objs) * 8
    data = bytearray()
    strs = bytearray()
    blobs = []
    for h, (t, v) in params:
        if t == 0:
            b = struct.pack('<I', 1 if v else 0)
        elif t == 1:
            b = struct.pack('<f', v)
        elif t in (2,):
            b = struct.pack('<i', v)
        elif t == 17:
            b = struct.pack('<I', v)
        elif t in (3, 4, 5, 6, 16):
            b = struct.pack('<%df' % len(v), *v)
        elif t in _STRING_TYPES:
            b = v.encode('utf-8') + b'\0'
            while len(b) % 4:
                b += b'\0'
        elif t in (13, 14, 18):
            ch = {13: 'i', 14: 'f', 18: 'I'}[t]
            b = struct.pack('<I%d%s' % (len(v), ch), len(v), *v)
        elif t == 19:
            b = struct.pack('<I', len(v)) + bytes(v)
            while len(b) % 4:
                b += b'\0'
        else:
            raise ValueError('aamp type %d not supported by the writer' % t)
        blobs.append((t, b))
    data_base = par_base + len(params) * 8
    for t, b in blobs:
        if t not in _STRING_TYPES:
            data += b
    str_base = data_base + len(data)
    out = bytearray(str_base)
    out[0:4] = b'AAMP'
    out[0x30:0x30 + len(type_str)] = type_str
    # params
    doff = 0
    soff = 0
    for k, ((h, (t, v)), (_, b)) in enumerate(zip(params, blobs)):
        pp = par_base + k * 8
        if t in _STRING_TYPES:
            target = str_base + soff
            soff += len(b)
            strs += b
        else:
            target = data_base + doff + (4 if t in (13, 14, 18, 19) else 0)
            doff += len(b)
        struct.pack_into('<II', out, pp, h, ((target - pp) // 4) | (t << 24))
    out[data_base:data_base + len(data)] = data
    out += strs
    # lists and objects
    pidx = 0
    for o in objs:
        op = obj_pos[id(o)]
        first = par_base + pidx * 8
        struct.pack_into('<IHH', out, op, o.crc, (first - op) // 4 if o.params else 0, len(o.params))
        pidx += len(o.params)
    oidx = 0
    for pl in lists:
        lp = list_pos[id(pl)]
        child = list_pos[id(pl.lists[0])] if pl.lists else lp
        firsto = obj_base + oidx * 8
        struct.pack_into('<IHHHH', out, lp, pl.crc, (child - lp) // 4 if pl.lists else 0, len(pl.lists),
                         (firsto - lp) // 4 if pl.objs else 0, len(pl.objs))
        oidx += len(pl.objs)
    struct.pack_into('<11I', out, 4, 2, 3, len(out), 0, len(type_str), len(lists), len(objs),
                     len(params), len(data), len(strs), 0)
    return bytes(out)

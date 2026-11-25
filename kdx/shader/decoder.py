"""Maxwell instruction decoder: NVN stage code (SPH + instructions) -> list of Inst.

Instructions are 64-bit words in groups of four: a scheduling control word, then three
instructions. Lookup follows Ryujinx: the top 14 bits pick the most specific encoding."""
import struct
from dataclasses import dataclass

from .maxwell_tables import ENUMS, FIELDS, OPCODES

SPH_SIZE = 0x50
_LOOKUP = None


def _table():
    global _LOOKUP
    if _LOOKUP is None:
        t = [None] * (1 << 14)
        xbits = [99] * (1 << 14)
        for mask, value, name, variant, props in OPCODES:
            m14, v14 = mask >> 50, value >> 50
            free = [b for b in range(14) if not (m14 >> b) & 1]
            nx = len(free)
            for i in range(1 << nx):
                k = v14
                for j, b in enumerate(free):
                    if (i >> j) & 1:
                        k |= 1 << b
                if t[k] is None or xbits[k] > nx:
                    t[k] = (name, variant, props)
                    xbits[k] = nx
        _LOOKUP = t
    return _LOOKUP


class Fields:
    """Lazy field access by Ryujinx's names (inst.f.Dest, inst.f.SrcA ...)."""
    __slots__ = ('_w', '_spec')

    def __init__(self, word, spec):
        self._w, self._spec = word, spec

    def __getattr__(self, name):
        try:
            kind, parts = self._spec[name]
        except KeyError:
            raise AttributeError(name)
        if kind == 'bool':
            return bool(self._w & parts[0][1])
        v = 0
        for p in parts:
            v |= (self._w >> p[0]) & p[1]
        return v

    def has(self, name):
        return name in self._spec

    def get(self, name, default=None):
        return getattr(self, name) if name in self._spec else default


@dataclass
class Inst:
    addr: int            # byte address relative to the code start (after the SPH)
    word: int
    name: str            # e.g. 'Ffma'
    variant: str         # e.g. 'FfmaR' (fields struct InstFfmaR)
    props: tuple
    f: Fields

    @property
    def pred(self):
        return (self.word >> 16) & 7

    @property
    def pred_inv(self):
        return bool(self.word & 0x80000)

    @property
    def always(self):
        return self.pred == 7 and not self.pred_inv

    @property
    def never(self):
        return self.pred == 7 and self.pred_inv


def enum_name(enum, value):
    return ENUMS.get(enum, {}).get(value, str(value))


def decode(code: bytes, start=SPH_SIZE):
    """code = SPH + instructions (as bfsha.StageCode.code). -> [Inst] in address order, only the
    instructions reachable from the entry (constant data and padding after the code are skipped)."""
    t = _table()
    n = (len(code) - start) // 8
    words = struct.unpack_from('<%dQ' % n, code, start)
    cache = {}

    def at(addr):
        k = addr // 8
        if k in cache:
            return cache[k]
        if k % 4 == 0 or not 0 <= k < n:
            return None
        w = words[k]
        e = t[w >> 50]
        if e is None:
            name, variant, props, spec = 'Invalid', 'Invalid', (), {}
        else:
            name, variant, props = e
            spec = FIELDS.get('Inst' + variant, FIELDS.get('Inst' + name, {}))
        cache[k] = Inst(k * 8, w, name, variant, props, Fields(w, spec))
        return cache[k]

    def nxt(addr):
        a = addr + 8
        return a + 8 if (a // 8) % 4 == 0 else a

    seen = set()
    todo = [8]
    while todo:
        a = todo.pop()
        while a not in seen:
            i = at(a)
            if i is None:
                break
            seen.add(a)
            if i.name in ('Bra', 'Ssy', 'Pbk', 'Pcnt', 'Cal') and i.f.has('Imm24'):
                tgt = branch_target(i)
                if tgt != a:
                    todo.append(tgt)
            if i.name in ('Exit', 'Sync', 'Brk', 'Cont', 'Ret', 'Bra') and i.always and _ccc_true(i):
                break
            a = nxt(a)
    return [cache[a // 8] for a in sorted(seen)]


def branch_target(i):
    """Absolute target of BRA / SSY / PBK ...; a target on a scheduling word means the next instruction."""
    t = i.addr + 8 + ((i.f.Imm24 ^ 0x800000) - 0x800000)
    return t + 8 if (t // 8) % 4 == 0 else t


def _ccc_true(i):
    return not i.f.has('Ccc') or i.f.Ccc == 15


def sph(code: bytes):
    """Shader program header fields the translator needs."""
    w = struct.unpack_from('<20I', code, 0)
    d = {
        'type': (w[0] >> 10) & 0xF,            # 1 vertex ... 5 pixel
        'version': (w[0] >> 17) & 0xF,
        'kills': bool(w[0] & (1 << 15)),
        'local_mem': (w[1] & 0xFFFFFF) | ((w[2] & 0xFF) << 24),
        'words': w,
    }
    if d['type'] == 5:
        # pixel: input interpolation per generic attribute (4 bits / 2 per component...), output targets
        d['imap_generic'] = code[0x18:0x18 + 32]   # 2 bits per component (0 unused, 1 const, 2 persp, 3 no-persp)
        d['omap_targets'] = w[18]                 # 4 bits per render target (rgba enable)
        d['omap_sample_mask'] = bool(w[19] & 1)
        d['omap_depth'] = bool(w[19] & 2)
    else:
        d['imap'] = code[0x14:0x14 + 0x20]
        d['omap'] = code[0x34:0x34 + 0x20]
    return d

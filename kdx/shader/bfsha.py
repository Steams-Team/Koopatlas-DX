"""BFSHA reader (v7: NSMBU Deluxe Wii_UBER, v8 / v9: Super Mario Bros. Wonder SecredUber, TotK):
shading models, their options / key table, programs and the NVN code of each program.

Layout (verified on every Deluxe distant view and on Wonder's SecredUber; matches KillzXGaming's
BfshaLibrary):

  header 0x20, archive: name (+0x38), path, model array (+0x48), model dict (+0x50), u16 count (+0x78)
  shading model (v7 0xE8 / v8+ 0x100): name, static / dynamic options, attributes, samplers,
      [v8+ images], uniform blocks, uniform array, storage buffers, unknown, programs, key table,
      archive, symbol table, BNSH file, ... counts at the end
  option (0x28): name, choices dict, choice values; v7/v8: u8 count, u8 default, u8 pad, u16 block
      offset, u8 key offset, u8 bit index, u8 bit shift, u32 mask; v9: u16 count, s16 default, u16 pad,
      u8 block offset, u8 key offset, u32 mask, u8 bit index, u8 bit shift
  program (v7 0x38 / v8+ 0x40): sampler / [image] / block / storage index tables, BNSH variation
      offset (absolute), parent model, u32 used attributes, u16 flags, counts
  key table: (static key words + dynamic key words) s32 per program; an option's choice goes into
      word `key offset + bit index` at `bit shift` under `mask`

  BNSH (embedded, one per shading model): variation (stride 0x40, from GRSC +0x20) -> binary
      program (+0x10): stage code pointers (+0x08 vertex ... +0x28 pixel), reflection (+0x78)
  stage code (0x40): control ptr, code ptr, code size, control size
  code: 0x30 byte NVN header, 0x50 byte Maxwell SPH, then instructions
  stage reflection: dicts of inputs, outputs, samplers, constant buffers, UAVs; s32 offsets of
      each group into a slot array; the slot array (binding slots in dict order)
"""
import struct
from dataclasses import dataclass, field

STAGES = ['vertex', 'hull', 'domain', 'geometry', 'pixel', 'compute']
BLOCK_TYPES = {0: 'none', 1: 'material', 2: 'shape', 3: 'skeleton', 4: 'option'}


class _R:
    def __init__(self, b, base=0):
        self.b, self.base = b, base

    def q(self, o):
        return struct.unpack_from('<Q', self.b, self.base + o)[0]

    def u(self, fmt, o):
        return struct.unpack_from('<' + fmt, self.b, self.base + o)

    def s(self, o):
        if not o:
            return ''
        n = struct.unpack_from('<H', self.b, self.base + o)[0]
        return self.b[self.base + o + 2:self.base + o + 2 + n].decode('utf-8', 'replace')

    def dic(self, o):
        if not o:
            return []
        c = struct.unpack_from('<i', self.b, self.base + o + 4)[0]
        if not 0 <= c < 1 << 16:
            return []
        return [self.s(self.q(o + 8 + 16 * (i + 1) + 8)) for i in range(c)]


@dataclass
class UniformMember:
    name: str
    index: int
    offset: int


@dataclass
class UniformBlock:
    name: str
    index: int
    type: int
    size: int
    members: list
    default: bytes

    @property
    def type_name(self):
        return BLOCK_TYPES.get(self.type, str(self.type))


@dataclass
class Option:
    name: str
    choices: list            # choice names ('0'/'1', or enum strings)
    choice_values: list      # u32 per choice (what the shader sees)
    default: int
    block_offset: int
    key_offset: int          # word of the program key this option starts in
    bit_index: int
    bit_shift: int
    mask: int
    dynamic: bool = False

    def encode(self, key, choice_index):
        """Put choice_index into a program key (list of s32 words, static then dynamic)."""
        w = self.key_offset + self.bit_index
        if 0 <= w < len(key):
            key[w] = (key[w] & ~self.mask | ((choice_index << self.bit_shift) & self.mask)) & 0xFFFFFFFF


@dataclass
class StageReflection:
    inputs: dict = field(default_factory=dict)     # symbol -> location
    outputs: dict = field(default_factory=dict)
    samplers: dict = field(default_factory=dict)   # symbol -> slot
    buffers: dict = field(default_factory=dict)    # symbol -> slot
    uavs: dict = field(default_factory=dict)


@dataclass
class StageCode:
    stage: str
    code: bytes          # SPH + instructions (what a Maxwell decompiler expects at address 0)
    control: bytes
    nvn_header: bytes
    reflection: StageReflection

    @property
    def sph_word0(self):
        return struct.unpack_from('<I', self.code, 0)[0]


@dataclass
class Program:
    index: int
    variation: int           # BNSH variation index
    used_attributes: int
    stages: dict             # stage -> StageCode


class ShadingModel:
    def __init__(self, data, version, name, static_options, dynamic_options, attributes, samplers, blocks,
                 bnsh, prog_info, key_table, skey, dkey, default_program, symbols):
        self.data, self.version, self.name = data, version, name
        self.options = static_options + dynamic_options
        self.static_options = [o.name for o in static_options]
        self.dynamic_options = [o.name for o in dynamic_options]
        self.attributes = attributes     # model attrib name (_p0...) -> location
        self.samplers = samplers         # names (_a0...)
        self.blocks = blocks
        self.bnsh_offset = bnsh
        self._prog_info = prog_info      # [(variation index, used attribute flags)]
        self.key_table = key_table       # flat list of s32
        self.static_key_len, self.dynamic_key_len = skey, dkey
        self.default_program = default_program
        self.symbols = symbols           # {'samplers': [...], 'blocks': [...]} symbol names (v9: one per entry)
        self._programs = {}

    # ---- programs ------------------------------------------------------------------------------
    @property
    def program_count(self):
        return len(self._prog_info)

    def program(self, i):
        if i not in self._programs:
            var, used = self._prog_info[i]
            stages = _bnsh_program(self.data, self.bnsh_offset, var) if self.bnsh_offset else {}
            self._programs[i] = Program(i, var, used, stages)
        return self._programs[i]

    @property
    def programs(self):
        """Every program (lazy for big uber shaders: use program(i) / find_program when possible)."""
        return _LazyList(self)

    def key(self, i):
        n = self.static_key_len + self.dynamic_key_len
        return self.key_table[i * n:(i + 1) * n]

    def option(self, name):
        for o in self.options:
            if o.name == name:
                return o
        return None

    def find_program(self, option_values: dict):
        """Program index for a material's shader options (name -> choice string); options it
        doesn't set use their defaults. Static keys must match; dynamic keys prefer defaults."""
        if self.program_count <= 1:
            return 0
        n = self.static_key_len + self.dynamic_key_len
        key = [0] * n
        for o in self.options:
            v = option_values.get(o.name)
            idx = o.choices.index(v) if v in o.choices else (o.default if o.default >= 0 else 0)
            o.encode(key, idx)
        # nearest static key: the engine overrides some gsys_* options from the render state
        # (alpha test, deferred pass ...), so an exact match is not guaranteed
        sk = self.static_key_len
        want = [k & 0xFFFFFFFF for k in key]
        best, best_score = 0, None
        for i in range(self.program_count):
            pk = self.key(i)
            st = sum(bin((a & 0xFFFFFFFF) ^ b).count('1') for a, b in zip(pk[:sk], want[:sk]))
            dy = sum(bin((a & 0xFFFFFFFF) ^ b).count('1') for a, b in zip(pk[sk:], want[sk:]))
            score = (st, dy)
            if best_score is None or score < best_score:
                best, best_score = i, score
                if score == (0, 0):
                    break
        return best

    def block(self, type_name):
        for b in self.blocks:
            if b.type_name == type_name:
                return b
        return None


class _LazyList:
    def __init__(self, sm):
        self.sm = sm

    def __len__(self):
        return max(1, self.sm.program_count)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[k] for k in range(*i.indices(len(self)))]
        if i < 0:
            i += len(self)
        if self.sm.program_count == 0:
            return Program(0, 0, 0, _bnsh_program(self.sm.data, self.sm.bnsh_offset, 0) if self.sm.bnsh_offset else {})
        return self.sm.program(i)

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]


def version(data: bytes):
    return struct.unpack_from('<I', data, 8)[0] >> 16 & 0xFF


def parse(data: bytes):
    """-> {shading model name: ShadingModel}"""
    if data[:4] != b'FSHA':
        raise ValueError('not a BFSHA')
    v = version(data)
    r = _R(data)
    model_arr = r.q(0x48)
    count = r.u('H', 0x78)[0]
    names = r.dic(r.q(0x50))
    count = max(count, len(names))
    size = 0x100 if v >= 8 else 0xE8
    out = {}
    for i in range(count):
        m = _model(r, data, model_arr + i * size, v)
        out[m.name] = m
    return out


def model_names(data: bytes):
    """Shading model names without parsing the models."""
    r = _R(data)
    return r.dic(r.q(0x50))


def _options(r, vals, n, v, dynamic):
    out = []
    for k in range(n):
        o = vals + k * 0x28
        name = r.s(r.q(o))
        choices = r.dic(r.q(o + 8))
        cv_p = r.q(o + 0x10)
        if v >= 9:
            cnt, default, _pad, boff, koff, mask, bidx, bshift = r.u('HhHBBIBB', o + 0x18)
        else:
            cnt, default, _pad, boff, koff, bidx, bshift, mask = r.u('BBBHBBBI', o + 0x18)
            default = default if default != 0xFF else -1
        cvals = list(r.u('%dI' % cnt, cv_p)) if cv_p and cnt else []
        out.append(Option(name, choices, cvals, default, boff, koff, bidx, bshift, mask, dynamic))
    return out


def _model(r, data, o, v):
    name = r.s(r.q(o))
    # pointer slots after the dicts: v8+ add the image dict (0x10) before the uniform blocks
    img = 0x10 if v >= 8 else 0
    n_static, n_dyn, n_prog = r.u('HHH', o + 0xD4 + (0x10 if v >= 8 else 0))
    skey, dkey, n_attr, n_smp = r.u('BBBB', o + 0xDA + (0x10 if v >= 8 else 0))
    default_program = r.u('i', o + 0xD0 + (0x10 if v >= 8 else 0))[0]
    sopt = _options(r, r.q(o + 0x08), n_static, v, False)
    dopt = _options(r, r.q(o + 0x18), n_dyn, v, True)
    at_names = r.dic(r.q(o + 0x30))
    at_vals = r.q(o + 0x28)
    attributes = {}
    for k, n in enumerate(at_names):
        idx, loc = r.u('Bb', at_vals + k * 2)
        attributes[n] = loc
    samplers = r.dic(r.q(o + 0x40))
    blk_arr, blk_dict = r.q(o + 0x48 + img), r.q(o + 0x50 + img)
    blocks = []
    for k, bn in enumerate(r.dic(blk_dict)):
        b = blk_arr + k * 0x20
        mv, md, dflt = r.q(b), r.q(b + 8), r.q(b + 0x10)
        bidx, btype, size, nmem = r.u('BBHH', b + 0x18)
        members = []
        for j, mn in enumerate(r.dic(md)):
            mi, off1 = r.u('IH', mv + j * 0x10 + 8)
            members.append(UniformMember(mn, mi, off1 - 1))
        default = data[dflt:dflt + size] if dflt else bytes(size)
        blocks.append(UniformBlock(bn, bidx, btype, size, members, default))
    # v7: programs +0x78 key table +0x80 archive +0x88 symbols +0x90 bnsh +0x98; v8+: all +0x10
    prog_arr, key_p = r.q(o + 0x78 + img), r.q(o + 0x80 + img)
    sym_p, bnsh = r.q(o + 0x90 + img), r.q(o + 0x98 + img)
    psize = 0x40 if v >= 8 else 0x38
    voff = 0x20 if v >= 8 else 0x18
    var_base = bnsh + 0xC0
    prog_info = []
    for p in range(n_prog):
        po = prog_arr + p * psize
        var_off = r.q(po + voff)
        used = r.u('I', po + voff + 0x10)[0]
        var = (var_off - var_base) // 0x40 if var_off >= var_base else p
        prog_info.append((var, used))
    nkey = (skey + dkey) * n_prog
    key_table = list(r.u('%di' % nkey, key_p)) if key_p and nkey else []
    symbols = _symbols(r, sym_p, len(samplers), len(blocks), v)
    return ShadingModel(data, v, name, sopt, dopt, attributes, samplers, blocks, bnsh, prog_info, key_table,
                        skey, dkey, default_program, symbols)


def _symbols(r, p, n_smp, n_blk, v):
    if not p:
        return {}
    esize = 0x10 if v >= 9 else (0x30 if v == 8 else 0x20)
    ptrs = [r.q(p + 8 * k) for k in range(3)]
    smp_p, blk_p = (ptrs[0], ptrs[2]) if v >= 8 else (ptrs[0], ptrs[1])

    def read(arr, n):
        out = []
        for k in range(n if arr else 0):
            e = arr + k * esize
            out.append(tuple(r.s(r.q(e + j)) for j in range(0, esize, 8)))
        return out
    try:
        return {'samplers': read(smp_p, n_smp), 'blocks': read(blk_p, n_blk)}
    except struct.error:
        return {}


# Wii_UBER symbol names (NVN binds by name; the bfsha's location tables point at these)
BLOCK_SYMBOLS = {'gsys_shader_option': 'ShaderOption', 'material': 'Material', 'env': 'MdlEnvView',
                 'skeleton': 'MdlMtx', 'shape': 'Shp'}


def sampler_symbol(sampler_name):
    """_aN -> tex_mapN"""
    return 'tex_map' + sampler_name[2:] if sampler_name.startswith('_a') else sampler_name


def _bnsh_program(data, base, variation):
    if data[base:base + 4] != b'BNSH':
        return {}
    r = _R(data, base)
    grsc = 0x60
    var_arr = r.q(grsc + 0x20)
    v = var_arr + variation * 0x40
    bp = r.q(v + 0x10)
    if not bp:
        return {}
    refl_arr = r.q(bp + 0x78)
    stages = {}
    for si, st in enumerate(STAGES):
        sc = r.q(bp + 8 + si * 8)
        if not sc:
            continue
        ctrl, code = r.q(sc + 8), r.q(sc + 0x10)
        csz, ctsz = r.u('II', sc + 0x18)
        blob = data[base + code:base + code + csz]
        refl = _reflection(r, r.q(refl_arr + si * 8)) if refl_arr else StageReflection()
        stages[st] = StageCode(st, blob[0x30:], data[base + ctrl:base + ctrl + ctsz], blob[:0x30], refl)
    return stages


def _reflection(r, o):
    if not o:
        return StageReflection()
    groups = [r.dic(r.q(o + k * 8)) for k in range(5)]
    offs = list(r.u('4i', o + 0x28))
    slot_arr = r.q(o + 0x38)
    n = r.u('i', o + 0x4C)[0]
    slots = list(r.u('%di' % n, slot_arr)) if slot_arr and n > 0 else []
    starts = [0] + offs
    out = StageReflection()
    for g, (names, dst) in enumerate(zip(groups, (out.inputs, out.outputs, out.samplers, out.buffers, out.uavs))):
        for k, nm in enumerate(names):
            idx = starts[g] + k
            dst[nm] = slots[idx] if idx < len(slots) else -1
    return out

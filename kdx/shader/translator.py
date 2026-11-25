"""Maxwell (NVN / Nintendo Switch) shader programs -> GLSL 4.50.

Yaz0loom's own translator. The decoder tables come from Ryujinx (Ryubing) and the instruction
semantics follow its emitters (Ryujinx.Graphics.Shader, MIT); the code generation is new:

  * straight-line programs (every NSMBU Deluxe program) become SSA temporaries, one per result,
    much like Ryujinx's own output;
  * programs with branches (SSY / SYNC / PBK / BRK / BRA, as in Super Mario Bros. Wonder) run as a
    block dispatcher (`switch` in a loop) with the registers in variables and per-thread SSY / PBK
    stacks, which keeps any control flow correct without structurisation.

The interface matches what Ryujinx.ShaderTools writes for OpenGL, so the renderer binds both the
same way: constant buffers `_{vp,fp}_cN { vec4 data[4096]; }` at binding N + 1, textures
`{vp,fp}_t_tcb_{handle:X}`, `in_attrN` / `out_attrN` per location, and `_support_buffer`.
"""
import math
import re
import struct

from . import decoder
from .decoder import enum_name

RZ = 255
PT = 7

USER_BASE, USER_END = 0x80, 0x80 + 32 * 16
COMP = 'xyzw'


class TranslateError(Exception):
    pass


_SIMPLE = re.compile(r'^(temp_\d+(\.[xyzw])?|in_attr\d+\.[xyzw]|gl_\w+(\.[xyzw])?|[vf]p_c\d+\.data\[\d+\]\.[xyzw]|'
                     r'-?\d+(\.\d+)?(e-?\d+)?u?|0x[0-9A-F]+u|true|false)$')


# ---------------------------------------------------------------------------------------------
# values

class Val:
    """An expression with its GLSL type: 'f' float, 'u' uint, 'i' int, 'b' bool.
    const: the 32-bit pattern when known at translation time."""
    __slots__ = ('e', 't', 'const', 'cbuf')

    def __init__(self, e, t, const=None, cbuf=None):
        self.e, self.t, self.const, self.cbuf = e, t, const, cbuf


def f2u(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def u2f(x):
    return struct.unpack('<f', struct.pack('<I', x & 0xFFFFFFFF))[0]


def flit(bits):
    """GLSL literal for a float bit pattern."""
    x = u2f(bits)
    if math.isnan(x) or math.isinf(x) or (x != 0.0 and abs(x) < 1.17549435e-38):
        return 'uintBitsToFloat(0x%08Xu)' % bits
    s = repr(x)
    if 'e' in s or 'E' in s:
        mant, exp = s.lower().split('e')
        if '.' not in mant:
            mant += '.0'
        s = '%se%d' % (mant, int(exp))
    elif '.' not in s:
        s += '.0'
    # repr is the shortest string that round-trips through a double; make sure it also
    # round-trips through a float
    if f2u(float(s)) != bits:
        return 'uintBitsToFloat(0x%08Xu)' % bits
    return s


def cf(x):
    return Val(flit(f2u(x)), 'f', f2u(x))


def cu(x):
    x &= 0xFFFFFFFF
    return Val('%du' % x if x < 0x10000 else '0x%Xu' % x, 'u', x)


def ci(x):
    return Val(str(x), 'i', x & 0xFFFFFFFF)


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def imm20_float(v):
    return (v << 12) & 0xFFFFFFFF


def imm20_int(v):
    return sext(v, 20) & 0xFFFFFFFF


# ---------------------------------------------------------------------------------------------

class Translator:
    def __init__(self, code: bytes, stage: str, name=''):
        self.code, self.stage, self.name = code, stage, name
        self.p = 'vp' if stage == 'vertex' else 'fp'
        self.sph = decoder.sph(code)
        self.insts = decoder.decode(code)
        self.by_addr = {i.addr: k for k, i in enumerate(self.insts)}
        self.lines = []
        self.indent = 1
        self.ntemp = 0
        self.cbufs = set()
        self.textures = {}         # name -> sampler type
        self.inputs = {}           # location -> qualifier
        self.outputs = set()
        self.uses_frag_depth = False
        self.local_size = 0
        self.uses_vertex_id = self.uses_instance_id = False
        self.warnings = []
        self.multi = False

    # ---- helpers -------------------------------------------------------------------------
    def emit(self, s):
        self.lines.append('    ' * self.indent + s)

    def temp(self, t, expr, const=None):
        if const is not None:
            return Val(expr, t, const)
        self.ntemp += 1
        n = 'temp_%d' % self.ntemp
        ty = {'f': 'precise float', 'u': 'uint', 'i': 'int', 'b': 'bool'}[t]
        self.emit('%s %s = %s;' % (ty, n, expr))
        return Val(n, t)

    @staticmethod
    def F(v):
        if v.t == 'f':
            return v.e
        if v.const is not None:
            return flit(v.const)
        return {'u': 'uintBitsToFloat(%s)', 'i': 'intBitsToFloat(%s)', 'b': '(%s ? 1.0 : 0.0)'}[v.t] % v.e

    @staticmethod
    def U(v):
        if v.t == 'u':
            return v.e
        if v.const is not None:
            return '%du' % v.const if v.const < 0x10000 else '0x%Xu' % v.const
        return {'f': 'floatBitsToUint(%s)', 'i': 'uint(%s)', 'b': '(%s ? 0xFFFFFFFFu : 0u)'}[v.t] % v.e

    @staticmethod
    def I(v):
        if v.t == 'i':
            return v.e
        if v.const is not None:
            return str(sext(v.const, 32))
        return {'f': 'floatBitsToInt(%s)', 'u': 'int(%s)', 'b': '(%s ? -1 : 0)'}[v.t] % v.e

    # registers / predicates -----------------------------------------------------------------
    def rd(self, r):
        if r == RZ:
            return cu(0)
        v = self.regs.get(r)
        if v is None:
            if self.multi:
                # snapshot the variable: flushing writes variables in register order
                self.used_regs.add(r)
                v = self.temp('u', 'r%d' % r)
            else:
                v = cu(0)            # never written: undefined on hardware, zero here
            self.regs[r] = v
        return v

    def wr(self, r, v):
        if r == RZ:
            return
        if self.cond is not None:
            old = self.rd(r)
            if v.t == old.t or v.t == 'f':
                t = v.t
                a, b = (self.F(v), self.F(old)) if t == 'f' else (self.U(v), self.U(old)) if t == 'u' else \
                    (self.I(v), self.I(old))
            else:
                t = 'u'
                a, b = self.U(v), self.U(old)
            v = self.temp(t, '%s ? %s : %s' % (self.cond, a, b))
        elif v.const is None and not _SIMPLE.match(v.e):
            v = self.temp(v.t, v.e)
        self.regs[r] = v
        self.dirty.add(r)

    def pr(self, p, inv=False):
        if p == PT:
            v = 'true'
        else:
            v = self.preds.get(p)
            if v is None:
                if self.multi:
                    self.used_preds.add(p)
                    v = self.temp('b', 'p%d' % p).e
                else:
                    v = 'false'
                self.preds[p] = v
        return ('!' + self._paren(v)) if inv else v

    @staticmethod
    def _paren(e):
        return e if e.replace('_', '').isalnum() else '(%s)' % e

    def wp(self, p, expr):
        if p == PT:
            return
        if self.cond is not None:
            expr = '%s ? %s : %s' % (self.cond, self._paren(expr), self.pr(p))
        v = self.temp('b', expr)
        self.preds[p] = v.e
        self.dirty_p.add(p)

    # operand B variants ----------------------------------------------------------------------
    def cbuf(self, slot, word):
        self.cbufs.add(slot)
        v = Val('%s_c%d.data[%d].%s' % (self.p, slot, word >> 2, COMP[word & 3]), 'f')
        v.cbuf = (slot, word)
        return v

    def src_b(self, i, float_imm=True):
        f = i.f
        v = i.variant
        if v.endswith('Rc'):
            return self.rd(f.SrcC)
        if v.endswith('32i'):
            return cu(f.Imm32)
        if v.endswith('R'):
            return self.rd(f.SrcB)
        if v.endswith('I'):
            if f.has('Imm20'):
                return cu(imm20_float(f.Imm20) if float_imm else imm20_int(f.Imm20))
            if f.has('Imm16'):
                return cu(f.Imm16)
            return cu(f.get('Imm', 0))
        if v.endswith('C'):
            return self.cbuf(f.CbufSlot, f.CbufOffset)
        raise TranslateError('operand B of %s' % v)

    def src_c(self, i):
        if i.variant.endswith('Rc'):
            return self.cbuf(i.f.CbufSlot, i.f.CbufOffset)
        if i.variant.endswith('32i'):
            return self.rd(i.f.Dest)
        return self.rd(i.f.SrcC)

    def fneg_abs(self, e, absv=False, neg=False):
        if absv:
            e = 'abs(%s)' % e
        if neg:
            e = '-%s' % self._paren(e) if not e.startswith('-') else e[1:]
        return e

    def sat(self, e, on):
        return 'clamp(%s, 0.0, 1.0)' % e if on else e

    def set_cc_f(self, i, res):
        if i.f.get('WriteCC'):
            self.cc = ('f', res)

    def set_cc_i(self, i, res):
        if i.f.get('WriteCC'):
            self.cc = ('i', res)

    def ccc(self, i):
        """Condition-code test of BRA / EXIT / KIL / SYNC (T: always)."""
        c = enum_name('Ccc', i.f.get('Ccc', 15))
        if c == 'T':
            return 'true'
        if c == 'F':
            return 'false'
        if self.cc is None:
            self.warnings.append('condition code %s read before being written' % c)
            return 'true'
        kind, v = self.cc
        x = self.F(v) if kind == 'f' else self.I(v)
        z = '0.0' if kind == 'f' else '0'
        table = {'Eq': '%s == %s', 'Ne': '%s != %s', 'Lt': '%s < %s', 'Le': '%s <= %s', 'Gt': '%s > %s',
                 'Ge': '%s >= %s', 'Equ': '%s == %s', 'Neu': '%s != %s', 'Ltu': '%s < %s', 'Leu': '%s <= %s',
                 'Gtu': '%s > %s', 'Geu': '%s >= %s'}
        if c in table:
            return table[c] % (x, z)
        self.warnings.append('condition code %s treated as true' % c)
        return 'true'

    # ---- main ------------------------------------------------------------------------------
    def translate(self):
        targets, leaders = self._flow()
        self.multi = len(leaders) > 1
        self.regs, self.preds = {}, {}
        self.dirty, self.dirty_p = set(), set()
        self.used_regs, self.used_preds = set(), set()
        self.cc = None
        self.cond = None
        if not self.multi:
            for i in self.insts:
                if self._one(i) == 'stop':
                    break
        else:
            self._multi(leaders)
        return self._assemble()

    def _flow(self):
        """Branch targets and block leaders (addresses)."""
        targets = set()
        leaders = {self.insts[0].addr} if self.insts else set()
        n = len(self.insts)
        for k, i in enumerate(self.insts):
            if i.name in ('Bra', 'Ssy', 'Pbk', 'Pcnt', 'Cal', 'Pret'):
                t = decoder.branch_target(i) if i.f.has('Imm24') else i.addr
                if i.name == 'Bra' and t == i.addr and k >= n - 2:
                    continue                      # the "BRA self" that pads the end
                if t in self.by_addr:
                    targets.add(t)
                    leaders.add(t)
                if i.name == 'Bra' and k + 1 < n:
                    leaders.add(self.insts[k + 1].addr)
            elif i.name in ('Sync', 'Brk', 'Cont', 'Ret') and k + 1 < n:
                leaders.add(self.insts[k + 1].addr)
            elif i.name == 'Exit' and not i.always and k + 1 < n:
                pass
        return targets, sorted(leaders)

    def _multi(self, leaders):
        self.block_ids = {a: k for k, a in enumerate(leaders)}
        self.emit('int pc = 0;')
        self.emit('int ssy_sp = 0;')
        self.emit('int pbk_sp = 0;')
        self.emit('int ssy_stack[16];')
        self.emit('int pbk_stack[16];')
        self.emit('while (true)')
        self.emit('{')
        self.indent += 1
        self.emit('switch (pc)')
        self.emit('{')
        bounds = leaders + [10 ** 9]
        for b, start in enumerate(leaders):
            self.emit('case %d:' % b)
            self.emit('{')
            self.indent += 1
            self.regs, self.preds = {}, {}
            self.dirty, self.dirty_p = set(), set()
            self.cc = None
            end = bounds[b + 1]
            terminated = False
            for i in self.insts[self.by_addr[start]:]:
                if i.addr >= end:
                    break
                r = self._one(i)
                if r == 'stop':
                    terminated = True
                    break
            if not terminated:
                self.flush()
                nxt = self.block_ids.get(end)
                if nxt is not None:
                    self.emit('pc = %d;' % nxt)
                    self.emit('break;')
                else:
                    self.emit('return;')
            self.indent -= 1
            self.emit('}')
        self.emit('default:')
        self.emit('    return;')
        self.emit('}')
        self.indent -= 1
        self.emit('}')

    def flush(self):
        """Write the block's register / predicate values back to their variables."""
        if not self.multi:
            return
        for r in sorted(self.dirty):
            v = self.regs[r]
            if v.e != 'r%d' % r:
                self.emit('r%d = %s;' % (r, self.U(v)))
            self.used_regs.add(r)
        for p in sorted(self.dirty_p):
            v = self.preds[p]
            if v != 'p%d' % p:
                self.emit('p%d = %s;' % (p, v))
            self.used_preds.add(p)
        self.regs, self.preds = {}, {}
        self.dirty, self.dirty_p = set(), set()

    def _one(self, i):
        if i.never or i.name in ('Nop', 'Depbar', 'Bar', 'Membar', 'Cctl', 'Cctll'):
            return None
        if i.name == 'Invalid':
            self.warnings.append('invalid instruction 0x%016X at 0x%X' % (i.word, i.addr))
            return None
        self.emit('// 0x%06X: 0x%016X %s' % (i.addr, i.word, i.variant))
        fn = getattr(self, 'op_' + i.name, None)
        if fn is None:
            self.warnings.append('unsupported %s at 0x%X' % (i.variant, i.addr))
            self.emit('// (unsupported: %s)' % i.variant)
            return None
        flow = i.name in ('Bra', 'Sync', 'Brk', 'Cont', 'Exit', 'Kil', 'Ssy', 'Pbk', 'Pcnt', 'Ret', 'Cal')
        if not i.always and not flow:
            self.cond = self.pr(i.pred, i.pred_inv)
        else:
            self.cond = None
        try:
            return fn(i)
        finally:
            self.cond = None

    def guard(self, i):
        """Predicate of a side-effect instruction ('' when always)."""
        return '' if i.always else 'if (%s) ' % self.pr(i.pred, i.pred_inv)

    # ---- float arithmetic --------------------------------------------------------------------
    def op_Fadd(self, i):
        f = i.f
        a = self.fneg_abs(self.F(self.rd(f.SrcA)), f.get('AbsA'), f.get('NegA'))
        b = self.fneg_abs(self.F(self.src_b(i)), f.get('AbsB'), f.get('NegB'))
        if b.startswith('-'):
            e = '%s - %s' % (a, b[1:])
        else:
            e = '%s + %s' % (a, b)
        v = Val(self.sat(e, f.get('Sat')), 'f')
        self.wr(f.Dest, v)
        self.set_cc_f(i, self.regs.get(f.Dest, v))

    def op_Fmul(self, i):
        f = i.f
        a = self.F(self.rd(f.SrcA))
        b = self.fneg_abs(self.F(self.src_b(i)), False, f.get('NegA'))
        sc = enum_name('MultiplyScale', f.get('Scale', 0))
        mul = {'D2': '0.5', 'D4': '0.25', 'D8': '0.125', 'M2': '2.0', 'M4': '4.0', 'M8': '8.0'}.get(sc)
        e = '%s * %s' % (a, b)
        if mul:
            e = '(%s * %s) * %s' % (a, mul, b)
        v = Val(self.sat(e, f.get('Sat')), 'f')
        self.wr(f.Dest, v)
        self.set_cc_f(i, self.regs.get(f.Dest, v))

    def op_Ffma(self, i):
        f = i.f
        a = self.F(self.rd(f.SrcA))
        b = self.fneg_abs(self.F(self.src_b(i)), False, f.get('NegA'))
        c = self.fneg_abs(self.F(self.src_c(i)), False, f.get('NegC'))
        v = Val(self.sat('fma(%s, %s, %s)' % (a, b, c), f.get('Sat')), 'f')
        self.wr(f.Dest, v)
        self.set_cc_f(i, self.regs.get(f.Dest, v))

    def op_Fmnmx(self, i):
        f = i.f
        a = self.fneg_abs(self.F(self.rd(f.SrcA)), f.get('AbsA'), f.get('NegA'))
        b = self.fneg_abs(self.F(self.src_b(i)), f.get('AbsB'), f.get('NegB'))
        p = self.pr(f.SrcPred, f.SrcPredInv)
        if p == 'true':
            e = 'min(%s, %s)' % (a, b)
        elif p == 'false':
            e = 'max(%s, %s)' % (a, b)
        else:
            e = '%s ? min(%s, %s) : max(%s, %s)' % (p, a, b, a, b)
        self.wr(f.Dest, Val(e, 'f'))

    def op_Mufu(self, i):
        f = i.f
        a = self.fneg_abs(self.F(self.rd(f.SrcA)), f.AbsA, f.NegA)
        op = enum_name('MufuOp', f.MufuOp)
        e = {'Cos': 'cos(%s)', 'Sin': 'sin(%s)', 'Ex2': 'exp2(%s)', 'Lg2': 'log2(%s)', 'Rcp': '1.0 / %s',
             'Rsq': 'inversesqrt(%s)', 'Sqrt': 'sqrt(%s)'}.get(op)
        if e is None:
            self.warnings.append('MUFU %s' % op)
            e = '%s'
        self.wr(f.Dest, Val(self.sat(e % self._paren(a) if op == 'Rcp' else e % a, f.Sat), 'f'))

    def op_Rro(self, i):
        f = i.f
        self.wr(f.Dest, Val(self.fneg_abs(self.F(self.src_b(i)), f.get('AbsB'), f.get('NegB')), 'f'))

    def _fcmp(self, comp, a, b):
        c = enum_name('FComp', comp)
        if c == 'T':
            return 'true'
        if c == 'F':
            return 'false'
        if c in ('Nan', 'Num'):
            e = 'isnan(%s) || isnan(%s)' % (a, b)
            return e if c == 'Nan' else '!(%s)' % e
        base = {'Lt': '<', 'Eq': '==', 'Le': '<=', 'Gt': '>', 'Ne': '!=', 'Ge': '>='}
        u = c.endswith('u') and c != 'Num'
        op = base[c[:-1] if u else c]
        e = '%s %s %s' % (a, op, b)
        if u:
            e = '(%s) || isnan(%s) || isnan(%s)' % (e, a, b)
        return e

    @staticmethod
    def _bop(op, a, b):
        o = enum_name('BoolOp', op)
        if b == 'true' and o == 'And':
            return a
        if b == 'false' and o == 'Or':
            return a
        return '(%s) %s (%s)' % (a, {'And': '&&', 'Or': '||', 'Xor': '^^'}[o], b)

    def op_Fsetp(self, i):
        f = i.f
        a = self.fneg_abs(self.F(self.rd(f.SrcA)), f.get('AbsA'), f.get('NegA'))
        b = self.fneg_abs(self.F(self.src_b(i)), f.get('AbsB'), f.get('NegB'))
        cmp = self.temp('b', self._fcmp(f.FComp, a, b))
        sp = self.pr(f.SrcPred, f.SrcPredInv)
        self.wp(f.DestPred, self._bop(f.Bop, cmp.e, sp))
        self.wp(f.DestPredInv, self._bop(f.Bop, '!' + cmp.e, sp))

    def op_Fset(self, i):
        f = i.f
        a = self.fneg_abs(self.F(self.rd(f.SrcA)), f.get('AbsA'), f.get('NegA'))
        b = self.fneg_abs(self.F(self.src_b(i)), f.get('AbsB'), f.get('NegB'))
        res = self._bop(f.Bop, self._fcmp(f.FComp, a, b), self.pr(f.SrcPred, f.SrcPredInv))
        if f.get('BVal'):
            self.wr(f.Dest, Val('(%s) ? 1.0 : 0.0' % res, 'f'))
        else:
            self.wr(f.Dest, Val('(%s) ? 0xFFFFFFFFu : 0u' % res, 'u'))

    def op_Fcmp(self, i):
        f = i.f
        a, b = self.F(self.rd(f.SrcA)), self.F(self.src_b(i))
        c = self.F(self.src_c(i))
        self.wr(f.Dest, Val('(%s) ? %s : %s' % (self._fcmp(f.FComp, c, '0.0'), a, b), 'f'))

    def op_Fswzadd(self, i):
        self.wr(i.f.Dest, Val('0.0', 'f', 0))

    # ---- half precision ------------------------------------------------------------------------
    def _half_src(self, v, swz):
        s = enum_name('HalfSwizzle', swz)
        u = self.U(v)
        if s == 'F32':
            return ['%s' % self.F(v)] * 2
        if s == 'H0H0':
            return ['unpackHalf2x16(%s).x' % u] * 2
        if s == 'H1H1':
            return ['unpackHalf2x16(%s).y' % u] * 2
        return ['unpackHalf2x16(%s).x' % u, 'unpackHalf2x16(%s).y' % u]

    def _half_imm(self, h0, h1):
        def h(x):
            x = (x << 6) & 0xFFFF
            return struct.unpack('<e', struct.pack('<H', x))[0]
        return [flit(f2u(h(h0))), flit(f2u(h(h1)))]

    def _half_dest(self, i, res):
        f = i.f
        fmt = enum_name('OFmt', f.get('OFmt', 0))
        if fmt == 'F32':
            self.wr(f.Dest, Val(res[0], 'f'))
            return
        old = self.U(self.rd(f.Dest))
        if fmt == 'MrgH0':
            e = 'packHalf2x16(vec2(%s, unpackHalf2x16(%s).y))' % (res[0], old)
        elif fmt == 'MrgH1':
            e = 'packHalf2x16(vec2(unpackHalf2x16(%s).x, %s))' % (old, res[1])
        else:
            e = 'packHalf2x16(vec2(%s, %s))' % (res[0], res[1])
        self.wr(f.Dest, Val(e, 'u'))

    def _half_ab(self, i):
        f = i.f
        a = [self.fneg_abs(x, f.get('AbsA'), f.get('NegA')) for x in self._half_src(self.rd(f.SrcA), f.get('ASwizzle', 0))]
        v = i.variant
        if v.endswith('I'):
            b = self._half_imm(f.BimmH0, f.BimmH1)
        elif v.endswith('32i'):
            b = self._half_imm(f.Imm32 & 0xFFFF, f.Imm32 >> 16)
        elif v.endswith('C'):
            b = self._half_src(self.cbuf(f.CbufSlot, f.CbufOffset), 1)
        else:
            b = self._half_src(self.rd(f.SrcB), f.get('BSwizzle', 0))
        b = [self.fneg_abs(x, f.get('AbsB'), f.get('NegB')) for x in b]
        return a, b

    def op_Hadd2(self, i):
        a, b = self._half_ab(i)
        self._half_dest(i, [self.sat('%s + %s' % (a[k], b[k]), i.f.get('Sat')) for k in range(2)])

    def op_Hmul2(self, i):
        a, b = self._half_ab(i)
        self._half_dest(i, [self.sat('%s * %s' % (a[k], b[k]), i.f.get('Sat')) for k in range(2)])

    def op_Hfma2(self, i):
        f = i.f
        a = self._half_src(self.rd(f.SrcA), f.get('ASwizzle', 0))
        if i.variant.endswith('I'):
            b = self._half_imm(f.BimmH0, f.BimmH1)
        elif i.variant.endswith('C'):
            b = self._half_src(self.cbuf(f.CbufSlot, f.CbufOffset), 1)
        else:
            b = self._half_src(self.rd(f.SrcB if not i.variant.endswith('Rc') else f.SrcC), f.get('BSwizzle', 0))
        c = self._half_src(self.rd(f.SrcC) if not i.variant.endswith('Rc') else self.cbuf(f.CbufSlot, f.CbufOffset),
                           f.get('CSwizzle', 0))
        b = [self.fneg_abs(x, False, f.get('NegA')) for x in b]
        c = [self.fneg_abs(x, False, f.get('NegC')) for x in c]
        self._half_dest(i, [self.sat('fma(%s, %s, %s)' % (a[k], b[k], c[k]), f.get('Sat')) for k in range(2)])

    def op_Hsetp2(self, i):
        f = i.f
        a, b = self._half_ab(i)
        comp = f.get('FComp2', f.get('FComp', 0))
        c0 = self._fcmp(comp, a[0], b[0])
        c1 = self._fcmp(comp, a[1], b[1])
        sp = self.pr(f.SrcPred, f.SrcPredInv)
        if f.get('HAnd'):
            both = '(%s) && (%s)' % (c0, c1)
            self.wp(f.DestPred, self._bop(f.Bop, both, sp))
            self.wp(f.DestPredInv, self._bop(f.Bop, '!(%s)' % both, sp))
        else:
            self.wp(f.DestPred, self._bop(f.Bop, c0, sp))
            self.wp(f.DestPredInv, self._bop(f.Bop, c1, sp))

    # ---- moves / conversions ----------------------------------------------------------------
    def op_Mov(self, i):
        f = i.f
        if i.variant == 'MovR':
            v = self.rd(f.SrcA)
        elif i.variant == 'MovI':
            v = cu(f.Imm20)
        else:
            v = self.src_b(i)
        self.wr(f.Dest, v)

    def op_Mov32i(self, i):
        self.wr(i.f.Dest, cu(i.f.Imm32))

    def op_Sel(self, i):
        f = i.f
        a, b = self.rd(f.SrcA), self.src_b(i, float_imm=False)
        p = self.pr(f.SrcPred, f.SrcPredInv)
        self.wr(f.Dest, Val('%s ? %s : %s' % (p, self.U(a), self.U(b)), 'u'))

    def op_I2f(self, i):
        f = i.f
        src = self.src_b(i, float_imm=False)
        st = enum_name('ISrcFmt', f.ISrcFmt)
        signed = st.startswith('S')
        x = self.I(src) if signed else self.U(src)
        if f.get('AbsB'):
            x = 'abs(%s)' % x
        if f.get('NegB'):
            x = '-%s' % self._paren(x)
        size = {'U8': 8, 'S8': 8, 'U16': 16, 'S16': 16}.get(st)
        if size:
            off = f.get('ByteSel', 0) * 8
            x = 'bitfieldExtract(%s, %d, %d)' % (x, off, size)
        e = 'float(%s)' % x
        if enum_name('DstFmt', f.DstFmt) == 'F16':
            self.wr(f.Dest, Val('packHalf2x16(vec2(%s, 0.0))' % e, 'u'))
        else:
            self.wr(f.Dest, Val(e, 'f'))

    def op_F2i(self, i):
        f = i.f
        src = self.src_b(i)
        x = self.fneg_abs(self.F(src), f.get('AbsB'), f.get('NegB'))
        if enum_name('DstFmt', f.SrcFmt) == 'F16':
            x = 'unpackHalf2x16(%s).%s' % (self.U(src), 'y' if f.get('Sh') else 'x')
        rm = enum_name('RoundMode2', f.RoundMode)
        x = {'Round': 'roundEven(%s)', 'Floor': 'floor(%s)', 'Ceil': 'ceil(%s)', 'Trunc': 'trunc(%s)'}[rm] % x
        dt = enum_name('IDstFmt', f.IDstFmt)
        if dt.startswith('S'):
            e = 'int(%s)' % x
            if dt == 'S16':
                e = 'clamp(%s, -32768, 32767)' % e
            self.wr(f.Dest, Val(e, 'i'))
        else:
            e = 'uint(max(%s, 0.0))' % x
            if dt == 'U16':
                e = 'min(%s, 65535u)' % e
            self.wr(f.Dest, Val(e, 'u'))

    def op_F2f(self, i):
        f = i.f
        src = self.src_b(i)
        st, dt = enum_name('DstFmt', f.SrcFmt), enum_name('DstFmt', f.DstFmt)
        if st == 'F16':
            x = 'unpackHalf2x16(%s).%s' % (self.U(src), 'y' if f.get('Sh') else 'x')
        else:
            x = self.F(src)
        x = self.fneg_abs(x, f.get('AbsB'), f.get('NegB'))
        if st == dt:
            rm = enum_name('IntegerRound', f.RoundMode)
            x = {'Round': 'roundEven(%s)', 'Floor': 'floor(%s)', 'Ceil': 'ceil(%s)', 'Trunc': 'trunc(%s)'}.get(
                rm, '%s') % x
        x = self.sat(x, f.get('Sat'))
        if dt == 'F16':
            self.wr(f.Dest, Val('packHalf2x16(vec2(%s, 0.0))' % x, 'u'))
        else:
            self.wr(f.Dest, Val(x, 'f'))

    def op_I2i(self, i):
        f = i.f
        src = self.src_b(i, float_imm=False)
        st = enum_name('ISrcDstFmt', f.get('ISrcFmt', 2))
        dt = enum_name('ISrcDstFmt', f.get('IDstFmt', 2))
        x = self.I(src) if st.startswith('S') else self.U(src)
        size = {'U8': 8, 'S8': 8, 'U16': 16, 'S16': 16}.get(st)
        if size:
            x = 'bitfieldExtract(%s, %d, %d)' % (x, f.get('ByteSel', 0) * 8, size)
        if f.get('AbsB'):
            x = 'abs(%s)' % x
        if f.get('NegB'):
            x = '-%s' % self._paren(x)
        dsize = {'U8': 8, 'S8': 8, 'U16': 16, 'S16': 16}.get(dt)
        if dsize and f.get('Sat'):
            lo, hi = (-(1 << (dsize - 1)), (1 << (dsize - 1)) - 1) if dt.startswith('S') else (0, (1 << dsize) - 1)
            x = 'clamp(int(%s), %d, %d)' % (x, lo, hi)
        self.wr(f.Dest, Val('uint(%s)' % x, 'u'))

    # ---- integer arithmetic / logic ----------------------------------------------------------
    def op_Iadd(self, i):
        f = i.f
        a, b = self.U(self.rd(f.SrcA)), self.U(self.src_b(i, float_imm=False))
        avg = enum_name('AvgMode', f.get('AvgMode', 0))
        if avg == 'NegA':
            e = '%s - %s' % (b, a)
        elif avg == 'NegB':
            e = '%s - %s' % (a, b)
        else:
            e = '%s + %s' % (a, b)
        v = Val(e, 'u')
        self.wr(f.Dest, v)
        self.set_cc_i(i, self.regs.get(f.Dest, v))

    op_Iadd32i = op_Iadd
    op_Fadd32i = op_Fadd
    op_Fmul32i = op_Fmul
    op_Ffma32i = op_Ffma
    op_Hadd232i = op_Hadd2
    op_Hmul232i = op_Hmul2
    op_Hfma232i = op_Hfma2

    def op_Iscadd(self, i):
        f = i.f
        a, b = self.U(self.rd(f.SrcA)), self.U(self.src_b(i, float_imm=False))
        sh = f.get('Imm5', f.get('Imm', 0))
        a = '(%s << %du)' % (a, sh) if sh else a
        avg = enum_name('AvgMode', f.get('AvgMode', 0))
        e = '%s - %s' % (b, a) if avg == 'NegA' else '%s - %s' % (a, b) if avg == 'NegB' else '%s + %s' % (a, b)
        self.wr(f.Dest, Val(e, 'u'))

    op_Iscadd32i = op_Iscadd

    def op_Iadd3(self, i):
        f = i.f
        vals = [self.U(self.rd(f.SrcA)), self.U(self.src_b(i, float_imm=False)), self.U(self.rd(f.SrcC))]
        negs = [f.get('NegA'), f.get('NegB'), f.get('NegC')]
        e = ' + '.join(('(0u - %s)' % v) if n else v for v, n in zip(vals, negs))
        self.wr(f.Dest, Val(e, 'u'))

    def op_Imad(self, i):
        f = i.f
        a, b, c = self.rd(f.SrcA), self.src_b(i, float_imm=False), self.src_c(i)
        if f.get('Hilo'):
            e = 'uint((uint64_t(%s) * uint64_t(%s)) >> 32)' % (self.U(a), self.U(b))
        else:
            e = '%s * %s' % (self.U(a), self.U(b))
        self.wr(f.Dest, Val('%s + %s' % (e, self.U(c)), 'u'))

    def op_Imul(self, i):
        f = i.f
        a, b = self.rd(f.SrcA), self.src_b(i, float_imm=False)
        self.wr(f.Dest, Val('%s * %s' % (self.U(a), self.U(b)), 'u'))

    op_Imul32i = op_Imul

    def op_Xmad(self, i):
        f = i.f
        A = self.U(self.rd(f.SrcA))
        if i.variant == 'XmadI':
            Bv = cu(f.Imm16)
        elif i.variant == 'XmadRc':
            Bv = self.rd(f.SrcC)
        else:
            Bv = self.src_b(i, float_imm=False)
        B = self.U(Bv)
        C = self.U(self.cbuf(f.CbufSlot, f.CbufOffset)) if i.variant == 'XmadRc' else self.U(self.rd(f.SrcC))

        def ext(x, high, signed):
            if signed:
                return 'uint(%s)' % ('(int(%s) >> 16)' % x if high else 'bitfieldExtract(int(%s), 0, 16)' % x)
            return '(%s >> 16u)' % x if high else '(%s & 0xFFFFu)' % x
        hb = f.get('HiloB') if i.variant != 'XmadI' else False
        a = ext(A, f.HiloA, f.ASigned)
        b = ext(B, hb, f.BSigned)
        prod = '(%s * %s)' % (a, b)
        if f.get('Psl'):
            prod = '(%s << 16u)' % prod
        mode = enum_name('XmadCop', f.XmadCop) if i.variant != 'XmadRc' else enum_name('XmadCop2', f.XmadCop)
        if mode == 'Clo':
            C = ext(C, False, False)
        elif mode == 'Chi':
            C = ext(C, True, False)
        elif mode == 'Cbcc':
            C = '(%s + (%s << 16u))' % (C, B)
        elif mode == 'Csfu':
            C = '(%s - (((%s >> 31u) << 16u) + ((%s >> 31u) << 16u)))' % (C, a, b)
        e = '%s + %s' % (prod, C)
        if f.get('Mrg'):
            e = '((%s) & 0xFFFFu) | (%s << 16u)' % (e, B)
        self.wr(f.Dest, Val(e, 'u'))

    def op_Shl(self, i):
        f = i.f
        a = self.U(self.rd(f.SrcA))
        b = self.src_b(i, float_imm=False)
        if b.const is not None:
            sh = b.const & 0xFF
            e = '0u' if sh >= 32 and not f.get('M') else '%s << %du' % (a, sh & 31)
        else:
            e = '%s << (%s & 31u)' % (a, self.U(b))
        self.wr(f.Dest, Val(e, 'u'))

    def op_Shr(self, i):
        f = i.f
        signed = f.get('Signed')
        a = self.I(self.rd(f.SrcA)) if signed else self.U(self.rd(f.SrcA))
        b = self.src_b(i, float_imm=False)
        if b.const is not None:
            sh = (b.const & 0xFF) & 31
            e = '%s >> %d%s' % (a, sh, '' if signed else 'u')
        else:
            e = '%s >> (%s & 31u)' % (a, self.U(b))
        if f.get('Brev'):
            e = 'bitfieldReverse(%s)' % e
        self.wr(f.Dest, Val(e if not signed else 'uint(%s)' % e, 'u'))

    def op_Lop(self, i):
        f = i.f
        a = self.U(self.rd(f.SrcA))
        b = self.U(self.src_b(i, float_imm=False))
        if f.get('NegA'):
            a = '~%s' % self._paren(a)
        if f.get('NegB'):
            b = '~%s' % self._paren(b)
        op = enum_name('LogicOp', f.LogicOp)
        e = {'And': '%s & %s', 'Or': '%s | %s', 'Xor': '%s ^ %s', 'PassB': '%s%s'}[op]
        e = e % (a, b) if op != 'PassB' else b
        v = Val(e, 'u')
        self.wr(f.Dest, v)
        if f.has('PredicateOp') and f.has('DestPred'):
            po = enum_name('PredicateOp', f.PredicateOp)
            r = self.U(self.regs.get(f.Dest, v))
            self.wp(f.DestPred, {'F': 'false', 'T': 'true', 'Z': '%s == 0u' % r, 'Nz': '%s != 0u' % r}[po])

    op_Lop32i = op_Lop

    def op_Lop3(self, i):
        f = i.f
        a = self.U(self.rd(f.SrcA))
        b = self.U(self.src_b(i, float_imm=False))
        c = self.U(self.rd(f.SrcC))
        lut = f.get('Imm', f.get('Lut', 0)) & 0xFF
        e = lut3(lut, a, b, c)
        v = Val(e, 'u')
        self.wr(f.Dest, v)
        if f.has('PredicateOp') and f.has('DestPred'):
            po = enum_name('PredicateOp', f.PredicateOp)
            r = self.U(self.regs.get(f.Dest, v))
            self.wp(f.DestPred, {'F': 'false', 'T': 'true', 'Z': '%s == 0u' % r, 'Nz': '%s != 0u' % r}[po])

    def _icmp(self, comp, a, b, signed):
        c = enum_name('IComp', comp)
        if c in ('T', 'F'):
            return 'true' if c == 'T' else 'false'
        x, y = (self.I(a), self.I(b)) if signed else (self.U(a), self.U(b))
        return '%s %s %s' % (x, {'Lt': '<', 'Eq': '==', 'Le': '<=', 'Gt': '>', 'Ne': '!=', 'Ge': '>='}[c], y)

    def op_Isetp(self, i):
        f = i.f
        cmp = self.temp('b', self._icmp(f.IComp, self.rd(f.SrcA), self.src_b(i, float_imm=False), f.Signed))
        sp = self.pr(f.SrcPred, f.SrcPredInv)
        self.wp(f.DestPred, self._bop(f.Bop, cmp.e, sp))
        self.wp(f.DestPredInv, self._bop(f.Bop, '!' + cmp.e, sp))

    def op_Iset(self, i):
        f = i.f
        res = self._bop(f.Bop, self._icmp(f.IComp, self.rd(f.SrcA), self.src_b(i, float_imm=False), f.Signed),
                        self.pr(f.SrcPred, f.SrcPredInv))
        if f.get('BVal'):
            self.wr(f.Dest, Val('(%s) ? 1.0 : 0.0' % res, 'f'))
        else:
            self.wr(f.Dest, Val('(%s) ? 0xFFFFFFFFu : 0u' % res, 'u'))

    def op_Imnmx(self, i):
        f = i.f
        a, b = self.rd(f.SrcA), self.src_b(i, float_imm=False)
        x, y = (self.I(a), self.I(b)) if f.Signed else (self.U(a), self.U(b))
        p = self.pr(f.SrcPred, f.SrcPredInv)
        e = '%s ? min(%s, %s) : max(%s, %s)' % (p, x, y, x, y)
        self.wr(f.Dest, Val('uint(%s)' % e if f.Signed else e, 'u'))

    def op_Bfe(self, i):
        f = i.f
        a = self.rd(f.SrcA)
        b = self.U(self.src_b(i, float_imm=False))
        x = self.I(a) if f.get('Signed') else self.U(a)
        if f.get('Brev'):
            x = 'bitfieldReverse(%s)' % x
        e = 'bitfieldExtract(%s, int(%s & 0xFFu), int((%s >> 8u) & 0xFFu))' % (x, b, b)
        self.wr(f.Dest, Val('uint(%s)' % e if f.get('Signed') else e, 'u'))

    def op_Bfi(self, i):
        f = i.f
        a = self.U(self.rd(f.SrcA))
        b = self.U(self.src_b(i, float_imm=False))
        c = self.U(self.src_c(i) if not i.variant.endswith('Rc') else self.cbuf(f.CbufSlot, f.CbufOffset))
        e = 'bitfieldInsert(%s, %s, int(%s & 0xFFu), int((%s >> 8u) & 0xFFu))' % (c, a, b, b)
        self.wr(f.Dest, Val(e, 'u'))

    def op_Flo(self, i):
        f = i.f
        b = self.U(self.src_b(i, float_imm=False))
        e = 'uint(findMSB(%s))' % b
        self.wr(f.Dest, Val(e, 'u'))

    def op_Popc(self, i):
        self.wr(i.f.Dest, Val('uint(bitCount(%s))' % self.U(self.src_b(i, float_imm=False)), 'u'))

    def op_Prmt(self, i):
        f = i.f
        a, b = self.U(self.rd(f.SrcA)), self.U(self.src_b(i, float_imm=False))
        c = self.U(self.src_c(i))
        self.wr(f.Dest, Val('yz_prmt(%s, %s, %s)' % (a, b, c), 'u'))
        self.need_prmt = True

    def op_Vset(self, i):
        f = i.f
        a, b = self.rd(f.SrcA), self.rd(f.SrcB)
        c = self.U(self.rd(f.SrcC))
        cmp = self._icmp(f.VComp, a, b, True)
        op = enum_name('VideoOp', f.get('VideoOp', 4))
        e = '(%s) ? 1u : 0u' % cmp
        if op == 'Acc':
            e = '(%s) + %s' % (e, c)
        self.wr(f.Dest, Val(e, 'u'))

    def op_Psetp(self, i):
        f = i.f
        a = self.pr(f.SrcA, f.get('NegA'))
        b = self.pr(f.SrcB, f.get('NegB'))
        sp = self.pr(f.SrcPred, f.SrcPredInv)
        o = enum_name('BoolOp', f.Bop2 if f.has('Bop2') else 0)
        ab = '(%s) %s (%s)' % (a, {'And': '&&', 'Or': '||', 'Xor': '^^'}[o], b)
        self.wp(f.DestPred, self._bop(f.Bop, ab, sp))
        self.wp(f.DestPredInv, self._bop(f.Bop, '!(%s)' % ab, sp))

    def op_P2r(self, i):
        f = i.f
        parts = ['(%s ? %du : 0u)' % (self.pr(k), 1 << k) for k in range(7)]
        mask = self.U(self.src_b(i, float_imm=False))
        self.wr(f.Dest, Val('(%s) & %s' % (' | '.join(parts), mask), 'u'))

    def op_R2p(self, i):
        f = i.f
        v = self.U(self.rd(f.SrcA))
        m = self.src_b(i, float_imm=False)
        sh = f.get('ByteSel', 0) * 8
        for k in range(7):
            if m.const is not None and not (m.const >> k) & 1:
                continue
            self.wp(k, '(%s & %du) != 0u' % (v, 1 << (k + sh)))

    def op_Csetp(self, i):
        f = i.f
        c = self.ccc(i)
        sp = self.pr(f.SrcPred, f.SrcPredInv)
        self.wp(f.DestPred, self._bop(f.Bop, c, sp))
        self.wp(f.DestPredInv, self._bop(f.Bop, '!(%s)' % c, sp))

    def op_S2r(self, i):
        sreg = enum_name('SReg', i.f.SReg)
        e = {'YDirection': '1.0'}.get(sreg)
        self.wr(i.f.Dest, Val(e, 'f') if e else cu(0))

    def op_Cs2r(self, i):
        self.wr(i.f.Dest, cu(0))

    def op_Shfl(self, i):
        self.wr(i.f.Dest, self.rd(i.f.SrcA))

    def op_Vote(self, i):
        f = i.f
        self.wp(f.get('VpDest', PT), self.pr(f.SrcPred, f.SrcPredInv))

    # ---- memory -----------------------------------------------------------------------------
    def op_Ldc(self, i):
        f = i.f
        slot = f.CbufSlot
        self.cbufs.add(slot)
        off = sext(f.CbufOffset, 16)
        a = self.rd(f.SrcA)
        size = enum_name('LsSize2', f.LsSize)
        n = {'B64': 2, 'B128': 4}.get(size, 1)
        if a.const is not None or f.SrcA == RZ:
            base = ((a.const or 0) + off) & 0xFFFFFFFF
            for k in range(n):
                w = (base >> 2) + k
                v = self.cbuf(slot, w)
                if size in ('U8', 'S8', 'U16', 'S16'):
                    bits = 8 if size[1] == '8' else 16
                    sh = (base & 3) * 8
                    v = Val('bitfieldExtract(%s(%s), %d, %d)' % ('int' if size[0] == 'S' else 'uint',
                                                               self.U(v), sh, bits), 'u' if size[0] == 'U' else 'i')
                self.wr(f.Dest + k, v)
            return
        addr = self.temp('u', '%s + %du' % (self.U(a), off & 0xFFFFFFFF) if off else self.U(a))
        for k in range(n):
            w = '((%s >> 2u) + %du)' % (addr.e, k) if k else '(%s >> 2u)' % addr.e
            e = '%s_c%d.data[%s >> 2u][%s & 3u]' % (self.p, slot, w, w)
            self.wr(f.Dest + k, Val(e, 'f'))

    def op_Ldl(self, i):
        f = i.f
        a = self.U(self.rd(f.SrcA))
        off = sext(f.Imm24, 24)
        n = {'B64': 2, 'B128': 4}.get(enum_name('LsSize2', f.LsSize), 1)
        self.local_size = max(self.local_size, 1024)
        idx = self.temp('u', '(%s + %du) >> 2u' % (a, off & 0xFFFFFFFF))
        for k in range(n):
            self.wr(f.Dest + k, Val('local_mem[(%s + %du) & 1023u]' % (idx.e, k), 'u'))

    def op_Stl(self, i):
        f = i.f
        a = self.U(self.rd(f.SrcA))
        off = sext(f.Imm24, 24)
        n = {'B64': 2, 'B128': 4}.get(enum_name('LsSize2', f.LsSize), 1)
        self.local_size = max(self.local_size, 1024)
        idx = self.temp('u', '(%s + %du) >> 2u' % (a, off & 0xFFFFFFFF))
        g = self.guard(i)
        for k in range(n):
            self.emit('%slocal_mem[(%s + %du) & 1023u] = %s;' % (g, idx.e, k, self.U(self.rd(f.Dest + k))))

    def op_Ldg(self, i):
        self.warnings.append('global memory load (LDG) reads 0')
        n = {'B64': 2, 'B128': 4, 'UB128': 4}.get(enum_name('LsSize', i.f.LsSize), 1)
        for k in range(n):
            self.wr(i.f.Dest + k, cu(0))

    def op_Lds(self, i):
        self.op_Ldg(i)

    def op_Stg(self, i):
        self.warnings.append('global memory store (STG) ignored')

    op_Sts = op_Stg
    op_Red = op_Stg

    # ---- attributes ----------------------------------------------------------------------------
    def _attr_in(self, addr):
        """Vertex / fragment input attribute at a Maxwell attribute address."""
        if USER_BASE <= addr < USER_END:
            loc = (addr - USER_BASE) >> 4
            self.inputs.setdefault(loc, self._interp(loc))
            return Val('in_attr%d.%s' % (loc, COMP[(addr >> 2) & 3]), 'f')
        if 0x70 <= addr <= 0x7C:
            if self.stage == 'pixel':
                return Val('gl_FragCoord.%s' % COMP[(addr >> 2) & 3], 'f')
            return cf(0.0)
        if addr == 0x2F8:
            self.uses_instance_id = True
            return Val('uint(gl_InstanceID)', 'u')
        if addr == 0x2FC:
            self.uses_vertex_id = True
            return Val('uint(gl_VertexID)', 'u')
        if addr == 0x3FC:
            return Val('(gl_FrontFacing ? 0xFFFFFFFFu : 0u)', 'u')
        if addr == 0x60:
            return Val('uint(gl_PrimitiveID)', 'u')
        if 0x2E0 <= addr < 0x2F0 or 0x300 <= addr < 0x3A0 or 0x280 <= addr < 0x2C0:
            # fixed-function colours / texcoords: Ryujinx maps these to free user attributes
            self.warnings.append('fixed-function attribute 0x%X read as 0' % addr)
            return cf(0.0)
        if addr == 0x2C0:
            return cf(0.0)
        self.warnings.append('attribute 0x%X read as 0' % addr)
        return cf(0.0)

    def _interp(self, loc):
        if self.stage != 'pixel':
            return ''
        imap = self.sph.get('imap_generic', b'\0' * 32)[loc]
        for c in range(4):
            t = (imap >> (2 * c)) & 3
            if t:
                return {1: 'flat ', 2: '', 3: 'noperspective '}[t]
        return ''

    def _perspective(self, loc):
        imap = self.sph.get('imap_generic', b'\0' * 32)[loc]
        for c in range(4):
            t = (imap >> (2 * c)) & 3
            if t:
                return t == 2
        return False

    def op_Ald(self, i):
        f = i.f
        n = enum_name('AlSize', f.AlSize)
        cnt = {'_32': 1, '_64': 2, '_96': 3, '_128': 4}[n]
        if f.get('O') or f.get('P'):
            self.warnings.append('ALD.O/P (output / patch) read as 0')
        for k in range(cnt):
            if f.Dest + k >= RZ:
                break
            if f.SrcA != RZ:
                self.warnings.append('indexed ALD')
            self.wr(f.Dest + k, self._attr_in(f.Imm11 + 4 * k))

    def op_Ast(self, i):
        f = i.f
        n = enum_name('AlSize', f.AlSize)
        cnt = {'_32': 1, '_64': 2, '_96': 3, '_128': 4}[n]
        g = self.guard(i)
        for k in range(cnt):
            if f.SrcB + k > RZ:
                break
            addr = f.Imm11 + 4 * k
            v = self.rd(f.SrcB + k)
            dst = self._attr_out(addr)
            if dst:
                self.emit('%s%s = %s;' % (g, dst, self.F(v)))

    def _attr_out(self, addr):
        if 0x70 <= addr <= 0x7C:
            return 'gl_Position.%s' % COMP[(addr >> 2) & 3]
        if USER_BASE <= addr < USER_END:
            loc = (addr - USER_BASE) >> 4
            self.outputs.add(loc)
            return 'out_attr%d.%s' % (loc, COMP[(addr >> 2) & 3])
        if addr == 0x6C:
            return 'gl_PointSize'
        if 0x2C0 <= addr < 0x2E0:
            self.warnings.append('clip distance %d ignored' % ((addr - 0x2C0) >> 2))
            return None
        self.warnings.append('output attribute 0x%X ignored' % addr)
        return None

    def op_Ipa(self, i):
        f = i.f
        addr = f.Imm10
        if f.Idx:
            self.warnings.append('indexed IPA read as 0')
            v = cf(0.0)
        else:
            v = self._attr_in(addr)
            if USER_BASE <= addr < USER_END and self._perspective((addr - USER_BASE) >> 4):
                v = Val('%s * gl_FragCoord.w' % v.e, 'f')
        e = self.F(v)
        if enum_name('IpaOp', f.IpaOp) == 'Multiply' and not (0x280 <= addr < 0x2C0):
            e = '%s * %s' % (e, self.F(self.rd(f.SrcB)))
        if v.t != 'f':
            self.wr(f.Dest, v)
            return
        self.wr(f.Dest, Val(self.sat(e, f.Sat), 'f'))

    # ---- textures ----------------------------------------------------------------------------
    SAMPLER = {('2D', False, False): 'sampler2D', ('2D', True, False): 'sampler2DArray',
               ('2D', False, True): 'sampler2DShadow', ('2D', True, True): 'sampler2DArrayShadow',
               ('3D', False, False): 'sampler3D', ('Cube', False, False): 'samplerCube',
               ('Cube', True, False): 'samplerCubeArray', ('Cube', False, True): 'samplerCubeShadow',
               ('1D', False, False): 'sampler2D'}

    def tex_name(self, handle, dim, array, shadow):
        name = '%s_t_tcb_%X' % (self.p, handle)
        typ = self.SAMPLER.get((dim, array, shadow), 'sampler2D')
        self.textures.setdefault(name, typ)
        return name

    def _tex_dests(self, comps, dests, res, shadow):
        """Write texture result components to registers."""
        if shadow:
            r = self.temp('f', res)
            for d in dests:
                self.wr(d, r)
            return
        self.ntemp += 1
        tn = 'temp_%d' % self.ntemp
        self.emit('vec4 %s = %s;' % (tn, res))
        for c, d in zip(comps, dests):
            self.wr(d, Val('%s.%s' % (tn, COMP[c]), 'f'))

    def op_Texs(self, i):
        f = i.f
        tgt = enum_name('TexsTarget', f.Target)
        dest, dest2 = f.Dest, f.Dest2
        if dest == RZ and dest2 == RZ:
            return
        ra, rb = [f.SrcA], [f.SrcB]

        def A():
            r = ra[0]
            ra[0] += 1
            return self.rd(r) if r < RZ else cu(0)

        def B():
            r = rb[0]
            rb[0] += 1
            return self.rd(r) if r < RZ else cu(0)
        F = self.F
        lod, shadow, array, dim = None, False, False, '2D'
        if tgt in ('Texture2DArray', 'Texture2DArrayLodZero', 'Texture2DArrayLodZeroDepthCompare'):
            array = True
            layer = A()
            x, y = F(A()), F(B())
            coord = 'vec3(%s, %s, float(%s))' % (x, y, self.I(layer))
            if tgt.endswith('DepthCompare'):
                shadow = True
                coord = 'vec4(%s, %s, float(%s), %s)' % (x, y, self.I(layer), F(B()))
            if 'LodZero' in tgt:
                lod = '0.0'
        elif tgt == 'Texture1DLodZero':
            coord, lod = 'vec2(%s, 0.0)' % F(A()), '0.0'
        elif tgt == 'Texture2D':
            coord = 'vec2(%s, %s)' % (F(A()), F(B()))
        elif tgt == 'Texture2DLodZero':
            coord, lod = 'vec2(%s, %s)' % (F(A()), F(B())), '0.0'
        elif tgt == 'Texture2DLodLevel':
            x, y = F(A()), F(A())
            coord, lod = 'vec2(%s, %s)' % (x, y), F(B())
        elif tgt == 'Texture2DDepthCompare':
            x, y = F(A()), F(A())
            coord, shadow = 'vec3(%s, %s, %s)' % (x, y, F(B())), True
        elif tgt == 'Texture2DLodZeroDepthCompare':
            x, y = F(A()), F(A())
            coord, shadow, lod = 'vec3(%s, %s, %s)' % (x, y, F(B())), True, '0.0'
        elif tgt == 'Texture2DLodLevelDepthCompare':
            x, y = F(A()), F(A())
            ref, l = F(B()), F(B())
            coord, shadow, lod = 'vec3(%s, %s, %s)' % (x, y, ref), True, l
        elif tgt in ('Texture3D', 'TextureCube'):
            dim = '3D' if tgt == 'Texture3D' else 'Cube'
            x, y = F(A()), F(A())
            coord = 'vec3(%s, %s, %s)' % (x, y, F(B()))
        elif tgt == 'Texture3DLodZero':
            dim = '3D'
            x, y = F(A()), F(A())
            coord, lod = 'vec3(%s, %s, %s)' % (x, y, F(B())), '0.0'
        elif tgt == 'TextureCubeLodLevel':
            dim = 'Cube'
            x, y = F(A()), F(A())
            z, l = F(B()), F(B())
            coord, lod = 'vec3(%s, %s, %s)' % (x, y, z), l
        else:
            self.warnings.append('TEXS target %s' % tgt)
            return
        name = self.tex_name(f.TidB, dim, array, shadow)
        if lod is not None and not (shadow and array):
            res = 'textureLod(%s, %s, %s)' % (name, coord, lod)
        else:
            res = 'texture(%s, %s)' % (name, coord)
        lut = [[0b0001, 0b0010, 0b0100, 0b1000, 0b0011, 0b1001, 0b1010, 0b1100],
               [0b0111, 0b1011, 0b1101, 0b1110, 0b1111, 0, 0, 0]]
        mask = lut[0 if dest2 == RZ else 1][f.WMask]
        comps = [c for c in range(4) if (mask >> c) & 1]
        dests = []
        for k in range(len(comps)):
            hi, lo = k >> 1, k & 1
            r = dest2 if hi else dest
            dests.append(r + lo if r < RZ else RZ)
        self._tex_dests(comps, dests, res, shadow)

    def _tex_full(self, i, bindless):
        f = i.f
        dim_e = enum_name('TexDim', f.Dim)
        array = dim_e.startswith('Array')
        base = dim_e.replace('Array', '').replace('_', '')
        dim = {'1d': '1D', '2d': '2D', '3d': '3D', 'Cube': 'Cube'}.get(base, '2D')
        ra, rb = [f.SrcA], [f.SrcB]

        def A():
            r = ra[0]
            ra[0] += 1
            return self.rd(r) if r < RZ else cu(0)

        def B():
            r = rb[0]
            rb[0] += 1
            return self.rd(r) if r < RZ else cu(0)
        layer = A() if array else None
        handle_v = B() if bindless else None
        n = {'1D': 1, '2D': 2, '3D': 3, 'Cube': 3}[dim]
        cs = [self.F(A()) for _ in range(n)]
        if dim == '1D':
            cs.append('0.0')
            dim = '2D'
        lodm = enum_name('Lod', f.get('Lodb') if bindless else f.get('Lod', 0))
        lod_v = self.F(B()) if lodm not in ('Lz', 'None', '0') and lodm in ('Lb', 'Ll', 'Lba', 'Lla') else None
        if f.get('Aoffib' if bindless else 'Aoffi'):
            B()
            self.warnings.append('texture offset ignored')
        shadow = f.get('Dc', False)
        if array:
            cs.append('float(%s)' % self.I(layer))
        if shadow:
            cs.append(self.F(B()))
        coord = 'vec%d(%s)' % (len(cs), ', '.join(cs))
        if bindless:
            if handle_v is not None and handle_v.cbuf is not None:
                handle = 0x10000 + handle_v.cbuf[0] * 0x1000 + handle_v.cbuf[1]
            else:
                handle = 0xFFFF
                self.warnings.append('bindless texture with an unknown handle')
        else:
            handle = f.TidB
        name = self.tex_name(handle, dim, array, shadow)
        if lodm == 'Lz':
            res = 'textureLod(%s, %s, 0.0)' % (name, coord)
        elif lodm in ('Ll', 'Lla'):
            res = 'textureLod(%s, %s, %s)' % (name, coord, lod_v)
        elif lodm in ('Lb', 'Lba'):
            res = 'texture(%s, %s, %s)' % (name, coord, lod_v)
        else:
            res = 'texture(%s, %s)' % (name, coord)
        comps = [c for c in range(4) if (f.WMask >> c) & 1]
        dests = [f.Dest + k if f.Dest + k < RZ else RZ for k in range(len(comps))]
        self._tex_dests(comps, dests, res, shadow)

    def op_Tex(self, i):
        self._tex_full(i, False)

    def op_TexB(self, i):
        self._tex_full(i, True)

    def op_Tld(self, i):
        f = i.f
        ra = f.SrcA
        x, y = self.I(self.rd(ra)), self.I(self.rd(ra + 1))
        lod = self.I(self.rd(f.SrcB)) if f.get('Lod') else '0'
        name = self.tex_name(f.TidB, '2D', False, False)
        comps = [c for c in range(4) if (f.WMask >> c) & 1]
        dests = [f.Dest + k for k in range(len(comps))]
        self._tex_dests(comps, dests, 'texelFetch(%s, ivec2(%s, %s), %s)' % (name, x, y, lod), False)

    def op_Tlds(self, i):
        f = i.f
        x, y = self.I(self.rd(f.SrcA)), self.I(self.rd(f.SrcB))
        name = self.tex_name(f.TidB, '2D', False, False)
        lut = [[0b0001, 0b0010, 0b0100, 0b1000, 0b0011, 0b1001, 0b1010, 0b1100],
               [0b0111, 0b1011, 0b1101, 0b1110, 0b1111, 0, 0, 0]]
        mask = lut[0 if f.Dest2 == RZ else 1][f.WMask]
        comps = [c for c in range(4) if (mask >> c) & 1]
        dests = [(f.Dest2 if k >> 1 else f.Dest) + (k & 1) for k in range(len(comps))]
        self.warnings.append('TLDS approximated as a 2D texel fetch')
        self._tex_dests(comps, dests, 'texelFetch(%s, ivec2(%s, %s), 0)' % (name, x, y), False)

    def op_Tld4(self, i):
        f = i.f
        x, y = self.F(self.rd(f.SrcA)), self.F(self.rd(f.SrcA + 1))
        name = self.tex_name(f.get('TidB', 0), '2D', False, False)
        comp = f.get('TexComp', 0)
        comps = [c for c in range(4) if (f.WMask >> c) & 1]
        dests = [f.Dest + k for k in range(len(comps))]
        self._tex_dests(comps, dests, 'textureGather(%s, vec2(%s, %s), %d)' % (name, x, y, comp), False)

    def op_Tld4s(self, i):
        f = i.f
        x, y = self.F(self.rd(f.SrcA)), self.F(self.rd(f.SrcB))
        name = self.tex_name(f.TidB, '2D', False, False)
        dests = [f.Dest, f.Dest + 1, f.Dest2, f.Dest2 + 1]
        self._tex_dests([0, 1, 2, 3], dests, 'textureGather(%s, vec2(%s, %s), %d)' % (name, x, y, f.get('TexComp', 0)),
                        False)

    def op_Txq(self, i):
        f = i.f
        q = enum_name('TexQuery', f.TexQuery)
        name = self.tex_name(f.TidB, '2D', False, False)
        lod = self.I(self.rd(f.SrcA)) if f.SrcA != RZ else '0'
        d = f.Dest
        if q == 'TexHeaderDimension':
            for c in range(4):
                if (f.WMask >> c) & 1:
                    if d >= RZ:
                        break
                    e = ['uint(textureSize(%s, %s).x)' % (name, lod), 'uint(textureSize(%s, %s).y)' % (name, lod),
                         '1u', 'uint(textureQueryLevels(%s))' % name][c]
                    self.wr(d, Val(e, 'u'))
                    d += 1
        else:
            self.warnings.append('TXQ %s' % q)

    def op_Tmml(self, i):
        f = i.f
        x, y = self.F(self.rd(f.SrcA)), self.F(self.rd(f.SrcA + 1))
        name = self.tex_name(f.get('TidB', 0), '2D', False, False)
        self.ntemp += 1
        tn = 'temp_%d' % self.ntemp
        self.emit('vec2 %s = textureQueryLod(%s, vec2(%s, %s));' % (tn, name, x, y))
        if (f.WMask & 1):
            self.wr(f.Dest, Val('uint(%s.y * 256.0)' % tn, 'u'))
        if (f.WMask & 2):
            self.wr(f.Dest + 1, Val('uint(%s.x * 256.0)' % tn, 'u'))

    def op_Txd(self, i):
        self.warnings.append('TXD (explicit derivatives) sampled without them')
        f = i.f
        x, y = self.F(self.rd(f.SrcA)), self.F(self.rd(f.SrcA + 1))
        name = self.tex_name(f.get('TidB', 0), '2D', False, False)
        comps = [c for c in range(4) if (f.WMask >> c) & 1]
        dests = [f.Dest + k for k in range(len(comps))]
        self._tex_dests(comps, dests, 'texture(%s, vec2(%s, %s))' % (name, x, y), False)

    # ---- flow ---------------------------------------------------------------------------------
    def _target(self, i):
        return decoder.branch_target(i)

    def _cond_of(self, i):
        c = self.ccc(i) if i.f.has('Ccc') else 'true'
        p = 'true' if i.always else self.pr(i.pred, i.pred_inv)
        if c == 'true':
            return p
        if p == 'true':
            return c
        return '(%s) && (%s)' % (p, c)

    def op_Exit(self, i):
        cond = self._cond_of(i)
        if self.stage == 'pixel':
            if cond != 'true':
                self.emit('if (%s)' % cond)
                self.emit('{')
                self.indent += 1
            self._pixel_outputs()
            self.emit('return;')
            if cond != 'true':
                self.indent -= 1
                self.emit('}')
                return None
            return 'stop'
        if cond != 'true':
            self.emit('if (%s) return;' % cond)
            return None
        self.emit('return;')
        return 'stop'

    def _pixel_outputs(self):
        omap = self.sph.get('omap_targets', 0)
        base = 0
        for rt in range(8):
            m = (omap >> (rt * 4)) & 0xF
            if not m:
                continue
            for c in range(4):
                if (m >> c) & 1:
                    self.outputs.add(rt)
                    self.emit('out_attr%d.%s = %s;' % (rt, COMP[c], self.F(self.rd(base + c))))
            base += 4
        if self.sph.get('omap_depth'):
            self.uses_frag_depth = True
            self.emit('gl_FragDepth = %s;' % self.F(self.rd(bin(omap).count('1') + 1)))

    def op_Kil(self, i):
        cond = self._cond_of(i)
        if cond == 'true':
            self.emit('discard;')
        else:
            self.emit('if (%s) discard;' % cond)

    def _jump(self, i, target_expr):
        cond = self._cond_of(i)
        if not self.multi:
            self.warnings.append('branch in a straight-line program ignored at 0x%X' % i.addr)
            return None
        self.flush()
        if cond == 'true':
            self.emit('pc = %s;' % target_expr)
            self.emit('break;')
            return 'stop'
        self.emit('if (%s)' % cond)
        self.emit('{')
        self.emit('    pc = %s;' % target_expr)
        self.emit('    break;')
        self.emit('}')
        return None

    def op_Bra(self, i):
        t = self._target(i)
        if t == i.addr:
            return None
        bid = self.block_ids.get(t) if self.multi else None
        if bid is None:
            self.warnings.append('branch to 0x%X outside the program' % t)
            return None
        return self._jump(i, str(bid))

    def op_Ssy(self, i):
        if not self.multi:
            return None
        bid = self.block_ids.get(self._target(i))
        if bid is not None:
            self.emit('ssy_stack[ssy_sp & 15] = %d; ssy_sp++;' % bid)
        return None

    def op_Pbk(self, i):
        if not self.multi:
            return None
        bid = self.block_ids.get(self._target(i))
        if bid is not None:
            self.emit('pbk_stack[pbk_sp & 15] = %d; pbk_sp++;' % bid)
        return None

    op_Pcnt = op_Pbk

    def op_Sync(self, i):
        if not self.multi:
            return None
        return self._jump(i, 'ssy_stack[(ssy_sp = max(ssy_sp - 1, 0)) & 15]')

    def op_Brk(self, i):
        if not self.multi:
            return None
        return self._jump(i, 'pbk_stack[(pbk_sp = max(pbk_sp - 1, 0)) & 15]')

    op_Cont = op_Brk

    def op_Cal(self, i):
        self.warnings.append('CAL (subroutine) not supported')

    def op_Ret(self, i):
        self.warnings.append('RET not supported')

    # ---- output --------------------------------------------------------------------------------
    def _assemble(self):
        out = ['#version 450 core', '#extension GL_ARB_gpu_shader_int64 : enable', '',
               '// Translated by Yaz0loom (Maxwell -> GLSL, semantics after Ryujinx / Ryubing)',
               '// %s %s: %d instructions' % (self.name, self.stage, len(self.insts)), '',
               'const int undef = 0;', '',
               'layout (binding = 0, std140) uniform _support_buffer',
               '{',
               '    uint alpha_test;',
               '    uint is_bgra[8];',
               '    precise vec4 viewport_inverse;',
               '    precise vec4 viewport_size;',
               '    int frag_scale_count;',
               '    precise float render_scale[73];',
               '    ivec4 tfe_offset;',
               '    int tfe_vertex_count;',
               '} support_buffer;', '']
        for c in sorted(self.cbufs, reverse=True):
            out += ['layout (binding = %d, std140) uniform _%s_c%d' % (c + 1, self.p, c), '{',
                    '    precise vec4 data[4096];', '} %s_c%d;' % (self.p, c), '']
        for k, (name, typ) in enumerate(sorted(self.textures.items(), key=lambda x: x[0])):
            out.append('layout (binding = %d) uniform %s %s;' % (k, typ, name))
        for loc in sorted(self.inputs):
            out.append('layout (location = %d) %sin vec4 in_attr%d;' % (loc, self.inputs[loc], loc))
        out.append('')
        for loc in sorted(self.outputs):
            out.append('layout (location = %d) out vec4 out_attr%d;' % (loc, loc))
        out.append('')
        if getattr(self, 'need_prmt', False):
            out += ['uint yz_prmt(uint a, uint b, uint s)', '{', '    uint r = 0u;',
                    '    for (int k = 0; k < 4; k++)', '    {',
                    '        uint sel = (s >> (4 * k)) & 7u;',
                    '        uint byte_ = sel < 4u ? (a >> (8u * sel)) & 0xFFu : (b >> (8u * (sel - 4u))) & 0xFFu;',
                    '        r |= byte_ << (8 * k);', '    }', '    return r;', '}', '']
        out.append('void main()')
        out.append('{')
        if self.local_size:
            out.append('    uint local_mem[%d];' % (self.local_size))
        for r in sorted(self.used_regs):
            out.append('    uint r%d = 0u;' % r)
        for p in sorted(self.used_preds):
            out.append('    bool p%d = false;' % p)
        if self.stage == 'pixel':
            for rt in sorted(self.outputs):
                out.append('    out_attr%d = vec4(0.0, 0.0, 0.0, 1.0);' % rt)
        else:
            for loc in sorted(self.outputs):
                out.append('    out_attr%d = vec4(0.0, 0.0, 0.0, 1.0);' % loc)
            out.append('    gl_Position = vec4(0.0, 0.0, 0.0, 1.0);')
        out += self.lines
        out.append('}')
        return '\n'.join(out) + '\n'


def lut3(lut, a, b, c):
    """LOP3 lookup table -> expression (common tables first, else sum of products)."""
    common = {0x00: '0u', 0xFF: '0xFFFFFFFFu', 0xF0: '{a}', 0xCC: '{b}', 0xAA: '{c}', 0x0F: '~{a}', 0x33: '~{b}',
              0x55: '~{c}', 0xC0: '({a} & {b})', 0xA0: '({a} & {c})', 0x88: '({b} & {c})', 0xFC: '({a} | {b})',
              0xFA: '({a} | {c})', 0xEE: '({b} | {c})', 0x3C: '({a} ^ {b})', 0x5A: '({a} ^ {c})', 0x66: '({b} ^ {c})',
              0x80: '({a} & {b} & {c})', 0xFE: '({a} | {b} | {c})', 0x96: '({a} ^ {b} ^ {c})',
              0xF8: '({a} | ({b} & {c}))', 0xEA: '(({a} & {b}) | {c})', 0xEC: '(({a} & {c}) | {b})',
              0xE8: '(({a} & ({b} | {c})) | ({b} & {c}))', 0x30: '({a} & ~{b})', 0x0C: '(~{a} & {b})',
              0x50: '({a} & ~{c})', 0x44: '({b} & ~{c})', 0x22: '(~{b} & {c})', 0x0A: '(~{a} & {c})',
              0xF3: '({a} | ~{b})', 0xCF: '(~{a} | {b})', 0xCA: '(({a} & {b}) | (~{a} & {c}))',
              0xE2: '(({b} & {a}) | (~{b} & {c}))', 0xB8: '(({a} & {c}) | (~{c} & {b}))'}
    if lut in common:
        return common[lut].format(a=a, b=b, c=c)
    terms = []
    for k in range(8):
        if (lut >> k) & 1:
            xa = a if k & 4 else '~%s' % a
            xb = b if k & 2 else '~%s' % b
            xc = c if k & 1 else '~%s' % c
            terms.append('(%s & %s & %s)' % (xa, xb, xc))
    return '(%s)' % ' | '.join(terms) if terms else '0u'


def translate(code: bytes, stage: str, name=''):
    """-> (glsl source, warnings)"""
    t = Translator(code, stage, name)
    return t.translate(), t.warnings

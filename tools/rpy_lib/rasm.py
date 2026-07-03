"""rasm -- a tiny x86-64 machine-code assembler, written in RPython-friendly
Python.

Purpose: replace the external GNU assembler (`as`) in the ShivyCX toolchain so
the compiler becomes fully self-contained. ShivyCX emits Intel-syntax x86-64
assembly text; this module encodes that text to machine-code bytes (and, later,
into an ELF relocatable object).

This first cut covers the instruction/operand vocabulary ShivyCX actually
emits: mov, movsx, lea, push, pop, add, sub, imul, idiv, cqo, and, or, xor,
shifts, cmp, test, call, ret, jmp and the Jcc family, with register / immediate
/ memory (base + index*scale + disp, RIP-relative, and symbol) operands.

The encoding logic (REX / ModRM / SIB / displacement / immediate layout) follows
the same model as pycca's assembler (campagnola/pycca) and the Intel SDM, but is
rewritten in a flat, statically-typed style so it can be translated by RPython
(and, eventually, run on minipy itself).

Style constraints for RPython compatibility:
  * no metaclasses, decorators, generators, or **kwargs;
  * uniform object shapes (one Operand class, not a union of types);
  * explicit integer math; bytes built as lists of ints then joined.
"""


# --------------------------------------------------------------------------
# Registers
# --------------------------------------------------------------------------
# Each general-purpose register maps to (encoding value 0..15, size in bits).
# The low 3 bits go in ModRM/SIB; value >= 8 additionally sets a REX extension
# bit (R, X, or B depending on the field). rsp/rbp (and r12/r13) have special
# addressing meaning handled in the memory encoder.

_REG64 = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi",
          "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]
_REG32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi",
          "r8d", "r9d", "r10d", "r11d", "r12d", "r13d", "r14d", "r15d"]
_REG16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di",
          "r8w", "r9w", "r10w", "r11w", "r12w", "r13w", "r14w", "r15w"]
_REG8 = ["al", "cl", "dl", "bl", "spl", "bpl", "sil", "dil",
         "r8b", "r9b", "r10b", "r11b", "r12b", "r13b", "r14b", "r15b"]
# legacy high-byte names (ah/ch/dh/bh) are not emitted by ShivyCX; omit them.

# name -> (val, bits)
REGISTERS = {}


def _init_registers():
    tables = [(_REG64, 64), (_REG32, 32), (_REG16, 16), (_REG8, 8)]
    for pair in tables:
        names = pair[0]
        bits = pair[1]
        i = 0
        while i < len(names):
            REGISTERS[names[i]] = (i, bits)
            i += 1


_init_registers()


def reg_val(name):
    return REGISTERS[name][0]


def reg_bits(name):
    return REGISTERS[name][1]


def is_register(name):
    return name in REGISTERS


# --------------------------------------------------------------------------
# REX / ModRM / SIB primitives
# --------------------------------------------------------------------------
REX_BASE = 0x40
REX_W = 0x08     # 64-bit operand size
REX_R = 0x04     # extension of ModRM.reg
REX_X = 0x02     # extension of SIB.index
REX_B = 0x01     # extension of ModRM.rm / SIB.base / opcode reg


def modrm(mod, reg, rm):
    """mod: 0..3; reg,rm: 0..7 (low 3 bits already masked)."""
    return ((mod & 3) << 6) | ((reg & 7) << 3) | (rm & 7)


def sib(scale_log2, index, base):
    """scale_log2: 0..3 (scale 1/2/4/8); index,base: 0..7."""
    return ((scale_log2 & 3) << 6) | ((index & 7) << 3) | (base & 7)


def scale_to_log2(scale):
    if scale == 1:
        return 0
    if scale == 2:
        return 1
    if scale == 4:
        return 2
    if scale == 8:
        return 3
    raise AssemblerError("bad scale %d" % scale)


def pack_le(value, nbytes):
    """Little-endian encode `value` into `nbytes` bytes (list of ints)."""
    out = []
    v = value & ((1 << (8 * nbytes)) - 1)
    i = 0
    while i < nbytes:
        out.append(v & 0xFF)
        v >>= 8
        i += 1
    return out


def fits_int8(v):
    return -128 <= v <= 127


def fits_int32(v):
    return -2147483648 <= v <= 2147483647


class AssemblerError(Exception):
    pass


# --------------------------------------------------------------------------
# Operands
# --------------------------------------------------------------------------
# One uniform class describes every operand. `kind` selects the interpretation:
#   "reg" : a register           -> reg_v (0..15), size (bits)
#   "imm" : an immediate         -> imm (int)
#   "mem" : a memory reference    -> base/index/scale/disp/sym/rip, size (bits)
# For memory, base/index are register values 0..15 or -1 when absent. `sym` is
# a symbol name ("" if none); when set the displacement is a relocation target.
# `rip` marks RIP-relative addressing ([rip + disp] / [symbol] under -fpic).

MEM_NONE = -1


class Operand(object):
    def __init__(self):
        self.kind = "reg"
        self.reg_v = 0
        self.size = 0          # operand size in bits (8/16/32/64)
        self.imm = 0
        self.base = MEM_NONE
        self.index = MEM_NONE
        self.scale = 1
        self.disp = 0
        self.sym = ""
        self.rip = False


def op_reg(name):
    o = Operand()
    o.kind = "reg"
    o.reg_v = reg_val(name)
    o.size = reg_bits(name)
    return o


def op_imm(value):
    o = Operand()
    o.kind = "imm"
    o.imm = value
    return o


def op_mem(size, base, index, scale, disp, sym, rip):
    o = Operand()
    o.kind = "mem"
    o.size = size
    o.base = base
    o.index = index
    o.scale = scale
    o.disp = disp
    o.sym = sym
    o.rip = rip
    return o


# A relocation the caller (ELF writer) must patch: at byte offset `where`,
# a `size`-byte field references `sym` with addend `add`; `pcrel` marks
# RIP/relative (R_X86_64_PC32) vs absolute (R_X86_64_32/32S).
class Reloc(object):
    def __init__(self, where, sym, size, pcrel, add):
        self.where = where
        self.sym = sym
        self.size = size
        self.pcrel = pcrel
        self.add = add


# --------------------------------------------------------------------------
# ModRM/SIB encoding for a (reg-field, rm-operand) pair
# --------------------------------------------------------------------------
# Returns (rex_bits, byte_list, relocs). `reg_field` is the 0..15 value that
# goes in ModRM.reg (a register number or an opcode extension /N). `rm` is an
# Operand that is either a register or a memory reference. `start` is the byte
# offset of the ModRM byte within the whole instruction, used to place any
# relocation for a symbolic displacement.

def encode_rm(reg_field, rm, start):
    rex = 0
    if reg_field >= 8:
        rex |= REX_R
    rf = reg_field & 7

    if rm.kind == "reg":
        if rm.reg_v >= 8:
            rex |= REX_B
        return rex, [modrm(3, rf, rm.reg_v & 7)], []

    # memory operand
    relocs = []
    out = []

    # RIP-relative (or symbolic under PIC): mod=00, rm=101, disp32.
    if rm.rip or (rm.sym != "" and rm.base == MEM_NONE and rm.index == MEM_NONE and rm.rip):
        out.append(modrm(0, rf, 5))
        # disp32 follows the ModRM byte
        if rm.sym != "":
            relocs.append(Reloc(start + 1, rm.sym, 4, True, rm.disp))
            out.extend(pack_le(0, 4))
        else:
            out.extend(pack_le(rm.disp, 4))
        return rex, out, relocs

    has_index = rm.index != MEM_NONE
    has_base = rm.base != MEM_NONE

    # absolute [disp32] or [sym] with no base/index: mod=00, rm=100, SIB with
    # base=101 index=100 (none), disp32.
    if not has_base and not has_index:
        out.append(modrm(0, rf, 4))
        out.append(sib(0, 4, 5))
        if rm.sym != "":
            relocs.append(Reloc(start + 2, rm.sym, 4, False, rm.disp))
            out.extend(pack_le(0, 4))
        else:
            out.extend(pack_le(rm.disp, 4))
        return rex, out, relocs

    need_sib = has_index or ((rm.base & 7) == 4)  # rsp/r12 base forces SIB

    if not need_sib:
        b = rm.base
        if b >= 8:
            rex |= REX_B
        # choose mod by displacement; rbp/r13 (rm==5) cannot use mod=00
        if rm.disp == 0 and (b & 7) != 5 and rm.sym == "":
            out.append(modrm(0, rf, b & 7))
        elif rm.sym == "" and fits_int8(rm.disp):
            out.append(modrm(1, rf, b & 7))
            out.extend(pack_le(rm.disp, 1))
        else:
            out.append(modrm(2, rf, b & 7))
            if rm.sym != "":
                relocs.append(Reloc(start + 1, rm.sym, 4, False, rm.disp))
                out.extend(pack_le(0, 4))
            else:
                out.extend(pack_le(rm.disp, 4))
        return rex, out, relocs

    # SIB form
    idx = 4  # 4 == "no index"
    if has_index:
        idx = rm.index
        if idx >= 8:
            rex |= REX_X
    base = 5  # 5 with mod=00 means "no base" (disp32 only)
    mod = 0
    if has_base:
        base = rm.base
        if base >= 8:
            rex |= REX_B
        if rm.disp == 0 and (base & 7) != 5 and rm.sym == "":
            mod = 0
        elif rm.sym == "" and fits_int8(rm.disp):
            mod = 1
        else:
            mod = 2
    out.append(modrm(mod, rf, 4))
    out.append(sib(scale_to_log2(rm.scale), idx & 7, base & 7))
    if mod == 1:
        out.extend(pack_le(rm.disp, 1))
    elif mod == 2 or (not has_base):
        if rm.sym != "":
            relocs.append(Reloc(start + 2, rm.sym, 4, False, rm.disp))
            out.extend(pack_le(0, 4))
        else:
            out.extend(pack_le(rm.disp, 4))
    return rex, out, relocs


def emit_rex(rex, size, force):
    """Return the REX byte list (0 or 1 bytes). `size`==64 sets REX.W."""
    if size == 64:
        rex |= REX_W
    if rex != 0 or force:
        return [REX_BASE | rex]
    return []


# --------------------------------------------------------------------------
# Instruction encoders
# --------------------------------------------------------------------------
# encode(mnem, ops) -> (byte_list, reloc_list). `ops` is a list of Operand.
# Operand-size handling: 16-bit adds a 0x66 prefix; 64-bit sets REX.W; 8-bit
# selects the low opcode (even) byte. ShivyCX emits mostly 32/64-bit forms.

# mnem -> (opcode_mr, opcode_rm, ext) for the classic ALU group. The immediate
# forms use 0x81 /ext (imm32) or the sign-extended 0x83 /ext (imm8).
# (opcode_mr, opcode_rm, ext, acc8) -- acc8 is the AL,imm opcode; the
# eAX/rAX,imm form is acc8+1. gas uses these shorter accumulator encodings when
# the destination is AL/AX/EAX/RAX and the immediate needs a full imm32.
_ALU = {
    "add": (0x01, 0x03, 0, 0x04),
    "or":  (0x09, 0x0B, 1, 0x0C),
    "and": (0x21, 0x23, 4, 0x24),
    "sub": (0x29, 0x2B, 5, 0x2C),
    "xor": (0x31, 0x33, 6, 0x34),
    "cmp": (0x39, 0x3B, 7, 0x3C),
    "test": (0x85, 0x85, 0, 0xA8),
}

# Jcc: condition mnemonic -> tttn nibble (0x0F 0x8x rel32).
_JCC = {
    "jo": 0x0, "jno": 0x1, "jb": 0x2, "jae": 0x3, "je": 0x4, "jz": 0x4,
    "jne": 0x5, "jnz": 0x5, "jbe": 0x6, "ja": 0x7, "js": 0x8, "jns": 0x9,
    "jp": 0xA, "jnp": 0xB, "jl": 0xC, "jge": 0xD, "jle": 0xE, "jg": 0xF,
}

# shift mnemonic -> ext (/N in 0xC1 /N ib, 0xD3 /N cl, 0xD1 /N by-1)
_SHIFT = {"rol": 0, "ror": 1, "sal": 4, "shl": 4, "shr": 5, "sar": 7}


def _pfx_size(size):
    if size == 16:
        return [0x66]
    return []


def _assemble(size, rex, opcode_bytes, mrm, imm_bytes, rl):
    """Build a full instruction from parts and fix reloc offsets.

    Layout: [66 prefix?] [REX?] [opcode...] [ModRM/SIB/disp] [imm]. Relocations
    returned by encode_rm are relative to the ModRM byte (start=0); shift them
    by the length of everything that precedes ModRM.
    """
    out = []
    out.extend(_pfx_size(size))
    out.extend(emit_rex(rex, size, False))
    out.extend(opcode_bytes)
    pre = len(out)
    out.extend(mrm)
    out.extend(imm_bytes)
    relocs = []
    i = 0
    while i < len(rl):
        r = rl[i]
        relocs.append(Reloc(r.where + pre, r.sym, r.size, r.pcrel, r.add))
        i += 1
    return out, relocs


def encode(mnem, ops):
    if mnem == "ret":
        return [0xC3], []
    if mnem == "cqo":
        return [0x48, 0x99], []
    if mnem == "cdq":
        return [0x99], []
    if mnem == "leave":
        return [0xC9], []
    if mnem == "nop":
        return [0x90], []

    if mnem == "push":
        return _encode_pushpop(ops[0], True)
    if mnem == "pop":
        return _encode_pushpop(ops[0], False)

    if mnem == "call":
        return _encode_calljmp(ops[0], True)
    if mnem == "jmp":
        return _encode_calljmp(ops[0], False)
    if mnem in _JCC:
        return _encode_jcc(_JCC[mnem], ops[0])

    if mnem == "lea":
        return _encode_lea(ops[0], ops[1])

    if mnem in _ALU:
        return _encode_alu(mnem, ops[0], ops[1])
    if mnem == "mov":
        return _encode_mov(ops[0], ops[1])

    if mnem == "imul":
        return _encode_imul(ops)
    if mnem == "idiv":
        return _encode_unary_group3(ops[0], 7)
    if mnem == "div":
        return _encode_unary_group3(ops[0], 6)
    if mnem == "imul1":
        return _encode_unary_group3(ops[0], 5)
    if mnem == "mul":
        return _encode_unary_group3(ops[0], 4)
    if mnem == "neg":
        return _encode_unary_group3(ops[0], 3)
    if mnem == "not":
        return _encode_unary_group3(ops[0], 2)

    if mnem in _SHIFT:
        return _encode_shift(_SHIFT[mnem], ops)

    if mnem == "movsx" or mnem == "movsxd":
        return _encode_movx(ops[0], ops[1], True)
    if mnem == "movzx":
        return _encode_movx(ops[0], ops[1], False)

    raise AssemblerError("unsupported mnemonic: %s" % mnem)


def _encode_alu(mnem, dst, src):
    tri = _ALU[mnem]
    if src.kind == "reg" and (dst.kind == "reg" or dst.kind == "mem"):
        rex, mrm, rl = encode_rm(src.reg_v, dst, 0)
        return _assemble(dst.size, rex, [tri[0]], mrm, [], rl)
    if dst.kind == "reg" and src.kind == "mem":
        rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
        return _assemble(dst.size, rex, [tri[1]], mrm, [], rl)
    if src.kind == "imm":
        size = dst.size
        ext = tri[2]
        if size != 8 and fits_int8(src.imm):
            rex, mrm, rl = encode_rm(ext, dst, 0)
            return _assemble(size, rex, [0x83], mrm, pack_le(src.imm, 1), rl)
        # accumulator short form: AL/AX/EAX/RAX, imm  (no ModRM)
        if dst.kind == "reg" and dst.reg_v == 0:
            acc = tri[3]
            if size == 8:
                out = []
                out.append(acc)
                out.extend(pack_le(src.imm, 1))
                return out, []
            out = []
            out.extend(_pfx_size(size))
            out.extend(emit_rex(0, size, False))
            out.append(acc + 1)
            out.extend(pack_le(src.imm, 4))
            return out, []
        opc = 0x80 if size == 8 else 0x81
        immn = 1 if size == 8 else 4
        rex, mrm, rl = encode_rm(ext, dst, 0)
        return _assemble(size, rex, [opc], mrm, pack_le(src.imm, immn), rl)
    raise AssemblerError("bad operands for %s" % mnem)


def _encode_mov(dst, src):
    if src.kind == "reg" and (dst.kind == "reg" or dst.kind == "mem"):
        opc = 0x88 if dst.size == 8 else 0x89
        rex, mrm, rl = encode_rm(src.reg_v, dst, 0)
        return _assemble(dst.size, rex, [opc], mrm, [], rl)
    if dst.kind == "reg" and src.kind == "mem":
        opc = 0x8A if dst.size == 8 else 0x8B
        rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
        return _assemble(dst.size, rex, [opc], mrm, [], rl)
    if src.kind == "imm":
        if dst.kind == "reg":
            size = dst.size
            if size == 64:
                # gas: mov r64, imm32 -> REX.W C7 /0 id (sign-extended)
                rex, mrm, rl = encode_rm(0, dst, 0)
                return _assemble(size, rex, [0xC7], mrm, pack_le(src.imm, 4), rl)
            out = []
            out.extend(_pfx_size(size))
            rex = REX_B if dst.reg_v >= 8 else 0
            out.extend(emit_rex(rex, size, False))
            base_op = 0xB0 if size == 8 else 0xB8
            out.append(base_op + (dst.reg_v & 7))
            immn = 1 if size == 8 else (2 if size == 16 else 4)
            out.extend(pack_le(src.imm, immn))
            return out, []
        opc = 0xC6 if dst.size == 8 else 0xC7
        immn = 1 if dst.size == 8 else 4
        rex, mrm, rl = encode_rm(0, dst, 0)
        return _assemble(dst.size, rex, [opc], mrm, pack_le(src.imm, immn), rl)
    raise AssemblerError("bad operands for mov")


def _encode_pushpop(o, is_push):
    if o.kind == "reg":
        # push/pop default to 64-bit operand size; no REX.W needed. REX.B for
        # r8..r15. 16-bit push (unusual) would need 0x66; ShivyCX uses r64.
        out = []
        if o.size == 16:
            out.append(0x66)
        if o.reg_v >= 8:
            out.append(REX_BASE | REX_B)
        base = 0x50 if is_push else 0x58
        out.append(base + (o.reg_v & 7))
        return out, []
    if o.kind == "imm" and is_push:
        if fits_int8(o.imm):
            return [0x6A] + pack_le(o.imm, 1), []
        return [0x68] + pack_le(o.imm, 4), []
    if o.kind == "mem":
        ext = 6 if is_push else 0
        opc = 0xFF if is_push else 0x8F
        rex, mrm, rl = encode_rm(ext, o, 0)
        # 64-bit default; do not set REX.W
        return _assemble(0, rex, [opc], mrm, [], rl)
    raise AssemblerError("bad push/pop operand")


def _encode_calljmp(o, is_call):
    if o.kind == "mem" or o.kind == "reg":
        ext = 2 if is_call else 4
        rex, mrm, rl = encode_rm(ext, o, 0)
        return _assemble(0, rex, [0xFF], mrm, [], rl)
    # label / rel32: e8 (call) / e9 (jmp) + rel32 (PC-relative reloc)
    opc = 0xE8 if is_call else 0xE9
    out = [opc] + pack_le(0, 4)
    return out, [Reloc(1, o.sym, 4, True, o.disp - 4)]


def _encode_jcc(tttn, o):
    out = [0x0F, 0x80 | tttn] + pack_le(0, 4)
    return out, [Reloc(2, o.sym, 4, True, o.disp - 4)]


def _encode_lea(dst, src):
    rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
    return _assemble(dst.size, rex, [0x8D], mrm, [], rl)


def _encode_imul(ops):
    if len(ops) == 1:
        return _encode_unary_group3(ops[0], 5)
    dst = ops[0]
    # imul reg, imm  is shorthand for  imul reg, reg, imm
    if len(ops) == 2 and ops[1].kind == "imm":
        imm = ops[1]
        if fits_int8(imm.imm):
            rex, mrm, rl = encode_rm(dst.reg_v, dst, 0)
            return _assemble(dst.size, rex, [0x6B], mrm, pack_le(imm.imm, 1), rl)
        rex, mrm, rl = encode_rm(dst.reg_v, dst, 0)
        return _assemble(dst.size, rex, [0x69], mrm, pack_le(imm.imm, 4), rl)
    src = ops[1]
    if len(ops) == 2:
        # imul r, r/m : 0F AF /r
        rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
        return _assemble(dst.size, rex, [0x0F, 0xAF], mrm, [], rl)
    # imul r, r/m, imm : 6B /r ib (imm8) or 69 /r id
    imm = ops[2]
    if fits_int8(imm.imm):
        rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
        return _assemble(dst.size, rex, [0x6B], mrm, pack_le(imm.imm, 1), rl)
    rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
    return _assemble(dst.size, rex, [0x69], mrm, pack_le(imm.imm, 4), rl)



def _encode_unary_group3(o, ext):
    # F7 /ext (idiv/imul1/mul/div/neg/not); F6 for 8-bit
    opc = 0xF6 if o.size == 8 else 0xF7
    rex, mrm, rl = encode_rm(ext, o, 0)
    return _assemble(o.size, rex, [opc], mrm, [], rl)


def _encode_shift(ext, ops):
    dst = ops[0]
    if len(ops) == 1:
        opc = 0xD0 if dst.size == 8 else 0xD1
        rex, mrm, rl = encode_rm(ext, dst, 0)
        return _assemble(dst.size, rex, [opc], mrm, [], rl)
    amt = ops[1]
    if amt.kind == "reg":  # shift by cl -> D3 /ext
        opc = 0xD2 if dst.size == 8 else 0xD3
        rex, mrm, rl = encode_rm(ext, dst, 0)
        return _assemble(dst.size, rex, [opc], mrm, [], rl)
    if amt.imm == 1:
        opc = 0xD0 if dst.size == 8 else 0xD1
        rex, mrm, rl = encode_rm(ext, dst, 0)
        return _assemble(dst.size, rex, [opc], mrm, [], rl)
    opc = 0xC0 if dst.size == 8 else 0xC1
    rex, mrm, rl = encode_rm(ext, dst, 0)
    return _assemble(dst.size, rex, [opc], mrm, pack_le(amt.imm, 1), rl)


def _encode_movx(dst, src, signed):
    # movsx/movzx dst(r16/32/64), src(r/m8 or r/m16). movsxd for r/m32->r64.
    ssize = src.size
    if signed and ssize == 32:
        # movsxd r64, r/m32 : REX.W 63 /r
        rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
        return _assemble(dst.size, rex, [0x63], mrm, [], rl)
    if ssize == 8:
        opc2 = 0xBE if signed else 0xB6
    else:
        opc2 = 0xBF if signed else 0xB7
    rex, mrm, rl = encode_rm(dst.reg_v, src, 0)
    return _assemble(dst.size, rex, [0x0F, opc2], mrm, [], rl)


# --------------------------------------------------------------------------
# Intel-syntax parser (the subset ShivyCX emits)
# --------------------------------------------------------------------------
# parse_line(text) -> ("insn", mnem, ops) | ("label", name, None)
#                     | ("dir", text, None) | ("blank", "", None)
# Operand text forms handled:
#   registers:  rax / eax / r8d ...
#   immediates: 5, -16, 0x1f
#   memory:     QWORD PTR [rbp-8], DWORD PTR [sym+4*rcx], [rip+0], [sym]

_PTR_SIZE = {"BYTE": 8, "WORD": 16, "DWORD": 32, "QWORD": 64}


def _parse_int(tok):
    neg = False
    t = tok
    if t[0:1] == "-":
        neg = True
        t = t[1:]
    elif t[0:1] == "+":
        t = t[1:]
    if t[0:2] == "0x" or t[0:2] == "0X":
        v = int(t[2:], 16)
    else:
        v = int(t)
    if neg:
        v = -v
    return v


def _looks_int(tok):
    t = tok
    if t[0:1] == "-" or t[0:1] == "+":
        t = t[1:]
    if t == "":
        return False
    if t[0:2] == "0x" or t[0:2] == "0X":
        t = t[2:]
        i = 0
        while i < len(t):
            c = t[i]
            if not (("0" <= c <= "9") or ("a" <= c <= "f") or ("A" <= c <= "F")):
                return False
            i += 1
        return len(t) > 0
    i = 0
    while i < len(t):
        if not ("0" <= t[i] <= "9"):
            return False
        i += 1
    return True


def _split_terms(expr):
    """Split an address expression into signed terms, e.g. 'sym+4*rcx-8' ->
    ['+sym', '+4*rcx', '-8']."""
    terms = []
    cur = ""
    i = 0
    while i < len(expr):
        c = expr[i]
        if c == "+" or c == "-":
            if cur != "":
                terms.append(cur)
            cur = c
        else:
            cur += c
        i += 1
    if cur != "":
        terms.append(cur)
    # ensure each term has a leading sign
    out = []
    i = 0
    while i < len(terms):
        t = terms[i]
        if t[0:1] != "+" and t[0:1] != "-":
            t = "+" + t
        out.append(t)
        i += 1
    return out


def parse_memory(inner, size):
    base = MEM_NONE
    index = MEM_NONE
    scale = 1
    disp = 0
    sym = ""
    rip = False
    terms = _split_terms(inner.replace(" ", ""))
    i = 0
    while i < len(terms):
        t = terms[i]
        sign = t[0]
        body = t[1:]
        if "*" in body:
            # index*scale, scale*index, or a constant product used as disp
            a, star, b = body.partition("*")
            if _looks_int(a) and _looks_int(b):
                d = _parse_int(a) * _parse_int(b)
                if sign == "-":
                    d = -d
                disp += d
            elif _looks_int(a):
                scale = _parse_int(a)
                index = reg_val(b)
            else:
                index = reg_val(a)
                scale = _parse_int(b)
        elif is_register(body):
            if body == "rip":
                rip = True
            elif base == MEM_NONE:
                base = reg_val(body)
            else:
                index = reg_val(body)
        elif body == "rip":
            rip = True
        elif _looks_int(body):
            d = _parse_int(body)
            if sign == "-":
                d = -d
            disp += d
        else:
            sym = body   # a symbol
        i += 1
    return op_mem(size, base, index, scale, disp, sym, rip)


def parse_operand(text):
    t = text.strip()
    up = t.upper()
    size = 0
    # size prefix "QWORD PTR [..]"
    j = 0
    for key in _PTR_SIZE:
        if up.startswith(key + " PTR"):
            size = _PTR_SIZE[key]
            t = t[len(key) + 4:].strip()
            break
    if t[0:1] == "[":
        end = t.rfind("]")
        return parse_memory(t[1:end], size)
    if is_register(t):
        return op_reg(t)
    if _looks_int(t):
        return op_imm(_parse_int(t))
    # bare symbol operand (a jump/call target or [sym] written without brackets)
    o = Operand()
    o.kind = "imm"
    o.sym = t
    o.imm = 0
    return o


def _split_ops(rest):
    """Split operands on commas not inside brackets."""
    out = []
    cur = ""
    depth = 0
    i = 0
    while i < len(rest):
        c = rest[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    if cur.strip() != "":
        out.append(cur)
    return out


def parse_line(raw):
    line = raw
    # strip // comments
    ci = line.find("//")
    if ci >= 0:
        line = line[:ci]
    line = line.strip()
    if line == "":
        return ("blank", "", None)
    if line[0:1] == ".":
        return ("dir", line, None)
    if line.endswith(":"):
        return ("label", line[:-1], None)
    # label followed by nothing else; instruction otherwise
    sp = line.find(" ")
    tab = line.find("\t")
    cut = sp
    if tab >= 0 and (tab < sp or sp < 0):
        cut = tab
    if cut < 0:
        return ("insn", line, [])
    mnem = line[:cut]
    rest = line[cut:].strip()
    ops = []
    parts = _split_ops(rest)
    i = 0
    while i < len(parts):
        p = parts[i].strip()
        if p != "":
            ops.append(parse_operand(p))
        i += 1
    return ("insn", mnem, ops)

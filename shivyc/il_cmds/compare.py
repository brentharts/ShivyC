"""IL commands for comparisons."""

import shivyc.asm_cmds as asm_cmds
from shivyc.il_cmds.base import ILCommand
from shivyc.spots import MemSpot, LiteralSpot


class _GeneralCmp(ILCommand):
    """_GeneralCmp - base class for the comparison commands.

    IL value output must have int type. arg1, arg2 must have types that can
    be compared for equality bit-by-bit. No type conversion or promotion is
    done here.

    """
    signed_cmp_cmd = None
    unsigned_cmp_cmd = None

    def __init__(self, output, arg1, arg2): # noqa D102
        self.output = output
        self.arg1 = arg1
        self.arg2 = arg2
        # Set by the compare-and-branch peephole (shivyc/peephole.py) to
        # (label, negate): emit `cmp; jcc label` directly instead of
        # materializing a 0/1 boolean. negate=True jumps when the comparison
        # is false (the JumpZero case). Only used for integer comparisons.
        self.fuse = None

    # Negated conditional-jump for the fused (jump-if-false) case.
    _neg_jump = None

    def neg_cmp_command(self):
        """The conditional jump for the *negated* comparison (used by the fused
        jump-if-false path)."""
        ctype = self.arg1.ctype
        if ctype.is_pointer() or (ctype.is_integral() and not ctype.signed):
            return self.unsigned_neg_cmd
        return self.signed_neg_cmd

    def inputs(self): # noqa D102
        return [self.arg1, self.arg2]

    def outputs(self): # noqa D102
        return [] if self.fuse else [self.output]

    def targets(self): # noqa D102
        # When fused, this command performs a conditional branch to the fused
        # label, so the CFG/liveness analysis must see that edge.
        return [self.fuse[0]] if self.fuse else []

    def rel_spot_conf(self):  # noqa D102
        return {self.output: [self.arg1, self.arg2]}

    def _fix_both_literal_or_mem(self, arg1_spot, arg2_spot, regs,
                                 get_reg, asm_code):
        """Fix arguments if both are literal or memory.

        Adds any called registers to given regs list. Returns tuple where
        first element is new spot of arg1 and second element is new spot of
        arg2.
        """
        if ((isinstance(arg1_spot, LiteralSpot)
             and isinstance(arg2_spot, LiteralSpot))
            or (isinstance(arg1_spot, MemSpot)
                and isinstance(arg2_spot, MemSpot))):

            # No need to worry about r overlapping with arg1 or arg2 because
            # in this case both are literal/memory.
            r = get_reg([], regs)
            regs.append(r)
            asm_code.add(asm_cmds.Mov(r, arg1_spot, self.arg1.ctype.size))
            return r, arg2_spot
        else:
            return arg1_spot, arg2_spot

    def _fix_either_literal64(self, arg1_spot, arg2_spot, regs,
                              get_reg, asm_code):
        """Move any 64-bit immediate operands to register."""

        if self._is_imm64(arg1_spot):
            size = self.arg1.ctype.size
            new_arg1_spot = get_reg([], regs + [arg2_spot])
            asm_code.add(asm_cmds.Mov(new_arg1_spot, arg1_spot, size))
            return new_arg1_spot, arg2_spot

        # We cannot have both cases because _fix_both_literal is called
        # before this.
        elif self._is_imm64(arg2_spot):
            size = self.arg2.ctype.size
            new_arg2_spot = get_reg([], regs + [arg1_spot])
            asm_code.add(asm_cmds.Mov(new_arg2_spot, arg2_spot, size))
            return arg1_spot, new_arg2_spot
        else:
            return arg1_spot, arg2_spot

    def _fix_literal_wrong_order(self, arg1_spot, arg2_spot):
        """If the first operand is a literal, swap the operands."""
        if self._is_imm(arg1_spot):
            return arg2_spot, arg1_spot
        else:
            return arg1_spot, arg2_spot

    def make_asm(self, spotmap, home_spots, get_reg, asm_code):  # noqa D102
        if self.arg1.ctype.is_floating() or self.arg2.ctype.is_floating():
            return self._make_float_asm(spotmap, get_reg, asm_code)
        regs = []

        # Fused compare-and-branch: emit `cmp; jcc label` and skip the 0/1
        # boolean. negate=True (the JumpZero case) branches when the comparison
        # is false. The output boolean is dead (outputs() is empty when fused).
        if self.fuse:
            label, negate = self.fuse
            arg1_spot, arg2_spot = self._fix_both_literal_or_mem(
                spotmap[self.arg1], spotmap[self.arg2], regs, get_reg, asm_code)
            arg1_spot, arg2_spot = self._fix_either_literal64(
                arg1_spot, arg2_spot, regs, get_reg, asm_code)
            arg1_spot, arg2_spot = self._fix_literal_wrong_order(
                arg1_spot, arg2_spot)
            arg_size = self.arg1.ctype.size
            asm_code.add(asm_cmds.Cmp(arg1_spot, arg2_spot, arg_size))
            jmp = self.neg_cmp_command() if negate else self.cmp_command()
            asm_code.add(jmp(label))
            return

        result = get_reg([spotmap[self.output]],
                         [spotmap[self.arg1], spotmap[self.arg2]])
        regs.append(result)

        out_size = self.output.ctype.size
        eq_val_spot = LiteralSpot(1)
        asm_code.add(asm_cmds.Mov(result, eq_val_spot, out_size))

        arg1_spot, arg2_spot = self._fix_both_literal_or_mem(
            spotmap[self.arg1], spotmap[self.arg2], regs, get_reg, asm_code)
        arg1_spot, arg2_spot = self._fix_either_literal64(
            arg1_spot, arg2_spot, regs, get_reg, asm_code)
        arg1_spot, arg2_spot = self._fix_literal_wrong_order(
            arg1_spot, arg2_spot)

        arg_size = self.arg1.ctype.size
        neq_val_spot = LiteralSpot(0)
        label = asm_code.get_label()

        asm_code.add(asm_cmds.Cmp(arg1_spot, arg2_spot, arg_size))
        asm_code.add(self.cmp_command()(label))
        asm_code.add(asm_cmds.Mov(result, neq_val_spot, out_size))
        asm_code.add(asm_cmds.AsmLabel(label))

        if result != spotmap[self.output]:
            asm_code.add(asm_cmds.Mov(spotmap[self.output], result, out_size))

    def cmp_command(self):
        ctype = self.arg1.ctype
        if ctype.is_pointer() or (ctype.is_integral() and not ctype.signed):
            return self.unsigned_cmp_cmd
        else:
            return self.signed_cmp_cmd

    # Floating comparison configuration, set per subclass:
    #   f_swap  - load arg2 (not arg1) into the scratch first, so the NaN-safe
    #             "above"/"above-or-equal" tests express < and <=.
    #   f_jump  - Ja or Jae (CF=0[/ZF=0]); both are false when unordered (NaN),
    #             which is the correct ordered-comparison result.
    #   f_eq / f_neq - equality forms, which must consult the parity flag (set
    #             on unordered) in addition to ZF.
    f_swap = False
    f_jump = None
    f_eq = False
    f_neq = False

    def _make_float_asm(self, spotmap, get_reg, asm_code):
        """Emit a floating comparison via ucomisd/ucomiss into a 0/1 result."""
        from shivyc.spots import XMM0
        size = self.arg1.ctype.size
        fmov = asm_cmds.Movss if size == 4 else asm_cmds.Movsd
        ucomi = asm_cmds.Ucomiss if size == 4 else asm_cmds.Ucomisd
        out_size = self.output.ctype.size
        result = get_reg([spotmap[self.output]], [])
        a1, a2 = spotmap[self.arg1], spotmap[self.arg2]
        one, zero = LiteralSpot(1), LiteralSpot(0)
        end = asm_code.get_label()

        if self.f_eq or self.f_neq:
            asm_code.add(fmov(XMM0, a1, size))
            asm_code.add(ucomi(XMM0, a2, size))
            keep, other = (zero, one) if self.f_eq else (one, zero)
            # Unordered (parity) and inequality (ZF=0) both leave `keep`.
            asm_code.add(asm_cmds.Mov(result, keep, out_size))
            asm_code.add(asm_cmds.Jp(end))
            asm_code.add(asm_cmds.Jne(end))
            asm_code.add(asm_cmds.Mov(result, other, out_size))
        else:
            first, second = (a2, a1) if self.f_swap else (a1, a2)
            asm_code.add(fmov(XMM0, first, size))
            asm_code.add(ucomi(XMM0, second, size))
            asm_code.add(asm_cmds.Mov(result, one, out_size))
            asm_code.add(self.f_jump(end))
            asm_code.add(asm_cmds.Mov(result, zero, out_size))
        asm_code.add(asm_cmds.AsmLabel(end))

        if result != spotmap[self.output]:
            asm_code.add(asm_cmds.Mov(spotmap[self.output], result, out_size))


class NotEqualCmp(_GeneralCmp):
    """NotEqualCmp - checks whether arg1 and arg2 are not equal.

    IL value output must have int type. arg1, arg2 must all have the same
    type. No type conversion or promotion is done here.

    """
    signed_cmp_cmd = asm_cmds.Jne
    unsigned_cmp_cmd = asm_cmds.Jne
    signed_neg_cmd = asm_cmds.Je
    unsigned_neg_cmd = asm_cmds.Je
    f_neq = True


class EqualCmp(_GeneralCmp):
    """EqualCmp - checks whether arg1 and arg2 are equal.

    IL value output must have int type. arg1, arg2 must all have the same
    type. No type conversion or promotion is done here.

    """
    signed_cmp_cmd = asm_cmds.Je
    unsigned_cmp_cmd = asm_cmds.Je
    signed_neg_cmd = asm_cmds.Jne
    unsigned_neg_cmd = asm_cmds.Jne
    f_eq = True


class LessCmp(_GeneralCmp):
    signed_cmp_cmd = asm_cmds.Jl
    unsigned_cmp_cmd = asm_cmds.Jb
    signed_neg_cmd = asm_cmds.Jge
    unsigned_neg_cmd = asm_cmds.Jae
    f_swap = True
    f_jump = asm_cmds.Ja


class GreaterCmp(_GeneralCmp):
    signed_cmp_cmd = asm_cmds.Jg
    unsigned_cmp_cmd = asm_cmds.Ja
    signed_neg_cmd = asm_cmds.Jle
    unsigned_neg_cmd = asm_cmds.Jbe
    f_jump = asm_cmds.Ja


class LessOrEqCmp(_GeneralCmp):
    signed_cmp_cmd = asm_cmds.Jle
    unsigned_cmp_cmd = asm_cmds.Jbe
    signed_neg_cmd = asm_cmds.Jg
    unsigned_neg_cmd = asm_cmds.Ja
    f_swap = True
    f_jump = asm_cmds.Jae


class GreaterOrEqCmp(_GeneralCmp):
    signed_cmp_cmd = asm_cmds.Jge
    unsigned_cmp_cmd = asm_cmds.Jae
    signed_neg_cmd = asm_cmds.Jl
    unsigned_neg_cmd = asm_cmds.Jb
    f_jump = asm_cmds.Jae

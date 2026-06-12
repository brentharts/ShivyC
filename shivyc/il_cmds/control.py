"""IL commands for labels, jumps, and function calls."""

import shivyc.asm_cmds as asm_cmds
import shivyc.spots as spots
from shivyc.il_cmds.base import ILCommand
from shivyc.spots import LiteralSpot, MemSpot


def _emit_parallel_int_moves(moves, asm_code):
    """Emit (dest_reg, src_spot, size) integer moves in an order where no move
    overwrites a register another move still needs to read, breaking cycles via
    a scratch register (r11/r10/rax, whichever is free of the operands)."""
    pending = [(d, s, sz) for (d, s, sz) in moves if d != s]
    # Deduplicate identical moves.
    seen, uniq = set(), []
    for m in pending:
        key = (m[0].asm_str(8), m[1].asm_str(m[2]), m[2])
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    pending = uniq

    def is_reg(spot):
        return isinstance(spot, spots.RegSpot)

    guard = 0
    while pending:
        guard += 1
        if guard > 10000:
            raise NotImplementedError("call: argument move scheduling failed")
        ready = None
        for m in pending:
            dest = m[0]
            if not any(o is not m and o[1] == dest for o in pending):
                ready = m
                break
        if ready is not None:
            asm_code.add(asm_cmds.Mov(ready[0], ready[1], ready[2]))
            pending.remove(ready)
            continue
        # A cycle remains (each dest is another move's source, all registers).
        # Relocate one source into a free scratch register, then redirect reads.
        used = {d.asm_str(8) for d, _, _ in pending}
        used |= {s.asm_str(8) for _, s, _ in pending if is_reg(s)}
        temp = None
        for cand in (spots.R11, spots.R10, spots.RAX):
            if cand.asm_str(8) not in used:
                temp = cand
                break
        if temp is None:
            raise NotImplementedError(
                "call: no scratch register to break an argument move cycle")
        src0 = pending[0][1]
        asm_code.add(asm_cmds.Mov(temp, src0, 8))
        pending = [(d, (temp if s == src0 else s), sz)
                   for (d, s, sz) in pending]


class Label(ILCommand):
    """Label - Analogous to an ASM label."""

    def __init__(self, label): # noqa D102
        """The label argument is an string label name unique to this label."""
        self.label = label

    def inputs(self): # noqa D102
        return []

    def outputs(self): # noqa D102
        return []

    def label_name(self):  # noqa D102
        return self.label

    def make_asm(self, spotmap, home_spots, get_reg, asm_code): # noqa D102
        asm_code.add(asm_cmds.Label(self.label))


class Jump(ILCommand):
    """Jumps unconditionally to a label."""

    def __init__(self, label): # noqa D102
        self.label = label

    def inputs(self): # noqa D102
        return []

    def outputs(self): # noqa D102
        return []

    def targets(self): # noqa D102
        return [self.label]

    def make_asm(self, spotmap, home_spots, get_reg, asm_code): # noqa D102
        asm_code.add(asm_cmds.Jmp(self.label))


class _GeneralJump(ILCommand):
    """General class for jumping to a label based on condition."""

    # ASM command to output for this jump IL command.
    # (asm_cmds.Je for JumpZero and asm_cmds.Jne for JumpNotZero)
    asm_cmd = None

    def __init__(self, cond, label): # noqa D102
        self.cond = cond
        self.label = label

    def inputs(self): # noqa D102
        return [self.cond]

    def outputs(self): # noqa D102
        return []

    def targets(self): # noqa D102
        return [self.label]

    def make_asm(self, spotmap, home_spots, get_reg, asm_code): # noqa D102
        size = self.cond.ctype.size

        if isinstance(spotmap[self.cond], LiteralSpot):
            r = get_reg()
            asm_code.add(asm_cmds.Mov(r, spotmap[self.cond], size))
            cond_spot = r
        else:
            cond_spot = spotmap[self.cond]

        zero_spot = LiteralSpot("0")
        asm_code.add(asm_cmds.Cmp(cond_spot, zero_spot, size))
        asm_code.add(self.command(self.label))


class JumpZero(_GeneralJump):
    """Jumps to a label if given condition is zero."""

    command = asm_cmds.Je


class JumpNotZero(_GeneralJump):
    """Jumps to a label if given condition is zero."""

    command = asm_cmds.Jne


class Return(ILCommand):
    """RETURN - returns the given value from function.

    If arg is None, then returns from the function without putting any value
    in the return register. Today, only supports values that fit in one
    register.
    """

    def __init__(self, arg=None): # noqa D102
        # arg must already be cast to return type
        self.arg = arg

    def inputs(self): # noqa D102
        return [self.arg]

    def outputs(self): # noqa D102
        return []

    def clobber(self):  # noqa D102
        return [spots.RAX]

    def abs_spot_pref(self):  # noqa D102
        # A large struct return lives in memory (RAX:RDX), so do not pin the
        # value to RAX; only a register-sized return value prefers RAX.
        if self.arg is not None and self.arg.ctype.size > 8:
            return {}
        return {self.arg: [spots.RAX]}

    def make_asm(self, spotmap, home_spots, get_reg, asm_code): # noqa D102
        if self.arg and self.arg.ctype.is_floating():
            size = self.arg.ctype.size
            mov = asm_cmds.Movss if size == 4 else asm_cmds.Movsd
            if spotmap[self.arg] != spots.XMM0:
                asm_code.add(mov(spots.XMM0, spotmap[self.arg], size))
        elif (self.arg and self.arg.ctype.is_struct_union()
              and self.arg.ctype.size > 8):
            # SysV AMD64: a struct of 9..16 bytes (INTEGER class) is returned
            # in RAX:RDX. The low eightbyte goes in RAX, the high one in RDX.
            size = self.arg.ctype.size
            src = spotmap[self.arg]
            if size > 16 or not isinstance(src, MemSpot):
                raise NotImplementedError(
                    "struct return larger than 16 bytes is not supported")
            hi = size - 8
            if hi not in (1, 2, 4, 8):
                raise NotImplementedError(
                    "struct return of this size is not supported")
            asm_code.add(asm_cmds.Mov(spots.RAX, src, 8))
            asm_code.add(asm_cmds.Mov(spots.RDX, src.shift(8), hi))
        elif self.arg and spotmap[self.arg] != spots.RAX:
            size = self.arg.ctype.size
            asm_code.add(asm_cmds.Mov(spots.RAX, spotmap[self.arg], size))

        # A frameless function set up no rbp frame, so there is nothing to
        # tear down before returning.
        if not getattr(asm_code, "frameless", False):
            asm_code.add(asm_cmds.Mov(spots.RSP, spots.RBP, 8))
            asm_code.add(asm_cmds.Pop(spots.RBP, None, 8))

        # Metamorphic return: jump through the writable .text slot the caller
        # patched, instead of popping a return address off the stack.
        slot = getattr(asm_code, "metamorphic_current", None)
        if slot:
            asm_code.add(asm_cmds.Raw("jmp QWORD PTR [rip + %s]" % slot))
        else:
            asm_code.add(asm_cmds.Ret())


class Call(ILCommand):
    """Call a given function.

    func - Pointer to function
    args - Arguments of the function, in left-to-right order. Must match the
    parameter types the function expects.
    ret - If function has non-void return type, IL value to save the return
    value. Its type must match the function return value.
    """

    arg_regs = [spots.RDI, spots.RSI, spots.RDX, spots.RCX, spots.R8, spots.R9]

    def __init__(self, func, args, ret): # noqa D102
        self.func = func
        self.args = args
        self.ret = ret
        self.void_return = self.func.ctype.arg.ret.is_void()

        # Set by the stackless-calls pass (shivyc/stackless.py):
        #   direct_name - name of a statically known callee (direct `call`)
        #   tail        - True if this call is in tail position (`jmp`)
        self.direct_name = None
        self.tail = False
        # Set by FuncCall: a variadic callee receives all args on the stack.
        self.variadic = False

        # Arguments beyond the sixth are passed on the stack (SysV AMD64).

    def inputs(self): # noqa D102
        # For a direct call the function pointer is never materialized, so it
        # is not a register input.
        if self.direct_name:
            return list(self.args)
        return [self.func] + self.args

    def outputs(self): # noqa D102
        return [] if self.void_return else [self.ret]

    def clobber(self): # noqa D102
        # All caller-saved registers are clobbered by function call
        return [spots.RAX, spots.RCX, spots.RDX, spots.RSI, spots.RDI,
                spots.R8, spots.R9, spots.R10, spots.R11]

    def abs_spot_pref(self): # noqa D102
        prefs = {}
        if not self.void_return and self.ret.ctype.size <= 8:
            prefs = {self.ret: [spots.RAX]}
        # Only scalar integer args have a single-GPR preference; floating args
        # travel through xmm registers and structs larger than a register live
        # in memory (loaded into their ABI registers by explicit moves).
        int_args = [a for a in self.args
                    if not a.ctype.is_floating()
                    and not (a.ctype.is_struct_union() and a.ctype.size > 8)]
        for arg, reg in zip(int_args, self.arg_regs):
            prefs[arg] = [reg]

        return prefs

    def abs_spot_conf(self): # noqa D102
        # We don't want the function pointer to be in the same register as
        # an argument will be placed into. (No function pointer for a direct
        # call, so no constraint.)
        if self.direct_name:
            return {}
        return {self.func: self.arg_regs[0:len(self.args)]}

    def indir_write(self): # noqa D102
        return self.args

    def indir_read(self): # noqa D102
        return self.args

    def make_asm(self, spotmap, home_spots, get_reg, asm_code): # noqa D102
        ret_size = self.func.ctype.arg.ret.size
        ret_float = self.func.ctype.arg.ret.is_floating()

        # Classify register arguments into the two SysV sequences: integer
        # args fill rdi,rsi,rdx,rcx,r8,r9; floating args fill xmm0-7; counted
        # independently. Anything that doesn't fit (or every arg, for a
        # variadic callee) is passed on the stack.
        xmm_regs = spots.xmm_arg_regs
        int_moves, flt_moves, stack_args = [], [], []
        struct_reg_moves = []  # (list-of-regs, struct arg) for 9..16-byte args
        if self.variadic:
            stack_args = list(self.args)
        else:
            ii = fi = 0
            for arg in self.args:
                if arg.ctype.is_struct_union() and arg.ctype.size > 8:
                    # SysV: 9..16-byte struct uses two consecutive integer
                    # registers (all-or-nothing); larger structs, or ones that
                    # do not fit, go on the stack as ceil(size/8) eightbytes.
                    n = (arg.ctype.size + 7) // 8
                    if arg.ctype.size <= 16 and ii + n <= len(self.arg_regs):
                        regs = [self.arg_regs[ii + k] for k in range(n)]
                        struct_reg_moves.append((regs, arg))
                        ii += n
                    else:
                        stack_args.append(arg)
                elif arg.ctype.is_floating() and fi < len(xmm_regs):
                    flt_moves.append((xmm_regs[fi], arg))
                    fi += 1
                elif not arg.ctype.is_floating() and ii < len(self.arg_regs):
                    int_moves.append((self.arg_regs[ii], arg))
                    ii += 1
                else:
                    stack_args.append(arg)
        int_regs_used = [r for r, _ in int_moves]
        for regs, _ in struct_reg_moves:
            int_regs_used.extend(regs)
        # Flatten stack arguments into eightbyte push slots (a struct occupies
        # ceil(size/8) of them), low eightbyte first.
        stack_slots = []  # list of MemSpot, in argument order
        for arg in stack_args:
            if arg.ctype.is_struct_union() and arg.ctype.size > 8:
                n = (arg.ctype.size + 7) // 8
                for k in range(n):
                    stack_slots.append(spotmap[arg].shift(8 * k))
            else:
                stack_slots.append(spotmap[arg])
        has_stack_args = len(stack_slots) > 0

        def emit_reg_moves():
            """Move integer args into GPRs and floating args into xmm regs."""
            # Integer argument moves must be ordered so that no move overwrites
            # a register another move still needs to read. With abs_spot_pref
            # most become no-ops, but a "shift" pattern (e.g. passing
            # (const, a, b, c) where a/b/c already sit in the next argument
            # registers) needs real parallel-move scheduling, breaking any
            # cycle through a scratch register.
            moves = [(reg, spotmap[arg], arg.ctype.size)
                     for reg, arg in int_moves if spotmap[arg] != reg]
            _emit_parallel_int_moves(moves, asm_code)
            for regs, arg in struct_reg_moves:
                base = spotmap[arg]
                for k, reg in enumerate(regs):
                    remaining = arg.ctype.size - 8 * k
                    chunk = next(cs for cs in (8, 4, 2, 1) if remaining >= cs)
                    asm_code.add(asm_cmds.Mov(reg, base.shift(8 * k), chunk))
            for xreg, arg in flt_moves:
                if spotmap[arg] != xreg:
                    size = arg.ctype.size
                    fmov = asm_cmds.Movss if size == 4 else asm_cmds.Movsd
                    asm_code.add(fmov(xreg, spotmap[arg], size))

        def emit_ret():
            """Move the return value out of its ABI register if needed."""
            if self.void_return:
                return
            ret_ctype = self.func.ctype.arg.ret
            if (ret_ctype.is_struct_union() and ret_size > 8):
                # SysV: a 9..16-byte struct comes back in RAX:RDX. Store both
                # eightbytes into the result's memory home.
                dst = spotmap[self.ret]
                if ret_size > 16 or not isinstance(dst, MemSpot):
                    raise NotImplementedError(
                        "struct return larger than 16 bytes is not supported")
                hi = ret_size - 8
                if hi not in (1, 2, 4, 8):
                    raise NotImplementedError(
                        "struct return of this size is not supported")
                asm_code.add(asm_cmds.Mov(dst, spots.RAX, 8))
                asm_code.add(asm_cmds.Mov(dst.shift(8), spots.RDX, hi))
                return
            if ret_float:
                if spotmap[self.ret] != spots.XMM0:
                    fmov = asm_cmds.Movss if ret_size == 4 else asm_cmds.Movsd
                    asm_code.add(fmov(spotmap[self.ret], spots.XMM0, ret_size))
            elif spotmap[self.ret] != spots.RAX:
                asm_code.add(asm_cmds.Mov(spotmap[self.ret], spots.RAX,
                                          ret_size))

        # Push stack arguments (the 7th onward) right-to-left, so the 7th ends
        # up at the lowest address ([rsp] at the call). The function body keeps
        # rsp 16-byte aligned, so an odd number of 8-byte pushes needs 8 bytes
        # of padding to keep the stack 16-aligned at the `call`.
        pad = 8 if (len(stack_slots) % 2 == 1) else 0
        cleanup = pad + 8 * len(stack_slots)
        if pad:
            asm_code.add(asm_cmds.Sub(spots.RSP, LiteralSpot(str(pad)), 8))
        for slot in reversed(stack_slots):
            # x86-64 `push imm` encodes only a 32-bit (sign-extended)
            # immediate, so a wider literal argument cannot be pushed directly.
            # No scratch register is guaranteed free here (the argument-register
            # moves have not happened yet), so push the low half as an 8-byte
            # slot and overwrite its high 4 bytes with a 32-bit store. e.g.
            # passing LLONG_MAX/LLONG_MIN as a stack argument.
            if (isinstance(slot, LiteralSpot)
                    and not (-(2 ** 31) <= int(slot.value) < 2 ** 31)):
                v = int(slot.value) & 0xFFFFFFFFFFFFFFFF
                low = v & 0xFFFFFFFF
                high = (v >> 32) & 0xFFFFFFFF
                low_s = low - (1 << 32) if low >= (1 << 31) else low
                high_s = high - (1 << 32) if high >= (1 << 31) else high
                asm_code.add(asm_cmds.Push(LiteralSpot(str(low_s)), None, 8))
                asm_code.add(asm_cmds.Raw(
                    "mov DWORD PTR [rsp+4], %d" % high_s))
            else:
                asm_code.add(asm_cmds.Push(slot, None, 8))

        # Move arguments into the argument registers (same for all call forms).
        if self.direct_name:
            emit_reg_moves()
            target = self.direct_name
        else:
            func_spot = spotmap[self.func]
            func_size = self.func.ctype.size

            # Check if function pointer spot will be clobbered by moving the
            # arguments into the correct (integer) registers.
            if spotmap[self.func] in int_regs_used:
                r = get_reg([], int_regs_used)
                asm_code.add(asm_cmds.Mov(r, spotmap[self.func], func_size))
                func_spot = r

            emit_reg_moves()
            target = func_spot

        # Metamorphic call: the target returns via a self-modified slot in
        # writable .text rather than the stack. We write our return address
        # into that slot, then jump (not call) into the callee. No return
        # address is pushed. Takes precedence over tail/ordinary forms.
        if (self.direct_name
                and not has_stack_args
                and self.direct_name in getattr(
                    asm_code, "metamorphic_funcs", set())):
            ret_label = asm_code.get_label()
            slot = self.direct_name + "__metaret"
            asm_code.add(asm_cmds.Raw("lea r11, [rip + %s]" % ret_label))
            asm_code.add(asm_cmds.Raw(
                "mov QWORD PTR [rip + %s], r11" % slot))
            asm_code.add(asm_cmds.Raw("jmp " + self.direct_name))
            asm_code.add(asm_cmds.Raw(ret_label + ":"))
            emit_ret()
            return

        if self.tail and not has_stack_args:
            # Tail call: tear down this frame (if any), then jump. The callee's
            # `ret` returns directly to our caller -- no return address for this
            # frame is ever pushed. (self.direct_name is guaranteed set here.)
            if not getattr(asm_code, "frameless", False):
                asm_code.add(asm_cmds.Mov(spots.RSP, spots.RBP, 8))
                asm_code.add(asm_cmds.Pop(spots.RBP, None, 8))
            asm_code.add(asm_cmds.Jmp(self.direct_name))
            return

        # Ordinary (non-tail) call.
        if self.direct_name:
            asm_code.add(asm_cmds.Raw("call " + self.direct_name))
        else:
            asm_code.add(asm_cmds.Call(target, None, self.func.ctype.size))

        # Caller cleans up any stack-passed arguments.
        if cleanup:
            asm_code.add(asm_cmds.Add(spots.RSP, LiteralSpot(str(cleanup)), 8))

        # xmm8-15 are caller-saved, so a call may have clobbered the packed
        # flag register. Inside a hot/interrupt routine, refresh it from the
        # memory mirror so subsequent zero-latency reads stay correct.
        if (getattr(asm_code, "simd_pack_enabled", False)
                and getattr(asm_code, "simd_pack_hot", False)
                and asm_code.simd_pack.active):
            asm_code.simd_pack.emit_refresh(asm_code)

        emit_ret()

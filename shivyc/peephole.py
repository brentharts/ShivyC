"""IL-level peephole optimizations, run per function just before register
allocation and code generation.

These are basic, target-independent simplifications that the -O0 code generator
otherwise leaves on the table, narrowing the gap with an optimizing compiler:

  * compare-and-branch fusion -- a comparison that feeds directly into a
    conditional jump becomes a single `cmp; jcc`, instead of materializing a
    0/1 boolean and then testing it (saves ~4 instructions per loop/if test).
  * arithmetic identities -- `x * 1`, `x + 0`, `x - 0` become a plain copy and
    `x * 0` becomes a load of 0 (these arise from address arithmetic, macro
    expansion, and generic code).

Each pass takes and returns the command list for a single function.
"""

import shivyc.il_cmds.compare as compare_cmds
import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.math as math_cmds
import shivyc.il_cmds.value as value_cmds


def _input_use_counts(commands):
    counts = {}
    for c in commands:
        for v in c.inputs():
            counts[v] = counts.get(v, 0) + 1
    return counts


def fuse_compare_jumps(commands, il_code):
    """Fuse `cmp -> conditional jump` into a single compare-and-branch.

    Only fires when the comparison's boolean result is consumed solely by the
    immediately following JumpZero/JumpNotZero, both operands are integers, and
    the first operand is not a literal. (A literal first operand makes the
    comparison codegen swap the operands, which would invert an ordering test;
    refusing to fuse there keeps the branch direction correct.)
    """
    n = len(commands)
    if n < 2:
        return commands
    uses = _input_use_counts(commands)
    out = []
    i = 0
    while i < n:
        c = commands[i]
        if isinstance(c, compare_cmds._GeneralCmp) and i + 1 < n \
                and getattr(c, "fuse", None) is None:
            nxt = commands[i + 1]
            if isinstance(nxt, (control_cmds.JumpZero, control_cmds.JumpNotZero)) \
                    and nxt.cond is c.output \
                    and uses.get(c.output, 0) == 1 \
                    and c.arg1 not in il_code.literals \
                    and not c.arg1.ctype.is_floating() \
                    and not c.arg2.ctype.is_floating():
                # JumpZero branches when the comparison is false -> negate.
                c.fuse = (nxt.label, isinstance(nxt, control_cmds.JumpZero))
                out.append(c)
                i += 2
                continue
        out.append(c)
        i += 1
    return out


def _lit(il_code, v):
    """The integer value of a literal IL value (stored as a string), or None."""
    s = il_code.literals.get(v)
    if s is None:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def simplify_arith(commands, il_code):
    """Replace arithmetic identities with copies / constant loads."""
    out = []
    for c in commands:
        repl = None
        if isinstance(c, math_cmds.Mult):
            a, b = _lit(il_code, c.arg1), _lit(il_code, c.arg2)
            if b == 1:
                repl = value_cmds.Set(c.output, c.arg1)
            elif a == 1:
                repl = value_cmds.Set(c.output, c.arg2)
        elif isinstance(c, math_cmds.Add):
            a, b = _lit(il_code, c.arg1), _lit(il_code, c.arg2)
            if b == 0:
                repl = value_cmds.Set(c.output, c.arg1)
            elif a == 0:
                repl = value_cmds.Set(c.output, c.arg2)
        elif isinstance(c, math_cmds.Subtr):
            if _lit(il_code, c.arg2) == 0:
                repl = value_cmds.Set(c.output, c.arg1)
        out.append(repl if repl is not None else c)
    return out


def optimize(commands, il_code):
    """Run all IL peephole passes for one function."""
    commands = simplify_arith(commands, il_code)
    commands = fuse_compare_jumps(commands, il_code)
    return commands

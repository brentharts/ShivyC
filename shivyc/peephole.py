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


# Pure, non-trapping IL command types: hoisting them out of a loop cannot
# introduce a fault (no division, no pointer dereference) or a side effect (no
# store, no call). Set copies an allocated value; arithmetic/bitwise/address
# computations are pure register ops.
def _hoistable_types():
    return (
        value_cmds.Set,
        value_cmds.AddrOf,
        math_cmds.Add, math_cmds.Subtr, math_cmds.Mult,
        math_cmds.BitAnd, math_cmds.BitOr, math_cmds.BitXor,
        math_cmds.Neg, math_cmds.Not,
        math_cmds.RBitShift, math_cmds.LBitShift,
    )


# Commands that write memory or call out: their presence makes a loop "unclean"
# because a store or call could change the memory a hoisted Set reads. Plain
# loads (ReadAt/ReadRel) do not modify memory, so they are allowed in the loop;
# they simply are not themselves hoisted (not in the hoistable set), which
# avoids any fault/aliasing concern from moving a dereference.
def _has_side_effects(c):
    return isinstance(c, (
        control_cmds.Call,
        value_cmds.SetAt, value_cmds.SetRel,
    ))


def hoist_loop_invariants(commands, il_code):
    """Hoist loop-invariant pure computations into a loop preheader.

    Conservative: only fires on a structured loop whose body contains no calls,
    stores, or loads (so all memory is provably invariant), and only hoists
    pure non-trapping commands whose operands are all defined outside the loop.
    This removes, e.g., a loop-invariant global reload from a tight numeric loop
    without risking aliasing or fault-introduction.
    """
    hoistable = _hoistable_types()
    changed = True
    guard = 0
    while changed and guard < 20:
        changed = False
        guard += 1
        labels = {c.label_name(): i for i, c in enumerate(commands)
                  if c.label_name()}
        # Count how many commands jump to each label (to require a single,
        # back-edge entry so the preheader is the only non-loop predecessor).
        target_counts = {}
        for c in commands:
            for t in c.targets():
                target_counts[t] = target_counts.get(t, 0) + 1

        for back_idx, c in enumerate(commands):
            tgts = c.targets()
            back_labels = [t for t in tgts if labels.get(t, back_idx) < back_idx]
            if not back_labels:
                continue
            start_idx = labels[back_labels[0]]
            body = commands[start_idx:back_idx + 1]

            # Require a clean loop and a single jump-entry (the back-edge).
            if any(_has_side_effects(b) for b in body):
                continue
            if target_counts.get(back_labels[0], 0) != 1:
                continue
            # Don't touch loops with nested back-edges (keep it simple/safe).
            nested = False
            for j in range(start_idx, back_idx):
                for t in commands[j].targets():
                    ti = labels.get(t)
                    if ti is not None and ti < j and not (j == back_idx):
                        nested = True
            if nested:
                continue

            defined_in_loop = set()
            def_count = {}
            for b in body:
                for o in b.outputs():
                    defined_in_loop.add(o)
                    def_count[o] = def_count.get(o, 0) + 1

            to_hoist = []
            for j in range(start_idx + 1, back_idx):  # skip start label, back-jump
                b = commands[j]
                if not isinstance(b, hoistable):
                    continue
                outs = b.outputs()
                ins = b.inputs()
                if len(outs) != 1:
                    continue
                out = outs[0]
                if def_count.get(out, 0) != 1:   # multiple defs -> not safe
                    continue
                if out in ins:                   # self-referential
                    continue
                if any(v in defined_in_loop for v in ins):
                    continue                     # an operand varies in the loop
                to_hoist.append(j)

            if not to_hoist:
                continue

            hoisted = [commands[j] for j in to_hoist]
            hoist_set = set(to_hoist)
            new_commands = []
            for i, cc in enumerate(commands):
                if i == start_idx:
                    new_commands.extend(hoisted)   # preheader (before the label)
                if i in hoist_set:
                    continue
                new_commands.append(cc)
            commands = new_commands
            changed = True
            break  # restart scan with updated indices

    return commands


def optimize(commands, il_code):
    """Run all IL peephole passes for one function."""
    commands = simplify_arith(commands, il_code)
    commands = hoist_loop_invariants(commands, il_code)
    commands = fuse_compare_jumps(commands, il_code)
    return commands

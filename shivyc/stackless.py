"""Stackless / low-overhead function calls.

This pass attacks the deeply-nested call pattern that jitbit and the
OpenSourceJesus C-Compiler use as their motivating benchmark
(foo -> bar -> boo -> zoo, each also calling a shared `sum`). Where the
flag-packing pass removed *memory* traffic for globals, this one removes
*call* overhead.

It applies three complementary, fully-correct transformations (opt-in via
`-fstackless-calls`):

1. **Direct calls.** ShivyC normally lowers a call to a named function as
   ``lea reg, [f]`` followed by ``call reg`` -- an extra instruction plus a
   clobbered register. When the callee is a statically known function we drop
   the address-load and emit a direct ``call f``.

2. **Tail-call elimination.** When a call is in tail position -- immediately
   followed by a return of that call's value (or both are void) -- the frame
   is torn down and the call becomes a ``jmp f``. The callee then returns
   straight to *our* caller, so no return address for this frame is ever
   pushed: this is the "stackless" core. ``foo() { sum(); }`` collapses to a
   single ``jmp sum``.

3. **Frame-pointer omission.** A function that needs no stack frame (no
   stack-resident locals) and makes no non-tail call needs no rbp prologue at
   all, removing the push/mov/pop dance and its memory traffic. (The final
   frameless decision also needs the stack-offset total, so it is completed in
   asm_gen; this pass supplies the call-structure half of the predicate.)

The transformations preserve call/return semantics exactly, so the program's
result is unchanged; only the instruction sequence is leaner. This is the
standard, safe realization of OSJ's "indexed-jump / metamorphic-return" goal
(returning without spending stack), achieved without self-modifying code.
"""

import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.value as value_cmds


def optimize(il_code, symbol_table, enabled=None, no_tail=None):
    """Apply the stackless-call transformations to `il_code` in place.

    `enabled` is a set of function names to optimize, or None to optimize all
    (the whole-program `-fstackless-calls` behavior). Per-function opt-in via
    the `__stackless__` specifier passes just those names. Functions not in the
    set are left completely untouched, so they keep ordinary calls and frames.

    `no_tail` is a set of callee names whose calls must never be turned into
    tail jumps -- used for metamorphic callees, which return to the call site.

    Annotates Call commands with `direct_name` (str) and `tail` (bool), and
    records per-function call-structure flags on `il_code.stackless_info`,
    which asm_gen combines with stack-offset data to decide framelessness.
    """
    no_tail = no_tail or set()
    info = {}
    for func_name in il_code.commands:
        if enabled is not None and func_name not in enabled:
            continue
        cmds = il_code.commands[func_name]
        cmds = _apply_direct_calls(cmds, symbol_table)
        cmds = _apply_tail_calls(cmds, no_tail)
        il_code.commands[func_name] = cmds

    for func_name in il_code.commands:
        if enabled is not None and func_name not in enabled:
            continue
        cmds = il_code.commands[func_name]
        # A "regular" call is a Call that was not turned into a tail jump.
        has_regular_call = any(
            isinstance(c, control_cmds.Call) and not getattr(c, "tail", False)
            for c in cmds)
        info[func_name] = {"no_regular_call": not has_regular_call}

    il_code.stackless_info = info
    return info


def _function_name(var, symbol_table):
    """Return the symbol name if `var` is a known function, else None."""
    ctype = getattr(var, "ctype", None)
    if ctype is None or not ctype.is_function():
        return None
    return symbol_table.names.get(var)


def _apply_direct_calls(cmds, symbol_table):
    """Fold ``AddrOf(p, f) ... Call(p, ...)`` into a direct call to ``f``.

    The AddrOf need not be adjacent to the Call (argument computation may sit
    between them). Folding is sound whenever the function-pointer value ``p`` is
    produced by an AddrOf of a known function and every use of ``p`` is as a
    Call's callee (never as an argument or any other operand): then ``p``'s
    value is never needed as data, each such Call can `call f` directly, and the
    AddrOf is dead. This intentionally handles a ``p`` shared by several call
    sites (e.g. a recursive helper called from many places) -- leaving those
    calls indirect would materialize ``f``'s address into a register that can
    collide with an argument's source register.
    """
    # AddrOf outputs that name a known function: output -> (addrof_cmd, name).
    candidates = {}
    for c in cmds:
        if isinstance(c, value_cmds.AddrOf):
            name = _function_name(c.var, symbol_table)
            if name is not None:
                candidates[c.output] = (c, name)

    # Classify every use of each candidate value. A use as a Call's callee is
    # foldable; any other use (a call argument, or any operand of a non-Call
    # command) makes the value unfoldable because its address is truly needed.
    unfoldable = set()
    targets = {}  # candidate output -> list of Call cmds using it as callee
    for c in cmds:
        if isinstance(c, control_cmds.Call) and not c.direct_name:
            for v in c.args:
                if v in candidates:
                    unfoldable.add(v)
            if c.func in candidates:
                targets.setdefault(c.func, []).append(c)
        else:
            for v in c.inputs():
                if v in candidates:
                    unfoldable.add(v)

    drop = set()
    for out, (addrof_cmd, name) in candidates.items():
        if out in unfoldable:
            continue
        calls = targets.get(out)
        if not calls:
            continue
        for call in calls:
            call.direct_name = name       # asm_gen emits `call name`
        drop.add(id(addrof_cmd))          # the AddrOf is now dead

    return [c for c in cmds if id(c) not in drop]


def _apply_tail_calls(cmds, no_tail=None):
    """Mark direct calls in tail position and drop the following Return.

    A Call at index i is a tail call when cmds[i+1] is a Return that returns
    exactly this call's value (or both are void). The call must be direct so it
    can be emitted as a plain ``jmp name``. Calls to names in `no_tail` (e.g.
    metamorphic callees, which return to the call site) are never tail-marked.
    """
    no_tail = no_tail or set()
    out = []
    i = 0
    n = len(cmds)
    while i < n:
        cmd = cmds[i]
        nxt = cmds[i + 1] if i + 1 < n else None
        if (isinstance(cmd, control_cmds.Call)
                and getattr(cmd, "direct_name", None)
                and cmd.direct_name not in no_tail
                and len(cmd.args) <= len(cmd.arg_regs)
                and isinstance(nxt, control_cmds.Return)):
            void_match = cmd.void_return and nxt.arg is None
            value_match = (not cmd.void_return) and nxt.arg is cmd.ret
            if void_match or value_match:
                cmd.tail = True      # asm_gen emits teardown + `jmp name`
                out.append(cmd)      # drop the Return; the callee returns
                i += 2               # straight to our caller
                continue
        out.append(cmd)
        i += 1
    return out

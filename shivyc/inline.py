"""Whole-program inlining of small, side-effect-free leaf functions.

Seeing every translation unit lets ShivyC inline a tiny callee defined in one
unit directly into a call site in another -- the canonical whole-program win,
since a single TU never has the callee's body. Bodies are captured while the
whole-program call graph is built (every TU is parsed and lowered there anyway)
and spliced into callers as a later IL pass.

An inlinable function must be a *leaf* (no calls) that touches no memory or
globals -- only its parameters, locals, and integer literals (any reference to
a value with static storage duration disqualifies it). It may, however, use
internal control flow: comparisons, conditional/unconditional jumps, labels,
and several return statements. Such a body is a pure function of its arguments,
so splicing it cannot change observable behavior; it only removes the call.

Splicing binds each parameter to a fresh copy of the corresponding argument (so
a callee that reassigns a parameter cannot clobber the caller's value, and an
argument expression is evaluated exactly once), clones the body with every
callee ILValue remapped to a fresh one (literals re-registered in the caller)
and every label remapped to a fresh label (so copies never collide), and routes
each return through a single fresh end label that assigns the call's result.
"""

import copy

import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.value as value_cmds
import shivyc.il_cmds.math as math_cmds
import shivyc.il_cmds.compare as compare_cmds
from shivyc.il_gen import ILValue

#: Value-producing commands permitted in an inlinable body. Anything that
#: touches memory (ReadAt/SetAt/AddrOf/...) or calls is excluded.
_VALUE_CMDS = (
    value_cmds.Set,
    math_cmds.Add, math_cmds.Subtr, math_cmds.Mult,
    math_cmds.Div, math_cmds.Mod,
    math_cmds.RBitShift, math_cmds.LBitShift,
    math_cmds.Neg, math_cmds.Not,
    math_cmds.BitAnd, math_cmds.BitOr, math_cmds.BitXor,
    compare_cmds._GeneralCmp,
)

#: Control-flow commands permitted in an inlinable body.
_CTRL_CMDS = (control_cmds.Label, control_cmds.Jump, control_cmds._GeneralJump)

#: Maximum number of body commands (excluding LoadArg) in an inlinable body.
_MAX_OPS = 24


class InlineBody:
    """A captured inlinable function body."""

    def __init__(self, param_values, body):
        self.param_values = param_values   # callee ILValues, by arg index
        self.body = body                   # body commands (no LoadArg)


def _cmd_ilvalues(cmd):
    """Return the ILValues referenced by `cmd` (via its attributes)."""
    out = []
    for v in vars(cmd).values():
        if isinstance(v, ILValue):
            out.append(v)
        elif (isinstance(v, list) and v
              and all(isinstance(x, ILValue) for x in v)):
            out.extend(v)
    return out


def _cmd_label(cmd):
    """Return the label string referenced by `cmd`, or None."""
    lab = getattr(cmd, "label", None)
    return lab if isinstance(lab, str) else None


def capture(cmds, symbol_table):
    """Return an InlineBody for `cmds` if the function is inlinable, else None.
    """
    if not cmds:
        return None

    # A value with static storage duration (a global or a function-local
    # static) has a fixed memory home that inlining would silently break.
    static_vals = {v for v, st in symbol_table.storage.items()
                   if st == symbol_table.STATIC}

    params = {}        # arg index -> callee ILValue
    body = []
    saw_return = False
    for c in cmds:
        if isinstance(c, value_cmds.LoadArg):
            if c.arg_reg is None:  # stack-passed (7th+ arg or variadic)
                return None
            params[c.arg_num] = c.output
            continue
        if isinstance(c, control_cmds.Return):
            if c.arg is None:      # void return: nothing to substitute
                return None
            saw_return = True
        elif not isinstance(c, _VALUE_CMDS + _CTRL_CMDS):
            return None            # call, memory op, etc.
        body.append(c)
        for v in _cmd_ilvalues(c):
            if v in static_vals:
                return None        # references a global / static object

    if not saw_return or len(body) > _MAX_OPS:
        return None
    n = len(params)
    if sorted(params) != list(range(n)):    # params must be 0..n-1
        return None
    return InlineBody([params[i] for i in range(n)], body)


def _clone(cmd, remap, label_map):
    """Shallow-copy `cmd`, remapping ILValue attributes and the label string."""
    nc = copy.copy(cmd)
    for k, v in list(vars(nc).items()):
        if isinstance(v, ILValue):
            setattr(nc, k, remap.get(v, v))
        elif (isinstance(v, list) and v
              and all(isinstance(x, ILValue) for x in v)):
            setattr(nc, k, [remap.get(x, x) for x in v])
        elif k == "label" and isinstance(v, str) and v in label_map:
            setattr(nc, k, label_map[v])
    return nc


def inline_calls(cmds, inlinable, il_code):
    """Return (cmds, changed) with direct calls to inlinable functions spliced.

    `inlinable` maps function name -> InlineBody. `il_code` is the *caller's*
    ILCode (used to register literals and mint fresh labels).
    """
    out = []
    changed = False
    for c in cmds:
        body = None
        if (isinstance(c, control_cmds.Call) and c.direct_name in inlinable
                and not c.void_return):
            body = inlinable[c.direct_name]
            if len(c.args) != len(body.param_values):
                body = None        # arity mismatch: leave the call alone

        if body is None:
            out.append(c)
            continue

        remap = {}
        # Bind each parameter to a fresh copy of the argument.
        for i, pv in enumerate(body.param_values):
            fresh = ILValue(pv.ctype)
            remap[pv] = fresh
            out.append(value_cmds.Set(fresh, c.args[i]))

        # Fresh ILValues for every other callee value, re-registering literals.
        def fresh_for(v):
            if v in remap:
                return
            nv = ILValue(v.ctype)
            remap[v] = nv
            if v.literal is not None:
                # Re-register the literal under the fresh value. Float literals
                # must go through the float-literal path so they are emitted as
                # data and loaded from memory: an SSE instruction cannot take an
                # immediate operand, so giving a float an integer LiteralSpot
                # produces `movsd xmm0, <float>`, which the assembler rejects.
                if v.ctype.is_floating():
                    il_code.register_float_literal(nv, v.literal.val)
                else:
                    il_code.register_literal_var(nv, v.literal.val)

        # Fresh label for every label in the body, plus one end label that all
        # returns funnel through.
        label_map = {}
        for op in body.body:
            for v in _cmd_ilvalues(op):
                fresh_for(v)
            lab = _cmd_label(op)
            if lab is not None and lab not in label_map:
                label_map[lab] = il_code.get_label()
        end_label = il_code.get_label()

        for op in body.body:
            if isinstance(op, control_cmds.Return):
                out.append(value_cmds.Set(c.ret, remap[op.arg]))
                out.append(control_cmds.Jump(end_label))
            else:
                out.append(_clone(op, remap, label_map))
        out.append(control_cmds.Label(end_label))
        changed = True

    return out, changed

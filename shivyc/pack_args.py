"""Argument packing: a non-standard calling convention (-f-pack-args).

When several small integer parameters would each consume a whole argument
register (or spill to the stack) under the System V AMD64 ABI, we instead pack
them by bit-offset into as few 64-bit registers as possible. The caller builds
each packed register with shifts and ORs; the callee unpacks each field back
into its parameter home. For e.g. ``int foo(char a, ..., char i)`` the nine
one-byte parameters occupy two registers (eight bytes in the first, one in the
second) instead of six registers plus three stack slots.

Because this convention is shared between caller and callee, both sides compute
the identical layout here from the function *signature* alone. The optimization
is only applied to statically-known (direct) calls of qualifying functions; a
qualifying function must therefore not be called indirectly or have its address
taken (see the whole-program guard in ``pack_args_guard``).
"""

import shivyc.spots as spots

# Same registers as the ordinary integer argument sequence; under packing each
# may carry several parameters rather than one.
ARG_REGS = [spots.RDI, spots.RSI, spots.RDX, spots.RCX, spots.R8, spots.R9]
REG_BITS = 64


class PackField:
    """One parameter's placement within the packed registers.

    arg_index  - positional index of the parameter
    reg_index  - which packed register (index into ARG_REGS)
    bit_offset - low bit of this field within that register
    size       - parameter size in bytes (1, 2, 4, or 8)
    signed     - whether the parameter type is signed (informational)
    """

    def __init__(self, arg_index, reg_index, bit_offset, size, signed):
        self.arg_index = arg_index
        self.reg_index = reg_index
        self.bit_offset = bit_offset
        self.size = size
        self.signed = signed


def _qualifies(ctype):
    """A parameter qualifies if it is a scalar integer/pointer of <= 8 bytes."""
    if ctype.is_floating():
        return False
    if ctype.is_struct_union():
        return False
    if ctype.size not in (1, 2, 4, 8):
        return False
    return True


def pack_plan(arg_ctypes, variadic=False):
    """Compute the packing layout for a parameter-type list.

    Returns (fields, num_regs) where ``fields`` is a list of PackField, or
    ``None`` if the signature does not qualify for packing (in which case the
    ordinary ABI is used unchanged).
    """
    if variadic or not arg_ctypes:
        return None
    if not all(_qualifies(c) for c in arg_ctypes):
        return None

    # Packing only helps when it uses strictly fewer registers than the
    # ordinary convention would (which is min(nargs, 6) integer registers).
    fields = []
    reg_index = 0
    bit_offset = 0
    for i, ctype in enumerate(arg_ctypes):
        width = ctype.size * 8
        if bit_offset + width > REG_BITS:
            reg_index += 1
            bit_offset = 0
        if reg_index >= len(ARG_REGS):
            return None  # would need more than six registers; do not pack
        signed = bool(getattr(ctype, "signed", False))
        fields.append(PackField(i, reg_index, bit_offset, ctype.size, signed))
        bit_offset += width

    num_regs = reg_index + 1
    ordinary_int_regs = min(len(arg_ctypes), len(ARG_REGS))
    if num_regs >= ordinary_int_regs:
        return None  # no register saving; leave the ABI alone
    return fields, num_regs


def regs_used(num_regs):
    """The concrete argument registers a plan of ``num_regs`` registers uses."""
    return ARG_REGS[:num_regs]


def _address_taken(il_code, symbol_table):
    """Names of functions whose address is taken (post direct-call folding).

    After ``stackless._apply_direct_calls`` has folded ``AddrOf(f) ... Call``
    into direct calls and dropped those AddrOfs, any remaining AddrOf naming a
    function means the function escapes as a value (stored, passed, compared),
    so it may be reached through a pointer with the ordinary ABI and must not be
    packed. We also scan static initializers, where a function-pointer global
    (e.g. ``int (*fp)(...) = g;``) records the address as a ``('sym', name, n)``
    reference rather than an IL AddrOf.
    """
    import shivyc.il_cmds.value as value_cmds
    import shivyc.stackless as stackless

    func_names = {n for v, n in symbol_table.names.items()
                  if getattr(getattr(v, "ctype", None), "is_function", None)
                  and v.ctype.is_function()}

    taken = set()
    for cmds in il_code.commands.values():
        for c in cmds:
            if isinstance(c, value_cmds.AddrOf):
                name = stackless._function_name(c.var, symbol_table)
                if name is not None:
                    taken.add(name)

    # Walk static initializers for symbol references to functions.
    def walk(obj):
        if isinstance(obj, tuple):
            if (len(obj) >= 2 and obj[0] == "sym" and obj[1] in func_names):
                taken.add(obj[1])
            for item in obj:
                walk(item)
        elif isinstance(obj, (list, set)):
            for item in obj:
                walk(item)

    walk(list(getattr(il_code, "static_inits", {}).values()))
    for entries, _total in getattr(il_code, "static_block_inits", {}).values():
        walk(entries)

    return taken


def optimize(il_code, symbol_table):
    """Apply the -f-pack-args calling convention, in place.

    Requires that direct calls have already been folded (``direct_name`` set)
    by ``stackless._apply_direct_calls``. For every function whose signature
    qualifies and that is pack-safe -- not ``main`` and not address-taken --
    the prologue's per-argument ``LoadArg`` commands are replaced by a single
    ``UnpackArgs``, and every direct call to it is annotated with ``pack`` so
    the caller emits the packed register build-up.
    """
    import shivyc.il_cmds.control as control_cmds
    import shivyc.il_cmds.value as value_cmds

    addr_taken = _address_taken(il_code, symbol_table)

    plans = {}  # func_name -> (outs, regs, fields, loadarg_ids)
    for func_name, cmds in il_code.commands.items():
        if func_name == "main" or func_name in addr_taken:
            continue
        loadargs, struct_param = [], False
        for c in cmds:
            if isinstance(c, value_cmds.LoadArg):
                loadargs.append(c)
            elif isinstance(c, value_cmds.LoadStructArg):
                struct_param = True
                break
            else:
                break  # the prologue ends at the first non-LoadArg command
        if struct_param or not loadargs:
            continue
        loadargs.sort(key=lambda c: c.arg_num)
        outs = [c.output for c in loadargs]
        plan = pack_plan([o.ctype for o in outs])
        if plan is None:
            continue
        fields, num_regs = plan
        plans[func_name] = (outs, regs_used(num_regs), fields,
                            {id(c) for c in loadargs})

    if not plans:
        return set()

    # Rewrite callee prologues: drop the per-arg LoadArgs, prepend one UnpackArgs.
    for func_name, (outs, regs, fields, la_ids) in plans.items():
        cmds = il_code.commands[func_name]
        unpack = value_cmds.UnpackArgs(outs, regs, fields)
        il_code.commands[func_name] = (
            [unpack] + [c for c in cmds if id(c) not in la_ids])

    # Annotate direct calls to packable callees so the caller packs.
    for cmds in il_code.commands.values():
        for c in cmds:
            if (isinstance(c, control_cmds.Call) and c.direct_name in plans):
                _outs, regs, fields, _ = plans[c.direct_name]
                c.pack = (regs, fields)

    return set(plans)

"""Dead-function elimination for unreachable internal-linkage functions.

Whole-program inlining frequently leaves a small ``static`` helper with no
remaining callers: every direct call was spliced into the caller. Such a
function is dead and can be dropped from the output.

Only *internal-linkage* (``static``) functions are eliminated, and this is what
makes the pass unconditionally sound without a closed-world assumption: a static
function is visible only inside its own translation unit, so every way of
reaching it -- a direct call, having its address taken, being named in a static
initializer, or being referenced from an inline-asm template -- must also live
in that same unit. The analysis therefore needs only the one TU in front of it.

A function with external linkage is always kept: another translation unit (or a
library linked later) may call it, and the compiler cannot prove otherwise from
one unit. ``main`` is external, so it is always a root.

The pass runs after inlining, so the call edges it sees already reflect the
calls that inlining removed.
"""


import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.value as value_cmds
import shivyc.il_cmds.asm as asm_cmds


def _addr_taken(il_code, names_by_val, defined):
    """Functions whose address is taken via an IL AddrOf in this unit."""
    out = set()
    for cmds in il_code.commands.values():
        for c in cmds:
            if isinstance(c, value_cmds.AddrOf):
                v = getattr(c, "var", None)
                target = names_by_val.get(v)
                if target in defined:
                    ct = getattr(v, "ctype", None)
                    if ct is not None and ct.is_function():
                        out.add(target)
    return out


def _static_init_refs(il_code, defined):
    """Functions whose address is embedded in a static initializer.

    Function-address initializers are stored as ``("sym", name, addend)``
    entries in ``static_block_inits`` (and, defensively, ``static_inits``).
    """
    out = set()

    def note(val):
        if (isinstance(val, tuple) and len(val) == 3 and val[0] == "sym"
                and val[1] in defined):
            out.add(val[1])

    for val in il_code.static_inits.values():
        note(val)
    for entries, _total in il_code.static_block_inits.values():
        for _off, _size, val in entries:
            note(val)
    return out


def _asm_refs(il_code, defined):
    """Functions named (as a whole token) in any inline-asm template.

    Conservative: a static function referenced only from hand-written assembly
    has no IL call or AddrOf, so without this it would look dead.
    """
    templates = []
    for cmds in il_code.commands.values():
        for c in cmds:
            if isinstance(c, asm_cmds.InlineAsm):
                templates.append(c.template)
    if not templates:
        return set()
    text = "\n".join(templates)
    return {fn for fn in defined if _word_in(fn, text)}


def _is_word_char(ch):
    return (ch == '_' or ('a' <= ch <= 'z') or ('A' <= ch <= 'Z')
            or ('0' <= ch <= '9'))


def _word_in(needle, hay):
    """True if `needle` occurs in `hay` as a whole token (word boundaries on
    both sides). Plain-string implementation so it transpiles to C; replaces a
    `re.search(r"\\b" + re.escape(fn) + r"\\b", ...)`."""
    n = len(needle)
    if n == 0:
        return False
    start = 0
    while True:
        idx = hay.find(needle, start)
        if idx < 0:
            return False
        before_ok = idx == 0 or not _is_word_char(hay[idx - 1])
        after = idx + n
        after_ok = after >= len(hay) or not _is_word_char(hay[after])
        if before_ok and after_ok:
            return True
        start = idx + 1


def eliminate_dead_functions(il_code, symbol_table):
    """Remove unreachable static functions from `il_code`. Returns the set
    removed.
    """
    names_by_val = {v: n for v, n in symbol_table.names.items()}
    defined = set(il_code.commands)

    def is_static(fn):
        v = symbol_table.linkages[symbol_table.INTERNAL].get(fn)
        return v is not None and fn in defined

    # Roots: everything that could be entered from outside the static call
    # graph -- external functions (incl. main), address-taken functions, and
    # functions referenced by static initializers or inline asm.
    roots = {fn for fn in defined if not is_static(fn)}
    roots |= _addr_taken(il_code, names_by_val, defined)
    roots |= _static_init_refs(il_code, defined)
    roots |= _asm_refs(il_code, defined)

    # Direct-call edges within this unit (post-inlining).
    edges = {}
    for fn, cmds in il_code.commands.items():
        callees = set()
        for c in cmds:
            if isinstance(c, control_cmds.Call) and c.direct_name:
                callees.add(c.direct_name)
        edges[fn] = callees

    # Mark everything reachable from a root.
    reachable = set(roots)
    work = list(roots)
    while work:
        cur = work.pop()
        for callee in edges.get(cur, ()):
            if callee not in reachable:
                reachable.add(callee)
                work.append(callee)

    dead = {fn for fn in defined if is_static(fn) and fn not in reachable}
    for fn in dead:
        del il_code.commands[fn]
    return dead

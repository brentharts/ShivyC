"""Contract-driven, fallback-free SIMD.

The language-extension front-end (shivyc/extensions.py) attaches `assert`-style
contracts to a function's array arguments, e.g. for

    int calc_sum(int *ptr, unsigned int len)
    assert len(ptr) >= 64
    assert not len(ptr) % 4

it records `{'ptr': {'len>=': 64, 'div-by': 4}}`.

GCC and Clang, when they auto-vectorize a reduction like `for(i) v += ptr[i]`,
must emit a scalar remainder loop (and sometimes runtime alignment checks) for
the cases where the length is not a multiple of the SIMD width. Those branches
are the "fallback".

When the compiler can *see the whole call graph* and prove that every call
satisfies the contracts -- for instance by parsing `malloc(N * sizeof(int))` at
the one call site -- the remainder can never run, so it can be omitted. This
module performs that proof and, for a recognized sum-reduction, emits a
fallback-free SSE2 loop (4x int32 per iteration, no scalar tail).

This is a deliberately narrow, verifiable slice: it vectorizes integer sum
reductions whose alignment is *proven*, and otherwise leaves ShivyC's ordinary
scalar codegen untouched.
"""

import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.value as value_cmds
import shivyc.il_cmds.math as math_cmds
import shivyc.asm_cmds as asm_cmds
import shivyc.spots as spots


class ProofResult:
    """Outcome of proving a contract function's call sites."""

    def __init__(self, name):
        self.name = name
        self.call_sites = 0
        self.proven = False
        self.reason = ""

    def __str__(self):
        if self.proven:
            return (f"simd-contracts: '{self.name}': contracts proven at all "
                    f"{self.call_sites} call site(s); scalar fallback omitted")
        return (f"simd-contracts: '{self.name}': not proven "
                f"({self.reason}); keeping scalar code")


def analyze(il_code, symbol_table, ext_info):
    """Prove contracts across the call graph.

    Returns a set of function names that are proven SIMD-safe (every call site
    satisfies the contracts) AND whose body is a recognized reduction, so
    asm_gen may emit the fallback-free SSE2 form. Also returns a list of
    human-readable ProofResult reports.
    """
    proven = {}
    reports = []
    if not ext_info or not ext_info.contracts:
        return proven, reports

    name_of = _build_function_names(il_code, symbol_table)

    for fname, arg_contracts in ext_info.contracts.items():
        result = ProofResult(fname)
        if fname not in il_code.commands:
            result.reason = "no definition"
            reports.append(result)
            continue

        layout = _arg_layout(fname, symbol_table)
        ptrs = [a for a in layout if a["is_ptr"]]
        ints = [a for a in layout if a["is_int"]]
        if not ptrs:
            result.reason = "no pointer argument with a contract"
            reports.append(result)
            continue
        len_index = ints[0]["index"] if ints else None
        if len_index is None:
            result.reason = "no length argument"
            reports.append(result)
            continue

        contract = _merge_contracts(arg_contracts)
        sites = _find_call_sites(il_code, name_of, fname)
        result.call_sites = len(sites)
        if not sites:
            result.reason = "no call sites visible"
            reports.append(result)
            continue

        all_ok = True
        for caller, call in sites:
            count = _prove_one_call_multi(
                il_code, name_of, caller, call, ptrs, len_index)
            if count is None or not _satisfies(count, contract):
                all_ok = False
                result.reason = "a call site could not be proven aligned"
                break
        if not all_ok:
            reports.append(result)
            continue

        cmds = il_code.commands[fname]
        if _is_sum_reduction(cmds):
            desc = {"kind": "reduce"}
        else:
            desc = _classify_elementwise(cmds, ptrs)
        if desc:
            result.proven = True
            proven[fname] = desc
        else:
            result.reason = "alignment proven but body is not a recognized kernel"

        reports.append(result)

    return proven, reports


def _satisfies(count, contract):
    """Check a proven element count against a contract dict."""
    if "len>=" in contract and count < contract["len>="]:
        return False
    if "len<=" in contract and count > contract["len<="]:
        return False
    if "div-by" in contract and count % contract["div-by"] != 0:
        return False
    return True


def _build_function_names(il_code, symbol_table):
    """Map each function ILValue to its name (for resolving call targets)."""
    names = {}
    for val, name in symbol_table.names.items():
        ctype = getattr(val, "ctype", None)
        if ctype is not None and ctype.is_function():
            names[val] = name
    return names


def _arg_layout(fname, symbol_table):
    """Per-argument shape for `fname`: index, whether it is a pointer (and its
    element size), or an integer/float scalar. Drives the multi-array proof."""
    func_val = None
    for val, name in symbol_table.names.items():
        ctype = getattr(val, "ctype", None)
        if name == fname and ctype is not None and ctype.is_function():
            func_val = val
            break
    if func_val is None:
        return []
    layout = []
    for i, at in enumerate(func_val.ctype.args):
        is_ptr = at.is_pointer()
        layout.append({
            "index": i,
            "is_ptr": is_ptr,
            "elem_size": (at.arg.size if is_ptr else None),
            "is_int": (not is_ptr) and at.is_integral(),
            "is_float": (not is_ptr) and at.is_floating(),
        })
    return layout


def _merge_contracts(arg_contracts):
    """Combine per-argument contracts into the single strongest constraint."""
    merged = {}
    for c in arg_contracts.values():
        if "div-by" in c:
            merged["div-by"] = max(merged.get("div-by", 1), c["div-by"])
        if "len>=" in c:
            merged["len>="] = max(merged.get("len>=", 0), c["len>="])
        if "len<=" in c:
            merged["len<="] = min(merged.get("len<=", 1 << 62), c["len<="])
    return merged


def _prove_one_call_multi(il_code, name_of, caller, call, ptrs, len_index):
    """Prove a call where the kernel has several pointer arguments and one
    length argument (e.g. saxpy(alpha, x, y, out, n)). Every pointer must trace
    to a malloc whose element count is at least the literal length. Returns the
    proven element count (the length) or None."""
    cmds = il_code.commands[caller]
    if len_index is None or len_index >= len(call.args):
        return None
    length = _trace_literal(il_code, cmds, call.args[len_index])
    if length is None:
        return None
    for p in ptrs:
        if p["index"] >= len(call.args):
            return None
        byte_size = _trace_malloc_bytes(
            il_code, name_of, cmds, call.args[p["index"]])
        if byte_size is None:
            return None
        if length > byte_size // p["elem_size"]:
            return None                 # would read/write out of bounds
    return length


def _classify_elementwise(cmds, ptrs):
    """Recognize a float element-wise store kernel and return a descriptor:

        out[i] = a[i] + b[i]        -> {kind: binary, op: add}
        out[i] = a[i] * b[i]        -> {kind: binary, op: mul}
        out[i] = a[i] - b[i]        -> {kind: binary, op: sub}
        out[i] = alpha*x[i] + y[i]  -> {kind: saxpy,  op: add}

    Conservative: exactly three pointer args, one store, two loads, and a
    floating element type. None if the body is not one of these shapes."""
    if len(ptrs) != 3:
        return None
    defmap = {}
    for c in cmds:
        for o in c.outputs():
            defmap[o] = c
    reads = [c for c in cmds if isinstance(c, value_cmds.ReadAt)]
    stores = [c for c in cmds if isinstance(c, value_cmds.SetAt)]
    if len(stores) != 1 or len(reads) != 2:
        return None
    store = stores[0]
    if not store.val.ctype.is_floating():
        return None
    elem_size = store.val.ctype.size
    read_outs = {r.output for r in reads}

    def origin(v):
        d = defmap.get(v)
        while isinstance(d, value_cmds.Set):
            v = d.arg
            d = defmap.get(v)
        return d

    def is_load(v):
        d = defmap.get(v)
        while isinstance(d, value_cmds.Set):
            v = d.arg
            d = defmap.get(v)
        return v in read_outs

    d = origin(store.val)
    if isinstance(d, math_cmds.Add):
        a, b = d.arg1, d.arg2
        for x, y in ((a, b), (b, a)):       # saxpy: one side is scalar*load
            dx = origin(x)
            if isinstance(dx, math_cmds.Mult) and \
                    (is_load(dx.arg1) or is_load(dx.arg2)) and is_load(y):
                return {"kind": "saxpy", "op": "add", "elem_size": elem_size}
        if is_load(a) and is_load(b):
            return {"kind": "binary", "op": "add", "elem_size": elem_size}
    elif isinstance(d, math_cmds.Mult):
        if is_load(d.arg1) and is_load(d.arg2):
            return {"kind": "binary", "op": "mul", "elem_size": elem_size}
    elif isinstance(d, math_cmds.Subtr):
        if is_load(d.arg1) and is_load(d.arg2):
            return {"kind": "binary", "op": "sub", "elem_size": elem_size}
    return None


def _pointer_arg_info(fname, symbol_table):
    """Return (element_size, arg_index) for the function's pointer arg."""
    func_val = None
    for val, name in symbol_table.names.items():
        if name == fname and val.ctype.is_function():
            func_val = val
            break
    if func_val is None:
        return None, None
    for i, arg_t in enumerate(func_val.ctype.args):
        if arg_t.is_pointer():
            return arg_t.arg.size, i
    return None, None


def _find_call_sites(il_code, name_of, target):
    """Return [(caller_name, Call), ...] calling `target`."""
    sites = []
    for caller, cmds in il_code.commands.items():
        addr_of = {}  # ptr ILValue -> function name it addresses
        for c in cmds:
            if isinstance(c, value_cmds.AddrOf) and c.var in name_of:
                addr_of[c.output] = name_of[c.var]
            elif isinstance(c, control_cmds.Call):
                tgt = getattr(c, "direct_name", None) or addr_of.get(c.func)
                if tgt == target:
                    sites.append((caller, c))
    return sites


def _prove_one_call(il_code, name_of, caller, call, arg_index, elem_size):
    """Return the proven element count for `call`, or None if unprovable."""
    cmds = il_code.commands[caller]

    # Resolve the pointer argument to a malloc(byte_size) with literal size.
    ptr_val = call.args[arg_index]
    byte_size = _trace_malloc_bytes(il_code, name_of, cmds, ptr_val)
    if byte_size is None:
        return None
    count_from_alloc = byte_size // elem_size

    # The length argument must be a literal that does not exceed the allocation.
    len_val = call.args[1 - arg_index] if len(call.args) > 1 else None
    length = _trace_literal(il_code, cmds, len_val) if len_val else None
    if length is None:
        return None
    if length > count_from_alloc:
        return None  # would read out of bounds; not safe
    return length


def _defs(cmds):
    """Map each ILValue to the command that defines (outputs) it."""
    d = {}
    for c in cmds:
        for o in c.outputs():
            d[o] = c
    return d


def _trace_literal(il_code, cmds, val, depth=0):
    """Follow Set-copies to an integer literal value, or None."""
    if val is None or depth > 16:
        return None
    if val in il_code.literals:
        return il_code.literals[val]
    defn = _defs(cmds).get(val)
    if isinstance(defn, value_cmds.Set):
        return _trace_literal(il_code, cmds, defn.arg, depth + 1)
    return None


def _trace_malloc_bytes(il_code, name_of, cmds, val, depth=0):
    """Follow Set-copies to a malloc() call; return its literal byte size."""
    if val is None or depth > 16:
        return None
    defs = _defs(cmds)
    defn = defs.get(val)
    if isinstance(defn, value_cmds.Set):
        return _trace_malloc_bytes(il_code, name_of, cmds, defn.arg, depth + 1)
    if isinstance(defn, control_cmds.Call):
        addr_of = {}
        for c in cmds:
            if isinstance(c, value_cmds.AddrOf) and c.var in name_of:
                addr_of[c.output] = name_of[c.var]
        tgt = getattr(defn, "direct_name", None) or addr_of.get(defn.func)
        if tgt == "malloc" and defn.args:
            return _trace_literal(il_code, cmds, defn.args[0])
    return None


def _is_sum_reduction(cmds):
    """Conservatively recognize `acc = acc + ptr[i]` over a loop.

    ShivyC lowers `v = v + ptr[i]` as `Add(T, v, load); Set(v, T)`, so the
    accumulator cycle is: a ReadAt loads a value, an Add combines it with some
    value `acc`, and a Set writes the Add's result back into that same `acc`.
    """
    read_outputs = set()
    for c in cmds:
        if isinstance(c, value_cmds.ReadAt):
            read_outputs.add(c.output)

    # Set arg -> output, to find what an Add result is copied into.
    set_targets = {}
    for c in cmds:
        if isinstance(c, value_cmds.Set):
            set_targets.setdefault(c.arg, []).append(c.output)

    for c in cmds:
        if not isinstance(c, math_cmds.Add):
            continue
        ins = c.inputs()
        loads = [i for i in ins if i in read_outputs]
        others = [i for i in ins if i not in read_outputs]
        if not loads or not others:
            continue
        # The Add result must be written back into one of its non-load inputs
        # (the accumulator).
        for acc in others:
            if acc in set_targets.get(c.output, []):
                return True
    return False


# --- SSE2 synthesis -------------------------------------------------------

def synth_sse2_reduce(asm_code, func_ctype):
    """Emit a fallback-free SSE2 int32 sum reduction for (int* ptr, len).

    System V: rdi = ptr, esi = len (a multiple of 4 by contract). The result is
    returned in eax. No scalar remainder loop is emitted -- that is the whole
    point: the contract proof guarantees len % 4 == 0.
    """
    loop = asm_code.get_label()
    raw = [
        "push rbp",
        "mov rbp, rsp",
        "pxor xmm0, xmm0",      # 4-lane int32 accumulator
        "mov ecx, esi",         # ecx = len
        "shr ecx, 2",           # ecx = len / 4  (groups of 4 ints)
        "xor rax, rax",         # rax = byte offset
        loop + ":",
        "movdqu xmm1, [rdi + rax]",
        "paddd xmm0, xmm1",
        "add rax, 16",
        "dec ecx",
        "jnz " + loop,
        # horizontal add of the 4 lanes -> eax
        "movdqa xmm1, xmm0",
        "psrldq xmm1, 8",
        "paddd xmm0, xmm1",
        "movdqa xmm1, xmm0",
        "psrldq xmm1, 4",
        "paddd xmm0, xmm1",
        "movd eax, xmm0",
        "mov rsp, rbp",
        "pop rbp",
        "ret",
    ]
    for line in raw:
        asm_code.add(asm_cmds.Raw(line))


def synth_sse_elementwise(asm_code, desc):
    """Emit a fallback-free packed-SSE element-wise kernel.

    Supported System V shapes (matching what tools/py2c.py emits):

        binary: void f(T* a, T* b, T* out, int n)   a=rdi b=rsi out=rdx n=ecx
                out[i] = a[i] {add,sub,mul} b[i]
        saxpy : void f(T a, T* x, T* y, T* out, int n)  a=xmm0 x=rdi y=rsi
                out[i] = a*x[i] + y[i]                   out=rdx n=ecx

    T is float (4 lanes/128 bits) or double (2 lanes). The contract proof
    guarantees n is a multiple of the lane count, so there is no scalar tail.
    """
    elem = desc["elem_size"]
    if elem == 4:
        mov, suf, shift, bcast = "movups", "ps", 2, "shufps xmm0, xmm0, 0"
    else:
        mov, suf, shift, bcast = "movupd", "pd", 1, "unpcklpd xmm0, xmm0"
    loop = asm_code.get_label()
    raw = ["push rbp", "mov rbp, rsp"]
    if desc["kind"] == "saxpy":
        raw.append(bcast)                       # xmm0 = broadcast scalar a
    raw += ["mov r8d, ecx",                     # r8d = n
            "shr r8d, %d" % shift,              # r8d = n / lanes
            "xor rax, rax",                     # rax = byte offset
            loop + ":"]
    if desc["kind"] == "saxpy":
        raw += [
            "%s xmm1, [rdi + rax]" % mov,       # x[i..]
            "mul%s xmm1, xmm0" % suf,           # a * x
            "%s xmm2, [rsi + rax]" % mov,       # y[i..]
            "add%s xmm1, xmm2" % suf,           # + y
            "%s [rdx + rax], xmm1" % mov,       # -> out
        ]
    else:
        raw += [
            "%s xmm1, [rdi + rax]" % mov,       # a[i..]
            "%s xmm2, [rsi + rax]" % mov,       # b[i..]
            "%s%s xmm1, xmm2" % (desc["op"], suf),
            "%s [rdx + rax], xmm1" % mov,       # -> out
        ]
    raw += ["add rax, 16", "dec r8d", "jnz " + loop,
            "mov rsp, rbp", "pop rbp", "ret"]
    for line in raw:
        asm_code.add(asm_cmds.Raw(line))

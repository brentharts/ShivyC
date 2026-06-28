"""minipy.interp -- the rpython interpreter, compiled to C by py2c.

Reads a flattened-bytecode JSON file (produced by minipy.compiler /
rpy.py2json_bytecode), decodes it into POD structs via rpy.json.generate_decoder,
and runs the register dispatch loop. Built and invoked by the rpy.py driver as
backend="native"; runs untranslated under CPython too, so it doubles as a check
on the format and matches the pure-Python reference VM.

v0 standup: scalar values only (None/int/float/str/bool + function/builtin
references). `for x in range(...)` is already lowered to a counter loop by the
compiler, so no list value is needed yet; lists/dicts/strings-as-objects come
next. Opcode numbers and the builtin table mirror minipy/compiler.py exactly.
"""
import sys
import json
import rpy


# ====================== JSON-decoded POD structs ======================
# Field names/order match the JSON emitted by minipy.compiler.to_program so the
# generated cursor decoder fills these directly.
class Const:
    def __init__(self, t: "char*", i: "long", d: "double", s: "char*"):
        self.t = t
        self.i = i
        self.d = d
        self.s = s


class Instr:
    def __init__(self, op: "int", a: "int", b: "int", c: "int"):
        self.op = op
        self.a = a
        self.b = b
        self.c = c


class Func:
    def __init__(self, name: "char*", nparams: "int", nregs: "int",
                 code: "list[Instr]"):
        self.name = name
        self.nparams = nparams
        self.nregs = nregs
        self.code = code


class Program:
    def __init__(self, version: "int", source: "char*",
                 consts: "list[Const]", names: "list[char*]",
                 nglobals: "int", funcs: "list[Func]", entry: "int"):
        self.version = version
        self.source = source
        self.consts = consts
        self.names = names
        self.nglobals = nglobals
        self.funcs = funcs
        self.entry = entry


# ====================== runtime value (tagged box) ======================
# Scalar-only for v0. tag: 0 none, 1 int, 2 float, 3 str, 4 bool, 5 func, 6
# builtin. `i` holds int/bool, or a function index, or a builtin id.
class V:
    def __init__(self, tag: "int", iv: "long", dv: "double", sv: "char*"):
        self.tag = tag
        self.iv = iv
        self.dv = dv
        self.sv = sv


def v_none() -> "V":
    return V(0, 0, 0.0, "")


def v_int(n: "long") -> "V":
    return V(1, n, 0.0, "")


def v_float(x: "double") -> "V":
    return V(2, 0, x, "")


def v_str(t: "char*") -> "V":
    return V(3, 0, 0.0, t)


def v_bool(b: "int") -> "V":
    return V(4, 1 if b else 0, 0.0, "")


def v_func(idx: "long") -> "V":
    return V(5, idx, 0.0, "")


def v_builtin(bid: "long") -> "V":
    return V(6, bid, 0.0, "")


def new_v_list() -> "list[V]":
    r = []
    return r


# ---- coercions / display ----
def to_float(v: "V") -> "double":
    if v.tag == 2:
        return v.dv
    return float(v.iv)


def to_int(v: "V") -> "long":
    if v.tag == 2:
        return int(v.dv)
    return v.iv


def to_display(v: "V") -> "char*":
    if v.tag == 1:
        return str(v.iv)
    if v.tag == 2:
        d = v.dv
        asint = int(d)
        if float(asint) == d:
            return str(asint) + ".0"
        return str(d)
    if v.tag == 3:
        return v.sv
    if v.tag == 4:
        if v.iv != 0:
            return "True"
        return "False"
    if v.tag == 0:
        return "None"
    return "<callable>"


def truthy(v: "V") -> "int":
    if v.tag == 0:
        return 0
    if v.tag == 1:
        return 1 if v.iv != 0 else 0
    if v.tag == 2:
        return 1 if v.dv != 0.0 else 0
    if v.tag == 3:
        return 1 if len(v.sv) != 0 else 0
    if v.tag == 4:
        return 1 if v.iv != 0 else 0
    return 1


# ---- arithmetic / comparison (generic, type-dispatched) ----
def _ifloordiv(a: "long", b: "long") -> "long":
    q = a // b
    return q


def _imod(a: "long", b: "long") -> "long":
    r = a % b
    return r


def v_add(x: "V", y: "V") -> "V":
    if x.tag == 3 and y.tag == 3:
        return v_str(x.sv + y.sv)
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) + to_float(y))
    return v_int(x.iv + y.iv)


def v_sub(x: "V", y: "V") -> "V":
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) - to_float(y))
    return v_int(x.iv - y.iv)


def v_mul(x: "V", y: "V") -> "V":
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) * to_float(y))
    return v_int(x.iv * y.iv)


def v_div(x: "V", y: "V") -> "V":
    return v_float(to_float(x) / to_float(y))


def v_floordiv(x: "V", y: "V") -> "V":
    if x.tag == 2 or y.tag == 2:
        return v_float(float(int(to_float(x) / to_float(y))))
    return v_int(_ifloordiv(x.iv, y.iv))


def v_mod(x: "V", y: "V") -> "V":
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) - to_float(y) * float(int(to_float(x) / to_float(y))))
    return v_int(_imod(x.iv, y.iv))


def _cmp(x: "V", y: "V") -> "double":
    return to_float(x) - to_float(y)


def v_lt(x: "V", y: "V") -> "V":
    return v_bool(1 if _cmp(x, y) < 0.0 else 0)


def v_le(x: "V", y: "V") -> "V":
    return v_bool(1 if _cmp(x, y) <= 0.0 else 0)


def v_gt(x: "V", y: "V") -> "V":
    return v_bool(1 if _cmp(x, y) > 0.0 else 0)


def v_ge(x: "V", y: "V") -> "V":
    return v_bool(1 if _cmp(x, y) >= 0.0 else 0)


def v_eq(x: "V", y: "V") -> "V":
    if x.tag == 3 and y.tag == 3:
        return v_bool(1 if x.sv == y.sv else 0)
    if x.tag == 0 or y.tag == 0:
        return v_bool(1 if x.tag == y.tag else 0)
    return v_bool(1 if _cmp(x, y) == 0.0 else 0)


def v_ne(x: "V", y: "V") -> "V":
    r = v_eq(x, y)
    return v_bool(1 if r.iv == 0 else 0)


def v_neg(x: "V") -> "V":
    if x.tag == 2:
        return v_float(-x.dv)
    return v_int(-x.iv)


# ---- const -> value ----
def const_to_v(prog: "Program", idx: "int") -> "V":
    k = prog.consts[idx]
    if k.t == "int":
        return v_int(k.i)
    if k.t == "float":
        return v_float(k.d)
    if k.t == "str":
        return v_str(k.s)
    if k.t == "bool":
        return v_bool(1 if k.i != 0 else 0)
    if k.t == "func":
        return v_func(k.i)
    if k.t == "builtin":
        return v_builtin(k.i)
    return v_none()


# ---- builtins (ids match compiler.BUILTINS) ----
def do_builtin(bid: "long", args: "list[V]") -> "V":
    if bid == 0:               # print
        out = ""
        k = 0
        while k < len(args):
            if k > 0:
                out = out + " "
            out = out + to_display(args[k])
            k = k + 1
        print(out)             # single-arg char* -> puts
        return v_none()
    if bid == 3:               # int
        if len(args) > 0:
            return v_int(to_int(args[0]))
        return v_int(0)
    if bid == 4:               # str
        if len(args) > 0:
            return v_str(to_display(args[0]))
        return v_str("")
    if bid == 5:               # float
        if len(args) > 0:
            return v_float(to_float(args[0]))
        return v_float(0.0)
    if bid == 6:               # abs
        if len(args) > 0:
            x = args[0]
            if x.tag == 2:
                return v_float(x.dv if x.dv >= 0.0 else -x.dv)
            return v_int(x.iv if x.iv >= 0 else -x.iv)
        return v_int(0)
    if bid == 7:               # bool
        if len(args) > 0:
            return v_bool(truthy(args[0]))
        return v_bool(0)
    return v_none()            # len/range unused in v0 scalar scripts


def do_call(prog: "Program", glob: "list[V]", callee: "V",
            args: "list[V]") -> "V":
    if callee.tag == 5:
        return run_func(prog, glob, callee.iv, args)
    if callee.tag == 6:
        return do_builtin(callee.iv, args)
    return v_none()


# ---- the dispatch loop ----
def run_func(prog: "Program", glob: "list[V]", fidx: "long",
             args: "list[V]") -> "V":
    fn = prog.funcs[fidx]
    regs = new_v_list()
    nr = fn.nregs
    if fn.nparams > nr:
        nr = fn.nparams
    k = 0
    while k < nr:
        if k < len(args):
            regs.append(args[k])
        else:
            regs.append(v_none())
        k = k + 1

    code = fn.code
    n = len(code)
    pc = 0
    while pc < n:
        ins = code[pc]
        op = ins.op
        a = ins.a
        b = ins.b
        c = ins.c
        if op == 1:                        # LOAD_CONST
            regs[a] = const_to_v(prog, b)
            pc = pc + 1
        elif op == 2:                      # LOAD_GLOBAL
            regs[a] = glob[b]
            pc = pc + 1
        elif op == 3:                      # STORE_GLOBAL
            glob[b] = regs[a]
            pc = pc + 1
        elif op == 4:                      # MOVE
            regs[a] = regs[b]
            pc = pc + 1
        elif op == 5:                      # RETURN
            return regs[a]
        elif op == 6:                      # JUMP
            pc = a
        elif op == 7:                      # JUMP_IF_FALSE
            if truthy(regs[a]) != 0:
                pc = pc + 1
            else:
                pc = b
        elif op == 8:                      # CALL
            callee = regs[b]
            cargs = new_v_list()
            j = 0
            while j < c:
                cargs.append(regs[b + 1 + j])
                j = j + 1
            regs[a] = do_call(prog, glob, callee, cargs)
            pc = pc + 1
        elif op == 20:                     # ADD
            regs[a] = v_add(regs[b], regs[c]); pc = pc + 1
        elif op == 21:
            regs[a] = v_sub(regs[b], regs[c]); pc = pc + 1
        elif op == 22:
            regs[a] = v_mul(regs[b], regs[c]); pc = pc + 1
        elif op == 23:
            regs[a] = v_div(regs[b], regs[c]); pc = pc + 1
        elif op == 24:
            regs[a] = v_mod(regs[b], regs[c]); pc = pc + 1
        elif op == 25:
            regs[a] = v_floordiv(regs[b], regs[c]); pc = pc + 1
        elif op == 30:                     # LT
            regs[a] = v_lt(regs[b], regs[c]); pc = pc + 1
        elif op == 31:
            regs[a] = v_le(regs[b], regs[c]); pc = pc + 1
        elif op == 32:
            regs[a] = v_gt(regs[b], regs[c]); pc = pc + 1
        elif op == 33:
            regs[a] = v_ge(regs[b], regs[c]); pc = pc + 1
        elif op == 34:
            regs[a] = v_eq(regs[b], regs[c]); pc = pc + 1
        elif op == 35:
            regs[a] = v_ne(regs[b], regs[c]); pc = pc + 1
        elif op == 40:                     # NEG
            regs[a] = v_neg(regs[b]); pc = pc + 1
        elif op == 41:                     # NOT
            regs[a] = v_bool(1 if truthy(regs[b]) == 0 else 0); pc = pc + 1
        elif op == 60:                     # ADD_NN (numeric fast path)
            regs[a] = v_add(regs[b], regs[c]); pc = pc + 1
        elif op == 61:
            regs[a] = v_sub(regs[b], regs[c]); pc = pc + 1
        elif op == 62:
            regs[a] = v_mul(regs[b], regs[c]); pc = pc + 1
        elif op == 63:                     # LT_NN
            regs[a] = v_lt(regs[b], regs[c]); pc = pc + 1
        elif op == 64:
            regs[a] = v_le(regs[b], regs[c]); pc = pc + 1
        elif op == 65:
            regs[a] = v_gt(regs[b], regs[c]); pc = pc + 1
        elif op == 66:
            regs[a] = v_ge(regs[b], regs[c]); pc = pc + 1
        else:
            pc = pc + 1                    # unknown op: skip (defensive)
    return v_none()


def interp_run(prog: "Program") -> "int":
    glob = new_v_list()
    k = 0
    while k < prog.nglobals:
        glob.append(v_none())
        k = k + 1
    run_func(prog, glob, prog.entry, new_v_list())
    return 0


def main() -> "int":
    if len(sys.argv) < 2:
        print("usage: interp <bytecode.json>")
        return 1
    src = open(sys.argv[1]).read()
    hook = rpy.json.generate_decoder(Program)
    prog = json.loads(src, object_hook=hook)
    return interp_run(prog)

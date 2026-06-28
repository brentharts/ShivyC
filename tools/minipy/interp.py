"""minipy.interp -- the rpython interpreter, compiled to C by py2c.

Reads flattened-bytecode JSON (rpy.py2json_bytecode / minipy.compiler), decodes
it into POD structs via rpy.json.generate_decoder, and runs the register
dispatch loop. Runs untranslated under CPython too, so it doubles as a check on
the format and is differentially tested against the pure-Python reference VM.

This revision adds container values -- list / tuple / dict / set, subscripting,
iteration, comprehensions (lowered to loops by the compiler), membership, the
common container/string methods, and %-formatting. Containers live in a side
heap indexed from the value box, so the scalar fast path stays allocation-free.
Opcode numbers and the builtin/method tables mirror minipy/compiler.py.
"""
import sys
import json
import rpy


# ====================== JSON-decoded POD structs ======================
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


class MethEnt:
    def __init__(self, mname: "char*", mfunc: "int"):
        self.mname = mname
        self.mfunc = mfunc


class ClassInfo:
    def __init__(self, cname: "char*", base: "int", methods: "list[MethEnt]"):
        self.cname = cname
        self.base = base
        self.methods = methods


class Program:
    def __init__(self, version: "int", source: "char*",
                 consts: "list[Const]", names: "list[char*]",
                 nglobals: "int", funcs: "list[Func]",
                 classes: "list[ClassInfo]", entry: "int"):
        self.version = version
        self.source = source
        self.consts = consts
        self.names = names
        self.nglobals = nglobals
        self.funcs = funcs
        self.classes = classes
        self.entry = entry


# ====================== runtime value + container heap ======================
# V.tag: 0 none, 1 int, 2 float, 3 str, 4 bool, 5 func, 6 builtin,
#        7 list, 8 dict, 9 set, 10 tuple, 11 iter.
# For containers V.iv is an index into St.heap; the scalar payload lives in
# iv/dv/sv as before so int/float/etc. need no heap allocation.
class V:
    def __init__(self, tag: "int", iv: "long", dv: "double", sv: "char*"):
        self.tag = tag
        self.iv = iv
        self.dv = dv
        self.sv = sv


# A heap cell. kind: 0 list, 1 dict (items = [k0,v0,k1,v1,...]), 2 set,
# 3 tuple, 4 iter (items = materialised elements, cursor = position).
class Cont:
    def __init__(self, kind: "int", cursor: "int", items: "list[V]"):
        self.kind = kind
        self.cursor = cursor
        self.items = items


class St:
    def __init__(self, prog: "Program", glob: "list[V]", heap: "list[Cont]",
                 exc_flag: "int", exc_val: "V"):
        self.prog = prog
        self.glob = glob
        self.heap = heap
        self.exc_flag = exc_flag
        self.exc_val = exc_val


def new_int_list() -> "list[int]":
    r = []
    return r


def new_v_list() -> "list[V]":
    r = []
    return r


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


def _heap_put(st: "St", kind: "int", items: "list[V]") -> "long":
    c = Cont(kind, 0, items)
    st.heap.append(c)
    return len(st.heap) - 1


def v_container(st: "St", tag: "int", kind: "int", items: "list[V]") -> "V":
    return V(tag, _heap_put(st, kind, items), 0.0, "")


def cont_of(st: "St", v: "V") -> "Cont":
    return st.heap[v.iv]


def items_of(st: "St", v: "V") -> "list[V]":
    return st.heap[v.iv].items


# ---- coercions / display ----
def to_float(v: "V") -> "double":
    if v.tag == 2:
        return v.dv
    return float(v.iv)


def to_int(v: "V") -> "long":
    if v.tag == 2:
        return int(v.dv)
    return v.iv


def _fmt_float(d: "double") -> "char*":
    asint = int(d)
    if float(asint) == d:
        return str(asint) + ".0"
    return str(d)


def to_disp(st: "St", v: "V", use_repr: "int") -> "char*":
    if v.tag == 1:
        return str(v.iv)
    if v.tag == 2:
        return _fmt_float(v.dv)
    if v.tag == 3:
        if use_repr != 0:
            return "'" + v.sv + "'"
        return v.sv
    if v.tag == 4:
        if v.iv != 0:
            return "True"
        return "False"
    if v.tag == 0:
        return "None"
    if v.tag == 7 or v.tag == 10:        # list / tuple
        items = items_of(st, v)
        opn = "["
        cls = "]"
        if v.tag == 10:
            opn = "("
            cls = ")"
        out = opn
        k = 0
        while k < len(items):
            if k > 0:
                out = out + ", "
            out = out + to_disp(st, items[k], 1)
            k = k + 1
        if v.tag == 10 and len(items) == 1:
            out = out + ","
        return out + cls
    if v.tag == 9:                       # set
        items = items_of(st, v)
        if len(items) == 0:
            return "set()"
        out = "{"
        k = 0
        while k < len(items):
            if k > 0:
                out = out + ", "
            out = out + to_disp(st, items[k], 1)
            k = k + 1
        return out + "}"
    if v.tag == 8:                       # dict
        items = items_of(st, v)
        out = "{"
        k = 0
        first = 1
        while k < len(items):
            if first == 0:
                out = out + ", "
            first = 0
            out = out + to_disp(st, items[k], 1) + ": " + to_disp(st, items[k + 1], 1)
            k = k + 2
        return out + "}"
    if v.tag == 12:                      # instance
        classes = st.prog.classes
        ci = classes[st.heap[v.iv].cursor]
        return "<" + ci.cname + " object>"
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


# ---- equality / ordering (value semantics for scalars) ----
def _strcmp(a: "char*", b: "char*") -> "int":
    i = 0
    la = len(a)
    lb = len(b)
    while i < la and i < lb:
        ca = ord(a[i])
        cb = ord(b[i])
        if ca < cb:
            return -1
        if ca > cb:
            return 1
        i = i + 1
    if la < lb:
        return -1
    if la > lb:
        return 1
    return 0


def v_eq_bool(x: "V", y: "V") -> "int":
    if x.tag == 3 and y.tag == 3:
        return 1 if _strcmp(x.sv, y.sv) == 0 else 0
    if x.tag == 0 or y.tag == 0:
        return 1 if x.tag == y.tag else 0
    if x.tag >= 7 or y.tag >= 7:
        return 1 if (x.tag == y.tag and x.iv == y.iv) else 0   # container identity
    return 1 if to_float(x) == to_float(y) else 0


def v_cmp(x: "V", y: "V") -> "int":
    if x.tag == 3 and y.tag == 3:
        return _strcmp(x.sv, y.sv)
    a = to_float(x)
    b = to_float(y)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


# ---- arithmetic ----
def v_add(st: "St", x: "V", y: "V") -> "V":
    if x.tag == 3 and y.tag == 3:
        return v_str(x.sv + y.sv)
    if (x.tag == 7 and y.tag == 7) or (x.tag == 10 and y.tag == 10):
        merged = new_v_list()
        for e in items_of(st, x):
            merged.append(e)
        for e in items_of(st, y):
            merged.append(e)
        return v_container(st, x.tag, 0 if x.tag == 7 else 3, merged)
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) + to_float(y))
    return v_int(x.iv + y.iv)


def v_sub(x: "V", y: "V") -> "V":
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) - to_float(y))
    return v_int(x.iv - y.iv)


def v_mul(st: "St", x: "V", y: "V") -> "V":
    if x.tag == 3 and y.tag == 1:
        out = ""
        k = 0
        while k < y.iv:
            out = out + x.sv
            k = k + 1
        return v_str(out)
    if x.tag == 2 or y.tag == 2:
        return v_float(to_float(x) * to_float(y))
    return v_int(x.iv * y.iv)


def v_div(x: "V", y: "V") -> "V":
    return v_float(to_float(x) / to_float(y))


def v_floordiv(x: "V", y: "V") -> "V":
    if x.tag == 2 or y.tag == 2:
        return v_float(float(int(to_float(x) / to_float(y))))
    return v_int(x.iv // y.iv)


def v_mod(st: "St", x: "V", y: "V") -> "V":
    if x.tag == 3:
        args = new_v_list()
        if y.tag == 10:
            for e in items_of(st, y):
                args.append(e)
        else:
            args.append(y)
        return v_str(str_format(st, x.sv, args))
    if x.tag == 2 or y.tag == 2:
        fa = to_float(x)
        fb = to_float(y)
        return v_float(fa - fb * float(int(fa / fb)))
    return v_int(x.iv % y.iv)


def v_neg(x: "V") -> "V":
    if x.tag == 2:
        return v_float(-x.dv)
    return v_int(-x.iv)


def _pw_int(base: "long", e: "long") -> "long":
    r: "long" = 1
    k = 0
    while k < e:
        r = r * base
        k = k + 1
    return r


def _pw_flt(base: "double", e: "long") -> "double":
    r: "double" = 1.0
    k = 0
    while k < e:
        r = r * base
        k = k + 1
    return r


def v_pow(x: "V", y: "V") -> "V":
    if y.tag != 2:                          # integer exponent
        e = y.iv
        if e >= 0:
            if x.tag == 2:
                return v_float(_pw_flt(x.dv, e))
            return v_int(_pw_int(x.iv, e))
    return v_float(0.0)                      # float/negative exponent: v0 stub


def set_has(st: "St", setv: "V", item: "V") -> "int":
    items = items_of(st, setv)
    j = 0
    while j < len(items):
        if v_eq_bool(items[j], item) != 0:
            return 1
        j = j + 1
    return 0


def v_bitor(st: "St", x: "V", y: "V") -> "V":
    if x.tag == 9 and y.tag == 9:           # set union
        out = new_v_list()
        sv = v_container(st, 9, 2, out)
        for e in items_of(st, x):
            _set_add(st, sv, e)
        for e in items_of(st, y):
            _set_add(st, sv, e)
        return sv
    return v_int(x.iv | y.iv)


def v_bitand(st: "St", x: "V", y: "V") -> "V":
    if x.tag == 9 and y.tag == 9:           # set intersection
        out = new_v_list()
        sv = v_container(st, 9, 2, out)
        for e in items_of(st, x):
            if set_has(st, y, e) != 0:
                _set_add(st, sv, e)
        return sv
    return v_int(x.iv & y.iv)


def v_bitxor(st: "St", x: "V", y: "V") -> "V":
    if x.tag == 9 and y.tag == 9:           # set symmetric difference
        out = new_v_list()
        sv = v_container(st, 9, 2, out)
        for e in items_of(st, x):
            if set_has(st, y, e) == 0:
                _set_add(st, sv, e)
        for e in items_of(st, y):
            if set_has(st, x, e) == 0:
                _set_add(st, sv, e)
        return sv
    return v_int(x.iv ^ y.iv)


def v_slice(st: "St", seq: "V", lo_v: "V", hi_v: "V") -> "V":
    if seq.tag == 3:
        n = len(seq.sv)
    elif seq.tag == 7 or seq.tag == 10:
        n = len(items_of(st, seq))
    else:
        return v_none()
    lo = lo_v.iv
    if lo < 0:
        lo = lo + n
    if lo < 0:
        lo = 0
    if lo > n:
        lo = n
    if hi_v.tag == 0:
        hi = n
    else:
        hi = hi_v.iv
        if hi < 0:
            hi = hi + n
        if hi < 0:
            hi = 0
        if hi > n:
            hi = n
    if hi < lo:
        hi = lo
    if seq.tag == 3:
        out = ""
        k = lo
        while k < hi:
            out = out + seq.sv[k]
            k = k + 1
        return v_str(out)
    src = items_of(st, seq)
    res = new_v_list()
    k = lo
    while k < hi:
        res.append(src[k])
        k = k + 1
    if seq.tag == 10:
        return v_container(st, 10, 3, res)
    return v_container(st, 7, 0, res)


# ---- subscript / membership / iteration ----
def _norm_index(i: "long", n: "long") -> "long":
    if i < 0:
        return i + n
    return i


def dict_find(items: "list[V]", key: "V") -> "int":
    j = 0
    n = len(items)
    while j < n:
        if v_eq_bool(items[j], key) != 0:
            return j
        j = j + 2
    return -1


def v_index(st: "St", seq: "V", idx: "V") -> "V":
    if seq.tag == 3:
        i = _norm_index(idx.iv, len(seq.sv))
        return v_str(seq.sv[i])
    if seq.tag == 7 or seq.tag == 10:
        items = items_of(st, seq)
        i = _norm_index(idx.iv, len(items))
        return items[i]
    if seq.tag == 8:
        items = items_of(st, seq)
        j = dict_find(items, idx)
        if j >= 0:
            return items[j + 1]
        return v_none()
    return v_none()


def v_setindex(st: "St", seq: "V", idx: "V", val: "V") -> "int":
    if seq.tag == 7:
        items = items_of(st, seq)
        i = _norm_index(idx.iv, len(items))
        items[i] = val
        return 0
    if seq.tag == 8:
        items = items_of(st, seq)
        j = dict_find(items, idx)
        if j >= 0:
            items[j + 1] = val
        else:
            items.append(idx)
            items.append(val)
        return 0
    return 0


def v_contains(st: "St", container: "V", item: "V") -> "V":
    if container.tag == 3:
        return v_bool(1 if item.sv in container.sv else 0)
    if container.tag == 7 or container.tag == 10 or container.tag == 9:
        items = items_of(st, container)
        j = 0
        while j < len(items):
            if v_eq_bool(items[j], item) != 0:
                return v_bool(1)
            j = j + 1
        return v_bool(0)
    if container.tag == 8:
        items = items_of(st, container)
        return v_bool(1 if dict_find(items, item) >= 0 else 0)
    return v_bool(0)


def materialize(st: "St", v: "V") -> "list[V]":
    out = new_v_list()
    if v.tag == 7 or v.tag == 10 or v.tag == 9:
        for e in items_of(st, v):
            out.append(e)
    elif v.tag == 8:
        items = items_of(st, v)
        k = 0
        while k < len(items):
            out.append(items[k])
            k = k + 2
    elif v.tag == 3:
        k = 0
        while k < len(v.sv):
            out.append(v_str(v.sv[k]))
            k = k + 1
    return out


def v_iter(st: "St", v: "V") -> "V":
    return v_container(st, 11, 4, materialize(st, v))


def _set_add(st: "St", setv: "V", item: "V") -> "int":
    items = items_of(st, setv)
    j = 0
    while j < len(items):
        if v_eq_bool(items[j], item) != 0:
            return 0
        j = j + 1
    items.append(item)
    return 0


def v_len(st: "St", v: "V") -> "long":
    if v.tag == 3:
        return len(v.sv)
    if v.tag == 8:
        return len(items_of(st, v)) // 2
    if v.tag == 7 or v.tag == 10 or v.tag == 9:
        return len(items_of(st, v))
    return 0


# ---- %-formatting ----
def _is_digit(ch: "char*") -> "int":
    o = ord(ch)
    return 1 if (o >= 48 and o <= 57) else 0


def _ffmt(x: "double", prec: "int") -> "char*":
    p = prec
    if p < 0:
        p = 6
    neg = 0
    if x < 0.0:
        neg = 1
        x = -x
    scale = 1
    k = 0
    while k < p:
        scale = scale * 10
        k = k + 1
    scaled = int(x * float(scale) + 0.5)
    ip = scaled // scale
    fp = scaled % scale
    out = str(ip)
    if p > 0:
        fs = str(fp)
        while len(fs) < p:
            fs = "0" + fs
        out = out + "." + fs
    if neg != 0:
        out = "-" + out
    return out


def _hexfmt(n: "long") -> "char*":
    if n == 0:
        return "0"
    digits = "0123456789abcdef"
    neg = 0
    m = n
    if m < 0:
        neg = 1
        m = -m
    out = ""
    while m > 0:
        out = digits[m % 16] + out
        m = m // 16
    if neg != 0:
        out = "-" + out
    return out


def _pad(piece: "char*", width: "int", left: "int", zero: "int") -> "char*":
    if len(piece) >= width:
        return piece
    padc = " "
    if zero != 0 and left == 0:
        padc = "0"
    pad = ""
    k = len(piece)
    while k < width:
        pad = pad + padc
        k = k + 1
    if left != 0:
        return piece + pad
    return pad + piece


def str_format(st: "St", fmt: "char*", args: "list[V]") -> "char*":
    out = ""
    i = 0
    ai = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch != "%":
            out = out + ch
            i = i + 1
            continue
        i = i + 1
        if i < n and fmt[i] == "%":
            out = out + "%"
            i = i + 1
            continue
        left = 0
        zero = 0
        while i < n and (fmt[i] == "-" or fmt[i] == "0" or fmt[i] == " " or fmt[i] == "+"):
            if fmt[i] == "-":
                left = 1
            if fmt[i] == "0":
                zero = 1
            i = i + 1
        width = 0
        while i < n and _is_digit(fmt[i]) != 0:
            width = width * 10 + (ord(fmt[i]) - 48)
            i = i + 1
        prec = -1
        if i < n and fmt[i] == ".":
            i = i + 1
            prec = 0
            while i < n and _is_digit(fmt[i]) != 0:
                prec = prec * 10 + (ord(fmt[i]) - 48)
                i = i + 1
        conv = "s"
        if i < n:
            conv = fmt[i]
            i = i + 1
        arg = v_none()
        if ai < len(args):
            arg = args[ai]
        ai = ai + 1
        piece = ""
        if conv == "d" or conv == "i":
            piece = str(to_int(arg))
        elif conv == "s":
            piece = to_disp(st, arg, 0)
            if prec >= 0 and len(piece) > prec:
                piece = piece[0:prec]
        elif conv == "r":
            piece = to_disp(st, arg, 1)
        elif conv == "f":
            piece = _ffmt(to_float(arg), prec)
        elif conv == "x":
            piece = _hexfmt(to_int(arg))
        else:
            piece = to_disp(st, arg, 0)
        out = out + _pad(piece, width, left, zero)
    return out


# ---- classes / instances ----
# Instance: Cont kind 5, cursor = class id, items = [attrname, attrval, ...].
# Bound user method: Cont kind 6, cursor = func idx, items = [self].
# Bound builtin method: Cont kind 7, cursor = method id, items = [self].
# V tags: OBJ 12, CLASS 13, BOUND 14, BOUNDB 15.
def lookup_method(st: "St", cid: "int", name: "char*") -> "int":
    classes = st.prog.classes
    c = cid
    while c >= 0:
        ci = classes[c]
        meths = ci.methods
        j = 0
        while j < len(meths):
            me = meths[j]
            if _strcmp(me.mname, name) == 0:
                return me.mfunc
            j = j + 1
        c = ci.base
    return -1


def inst_get(st: "St", obj: "V", name: "char*") -> "V":
    items = items_of(st, obj)
    j = 0
    while j < len(items):
        if items[j].tag == 3 and _strcmp(items[j].sv, name) == 0:
            return items[j + 1]
        j = j + 2
    return v_none()


def inst_set(st: "St", obj: "V", name: "char*", val: "V") -> "int":
    items = items_of(st, obj)
    j = 0
    while j < len(items):
        if items[j].tag == 3 and _strcmp(items[j].sv, name) == 0:
            items[j + 1] = val
            return 0
        j = j + 2
    items.append(v_str(name))
    items.append(val)
    return 0


def instantiate(st: "St", classid: "int", args: "list[V]") -> "V":
    inst = v_container(st, 12, 5, new_v_list())
    st.heap[inst.iv].cursor = classid
    fidx = lookup_method(st, classid, "__init__")
    if fidx >= 0:
        callargs = new_v_list()
        callargs.append(inst)
        for a in args:
            callargs.append(a)
        run_func(st, fidx, callargs)
    return inst


def method_id(name: "char*") -> "long":
    if name == "append":
        return 100
    if name == "pop":
        return 101
    if name == "get":
        return 102
    if name == "keys":
        return 103
    if name == "values":
        return 104
    if name == "items":
        return 105
    if name == "add":
        return 106
    if name == "split":
        return 107
    if name == "join":
        return 108
    if name == "strip":
        return 109
    if name == "startswith":
        return 110
    if name == "endswith":
        return 111
    if name == "find":
        return 112
    if name == "replace":
        return 113
    if name == "upper":
        return 114
    if name == "lower":
        return 115
    if name == "extend":
        return 116
    if name == "insert":
        return 117
    if name == "index":
        return 118
    if name == "count":
        return 119
    if name == "update":
        return 120
    if name == "setdefault":
        return 121
    if name == "splitlines":
        return 122
    if name == "rstrip":
        return 123
    if name == "lstrip":
        return 124
    if name == "isdigit":
        return 125
    if name == "isupper":
        return 126
    if name == "islower":
        return 127
    return -1


def is_instance(st: "St", exc: "V", clsv: "V") -> "int":
    if exc.tag != 12 or clsv.tag != 13:
        return 0
    target = clsv.iv
    classes = st.prog.classes
    c = st.heap[exc.iv].cursor
    while c >= 0:
        if c == target:
            return 1
        ci = classes[c]
        c = ci.base
    return 0


def inst_has(st: "St", obj: "V", name: "char*") -> "int":
    items = items_of(st, obj)
    j = 0
    while j < len(items):
        if items[j].tag == 3 and _strcmp(items[j].sv, name) == 0:
            return 1
        j = j + 2
    return 0


def _isinst_type(obj: "V", bid: "long") -> "int":
    if bid == 3:                            # int (bool counts as int)
        return 1 if (obj.tag == 1 or obj.tag == 4) else 0
    if bid == 5:
        return 1 if obj.tag == 2 else 0     # float
    if bid == 4:
        return 1 if obj.tag == 3 else 0     # str
    if bid == 7:
        return 1 if obj.tag == 4 else 0     # bool
    if bid == 8:
        return 1 if obj.tag == 7 else 0     # list
    if bid == 9:
        return 1 if obj.tag == 8 else 0     # dict
    if bid == 10:
        return 1 if obj.tag == 9 else 0     # set
    if bid == 11:
        return 1 if obj.tag == 10 else 0    # tuple
    return 0


def native_isinstance(st: "St", obj: "V", spec: "V") -> "int":
    if spec.tag == 13:                      # user class
        return is_instance(st, obj, spec)
    if spec.tag == 6:                       # type builtin (int/str/list/...)
        return _isinst_type(obj, spec.iv)
    if spec.tag == 10:                      # tuple of types
        for s in items_of(st, spec):
            if native_isinstance(st, obj, s) != 0:
                return 1
        return 0
    return 0


def _is_ws(ch: "char*") -> "int":
    o = ord(ch)
    return 1 if (o == 32 or o == 9 or o == 10 or o == 13) else 0


def _rstrip(s: "char*") -> "char*":
    e = len(s)
    while e > 0 and _is_ws(s[e - 1]) != 0:
        e = e - 1
    return s[0:e]


def _lstrip(s: "char*") -> "char*":
    i = 0
    n = len(s)
    while i < n and _is_ws(s[i]) != 0:
        i = i + 1
    return s[i:n]


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
    if k.t == "class":
        return V(13, k.i, 0.0, "")
    return v_none()


# ---- builtins (ids 0-99) and methods (ids 100+) ----
def do_builtin(st: "St", bid: "long", args: "list[V]") -> "V":
    if bid >= 100:
        return do_method(st, bid, args)
    if bid == 0:               # print
        out = ""
        k = 0
        while k < len(args):
            if k > 0:
                out = out + " "
            out = out + to_disp(st, args[k], 0)
            k = k + 1
        print(out)
        return v_none()
    if bid == 1:               # len
        if len(args) > 0:
            return v_int(v_len(st, args[0]))
        return v_int(0)
    if bid == 2:               # range
        lo = 0
        hi = 0
        step = 1
        if len(args) == 1:
            hi = args[0].iv
        elif len(args) == 2:
            lo = args[0].iv
            hi = args[1].iv
        elif len(args) >= 3:
            lo = args[0].iv
            hi = args[1].iv
            step = args[2].iv
        out = new_v_list()
        i = lo
        if step > 0:
            while i < hi:
                out.append(v_int(i))
                i = i + step
        else:
            while i > hi:
                out.append(v_int(i))
                i = i + step
        return v_container(st, 7, 0, out)
    if bid == 3:               # int
        if len(args) > 0:
            return v_int(to_int(args[0]))
        return v_int(0)
    if bid == 4:               # str
        if len(args) > 0:
            return v_str(to_disp(st, args[0], 0))
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
    if bid == 8:               # list
        if len(args) > 0:
            return v_container(st, 7, 0, materialize(st, args[0]))
        return v_container(st, 7, 0, new_v_list())
    if bid == 9:               # dict (copy from list of pairs not supported; empty)
        return v_container(st, 8, 1, new_v_list())
    if bid == 10:              # set
        out = new_v_list()
        sv = v_container(st, 9, 2, out)
        if len(args) > 0:
            for e in materialize(st, args[0]):
                _set_add(st, sv, e)
        return sv
    if bid == 11:              # tuple
        if len(args) > 0:
            return v_container(st, 10, 3, materialize(st, args[0]))
        return v_container(st, 10, 3, new_v_list())
    if bid == 12:              # repr
        if len(args) > 0:
            return v_str(to_disp(st, args[0], 1))
        return v_str("")
    if bid == 13:              # sorted
        if len(args) > 0:
            els = materialize(st, args[0])
            _sort(els)
            return v_container(st, 7, 0, els)
        return v_container(st, 7, 0, new_v_list())
    if bid == 14:              # sum
        acc = v_int(0)
        if len(args) > 0:
            for e in materialize(st, args[0]):
                acc = v_add(st, acc, e)
        return acc
    if bid == 15:              # min
        return _minmax(st, args, -1)
    if bid == 16:              # max
        return _minmax(st, args, 1)
    if bid == 17:              # isinstance
        if len(args) >= 2:
            return v_bool(native_isinstance(st, args[0], args[1]))
        return v_bool(0)
    if bid == 18:              # enumerate
        out = new_v_list()
        if len(args) > 0:
            els = materialize(st, args[0])
            k = 0
            while k < len(els):
                pair = new_v_list()
                pair.append(v_int(k))
                pair.append(els[k])
                out.append(v_container(st, 10, 3, pair))
                k = k + 1
        return v_container(st, 7, 0, out)
    if bid == 19:              # zip
        out = new_v_list()
        if len(args) == 2:
            a0 = materialize(st, args[0])
            a1 = materialize(st, args[1])
            m = len(a0)
            if len(a1) < m:
                m = len(a1)
            k = 0
            while k < m:
                pair = new_v_list()
                pair.append(a0[k])
                pair.append(a1[k])
                out.append(v_container(st, 10, 3, pair))
                k = k + 1
        elif len(args) == 3:
            a0 = materialize(st, args[0])
            a1 = materialize(st, args[1])
            a2 = materialize(st, args[2])
            m = len(a0)
            if len(a1) < m:
                m = len(a1)
            if len(a2) < m:
                m = len(a2)
            k = 0
            while k < m:
                pair = new_v_list()
                pair.append(a0[k])
                pair.append(a1[k])
                pair.append(a2[k])
                out.append(v_container(st, 10, 3, pair))
                k = k + 1
        return v_container(st, 7, 0, out)
    if bid == 20:              # any
        if len(args) > 0:
            for e in materialize(st, args[0]):
                if truthy(e) != 0:
                    return v_bool(1)
        return v_bool(0)
    if bid == 21:              # all
        if len(args) > 0:
            for e in materialize(st, args[0]):
                if truthy(e) == 0:
                    return v_bool(0)
        return v_bool(1)
    if bid == 22:              # ord
        if len(args) > 0:
            return v_int(ord(args[0].sv))
        return v_int(0)
    if bid == 23:              # chr
        if len(args) > 0:
            return v_str(chr(args[0].iv))
        return v_str("")
    if bid == 24:              # reversed
        out = new_v_list()
        if len(args) > 0:
            els = materialize(st, args[0])
            k = len(els) - 1
            while k >= 0:
                out.append(els[k])
                k = k - 1
        return v_container(st, 7, 0, out)
    if bid == 25:              # getattr
        if len(args) >= 2:
            obj = args[0]
            nm = args[1].sv
            if obj.tag == 12:
                if inst_has(st, obj, nm) != 0:
                    return inst_get(st, obj, nm)
                fidx = lookup_method(st, st.heap[obj.iv].cursor, nm)
                if fidx >= 0:
                    bargs = new_v_list(); bargs.append(obj)
                    bv = v_container(st, 14, 6, bargs)
                    st.heap[bv.iv].cursor = fidx
                    return bv
            if len(args) >= 3:
                return args[2]
        return v_none()
    if bid == 26:              # hasattr
        if len(args) >= 2:
            obj = args[0]
            nm = args[1].sv
            if obj.tag == 12:
                if inst_has(st, obj, nm) != 0:
                    return v_bool(1)
                if lookup_method(st, st.heap[obj.iv].cursor, nm) >= 0:
                    return v_bool(1)
        return v_bool(0)
    return v_none()


def _minmax(st: "St", args: "list[V]", want: "int") -> "V":
    els = new_v_list()
    if len(args) == 1:
        els = materialize(st, args[0])
    else:
        els = args
    if len(els) == 0:
        return v_none()
    best = els[0]
    k = 1
    while k < len(els):
        c = v_cmp(els[k], best)
        if (want < 0 and c < 0) or (want > 0 and c > 0):
            best = els[k]
        k = k + 1
    return best


def _sort(els: "list[V]") -> "int":
    n = len(els)
    i = 1
    while i < n:
        key = els[i]
        j = i - 1
        while j >= 0 and v_cmp(els[j], key) > 0:
            els[j + 1] = els[j]
            j = j - 1
        els[j + 1] = key
        i = i + 1
    return 0


def do_method(st: "St", mid: "long", args: "list[V]") -> "V":
    recv = args[0]
    if mid == 100:             # append
        items_of(st, recv).append(args[1])
        return v_none()
    if mid == 101:             # pop
        items = items_of(st, recv)
        if len(args) >= 2:
            i = _norm_index(args[1].iv, len(items))
            return items.pop(i)
        return items.pop(len(items) - 1)
    if mid == 102:             # dict.get
        items = items_of(st, recv)
        j = dict_find(items, args[1])
        if j >= 0:
            return items[j + 1]
        if len(args) >= 3:
            return args[2]
        return v_none()
    if mid == 103:             # dict.keys
        out = new_v_list()
        items = items_of(st, recv)
        k = 0
        while k < len(items):
            out.append(items[k])
            k = k + 2
        return v_container(st, 7, 0, out)
    if mid == 104:             # dict.values
        out = new_v_list()
        items = items_of(st, recv)
        k = 1
        while k < len(items):
            out.append(items[k])
            k = k + 2
        return v_container(st, 7, 0, out)
    if mid == 105:             # dict.items -> list of [k, v]
        out = new_v_list()
        items = items_of(st, recv)
        k = 0
        while k < len(items):
            pair = new_v_list()
            pair.append(items[k])
            pair.append(items[k + 1])
            out.append(v_container(st, 10, 3, pair))
            k = k + 2
        return v_container(st, 7, 0, out)
    if mid == 106:             # set.add
        _set_add(st, recv, args[1])
        return v_none()
    if mid == 110:             # startswith
        return v_bool(1 if recv.sv.startswith(args[1].sv) else 0)
    if mid == 111:             # endswith
        return v_bool(1 if recv.sv.endswith(args[1].sv) else 0)
    if mid == 114:             # upper
        return v_str(recv.sv.upper())
    if mid == 115:             # lower
        return v_str(recv.sv.lower())
    if mid == 116:             # list.extend
        items = items_of(st, recv)
        for e in materialize(st, args[1]):
            items.append(e)
        return v_none()
    if mid == 117:             # list.insert(i, val)
        items = items_of(st, recv)
        i = _norm_index(args[1].iv, len(items) + 1)
        items.append(v_none())             # grow by one, then shift right
        j = len(items) - 1
        while j > i:
            items[j] = items[j - 1]
            j = j - 1
        items[i] = args[2]
        return v_none()
    if mid == 118:             # index(val)
        items = items_of(st, recv)
        j = 0
        while j < len(items):
            if v_eq_bool(items[j], args[1]) != 0:
                return v_int(j)
            j = j + 1
        return v_int(-1)
    if mid == 119:             # count(val)
        items = items_of(st, recv)
        c = 0
        j = 0
        while j < len(items):
            if v_eq_bool(items[j], args[1]) != 0:
                c = c + 1
            j = j + 1
        return v_int(c)
    if mid == 120:             # dict.update(other)
        other = items_of(st, args[1])
        j = 0
        while j < len(other):
            v_setindex(st, recv, other[j], other[j + 1])
            j = j + 2
        return v_none()
    if mid == 121:             # dict.setdefault(key[, default])
        items = items_of(st, recv)
        j = dict_find(items, args[1])
        if j >= 0:
            return items[j + 1]
        dv = v_none()
        if len(args) >= 3:
            dv = args[2]
        v_setindex(st, recv, args[1], dv)
        return dv
    if mid == 122:             # str.splitlines
        out = new_v_list()
        s = recv.sv
        cur = ""
        k = 0
        while k < len(s):
            ch = s[k]
            if ord(ch) == 10:
                out.append(v_str(cur)); cur = ""
            elif ord(ch) == 13:
                k = k + 1
                continue
            else:
                cur = cur + ch
            k = k + 1
        if len(cur) > 0:
            out.append(v_str(cur))
        return v_container(st, 7, 0, out)
    if mid == 123:             # str.rstrip (whitespace)
        return v_str(_rstrip(recv.sv))
    if mid == 124:             # str.lstrip (whitespace)
        return v_str(_lstrip(recv.sv))
    if mid == 125:             # str.isdigit
        s = recv.sv
        if len(s) == 0:
            return v_bool(0)
        k = 0
        while k < len(s):
            o = ord(s[k])
            if o < 48 or o > 57:
                return v_bool(0)
            k = k + 1
        return v_bool(1)
    if mid == 126:             # str.isupper
        s = recv.sv
        up = 0
        lo = 0
        k = 0
        while k < len(s):
            o = ord(s[k])
            if o >= 65 and o <= 90:
                up = up + 1
            elif o >= 97 and o <= 122:
                lo = lo + 1
            k = k + 1
        return v_bool(1 if (up > 0 and lo == 0) else 0)
    if mid == 127:             # str.islower
        s = recv.sv
        up = 0
        lo = 0
        k = 0
        while k < len(s):
            o = ord(s[k])
            if o >= 65 and o <= 90:
                up = up + 1
            elif o >= 97 and o <= 122:
                lo = lo + 1
            k = k + 1
        return v_bool(1 if (lo > 0 and up == 0) else 0)
    return v_none()


def do_call(st: "St", callee: "V", args: "list[V]") -> "V":
    if callee.tag == 5:
        return run_func(st, callee.iv, args)
    if callee.tag == 6:
        return do_builtin(st, callee.iv, args)
    if callee.tag == 13:                   # CLASS -> instantiate
        return instantiate(st, callee.iv, args)
    if callee.tag == 14:                   # bound user method
        cont = st.heap[callee.iv]
        callargs = new_v_list()
        callargs.append(cont.items[0])
        for a in args:
            callargs.append(a)
        return run_func(st, cont.cursor, callargs)
    if callee.tag == 15:                   # bound builtin method
        cont = st.heap[callee.iv]
        callargs = new_v_list()
        callargs.append(cont.items[0])
        for a in args:
            callargs.append(a)
        return do_builtin(st, cont.cursor, callargs)
    return v_none()


# ---- the dispatch loop ----
def run_func(st: "St", fidx: "long", args: "list[V]") -> "V":
    fn = st.prog.funcs[fidx]
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
    blocks = new_int_list()
    bn = 0                                  # block-stack depth (list[int].pop is
    pc = 0                                  # miscompiled by py2c, so index by bn)
    while pc < n:
        ins = code[pc]
        op = ins.op
        a = ins.a
        b = ins.b
        c = ins.c
        if op == 1:
            regs[a] = const_to_v(st.prog, b); pc = pc + 1
        elif op == 2:
            regs[a] = st.glob[b]; pc = pc + 1
        elif op == 3:
            st.glob[b] = regs[a]; pc = pc + 1
        elif op == 4:
            regs[a] = regs[b]; pc = pc + 1
        elif op == 5:
            return regs[a]
        elif op == 6:
            pc = a
        elif op == 7:
            if truthy(regs[a]) != 0:
                pc = pc + 1
            else:
                pc = b
        elif op == 8:
            callee = regs[b]
            cargs = new_v_list()
            j = 0
            while j < c:
                cargs.append(regs[b + 1 + j])
                j = j + 1
            regs[a] = do_call(st, callee, cargs); pc = pc + 1
        elif op == 9:                      # BUILD_LIST
            items = new_v_list()
            j = 0
            while j < c:
                items.append(regs[b + j]); j = j + 1
            regs[a] = v_container(st, 7, 0, items); pc = pc + 1
        elif op == 10:                     # BUILD_TUPLE
            items = new_v_list()
            j = 0
            while j < c:
                items.append(regs[b + j]); j = j + 1
            regs[a] = v_container(st, 10, 3, items); pc = pc + 1
        elif op == 11:                     # BUILD_DICT
            dv = v_container(st, 8, 1, new_v_list())
            j = 0
            while j < c:
                v_setindex(st, dv, regs[b + 2 * j], regs[b + 2 * j + 1])
                j = j + 1
            regs[a] = dv; pc = pc + 1
        elif op == 12:                     # BUILD_SET
            sv = v_container(st, 9, 2, new_v_list())
            j = 0
            while j < c:
                _set_add(st, sv, regs[b + j]); j = j + 1
            regs[a] = sv; pc = pc + 1
        elif op == 13:                     # INDEX
            regs[a] = v_index(st, regs[b], regs[c]); pc = pc + 1
        elif op == 14:                     # SETINDEX
            v_setindex(st, regs[a], regs[b], regs[c]); pc = pc + 1
        elif op == 15:                     # ITER_NEW
            regs[a] = v_iter(st, regs[b]); pc = pc + 1
        elif op == 16:                     # ITER_NEXT
            it = regs[b]
            cont = st.heap[it.iv]
            if cont.cursor < len(cont.items):
                regs[a] = cont.items[cont.cursor]
                cont.cursor = cont.cursor + 1
                pc = pc + 1
            else:
                pc = c
        elif op == 17:                     # CONTAINS
            regs[a] = v_contains(st, regs[b], regs[c]); pc = pc + 1
        elif op == 18:                     # LIST_APPEND
            items_of(st, regs[a]).append(regs[b]); pc = pc + 1
        elif op == 19:                     # SET_ADD
            _set_add(st, regs[a], regs[b]); pc = pc + 1
        elif op == 50:                     # LOAD_ATTR
            cs = st.prog.consts
            nm = cs[c].s
            regs[a] = inst_get(st, regs[b], nm); pc = pc + 1
        elif op == 51:                     # STORE_ATTR
            cs = st.prog.consts
            nm = cs[c].s
            inst_set(st, regs[a], nm, regs[b]); pc = pc + 1
        elif op == 52:                     # LOAD_METHOD
            cs = st.prog.consts
            nm = cs[c].s
            obj = regs[b]
            if obj.tag == 12:              # instance -> bound user method
                fidx = lookup_method(st, st.heap[obj.iv].cursor, nm)
                bargs = new_v_list(); bargs.append(obj)
                regs[a] = v_container(st, 14, 6, bargs)
                st.heap[regs[a].iv].cursor = fidx
            else:                          # container/str -> bound builtin
                bargs = new_v_list(); bargs.append(obj)
                regs[a] = v_container(st, 15, 7, bargs)
                st.heap[regs[a].iv].cursor = method_id(nm)
            pc = pc + 1
        elif op == 20:
            regs[a] = v_add(st, regs[b], regs[c]); pc = pc + 1
        elif op == 21:
            regs[a] = v_sub(regs[b], regs[c]); pc = pc + 1
        elif op == 22:
            regs[a] = v_mul(st, regs[b], regs[c]); pc = pc + 1
        elif op == 23:
            regs[a] = v_div(regs[b], regs[c]); pc = pc + 1
        elif op == 24:
            regs[a] = v_mod(st, regs[b], regs[c]); pc = pc + 1
        elif op == 25:
            regs[a] = v_floordiv(regs[b], regs[c]); pc = pc + 1
        elif op == 26:
            regs[a] = v_pow(regs[b], regs[c]); pc = pc + 1
        elif op == 27:
            regs[a] = v_bitor(st, regs[b], regs[c]); pc = pc + 1
        elif op == 28:
            regs[a] = v_bitand(st, regs[b], regs[c]); pc = pc + 1
        elif op == 29:
            regs[a] = v_bitxor(st, regs[b], regs[c]); pc = pc + 1
        elif op == 36:
            regs[a] = v_int(regs[b].iv << regs[c].iv); pc = pc + 1
        elif op == 37:
            regs[a] = v_int(regs[b].iv >> regs[c].iv); pc = pc + 1
        elif op == 38:
            regs[a] = v_slice(st, regs[a], regs[b], regs[c]); pc = pc + 1
        elif op == 30:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) < 0 else 0); pc = pc + 1
        elif op == 31:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) <= 0 else 0); pc = pc + 1
        elif op == 32:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) > 0 else 0); pc = pc + 1
        elif op == 33:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) >= 0 else 0); pc = pc + 1
        elif op == 34:
            regs[a] = v_bool(v_eq_bool(regs[b], regs[c])); pc = pc + 1
        elif op == 35:
            regs[a] = v_bool(1 if v_eq_bool(regs[b], regs[c]) == 0 else 0); pc = pc + 1
        elif op == 40:
            regs[a] = v_neg(regs[b]); pc = pc + 1
        elif op == 41:
            regs[a] = v_bool(1 if truthy(regs[b]) == 0 else 0); pc = pc + 1
        elif op == 60:
            regs[a] = v_add(st, regs[b], regs[c]); pc = pc + 1
        elif op == 61:
            regs[a] = v_sub(regs[b], regs[c]); pc = pc + 1
        elif op == 62:
            regs[a] = v_mul(st, regs[b], regs[c]); pc = pc + 1
        elif op == 63:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) < 0 else 0); pc = pc + 1
        elif op == 64:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) <= 0 else 0); pc = pc + 1
        elif op == 65:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) > 0 else 0); pc = pc + 1
        elif op == 66:
            regs[a] = v_bool(1 if v_cmp(regs[b], regs[c]) >= 0 else 0); pc = pc + 1
        elif op == 70:                     # SETUP_EXCEPT
            if bn < len(blocks):
                blocks[bn] = a
            else:
                blocks.append(a)
            bn = bn + 1
            pc = pc + 1
        elif op == 71:                     # POP_BLOCK
            bn = bn - 1; pc = pc + 1
        elif op == 72:                     # RAISE
            ev = regs[a]
            if ev.tag == 13:               # raising a bare class -> instantiate
                ev = instantiate(st, ev.iv, new_v_list())
            st.exc_val = ev
            st.exc_flag = 1
        elif op == 73:                     # RERAISE
            st.exc_flag = 1
        elif op == 74:                     # LOAD_EXC
            regs[a] = st.exc_val; pc = pc + 1
        elif op == 75:                     # EXC_MATCH
            regs[a] = v_bool(is_instance(st, st.exc_val, regs[b])); pc = pc + 1
        else:
            pc = pc + 1
        if st.exc_flag != 0:               # an exception is in flight
            if bn > 0:
                bn = bn - 1
                pc = blocks[bn]            # jump to nearest handler
                st.exc_flag = 0
            else:
                return v_none()            # propagate to caller
    return v_none()


def interp_run(prog: "Program") -> "int":
    glob = new_v_list()
    heap = []
    cz = Cont(0, 0, new_v_list())
    heap.append(cz)                        # heap[0] reserved; anchors list[Cont]
    st = St(prog, glob, heap, 0, v_none())
    k = 0
    while k < prog.nglobals:
        glob.append(v_none())
        k = k + 1
    run_func(st, prog.entry, new_v_list())
    return 0


def main() -> "int":
    if len(sys.argv) < 2:
        print("usage: interp <bytecode.json>")
        return 1
    src = open(sys.argv[1]).read()
    hook = rpy.json.generate_decoder(Program)
    prog = json.loads(src, object_hook=hook)
    return interp_run(prog)

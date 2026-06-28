"""minipy.compiler -- CPython-side AOT front end for the rpython interpreter.

This is the ".py -> flattened bytecode" stage. It does NOT hand the rpython
interpreter a raw AST tree (that would force the interpreter to walk a deep,
irregular structure). Instead it lowers the AST to a *flat, register-based*
instruction stream over uniform POD records, the form a fast dispatch loop wants
(the same shape TPython's `tp_step` runs). The result serialises to JSON whose
objects map one-to-one onto the rpython POD classes in `schema.py`, so
`rpy.json.generate_decoder` can unpack it straight into structs -- no dict, no
boxing.

Everything here is CPython-only (it runs inside `rpy.py`, the driver). The
`Program` it emits is the contract; `VM` below is a faithful reference executor
used to validate the bytecode against `python3` before the compiled rpython
interpreter exists, and as the differential oracle thereafter.

Scope (v0): module + top-level defs (positional params), return, assignment,
aug-assign, arithmetic (+ - * / // %), single-comparator compares, and/or, not,
unary minus, if/else, while, for-in-range, break/continue/pass, calls, and the
builtins print/len/range/int/str/float/abs/bool. Comprehensions, classes,
closures, exceptions, **kwargs etc. are deliberately out of v0 -- they slot in
later without changing the format.
"""
from __future__ import annotations
import ast

# ---------------------------------------------------------------------------
# Opcodes. Numbers are grouped so AOT-specialised variants get their own band
# (60-89), exactly like TPython reserves a fast-op range. Every instruction is
# the uniform 4-int record (op, a, b, c); operand meaning is per-opcode below.
# ---------------------------------------------------------------------------
OPS = {
    # --- core (0-19) ---
    "NOP":          0,
    "LOAD_CONST":   1,   # reg[a] = consts[b]
    "LOAD_GLOBAL":  2,   # reg[a] = globals[b]            (b = global slot)
    "STORE_GLOBAL": 3,   # globals[b] = reg[a]
    "MOVE":         4,   # reg[a] = reg[b]
    "RETURN":       5,   # return reg[a]
    "JUMP":         6,   # pc = a
    "JUMP_IF_FALSE": 7,  # if not truthy(reg[a]): pc = b
    "CALL":         8,   # reg[a] = reg[b](reg[b+1..b+c]); c = argcount
    # --- generic arithmetic (20-29): reg[a] = reg[b] OP reg[c] ---
    "ADD":         20, "SUB": 21, "MUL": 22, "DIV": 23,
    "MOD":         24, "FLOORDIV": 25, "POW": 26,
    # --- generic compare (30-39): reg[a] = reg[b] CMP reg[c] ---
    "LT":          30, "LE": 31, "GT": 32, "GE": 33, "EQ": 34, "NE": 35,
    # --- unary (40-49): reg[a] = OP reg[b] ---
    "NEG":         40, "NOT": 41,
    # --- AOT-specialised numeric fast paths (60-89): no tag check ---
    "ADD_NN":      60, "SUB_NN": 61, "MUL_NN": 62,
    "LT_NN":       63, "LE_NN": 64, "GT_NN": 65, "GE_NN": 66,
}
OPNAME = {v: k for k, v in OPS.items()}

# Builtin ids, stored in a const of kind "builtin".
BUILTINS = {n: i for i, n in enumerate(
    ["print", "len", "range", "int", "str", "float", "abs", "bool"])}


class CompileError(Exception):
    pass


# --- constant interning ----------------------------------------------------
class _Consts:
    def __init__(self):
        self.items = []          # list of (kind, payload)
        self._index = {}

    def add(self, kind, payload):
        key = (kind, payload)
        if key in self._index:
            return self._index[key]
        i = len(self.items)
        self.items.append((kind, payload))
        self._index[key] = i
        return i

    def as_json(self):
        out = []
        for kind, payload in self.items:
            # one uniform record; unused slots zeroed so the POD decoder is trivial
            rec = {"t": kind, "i": 0, "d": 0.0, "s": ""}
            if kind == "int":
                rec["i"] = payload
            elif kind == "float":
                rec["d"] = payload
            elif kind in ("str",):
                rec["s"] = payload
            elif kind == "bool":
                rec["i"] = 1 if payload else 0
            elif kind == "none":
                pass
            elif kind == "func":
                rec["i"] = payload          # func index
            elif kind == "builtin":
                rec["i"] = payload          # builtin id
            out.append(rec)
        return out


# --- a function being compiled --------------------------------------------
class _Frame:
    def __init__(self, name, params, extra_locals, is_module):
        self.name = name
        self.is_module = is_module
        self.locals = {}                 # name -> fixed reg (functions only)
        # params occupy regs 0..nparams-1, other locals follow; module scope has
        # no fixed locals (all names are globals)
        ordered = list(params)
        for nm in (extra_locals or []):
            if nm not in self.locals and nm not in ordered:
                ordered.append(nm)
        for nm in ordered:
            self.locals[nm] = len(self.locals)
        self.nparams = len(params)
        self.base = len(self.locals)     # temps start above locals
        self.top = self.base             # register stack pointer
        self.maxreg = self.base
        self.code = []                   # list of [op, a, b, c]
        self.numreg = set()              # regs statically known to hold a number

    # register stack discipline
    def push(self):
        r = self.top
        self.top += 1
        if self.top > self.maxreg:
            self.maxreg = self.top
        return r

    def pop_to(self, mark):
        # forget numeric facts about freed temps
        for r in range(mark, self.top):
            self.numreg.discard(r)
        self.top = mark

    def emit(self, op, a=0, b=0, c=0):
        self.code.append([OPS[op], a, b, c])


class Compiler:
    def __init__(self):
        self.consts = _Consts()
        self.gnames = {}                 # global name -> slot
        self.funcs = []                  # list of _Frame (entry = 0)
        self._pending = []               # (FunctionDef, frame) to compile

    # --- name/slot helpers ---
    def gslot(self, name):
        if name not in self.gnames:
            self.gnames[name] = len(self.gnames)
        return self.gnames[name]

    # ---- entry ----
    def compile_module(self, tree, source_name="<module>"):
        mod = _Frame(source_name, [], [], is_module=True)
        self.funcs.append(mod)
        # pre-bind builtins into globals so `print` etc. resolve as names
        for bname, bid in BUILTINS.items():
            ci = self.consts.add("builtin", bid)
            r = mod.push()
            mod.emit("LOAD_CONST", r, ci)
            mod.emit("STORE_GLOBAL", r, self.gslot(bname))
            mod.pop_to(mod.base)
        for stmt in tree.body:
            self.stmt(mod, stmt)
        mod.emit("RETURN", self._const_reg(mod, ("none", None)))
        # compile any function bodies queued during module lowering
        while self._pending:
            node, frame = self._pending.pop(0)
            self._compile_func_body(node, frame)
        return self.to_program(source_name)

    def _const_reg(self, f, kp):
        ci = self.consts.add(*kp)
        r = f.push()
        f.emit("LOAD_CONST", r, ci)
        if kp[0] in ("int", "float", "bool"):
            f.numreg.add(r)
        return r

    # ===================== statements =====================
    def stmt(self, f, node):
        m = getattr(self, "st_" + type(node).__name__, None)
        if m is None:
            raise CompileError("unsupported statement: %s (line %s)"
                               % (type(node).__name__, getattr(node, "lineno", "?")))
        m(f, node)

    def st_Expr(self, f, node):
        r = self.expr(f, node.value)
        f.pop_to(r)                       # discard value

    def st_Pass(self, f, node):
        pass

    def st_Assign(self, f, node):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise CompileError("v0 assign: single Name target only (line %s)"
                               % node.lineno)
        r = self.expr(f, node.value)
        self._store_name(f, node.targets[0].id, r)
        f.pop_to(r)

    def st_AugAssign(self, f, node):
        if not isinstance(node.target, ast.Name):
            raise CompileError("v0 augassign: Name target only (line %s)" % node.lineno)
        name = node.target.id
        rb = self._load_name(f, name)
        rc = self.expr(f, node.value)
        self._binop(f, node.op, rb, rb, rc)
        f.pop_to(rb + 1)
        self._store_name(f, name, rb)
        f.pop_to(rb)

    def st_Return(self, f, node):
        if node.value is None:
            r = self._const_reg(f, ("none", None))
        else:
            r = self.expr(f, node.value)
        f.emit("RETURN", r)
        f.pop_to(r)

    def st_If(self, f, node):
        rc = self.expr(f, node.test)
        jf = len(f.code); f.emit("JUMP_IF_FALSE", rc, 0)
        f.pop_to(rc)
        for s in node.body:
            self.stmt(f, s)
        if node.orelse:
            jend = len(f.code); f.emit("JUMP", 0)
            f.code[jf][2] = len(f.code)       # false -> else
            for s in node.orelse:
                self.stmt(f, s)
            f.code[jend][1] = len(f.code)
        else:
            f.code[jf][2] = len(f.code)

    def st_While(self, f, node):
        top = len(f.code)
        rc = self.expr(f, node.test)
        jf = len(f.code); f.emit("JUMP_IF_FALSE", rc, 0)
        f.pop_to(rc)
        loop = _Loop(top)
        self._loops.append(loop)
        for s in node.body:
            self.stmt(f, s)
        f.emit("JUMP", top)
        end = len(f.code)
        f.code[jf][2] = end
        self._loops.pop()
        for pc in loop.breaks:
            f.code[pc][1] = end

    def st_For(self, f, node):
        # v0: only `for <name> in range(...)`, lowered to a counter while-loop.
        # The fused compare+increment op (TPython op 80) is the specialiser's job;
        # here we emit generic ops, which the numeric fast-path already upgrades.
        if not (isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "range"):
            raise CompileError("v0 for: only `for x in range(...)` (line %s)" % node.lineno)
        if not isinstance(node.target, ast.Name):
            raise CompileError("v0 for: Name target only (line %s)" % node.lineno)
        args = node.iter.args
        if len(args) == 1:
            start, stop, step = ast.Constant(0), args[0], ast.Constant(1)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], ast.Constant(1)
        else:
            start, stop, step = args[0], args[1], args[2]
        name = node.target.id
        # i = start
        rs = self.expr(f, start); self._store_name(f, name, rs); f.pop_to(rs)
        # stop/step into stable temps (recomputed each turn for v0 simplicity)
        top = len(f.code)
        ri = self._load_name(f, name)
        rstop = self.expr(f, stop)
        f.emit("LT", ri, ri, rstop)        # ri = (i < stop); numeric -> LT_NN
        self._maybe_num_cmp(f, ri)
        jf = len(f.code); f.emit("JUMP_IF_FALSE", ri, 0)
        f.pop_to(ri)
        loop = _Loop(top); self._loops.append(loop)
        for s in node.body:
            self.stmt(f, s)
        # i += step
        ri2 = self._load_name(f, name)
        rstep = self.expr(f, step)
        self._binop_op(f, "ADD", ri2, ri2, rstep)
        f.pop_to(ri2 + 1); self._store_name(f, name, ri2); f.pop_to(ri2)
        f.emit("JUMP", top)
        end = len(f.code); f.code[jf][2] = end
        self._loops.pop()
        for pc in loop.breaks:
            f.code[pc][1] = end

    def st_Break(self, f, node):
        if not self._loops:
            raise CompileError("break outside loop (line %s)" % node.lineno)
        pc = len(f.code); f.emit("JUMP", 0)
        self._loops[-1].breaks.append(pc)

    def st_Continue(self, f, node):
        if not self._loops:
            raise CompileError("continue outside loop (line %s)" % node.lineno)
        f.emit("JUMP", self._loops[-1].top)

    def st_FunctionDef(self, f, node):
        if not f.is_module:
            raise CompileError("v0: nested functions unsupported (line %s)" % node.lineno)
        params = [a.arg for a in node.args.args]
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.defaults:
            raise CompileError("v0 def: positional params only (line %s)" % node.lineno)
        extra = _collect_locals(node)
        frame = _Frame(node.name, params, extra, is_module=False)
        # annotation-driven typing: int/float params start known-numeric, so
        # arithmetic on them lowers to the *_NN fast ops (py2c annotates heavily)
        for ai, arg in enumerate(node.args.args):
            ann = _ann_name(arg.annotation)
            if ann in ("int", "float", "long", "double") and arg.arg in frame.locals:
                frame.numreg.add(frame.locals[arg.arg])
        idx = len(self.funcs)
        self.funcs.append(frame)
        self._pending.append((node, frame))
        # bind the function object into a global slot
        ci = self.consts.add("func", idx)
        r = f.push(); f.emit("LOAD_CONST", r, ci)
        f.emit("STORE_GLOBAL", r, self.gslot(node.name)); f.pop_to(r)

    def _compile_func_body(self, node, frame):
        self._loops = []
        for s in node.body:
            self.stmt(frame, s)
        frame.emit("RETURN", self._const_reg(frame, ("none", None)))

    # ===================== expressions ====================
    # each returns the register holding the result, leaving f.top one above it
    def expr(self, f, node):
        m = getattr(self, "ex_" + type(node).__name__, None)
        if m is None:
            raise CompileError("unsupported expr: %s (line %s)"
                               % (type(node).__name__, getattr(node, "lineno", "?")))
        return m(f, node)

    def ex_Constant(self, f, node):
        v = node.value
        if isinstance(v, bool):
            return self._const_reg(f, ("bool", v))
        if isinstance(v, int):
            return self._const_reg(f, ("int", v))
        if isinstance(v, float):
            return self._const_reg(f, ("float", v))
        if isinstance(v, str):
            return self._const_reg(f, ("str", v))
        if v is None:
            return self._const_reg(f, ("none", None))
        raise CompileError("v0 const: unsupported literal %r" % (v,))

    def ex_Name(self, f, node):
        return self._load_name(f, node.id)

    def ex_BinOp(self, f, node):
        rb = self.expr(f, node.left)
        rc = self.expr(f, node.right)
        self._binop(f, node.op, rb, rb, rc)
        f.pop_to(rb + 1)
        return rb

    def ex_UnaryOp(self, f, node):
        r = self.expr(f, node.operand)
        if isinstance(node.op, ast.USub):
            f.emit("NEG", r, r)
        elif isinstance(node.op, ast.Not):
            f.emit("NOT", r, r); f.numreg.discard(r)
        else:
            raise CompileError("v0 unary: only - and not")
        return r

    def ex_Compare(self, f, node):
        if len(node.ops) != 1:
            raise CompileError("v0 compare: single comparator only (line %s)" % node.lineno)
        rb = self.expr(f, node.left)
        rc = self.expr(f, node.comparators[0])
        op = node.ops[0]
        name = {ast.Lt: "LT", ast.LtE: "LE", ast.Gt: "GT", ast.GtE: "GE",
                ast.Eq: "EQ", ast.NotEq: "NE"}.get(type(op))
        if name is None:
            raise CompileError("v0 compare: unsupported op %s" % type(op).__name__)
        numeric = rb in f.numreg and rc in f.numreg
        f.emit(name + ("_NN" if numeric and name in ("LT", "LE", "GT", "GE") else ""),
               rb, rb, rc)
        f.pop_to(rb + 1)
        f.numreg.discard(rb)              # result is a bool
        return rb

    def ex_BoolOp(self, f, node):
        # short-circuit and/or; result reg holds the last evaluated operand
        r = self.expr(f, node.values[0])
        jumps = []
        for v in node.values[1:]:
            if isinstance(node.op, ast.And):
                jumps.append(len(f.code)); f.emit("JUMP_IF_FALSE", r, 0)
            else:  # Or: jump out when truthy -> emulate with NOT-test
                # if truthy, skip the rest; encode as: if not r goto next-eval
                tmp = f.push(); f.emit("NOT", tmp, r); f.pop_to(tmp)
                jumps.append(len(f.code)); f.emit("JUMP_IF_FALSE", tmp, 0)
            f.pop_to(r)                    # reuse r for next operand
            r2 = self.expr(f, v)
            assert r2 == r                 # stack discipline keeps them aligned
        end = len(f.code)
        for j in jumps:
            f.code[j][2] = end
        f.numreg.discard(r)
        return r

    def ex_Call(self, f, node):
        if node.keywords:
            raise CompileError("v0 call: no keyword args (line %s)" % node.lineno)
        # contiguous window: callable, then args
        rfun = self.expr(f, node.func)
        n = 0
        for a in node.args:
            ra = self.expr(f, a)
            assert ra == rfun + 1 + n
            n += 1
        f.emit("CALL", rfun, rfun, n)
        f.pop_to(rfun + 1)                 # result lands in rfun
        f.numreg.discard(rfun)
        return rfun

    # ---- shared lowering ----
    def _binop(self, f, op, dst, rb, rc):
        name = {ast.Add: "ADD", ast.Sub: "SUB", ast.Mult: "MUL",
                ast.Div: "DIV", ast.Mod: "MOD", ast.FloorDiv: "FLOORDIV",
                ast.Pow: "POW"}.get(type(op))
        if name is None:
            raise CompileError("v0 binop: unsupported %s" % type(op).__name__)
        self._binop_op(f, name, dst, rb, rc)

    def _binop_op(self, f, name, dst, rb, rc):
        numeric = rb in f.numreg and rc in f.numreg
        if numeric and name in ("ADD", "SUB", "MUL"):
            f.emit(name + "_NN", dst, rb, rc)
            f.numreg.add(dst)
        else:
            f.emit(name, dst, rb, rc)
            if numeric and name in ("FLOORDIV", "MOD", "POW", "DIV"):
                f.numreg.add(dst)
            else:
                f.numreg.discard(dst)

    def _maybe_num_cmp(self, f, r):
        f.numreg.discard(r)

    def _load_name(self, f, name):
        if not f.is_module and name in f.locals:
            src = f.locals[name]
            r = f.push(); f.emit("MOVE", r, src)
            if src in f.numreg:           # numeric fact survives the move
                f.numreg.add(r)
            return r
        r = f.push(); f.emit("LOAD_GLOBAL", r, self.gslot(name))
        return r

    def _store_name(self, f, name, r):
        if not f.is_module:
            if name not in f.locals:
                # new local: give it a fixed reg below temps is hard post-hoc;
                # v0 keeps locals discovered up-front, so treat unknown as global
                pass
            if name in f.locals:
                f.emit("MOVE", f.locals[name], r)
                if r in f.numreg:
                    f.numreg.add(f.locals[name])
                return
        f.emit("STORE_GLOBAL", r, self.gslot(name))

    # ---- serialise ----
    def to_program(self, source_name):
        funcs = []
        for fr in self.funcs:
            funcs.append({
                "name": fr.name,
                "nparams": fr.nparams,
                "nregs": fr.maxreg,
                "code": [{"op": op, "a": a, "b": b, "c": c}
                         for (op, a, b, c) in fr.code],
            })
        names = [None] * len(self.gnames)
        for nm, slot in self.gnames.items():
            names[slot] = nm
        return {
            "version": 1,
            "source": source_name,
            "consts": self.consts.as_json(),
            "names": names,
            "nglobals": len(self.gnames),
            "funcs": funcs,
            "entry": 0,
        }


class _Loop:
    def __init__(self, top):
        self.top = top
        self.breaks = []


def _ann_name(node):
    """The annotation written on a param, as a bare name: `n: int` -> 'int',
    `n: "int"` -> 'int'. Returns None if absent/complex."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip().strip("'\"").rstrip("*").strip()
    return None


# Discover function locals up-front (params + names assigned in the body) so the
# single-pass compiler can map them to fixed registers. Module scope skips this.
def _collect_locals(node):
    found = []
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id not in found:
                    found.append(t.id)
        elif isinstance(n, (ast.AugAssign, ast.For)) and isinstance(getattr(n, "target", None), ast.Name):
            if n.target.id not in found:
                found.append(n.target.id)
    return found


def compile_source(src, source_name="<module>"):
    tree = ast.parse(src, filename=source_name)
    c = Compiler()
    c._loops = []
    # give every top-level def its locals before lowering its body
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            extra = [n for n in _collect_locals(node)]
            node._extra_locals = extra
    prog = c.compile_module(tree, source_name)
    # patch frames so discovered locals exist as fixed regs (params already in)
    return prog


def compile_file(path):
    with open(path, encoding="utf-8") as fh:
        return compile_source(fh.read(), path)


# ===========================================================================
# Reference VM (CPython). Faithful executor of the flattened bytecode, used to
# validate the format against python3 and as the rpython interpreter's oracle.
# ===========================================================================
class VM:
    def __init__(self, prog, out=None):
        self.prog = prog
        self.consts = prog["consts"]
        self.names = prog["names"]
        self.globals = [None] * prog["nglobals"]
        self.funcs = prog["funcs"]
        import sys as _sys
        self.out = out if out is not None else _sys.stdout

    def _const(self, i):
        c = self.consts[i]
        t = c["t"]
        if t == "int":
            return c["i"]
        if t == "float":
            return c["d"]
        if t == "str":
            return c["s"]
        if t == "bool":
            return bool(c["i"])
        if t == "none":
            return None
        if t == "func":
            return ("func", c["i"])
        if t == "builtin":
            return ("builtin", c["i"])
        raise RuntimeError("bad const %r" % (c,))

    def run(self):
        self._call(self.prog["entry"], [])

    def _call(self, fidx, args):
        fn = self.funcs[fidx]
        regs = [None] * max(fn["nregs"], fn["nparams"])
        for i, a in enumerate(args):
            regs[i] = a
        code = fn["code"]
        pc = 0
        while pc < len(code):
            ins = code[pc]; op = ins["op"]; a = ins["a"]; b = ins["b"]; c = ins["c"]
            pc += 1
            if op == 1:    regs[a] = self._const(b)
            elif op == 2:  regs[a] = self.globals[b]
            elif op == 3:  self.globals[b] = regs[a]
            elif op == 4:  regs[a] = regs[b]
            elif op == 5:  return regs[a]
            elif op == 6:  pc = a
            elif op == 7:
                if not regs[a]: pc = b
            elif op == 8:
                callee = regs[b]; ca = [regs[b + 1 + k] for k in range(c)]
                regs[a] = self._invoke(callee, ca)
            elif op == 20: regs[a] = regs[b] + regs[c]
            elif op == 21: regs[a] = regs[b] - regs[c]
            elif op == 22: regs[a] = regs[b] * regs[c]
            elif op == 23: regs[a] = regs[b] / regs[c]
            elif op == 24: regs[a] = regs[b] % regs[c]
            elif op == 25: regs[a] = regs[b] // regs[c]
            elif op == 26: regs[a] = regs[b] ** regs[c]
            elif op == 30: regs[a] = regs[b] < regs[c]
            elif op == 31: regs[a] = regs[b] <= regs[c]
            elif op == 32: regs[a] = regs[b] > regs[c]
            elif op == 33: regs[a] = regs[b] >= regs[c]
            elif op == 34: regs[a] = regs[b] == regs[c]
            elif op == 35: regs[a] = regs[b] != regs[c]
            elif op == 40: regs[a] = -regs[b]
            elif op == 41: regs[a] = not regs[b]
            elif op == 60: regs[a] = regs[b] + regs[c]   # ADD_NN
            elif op == 61: regs[a] = regs[b] - regs[c]
            elif op == 62: regs[a] = regs[b] * regs[c]
            elif op == 63: regs[a] = regs[b] < regs[c]   # LT_NN
            elif op == 64: regs[a] = regs[b] <= regs[c]
            elif op == 65: regs[a] = regs[b] > regs[c]
            elif op == 66: regs[a] = regs[b] >= regs[c]
            else:
                raise RuntimeError("unknown op %d" % op)
        return None

    def _invoke(self, callee, args):
        if isinstance(callee, tuple) and callee[0] == "func":
            return self._call(callee[1], args)
        if isinstance(callee, tuple) and callee[0] == "builtin":
            return self._builtin(callee[1], args)
        raise RuntimeError("not callable: %r" % (callee,))

    def _builtin(self, bid, args):
        name = [k for k, v in BUILTINS.items() if v == bid][0]
        if name == "print":
            self.out.write(" ".join(_pystr(x) for x in args) + "\n")
            return None
        if name == "len":   return len(args[0])
        if name == "range": return list(range(*args))
        if name == "int":   return int(args[0]) if args else 0
        if name == "str":   return _pystr(args[0]) if args else ""
        if name == "float": return float(args[0]) if args else 0.0
        if name == "abs":   return abs(args[0])
        if name == "bool":  return bool(args[0]) if args else False
        raise RuntimeError("unknown builtin id %d" % bid)


def _pystr(x):
    if x is True:  return "True"
    if x is False: return "False"
    if x is None:  return "None"
    return str(x)


# ---- disassembler (debug) -------------------------------------------------
def disassemble(prog):
    lines = []
    for fi, fn in enumerate(prog["funcs"]):
        lines.append("func %d %s(nparams=%d, nregs=%d)"
                     % (fi, fn["name"], fn["nparams"], fn["nregs"]))
        for pc, ins in enumerate(fn["code"]):
            lines.append("  %3d  %-12s %3d %3d %3d"
                         % (pc, OPNAME.get(ins["op"], "?%d" % ins["op"]),
                            ins["a"], ins["b"], ins["c"]))
    return "\n".join(lines)

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
    # --- containers (9-19) ---
    "BUILD_LIST":   9,   # reg[a] = [regs[b..b+c-1]]
    "BUILD_TUPLE": 10,   # reg[a] = (regs[b..b+c-1])
    "BUILD_DICT":  11,   # reg[a] = {regs[b],regs[b+1] : ...}  c pairs
    "BUILD_SET":   12,   # reg[a] = {regs[b..b+c-1]}
    "INDEX":       13,   # reg[a] = reg[b][reg[c]]
    "SETINDEX":    14,   # reg[a][reg[b]] = reg[c]
    "ITER_NEW":    15,   # reg[a] = iter(reg[b])
    "ITER_NEXT":   16,   # if next: reg[a]=next, pc++ ; else pc=c
    "CONTAINS":    17,   # reg[a] = (reg[c] in reg[b])
    "LIST_APPEND": 18,   # reg[a].append(reg[b])
    "SET_ADD":     19,   # reg[a].add(reg[b])
    # --- classes (50-52) ---
    "LOAD_ATTR":   50,   # reg[a] = reg[b].<consts[c]>
    "STORE_ATTR":  51,   # reg[a].<consts[c]> = reg[b]
    "LOAD_METHOD": 52,   # reg[a] = bound-method reg[b].<consts[c]>
    # --- exceptions (70-75) ---
    "SETUP_EXCEPT": 70,  # push handler block; a = dispatch pc
    "POP_BLOCK":    71,  # pop handler block (try body finished normally)
    "RAISE":        72,  # raise reg[a]
    "RERAISE":      73,  # re-raise the in-flight exception
    "LOAD_EXC":     74,  # reg[a] = current in-flight exception value
    "EXC_MATCH":    75,  # reg[a] = isinstance(in-flight exc, class reg[b])
    # --- generic arithmetic (20-29): reg[a] = reg[b] OP reg[c] ---
    "ADD":         20, "SUB": 21, "MUL": 22, "DIV": 23,
    "MOD":         24, "FLOORDIV": 25, "POW": 26,
    "BITOR":       27, "BITAND": 28, "BITXOR": 29,  # int bitwise / set ops
    # --- generic compare (30-39): reg[a] = reg[b] CMP reg[c] ---
    "LT":          30, "LE": 31, "GT": 32, "GE": 33, "EQ": 34, "NE": 35,
    "SHL":         36, "SHR": 37,        # reg[a] = reg[b] << / >> reg[c]
    "SLICE":       38,                   # reg[a] = reg[a][lo:hi:step], (lo,hi,
                                         # step) = reg[b],reg[b+1],reg[b+2]
    # --- unary (40-49): reg[a] = OP reg[b] ---
    "NEG":         40, "NOT": 41,
    # --- AOT-specialised numeric fast paths (60-89): no tag check ---
    "ADD_NN":      60, "SUB_NN": 61, "MUL_NN": 62,
    "LT_NN":       63, "LE_NN": 64, "GT_NN": 65, "GE_NN": 66,
}
OPNAME = {v: k for k, v in OPS.items()}

# Builtin ids, stored in a const of kind "builtin".
BUILTINS = {n: i for i, n in enumerate(
    ["print", "len", "range", "int", "str", "float", "abs", "bool",
     "list", "dict", "set", "tuple", "repr", "sorted", "sum", "min", "max",
     "isinstance", "enumerate", "zip", "any", "all", "ord", "chr",
     "reversed", "getattr", "hasattr", "type"])}

# Method ids (receiver passed as arg0). Distinct id band (100+) so do_builtin can
# tell a global builtin from a bound method; invoked through the same CALL path.
METHODS = {
    "append": 100, "pop": 101, "get": 102, "keys": 103, "values": 104,
    "items": 105, "add": 106, "split": 107, "join": 108, "strip": 109,
    "startswith": 110, "endswith": 111, "find": 112, "replace": 113,
    "upper": 114, "lower": 115,
    "extend": 116, "insert": 117, "index": 118, "count": 119,
    "update": 120, "setdefault": 121, "splitlines": 122, "rstrip": 123,
    "lstrip": 124, "isdigit": 125, "isupper": 126, "islower": 127,
    "isalpha": 128, "isalnum": 129,
}


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
            elif kind == "class":
                rec["i"] = payload          # class id
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
        self.defaults = []               # per-param const index, -1 = required

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
        self.classes = []                # list of {name, base, methods:[...]}
        self._class_id = {}              # class name -> id

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

    def st_Assert(self, f, node):
        # v0: the test is evaluated (so any side effects occur) but a failing
        # assertion does not raise (no AssertionError type yet). Invariant-style
        # asserts that hold behave identically to CPython.
        r = self.expr(f, node.test)
        f.pop_to(r)

    def st_Assign(self, f, node):
        if len(node.targets) != 1:
            raise CompileError("v0 assign: single target (line %s)" % node.lineno)
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name):
            r = self.expr(f, node.value)
            self._store_name(f, tgt.id, r)
            f.pop_to(r)
            return
        if isinstance(tgt, ast.Attribute):
            robj = self.expr(f, tgt.value)
            rval = self.expr(f, node.value)
            cn = self.consts.add("str", tgt.attr)
            f.emit("STORE_ATTR", robj, rval, cn)
            f.pop_to(robj)
            return
        if isinstance(tgt, ast.Subscript):
            if isinstance(tgt.slice, ast.Slice):
                raise CompileError("v0: slice assignment unsupported (line %s)" % node.lineno)
            robj = self.expr(f, tgt.value)
            ridx = self.expr(f, tgt.slice)
            rval = self.expr(f, node.value)
            f.emit("SETINDEX", robj, ridx, rval)
            f.pop_to(robj)
            return
        if isinstance(tgt, (ast.Tuple, ast.List)):
            # a, b = <iterable>: index the (materialised) value positionally
            rseq = self.expr(f, node.value)
            for i, elt in enumerate(tgt.elts):
                if not isinstance(elt, ast.Name):
                    raise CompileError("v0 unpack: Name targets only (line %s)" % node.lineno)
                ridx = self._const_reg(f, ("int", i))
                f.emit("INDEX", ridx, rseq, ridx)
                self._store_name(f, elt.id, ridx)
                f.pop_to(ridx)
            f.pop_to(rseq)
            return
        raise CompileError("v0 assign: unsupported target %s (line %s)"
                           % (type(tgt).__name__, node.lineno))

    def st_AnnAssign(self, f, node):
        # `x: T = v` (annotation ignored); bare `x: T` is a no-op declaration
        if node.value is None:
            return
        if isinstance(node.target, ast.Name):
            r = self.expr(f, node.value)
            self._store_name(f, node.target.id, r)
            f.pop_to(r)
            return
        if isinstance(node.target, ast.Attribute):
            robj = self.expr(f, node.target.value)
            rval = self.expr(f, node.value)
            cn = self.consts.add("str", node.target.attr)
            f.emit("STORE_ATTR", robj, rval, cn)
            f.pop_to(robj)
            return
        raise CompileError("v0 annassign: Name/Attribute target only (line %s)"
                           % node.lineno)

    def st_AugAssign(self, f, node):
        if isinstance(node.target, ast.Name):
            name = node.target.id
            rb = self._load_name(f, name)
            rc = self.expr(f, node.value)
            self._binop(f, node.op, rb, rb, rc)
            f.pop_to(rb + 1)
            self._store_name(f, name, rb)
            f.pop_to(rb)
            return
        # `obj.attr op= val` / `obj[idx] op= val`: evaluate the target object
        # (and index) exactly once, load the current value, apply the op, and
        # store back through the same object/index registers.
        if isinstance(node.target, ast.Attribute):
            robj = self.expr(f, node.target.value)
            cn = self.consts.add("str", node.target.attr)
            rcur = f.push()
            f.emit("LOAD_ATTR", rcur, robj, cn)
            rval = self.expr(f, node.value)
            self._binop(f, node.op, rcur, rcur, rval)
            f.emit("STORE_ATTR", robj, rcur, cn)
            f.pop_to(robj)
            return
        if isinstance(node.target, ast.Subscript):
            if isinstance(node.target.slice, ast.Slice):
                raise CompileError(
                    "v0: slice aug-assign unsupported (line %s)" % node.lineno)
            robj = self.expr(f, node.target.value)
            ridx = self.expr(f, node.target.slice)
            rcur = f.push()
            f.emit("INDEX", rcur, robj, ridx)
            rval = self.expr(f, node.value)
            self._binop(f, node.op, rcur, rcur, rval)
            f.emit("SETINDEX", robj, ridx, rcur)
            f.pop_to(robj)
            return
        raise CompileError(
            "v0 augassign: Name/Attribute/Subscript target only (line %s)"
            % node.lineno)

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
        tgt = node.target
        is_range = (isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Name)
                    and node.iter.func.id == "range")
        if is_range:
            if not isinstance(tgt, ast.Name):
                raise CompileError("v0 for-range: Name target only (line %s)" % node.lineno)
            self._for_range(f, tgt.id, node)
            return
        if not isinstance(tgt, (ast.Name, ast.Tuple, ast.List)):
            raise CompileError("v0 for: Name/tuple target only (line %s)" % node.lineno)
        # general iterable: ITER_NEW once, ITER_NEXT per turn (target = loop end)
        rit = self.expr(f, node.iter)
        f.emit("ITER_NEW", rit, rit)         # rit now holds the iterator
        f.numreg.discard(rit)
        top = len(f.code)
        rx = f.push()
        nx = len(f.code); f.emit("ITER_NEXT", rx, rit, 0)   # c patched to end
        if isinstance(tgt, ast.Name):
            self._store_name(f, tgt.id, rx)
            f.pop_to(rit + 1)
        else:                                # unpack tuple element into names
            for i, elt in enumerate(tgt.elts):
                if not isinstance(elt, ast.Name):
                    raise CompileError("v0 for-unpack: Name targets only (line %s)" % node.lineno)
                ridx = self._const_reg(f, ("int", i))
                f.emit("INDEX", ridx, rx, ridx)
                self._store_name(f, elt.id, ridx)
                f.pop_to(ridx)
            f.pop_to(rit + 1)
        loop = _Loop(top); self._loops.append(loop)
        for s in node.body:
            self.stmt(f, s)
        f.emit("JUMP", top)
        end = len(f.code)
        f.code[nx][3] = end
        self._loops.pop()
        f.pop_to(rit)
        for pc in loop.breaks:
            f.code[pc][1] = end

    def _for_range(self, f, name, node):
        # `for <name> in range(...)`, lowered to a counter while-loop. Generic
        # ops here; the numeric fast-path upgrades the compare/increment.
        args = node.iter.args
        if len(args) == 1:
            start, stop, step = ast.Constant(0), args[0], ast.Constant(1)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], ast.Constant(1)
        else:
            start, stop, step = args[0], args[1], args[2]
        rs = self.expr(f, start); self._store_name(f, name, rs); f.pop_to(rs)
        top = len(f.code)
        ri = self._load_name(f, name)
        rstop = self.expr(f, stop)
        f.emit("LT", ri, ri, rstop)
        self._maybe_num_cmp(f, ri)
        jf = len(f.code); f.emit("JUMP_IF_FALSE", ri, 0)
        f.pop_to(ri)
        loop = _Loop(top); self._loops.append(loop)
        for s in node.body:
            self.stmt(f, s)
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

    def st_Try(self, f, node):
        if node.finalbody:
            raise CompileError("v0 try: finally not supported (line %s)" % node.lineno)
        setup = len(f.code); f.emit("SETUP_EXCEPT", 0)
        for s in node.body:
            self.stmt(f, s)
        f.emit("POP_BLOCK")
        if node.orelse:
            for s in node.orelse:
                self.stmt(f, s)
        end_jumps = [len(f.code)]; f.emit("JUMP", 0)
        f.code[setup][1] = len(f.code)        # dispatch entry
        for h in node.handlers:
            jf = None
            if h.type is not None:
                rexc = f.push(); f.emit("LOAD_EXC", rexc)
                rcls = self.expr(f, h.type)
                assert rcls == rexc + 1
                f.emit("EXC_MATCH", rexc, rcls)
                f.pop_to(rexc + 1)
                jf = len(f.code); f.emit("JUMP_IF_FALSE", rexc, 0)
                f.pop_to(rexc)
            if h.name:
                re = f.push(); f.emit("LOAD_EXC", re)
                self._store_name(f, h.name, re); f.pop_to(re)
            for s in h.body:
                self.stmt(f, s)
            end_jumps.append(len(f.code)); f.emit("JUMP", 0)
            if jf is not None:
                f.code[jf][2] = len(f.code)   # type mismatch -> next handler
        f.emit("RERAISE")                     # nothing matched -> propagate
        after = len(f.code)
        for ej in end_jumps:
            f.code[ej][1] = after

    def st_Raise(self, f, node):
        if node.exc is None:
            f.emit("RERAISE")
            return
        if node.cause is not None:
            raise CompileError("v0 raise: no `from` cause (line %s)" % node.lineno)
        r = self.expr(f, node.exc)
        f.emit("RAISE", r)
        f.pop_to(r)

    def st_ClassDef(self, f, node):
        if node.keywords or node.decorator_list:
            raise CompileError("v0 class: no keywords/decorators (line %s)" % node.lineno)
        base = -1
        if node.bases:
            if len(node.bases) != 1 or not isinstance(node.bases[0], ast.Name):
                raise CompileError("v0 class: single Name base only (line %s)" % node.lineno)
            base = self._class_id.get(node.bases[0].id, -1)
        cid = len(self.classes)
        self._class_id[node.name] = cid
        methods = []
        self.classes.append({"name": node.name, "base": base, "methods": methods})
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                fidx = self._compile_method(item)
                methods.append({"name": item.name, "func": fidx})
            # class-level (non-method) statements are ignored in v0
        ci = self.consts.add("class", cid)
        r = f.push(); f.emit("LOAD_CONST", r, ci)
        f.emit("STORE_GLOBAL", r, self.gslot(node.name)); f.pop_to(r)

    def _literal_const_index(self, node):
        # A unary-minus on a numeric literal (`-1`, `-2.5`) is a constant for
        # default-argument purposes: fold it to the negated literal.
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
                and isinstance(node.operand, ast.Constant) \
                and isinstance(node.operand.value, (int, float)) \
                and not isinstance(node.operand.value, bool):
            v = node.operand.value
            kind = "float" if isinstance(v, float) else "int"
            return self.consts.add(kind, -v)
        if not isinstance(node, ast.Constant):
            raise CompileError("v0 default: constant literal only (line %s)"
                               % getattr(node, "lineno", "?"))
        v = node.value
        if isinstance(v, bool):
            return self.consts.add("bool", v)     # bool before int (subclass)
        if isinstance(v, int):
            return self.consts.add("int", v)
        if isinstance(v, float):
            return self.consts.add("float", v)
        if isinstance(v, str):
            return self.consts.add("str", v)
        if v is None:
            return self.consts.add("none", None)
        raise CompileError("v0 default: unsupported literal %r" % (v,))

    def _build_defaults(self, node, nparams):
        # defaults apply to the trailing params; store one const index per param
        # (-1 = required). Restricting defaults to literals keeps them constant
        # (CPython evaluates defaults once at def time, which literals satisfy).
        defs = [-1] * nparams
        dlist = node.args.defaults
        start = nparams - len(dlist)
        for j, dexpr in enumerate(dlist):
            defs[start + j] = self._literal_const_index(dexpr)
        return defs

    def _compile_method(self, node):
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
            raise CompileError("v0 method: no *args/**kwargs/kwonly (line %s)" % node.lineno)
        params = [a.arg for a in node.args.args]
        extra = _collect_locals(node)
        frame = _Frame(node.name, params, extra, is_module=False)
        frame.defaults = self._build_defaults(node, len(params))
        for arg in node.args.args:
            ann = _ann_name(arg.annotation)
            if ann in ("int", "float", "long", "double") and arg.arg in frame.locals:
                frame.numreg.add(frame.locals[arg.arg])
        idx = len(self.funcs)
        self.funcs.append(frame)
        self._pending.append((node, frame))
        return idx

    def st_FunctionDef(self, f, node):
        if not f.is_module:
            raise CompileError("v0: nested functions unsupported (line %s)" % node.lineno)
        params = [a.arg for a in node.args.args]
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
            raise CompileError("v0 def: no *args/**kwargs/kwonly (line %s)" % node.lineno)
        extra = _collect_locals(node)
        frame = _Frame(node.name, params, extra, is_module=False)
        frame.defaults = self._build_defaults(node, len(params))
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

    def ex_IfExp(self, f, node):
        # ternary `body if test else orelse`; both arms land in the same reg
        rc = self.expr(f, node.test)
        jf = len(f.code); f.emit("JUMP_IF_FALSE", rc, 0)
        f.pop_to(rc)
        self.expr(f, node.body)               # -> rc
        f.pop_to(rc)                          # value persists in reg rc at runtime
        jend = len(f.code); f.emit("JUMP", 0)
        f.code[jf][2] = len(f.code)           # false -> orelse
        self.expr(f, node.orelse)             # -> rc
        f.code[jend][1] = len(f.code)
        f.numreg.discard(rc)
        return rc

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
        op = node.ops[0]
        # membership: `x in seq` / `x not in seq`
        if isinstance(op, (ast.In, ast.NotIn)):
            rx = self.expr(f, node.left)
            rseq = self.expr(f, node.comparators[0])
            f.emit("CONTAINS", rx, rseq, rx)   # reg[rx] = (rx in rseq)
            f.pop_to(rx + 1)
            if isinstance(op, ast.NotIn):
                f.emit("NOT", rx, rx)
            f.numreg.discard(rx)
            return rx
        rb = self.expr(f, node.left)
        rc = self.expr(f, node.comparators[0])
        name = {ast.Lt: "LT", ast.LtE: "LE", ast.Gt: "GT", ast.GtE: "GE",
                ast.Eq: "EQ", ast.NotEq: "NE",
                ast.Is: "EQ", ast.IsNot: "NE"}.get(type(op))
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
        # method call `recv.meth(args)`: callable = method-id builtin, receiver
        # is arg0, then the real args -- reuses the CALL window machinery.
        if isinstance(node.func, ast.Attribute):
            # all `obj.meth(args)` go through LOAD_METHOD; the runtime resolves
            # a user method (instance receiver) vs a builtin method (container/
            # string receiver) by type, so names like .get/.pop/.add never clash.
            meth = node.func.attr
            rfun = f.push()
            ro = self.expr(f, node.func.value)
            assert ro == rfun + 1
            cn = self.consts.add("str", meth)
            f.emit("LOAD_METHOD", rfun, ro, cn)   # bound method captures self
            f.pop_to(rfun + 1)
            n = 0
            for a in node.args:
                ra = self.expr(f, a)
                assert ra == rfun + 1 + n
                n += 1
            f.emit("CALL", rfun, rfun, n)
            f.pop_to(rfun + 1); f.numreg.discard(rfun)
            return rfun
        # plain call
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

    # ---- container / subscript / comprehension expressions ----
    def ex_List(self, f, node):
        base = f.top
        for e in node.elts:
            self.expr(f, e)
        f.emit("BUILD_LIST", base, base, len(node.elts))
        f.pop_to(base + 1); f.numreg.discard(base)
        return base

    def ex_Tuple(self, f, node):
        base = f.top
        for e in node.elts:
            self.expr(f, e)
        f.emit("BUILD_TUPLE", base, base, len(node.elts))
        f.pop_to(base + 1); f.numreg.discard(base)
        return base

    def ex_Set(self, f, node):
        base = f.top
        for e in node.elts:
            self.expr(f, e)
        f.emit("BUILD_SET", base, base, len(node.elts))
        f.pop_to(base + 1); f.numreg.discard(base)
        return base

    def ex_Dict(self, f, node):
        base = f.top
        for k, v in zip(node.keys, node.values):
            self.expr(f, k)
            self.expr(f, v)
        f.emit("BUILD_DICT", base, base, len(node.keys))
        f.pop_to(base + 1); f.numreg.discard(base)
        return base

    def ex_Attribute(self, f, node):
        rb = self.expr(f, node.value)
        cn = self.consts.add("str", node.attr)
        f.emit("LOAD_ATTR", rb, rb, cn)
        f.numreg.discard(rb)
        return rb

    def ex_Subscript(self, f, node):
        if isinstance(node.slice, ast.Slice):
            sl = node.slice
            rb = self.expr(f, node.value)               # seq at rb
            if sl.lower is None:
                rlo = self._const_reg(f, ("int", 0))
            else:
                rlo = self.expr(f, sl.lower)
            assert rlo == rb + 1
            if sl.upper is None:
                rhi = self._const_reg(f, ("none", None))  # sentinel -> end
            else:
                rhi = self.expr(f, sl.upper)
            assert rhi == rb + 2
            if sl.step is None:
                rst = self._const_reg(f, ("int", 1))
            else:
                rst = self.expr(f, sl.step)
            assert rst == rb + 3
            f.emit("SLICE", rb, rb + 1, 0)              # (lo,hi,step) at rb+1..rb+3
            f.pop_to(rb + 1); f.numreg.discard(rb)
            return rb
        rb = self.expr(f, node.value)
        rc = self.expr(f, node.slice)
        f.emit("INDEX", rb, rb, rc)
        f.pop_to(rb + 1); f.numreg.discard(rb)
        return rb

    def ex_ListComp(self, f, node):
        return self._comp(f, node, "list")

    def ex_SetComp(self, f, node):
        return self._comp(f, node, "set")

    def ex_GeneratorExp(self, f, node):
        return self._comp(f, node, "list")   # materialised; good enough for v0

    def ex_DictComp(self, f, node):
        return self._comp(f, node, "dict")

    def _comp(self, f, node, kind):
        acc = f.push()
        if kind == "list":
            f.emit("BUILD_LIST", acc, acc, 0)
        elif kind == "set":
            f.emit("BUILD_SET", acc, acc, 0)
        else:
            f.emit("BUILD_DICT", acc, acc, 0)
        f.numreg.discard(acc)
        self._comp_gen(f, node, kind, acc, 0)
        return acc

    def _comp_gen(self, f, node, kind, acc, gi):
        gens = node.generators
        if gi >= len(gens):
            if kind == "dict":
                rk = self.expr(f, node.key)
                rv = self.expr(f, node.value)
                f.emit("SETINDEX", acc, rk, rv)
                f.pop_to(rk)
            else:
                re = self.expr(f, node.elt)
                f.emit("LIST_APPEND" if kind == "list" else "SET_ADD", acc, re)
                f.pop_to(re)
            return
        gen = gens[gi]
        tgt = gen.target
        if not isinstance(tgt, (ast.Name, ast.Tuple, ast.List)):
            raise CompileError("v0 comp: Name/tuple target only")
        rit = self.expr(f, gen.iter)
        f.emit("ITER_NEW", rit, rit); f.numreg.discard(rit)
        top = len(f.code)
        rx = f.push()
        nx = len(f.code); f.emit("ITER_NEXT", rx, rit, 0)
        if isinstance(tgt, ast.Name):
            self._store_name(f, tgt.id, rx)
            f.pop_to(rit + 1)
        else:                               # unpack tuple element into names
            for i, elt in enumerate(tgt.elts):
                if not isinstance(elt, ast.Name):
                    raise CompileError("v0 comp-unpack: Name targets only")
                ridx = self._const_reg(f, ("int", i))
                f.emit("INDEX", ridx, rx, ridx)
                self._store_name(f, elt.id, ridx)
                f.pop_to(ridx)
            f.pop_to(rit + 1)
        skip_jumps = []
        for cond in gen.ifs:
            rc = self.expr(f, cond)
            skip_jumps.append(len(f.code)); f.emit("JUMP_IF_FALSE", rc, 0)
            f.pop_to(rc)
        self._comp_gen(f, node, kind, acc, gi + 1)
        for sj in skip_jumps:
            f.code[sj][2] = len(f.code)     # failed if -> next iteration
        f.emit("JUMP", top)
        end = len(f.code); f.code[nx][3] = end
        f.pop_to(rit)

    # ---- shared lowering ----
    def _binop(self, f, op, dst, rb, rc):
        name = {ast.Add: "ADD", ast.Sub: "SUB", ast.Mult: "MUL",
                ast.Div: "DIV", ast.Mod: "MOD", ast.FloorDiv: "FLOORDIV",
                ast.Pow: "POW", ast.BitOr: "BITOR", ast.BitAnd: "BITAND",
                ast.BitXor: "BITXOR", ast.LShift: "SHL",
                ast.RShift: "SHR"}.get(type(op))
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
                "defaults": fr.defaults,
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
            "classes": [
                {"cname": ci["name"], "base": ci["base"],
                 "methods": [{"mname": m["name"], "mfunc": m["func"]}
                             for m in ci["methods"]]}
                for ci in self.classes
            ],
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
                elif isinstance(t, (ast.Tuple, ast.List)):
                    for e in t.elts:
                        if isinstance(e, ast.Name) and e.id not in found:
                            found.append(e.id)
        elif isinstance(n, (ast.AugAssign, ast.For)) and isinstance(getattr(n, "target", None), ast.Name):
            if n.target.id not in found:
                found.append(n.target.id)
        elif isinstance(n, (ast.For, ast.comprehension)) and isinstance(getattr(n, "target", None), (ast.Tuple, ast.List)):
            for e in n.target.elts:
                if isinstance(e, ast.Name) and e.id not in found:
                    found.append(e.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            if n.target.id not in found:
                found.append(n.target.id)
        elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
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
        self._exc_flag = False
        self._exc_val = None

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
        if t == "class":
            return ("class", c["i"])
        raise RuntimeError("bad const %r" % (c,))

    def run(self):
        self._call(self.prog["entry"], [])

    def _call(self, fidx, args):
        fn = self.funcs[fidx]
        regs = [None] * max(fn["nregs"], fn["nparams"])
        for i, a in enumerate(args):
            regs[i] = a
        defs = fn.get("defaults", [])       # supply defaults for missing params
        for i in range(len(args), fn["nparams"]):
            if i < len(defs) and defs[i] >= 0:
                regs[i] = self._const(defs[i])
        code = fn["code"]
        blocks = []
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
            elif op == 9:                       # BUILD_LIST
                regs[a] = [regs[b + k] for k in range(c)]
            elif op == 10:                      # BUILD_TUPLE
                regs[a] = tuple(regs[b + k] for k in range(c))
            elif op == 11:                      # BUILD_DICT
                d = {}
                for k in range(c):
                    d[regs[b + 2 * k]] = regs[b + 2 * k + 1]
                regs[a] = d
            elif op == 12:                      # BUILD_SET
                regs[a] = set(regs[b + k] for k in range(c))
            elif op == 13:                      # INDEX
                regs[a] = regs[b][regs[c]]
            elif op == 14:                      # SETINDEX
                regs[a][regs[b]] = regs[c]
            elif op == 15:                      # ITER_NEW
                regs[a] = [list(regs[b]), 0]
            elif op == 16:                      # ITER_NEXT
                it = regs[b]
                if it[1] < len(it[0]):
                    regs[a] = it[0][it[1]]; it[1] += 1
                else:
                    pc = c
            elif op == 17:                      # CONTAINS
                regs[a] = regs[c] in regs[b]
            elif op == 18:                      # LIST_APPEND
                regs[a].append(regs[b])
            elif op == 19:                      # SET_ADD
                regs[a].add(regs[b])
            elif op == 50:                      # LOAD_ATTR
                regs[a] = regs[b].attrs[self._const(c)]
            elif op == 51:                      # STORE_ATTR
                regs[a].attrs[self._const(c)] = regs[b]
            elif op == 52:                      # LOAD_METHOD
                obj = regs[b]; name = self._const(c)
                if isinstance(obj, _Inst):
                    regs[a] = ("bound", self._lookup_method(obj.cid, name), obj)
                else:                            # builtin method on a container/str
                    regs[a] = ("boundb", METHODS[name], obj)
            elif op == 20: regs[a] = regs[b] + regs[c]
            elif op == 21: regs[a] = regs[b] - regs[c]
            elif op == 22: regs[a] = regs[b] * regs[c]
            elif op == 23: regs[a] = regs[b] / regs[c]
            elif op == 24: regs[a] = regs[b] % regs[c]
            elif op == 25: regs[a] = regs[b] // regs[c]
            elif op == 26: regs[a] = regs[b] ** regs[c]
            elif op == 27: regs[a] = regs[b] | regs[c]    # int-or / set union
            elif op == 28: regs[a] = regs[b] & regs[c]    # int-and / set inter
            elif op == 29: regs[a] = regs[b] ^ regs[c]    # int-xor / set symdiff
            elif op == 36: regs[a] = regs[b] << regs[c]
            elif op == 37: regs[a] = regs[b] >> regs[c]
            elif op == 38:                                # SLICE seq[lo:hi:step]
                seq = regs[a]; lo = regs[b]; hi = regs[b + 1]; step = regs[b + 2]
                regs[a] = seq[lo:(len(seq) if hi is None else hi):step]
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
            elif op == 70:                      # SETUP_EXCEPT
                blocks.append(a)
            elif op == 71:                      # POP_BLOCK
                blocks.pop()
            elif op == 72:                      # RAISE
                v = regs[a]
                if isinstance(v, tuple) and v and v[0] == "class":
                    v = self._invoke(v, [])
                self._exc_val = v; self._exc_flag = True
            elif op == 73:                      # RERAISE
                self._exc_flag = True
            elif op == 74:                      # LOAD_EXC
                regs[a] = self._exc_val
            elif op == 75:                      # EXC_MATCH
                regs[a] = self._isinstance(self._exc_val, regs[b])
            else:
                raise RuntimeError("unknown op %d" % op)
            if self._exc_flag:                  # an exception is in flight
                if blocks:
                    pc = blocks.pop()           # jump to nearest handler
                    self._exc_flag = False
                else:
                    return None                 # propagate to caller
        return None

    def _invoke(self, callee, args):
        if isinstance(callee, tuple) and callee[0] == "func":
            return self._call(callee[1], args)
        if isinstance(callee, tuple) and callee[0] == "builtin":
            return self._builtin(callee[1], args)
        if isinstance(callee, tuple) and callee[0] == "class":
            inst = _Inst(callee[1], self.prog["classes"][callee[1]]["cname"])
            init = self._lookup_method(callee[1], "__init__")
            if init is not None:
                self._call(init, [inst] + args)
            return inst
        if isinstance(callee, tuple) and callee[0] == "bound":
            return self._call(callee[1], [callee[2]] + args)
        if isinstance(callee, tuple) and callee[0] == "boundb":
            return self._method(callee[1], [callee[2]] + args)
        raise RuntimeError("not callable: %r" % (callee,))

    def _lookup_method(self, cid, name):
        classes = self.prog["classes"]
        while cid >= 0:
            ci = classes[cid]
            for m in ci["methods"]:
                if m["mname"] == name:
                    return m["mfunc"]
            cid = ci["base"]
        return None

    def _isinstance(self, exc, classval):
        if not isinstance(exc, _Inst):
            return False
        if not (isinstance(classval, tuple) and classval and classval[0] == "class"):
            return False
        target = classval[1]
        cid = exc.cid
        classes = self.prog["classes"]
        while cid >= 0:
            if cid == target:
                return True
            cid = classes[cid]["base"]
        return False

    def _builtin(self, bid, args):
        if bid >= 100:
            return self._method(bid, args)
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
        if name == "list":  return list(args[0]) if args else []
        if name == "dict":  return dict(args[0]) if args else {}
        if name == "set":   return set(args[0]) if args else set()
        if name == "tuple": return tuple(args[0]) if args else ()
        if name == "repr":  return repr(args[0])
        if name == "sorted": return sorted(args[0])
        if name == "sum":   return sum(args[0])
        if name == "min":   return min(args[0]) if len(args) == 1 else min(args)
        if name == "max":   return max(args[0]) if len(args) == 1 else max(args)
        if name == "isinstance": return self._isinst(args[0], args[1])
        if name == "enumerate": return [(i, x) for i, x in enumerate(args[0])]
        if name == "zip":   return [tuple(t) for t in zip(*args)]
        if name == "any":   return any(args[0])
        if name == "all":   return all(args[0])
        if name == "ord":   return ord(args[0])
        if name == "chr":   return chr(args[0])
        if name == "reversed": return list(reversed(args[0]))
        if name == "getattr":  return self._getattr(args)
        if name == "hasattr":
            obj = args[0]
            return (isinstance(obj, _Inst)
                    and (args[1] in obj.attrs
                         or self._lookup_method(obj.cid, args[1]) is not None))
        if name == "type":
            return self._typeof(args[0])
        raise RuntimeError("unknown builtin id %d" % bid)

    def _typeof(self, x):
        # Mirror the native interp: type(x) is the type's builtin value
        # (("builtin", id), comparable to the bare name `int`/`str`/...), or
        # ("class", cid) for a user instance.
        if isinstance(x, _Inst):           return ("class", x.cid)
        if isinstance(x, bool):            return ("builtin", BUILTINS["bool"])
        if isinstance(x, int):             return ("builtin", BUILTINS["int"])
        if isinstance(x, float):           return ("builtin", BUILTINS["float"])
        if isinstance(x, str):             return ("builtin", BUILTINS["str"])
        if isinstance(x, list):            return ("builtin", BUILTINS["list"])
        if isinstance(x, dict):            return ("builtin", BUILTINS["dict"])
        if isinstance(x, set):             return ("builtin", BUILTINS["set"])
        if isinstance(x, tuple):           return ("builtin", BUILTINS["tuple"])
        return ("builtin", -1)

    def _isinst(self, obj, spec):
        if isinstance(spec, tuple) and spec and spec[0] == "class":
            return self._isinstance(obj, spec)
        if isinstance(spec, tuple) and spec and spec[0] == "builtin":
            return self._isinst_type(obj, spec[1])
        if isinstance(spec, tuple):                  # tuple of types
            for s in spec:
                if self._isinst(obj, s):
                    return True
            return False
        return False

    def _isinst_type(self, obj, bid):
        nm = [k for k, v in BUILTINS.items() if v == bid][0]
        if nm == "int":   return isinstance(obj, int)
        if nm == "float": return isinstance(obj, float)
        if nm == "str":   return isinstance(obj, str)
        if nm == "bool":  return isinstance(obj, bool)
        if nm == "list":  return isinstance(obj, list)
        if nm == "dict":  return isinstance(obj, dict)
        if nm == "set":   return isinstance(obj, set)
        if nm == "tuple": return isinstance(obj, tuple)
        return False

    def _getattr(self, args):
        obj, attr = args[0], args[1]
        if isinstance(obj, _Inst):
            if attr in obj.attrs:
                return obj.attrs[attr]
            fidx = self._lookup_method(obj.cid, attr)
            if fidx is not None:
                return ("bound", fidx, obj)
        if len(args) >= 3:
            return args[2]
        return None

    def _method(self, mid, args):
        name = [k for k, v in METHODS.items() if v == mid][0]
        recv = args[0]; rest = args[1:]
        if name == "append": recv.append(rest[0]); return None
        if name == "pop":    return recv.pop(*rest)
        if name == "get":    return recv.get(*rest)
        if name == "keys":   return list(recv.keys())
        if name == "values": return list(recv.values())
        if name == "items":  return [list(t) for t in recv.items()]
        if name == "add":    recv.add(rest[0]); return None
        if name == "split":  return recv.split(*rest) if rest else recv.split()
        if name == "join":   return recv.join(rest[0])
        if name == "strip":  return recv.strip(*rest)
        if name == "startswith": return recv.startswith(rest[0])
        if name == "endswith":   return recv.endswith(rest[0])
        if name == "find":   return recv.find(*rest)
        if name == "replace": return recv.replace(rest[0], rest[1])
        if name == "upper":  return recv.upper()
        if name == "lower":  return recv.lower()
        if name == "extend": recv.extend(rest[0]); return None
        if name == "insert": recv.insert(rest[0], rest[1]); return None
        if name == "index":  return recv.index(*rest)
        if name == "count":  return recv.count(rest[0])
        if name == "update": recv.update(rest[0]); return None
        if name == "setdefault": return recv.setdefault(*rest)
        if name == "splitlines": return recv.splitlines()
        if name == "rstrip": return recv.rstrip(*rest)
        if name == "lstrip": return recv.lstrip(*rest)
        if name == "isdigit": return recv.isdigit()
        if name == "isupper": return recv.isupper()
        if name == "islower": return recv.islower()
        if name == "isalpha": return recv.isalpha()
        if name == "isalnum": return recv.isalnum()
        raise RuntimeError("unknown method id %d" % mid)


class _Inst:
    """A minipy instance in the reference VM: a class id plus an attribute map.
    (The native interpreter stores the same thing in its container heap.)"""
    def __init__(self, cid, name):
        self.cid = cid
        self.name = name
        self.attrs = {}


def _pystr(x):
    if x is True:  return "True"
    if x is False: return "False"
    if x is None:  return "None"
    if isinstance(x, _Inst): return "<%s object>" % x.name
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

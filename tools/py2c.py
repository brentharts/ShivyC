#!/usr/bin/env python3
"""py2c.py -- a source-to-source transpiler from the ShivyCX compiler's
Python source into (abstract) C.

This is NOT a general Python->C transpiler. Writing one of those for arbitrary
Python is effectively impossible. Instead, this tool only ever has to handle the
ShivyCX code base, which evolves slowly and follows consistent conventions. That
lets us cheat: where a value's type is not annotated and not easy to recover by
tracing the call graph, we *guess the type from the variable's name*. This keeps
the transpiler small and fast.

This first pass is deliberately ABSTRACT. The emitted .c files are meant to show
"what the most direct C rendering of this Python looks like" -- they are not yet
expected to compile or to faithfully emulate Python's runtime semantics. Things
that have no direct C analogue (comprehensions, exceptions, dict/set/list
literals, lambdas, ...) are lowered to clearly-marked placeholder helpers or
left as `/* ... */` comments carrying the original source, so nothing is lost.


===========================================================================
NAMING CONVENTIONS (the contract for ShivyCX contributors)
===========================================================================
When a name is not annotated, the transpiler infers its C type purely from the
name. To keep the generated C accurate, ShivyCX code should honor these rules
(or add a `: type` annotation to override them):

  * Integers -- a variable is treated as `int` when its (lower-cased) name is
    one of, or ends with, any of:
        index, idx, i, j, k, n, n1, n2, count, size, offset, chunk, num,
        length, len, pos, position, line, lineno, col, column, start, end,
        depth, level, width, height, amount, total, addr, address, byte,
        bytes, bits, rbp_offset, spot_size
    Rationale: the code uses `index` everywhere for loop/array indices, etc.

  * Strings (char*) -- treated as `str` (char*) when the name is one of:
        name, text, s, string, msg, message, filename, fname, func_name,
        tag, rep, content, spelling, label, identifier, prog, code,
        asm_code, asm_str, text_repr, mangled, suffix, prefix

  * Booleans -- treated as `bool` when the name starts with one of
        is_, has_, can_, should_, was_, use_, allow_
    or is exactly one of:
        defined, ok, found, done, wide, signed, unsigned, const, volatile,
        valid, empty, present, enabled, success

  * `self` -- always a pointer to the enclosing class' struct.

  * Capitalized annotations / 'ForwardRef' strings (e.g. `Spot`, `'Spot'`) are
    treated as a pointer to that struct: `Spot*`.

Everything else falls back to the generic `obj` type (see the prelude emitted
at the top of each .c file).


Usage:
    python3 py2c.py                 # transpile every ../shivyc/*.py -> /tmp/*.c
    python3 py2c.py a.py b.py       # transpile the given files -> /tmp
    python3 py2c.py --out DIR ...   # choose a different output directory
"""

import ast
import os
import sys


# --------------------------------------------------------------------------
# Type inference from naming conventions
# --------------------------------------------------------------------------

INT_NAMES = {
    "index", "idx", "i", "j", "k", "n", "n1", "n2", "count", "size",
    "offset", "chunk", "num", "length", "len", "pos", "position", "line",
    "lineno", "col", "column", "start", "end", "depth", "level", "width",
    "height", "amount", "total", "addr", "address", "byte", "bytes", "bits",
    "rbp_offset", "spot_size",
}

# Names that, as a *suffix* (after '_'), imply int.  e.g. "spot_size", "x_offset"
INT_SUFFIXES = ("size", "offset", "count", "index", "len", "num", "idx")

STR_NAMES = {
    "name", "text", "s", "string", "msg", "message", "filename", "fname",
    "func_name", "tag", "rep", "content", "spelling", "label", "identifier",
    "prog", "code", "asm_code", "asm_str", "text_repr", "mangled", "suffix",
    "prefix",
}

BOOL_NAMES = {
    "defined", "ok", "found", "done", "wide", "signed", "unsigned", "const",
    "volatile", "valid", "empty", "present", "enabled", "success",
}

BOOL_PREFIXES = ("is_", "has_", "can_", "should_", "was_", "use_", "allow_")

# Generic dynamic / unknown type.
OBJ = "obj"


def ann_to_ctype(ann):
    """Map an ast annotation node to a C type string, or None if unknown."""
    if ann is None:
        return None
    try:
        text = ast.unparse(ann)
    except Exception:
        return None
    return ann_text_to_ctype(text)


def ann_text_to_ctype(text):
    text = text.strip().strip("'\"")
    simple = {
        "int": "int", "bool": "bool", "None": "void",
        "str": "char*", "float": "double", "bytes": "char*",
    }
    if text in simple:
        return simple[text]
    # Container annotations have no direct C form in this abstract pass.
    if text.split("[", 1)[0] in ("List", "list", "Dict", "dict", "Set",
                                 "set", "Tuple", "tuple", "Optional", "Any"):
        return OBJ
    # A bare Capitalized name -> pointer to that struct.
    if text and (text[0].isupper() or text[0] == "_") and text.isidentifier():
        return text + "*"
    return None


def infer_from_name(name):
    """Guess a C type purely from a variable name, per the documented rules."""
    if name == "self":
        return None  # handled specially by the class context
    low = name.lower()
    if low in INT_NAMES:
        return "int"
    if low in STR_NAMES:
        return "char*"
    if low in BOOL_NAMES:
        return "bool"
    if low.startswith(BOOL_PREFIXES):
        return "bool"
    tail = low.rsplit("_", 1)[-1]
    if tail in INT_SUFFIXES:
        return "int"
    return None


def infer_type(name, ann=None):
    """Full inference: annotation wins, else naming convention, else `obj`."""
    t = ann_to_ctype(ann)
    if t:
        return t
    t = infer_from_name(name)
    if t:
        return t
    return OBJ


# --------------------------------------------------------------------------
# The transpiler
# --------------------------------------------------------------------------

class Unsupported(Exception):
    pass


class Transpiler:
    def __init__(self, modname):
        self.modname = modname
        self.lines = []          # output lines (top level)
        self.cur_class = None     # name of class currently being emitted
        self.modules = set()      # imported module names / aliases (for ns)
        self.indent = 0

    # ---- output helpers ---------------------------------------------------

    def emit(self, line=""):
        if line:
            self.lines.append("    " * self.indent + line)
        else:
            self.lines.append("")

    def emit_block(self, text):
        for ln in text.splitlines():
            self.emit(ln)

    # ---- module ----------------------------------------------------------

    def run(self, tree):
        self.prelude()
        self.collect_imports(tree)
        for node in tree.body:
            self.toplevel(node)
        return "\n".join(self.lines) + "\n"

    def prelude(self):
        bar = "/* " + "=" * 64 + " */"
        self.emit(bar)
        self.emit("/*  Transpiled from shivyc/%s.py by tools/py2c.py%s*/"
                  % (self.modname, " " * 8))
        self.emit("/*  ABSTRACT first-pass C -- not yet expected to compile. */")
        self.emit(bar)
        self.emit('#include <stdbool.h>')
        self.emit('#include <stddef.h>')
        self.emit()
        self.emit("/* Generic dynamic value used wherever a concrete C type "
                  "could not be inferred. */")
        self.emit("typedef void* obj;")
        self.emit("typedef char* str;")
        self.emit()

    def collect_imports(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.modules.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                # `from x import y` -> y is a usable symbol, not a module ns
                pass

    def toplevel(self, node):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            # Module docstring.
            self.emit("/* " + node.value.value.strip().splitlines()[0] + " */")
            self.emit()
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self.emit("/* " + self.src(node) + " */")
        elif isinstance(node, ast.ClassDef):
            self.class_def(node)
        elif isinstance(node, ast.FunctionDef):
            self.func_def(node)
        elif isinstance(node, ast.Assign):
            for ln in self.assign(node, toplevel=True):
                self.emit(ln)
        elif isinstance(node, (ast.If, ast.For, ast.While, ast.AugAssign,
                               ast.AnnAssign, ast.Try, ast.With)):
            for ln in self.stmt(node):
                self.emit(ln)
        else:
            self.emit("/* unsupported top-level: " + self.src(node) + " */")
        self.emit()

    # ---- classes ---------------------------------------------------------

    def class_def(self, node):
        bases = [self.src(b) for b in node.bases]
        base_note = (" : " + ", ".join(bases)) if bases else ""
        self.emit("/* class %s%s */" % (node.name, base_note))
        # Discover fields: every `self.<x> = ...` across all methods, plus any
        # class-level assignments.
        fields = self.discover_fields(node)
        self.emit("typedef struct %s {" % node.name)
        self.indent += 1
        if bases:
            self.emit("/* inherited from: %s */" % ", ".join(bases))
        if not fields:
            self.emit("char _empty; /* no fields discovered */")
        for fname, ftype in fields.items():
            self.emit("%s %s;" % (ftype, fname))
        self.indent -= 1
        self.emit("} %s;" % node.name)
        self.emit()
        # Methods.
        prev = self.cur_class
        self.cur_class = node.name
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                self.func_def(item, method=True)
                self.emit()
            elif isinstance(item, ast.Assign):
                for ln in self.assign(item, toplevel=True):
                    self.emit("/* class var */ " + ln)
            elif isinstance(item, ast.Expr) and \
                    isinstance(item.value, ast.Constant):
                pass  # docstring already represented by the struct comment
        self.cur_class = prev

    def discover_fields(self, node):
        fields = {}
        # class-level simple assignments
        for item in node.body:
            if isinstance(item, ast.Assign):
                for tgt in item.targets:
                    if isinstance(tgt, ast.Name):
                        fields.setdefault(tgt.id,
                                          infer_type(tgt.id))
            if isinstance(item, ast.AnnAssign) and \
                    isinstance(item.target, ast.Name):
                fields.setdefault(item.target.id,
                                  infer_type(item.target.id, item.annotation))
        # self.x = ... inside any method
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            for sub in ast.walk(item):
                tgts = []
                if isinstance(sub, ast.Assign):
                    tgts = sub.targets
                elif isinstance(sub, ast.AnnAssign):
                    tgts = [sub.target]
                for tgt in tgts:
                    if isinstance(tgt, ast.Attribute) and \
                            isinstance(tgt.value, ast.Name) and \
                            tgt.value.id == "self":
                        ann = getattr(sub, "annotation", None)
                        fields.setdefault(tgt.attr,
                                          infer_type(tgt.attr, ann))
        return fields

    # ---- functions -------------------------------------------------------

    def func_def(self, node, method=False):
        ret = ann_to_ctype(node.returns) or OBJ
        params = []
        a = node.args
        all_args = list(a.args)
        if method and all_args and all_args[0].arg == "self":
            params.append("%s* self" % self.cur_class)
            all_args = all_args[1:]
        for arg in all_args:
            ctype = infer_type(arg.arg, arg.annotation)
            params.append("%s %s" % (ctype, arg.arg))
        if a.vararg:
            params.append("/* *%s */ ..." % a.vararg.arg)
        if a.kwarg:
            params.append("/* **%s */ ..." % a.kwarg.arg)
        plist = ", ".join(params) if params else "void"

        cname = (self.cur_class + "_" + node.name) if method else node.name
        # Constructor convention note.
        if method and node.name == "__init__":
            self.emit("/* constructor */")
        self.emit("%s %s(%s) {" % (ret, cname, plist))
        self.indent += 1
        self.emit_body(node.body)
        self.indent -= 1
        self.emit("}")

    def emit_body(self, body):
        if not body:
            self.emit("/* pass */")
            return
        for stmt in body:
            for ln in self.stmt(stmt):
                self.emit(ln)

    # ---- statements (return list of un-indented-by-emit lines) -----------

    def stmt(self, node):
        m = getattr(self, "st_" + type(node).__name__, None)
        if m is None:
            return ["/* unsupported stmt %s: %s */"
                    % (type(node).__name__, self.src1(node))]
        try:
            return m(node)
        except Unsupported as e:
            return ["/* %s */" % e]
        except Exception as e:  # never crash on a single statement
            return ["/* transpile-error (%s): %s */"
                    % (e, self.src1(node))]

    def st_Expr(self, node):
        v = node.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            # docstring / comment
            first = v.value.strip().splitlines()[0] if v.value.strip() else ""
            return ["/* " + first + " */"] if first else []
        return [self.expr(v) + ";"]

    def st_Assign(self, node):
        return self.assign(node)

    def assign(self, node, toplevel=False):
        rhs = self.expr(node.value)
        lines = []
        for tgt in node.targets:
            if isinstance(tgt, ast.Tuple):
                # tuple unpacking -> one line per element (abstract)
                lines.append("/* tuple unpack: %s = %s */"
                             % (self.src1(tgt), rhs))
                for i, el in enumerate(tgt.elts):
                    lines.append("%s = %s[%d];" % (self.expr(el), rhs, i))
                continue
            decl = ""
            if isinstance(tgt, ast.Name):
                ctype = infer_from_name(tgt.id) or self.guess_from_value(
                    node.value) or OBJ
                decl = ctype + " "
            lines.append("%s%s = %s;" % (decl, self.expr(tgt), rhs))
        return lines

    def st_AnnAssign(self, node):
        ctype = infer_type(
            getattr(node.target, "id", "x"), node.annotation)
        tgt = self.expr(node.target)
        if node.value is None:
            return ["%s %s;" % (ctype, tgt)]
        decl = (ctype + " ") if isinstance(node.target, ast.Name) else ""
        return ["%s%s = %s;" % (decl, tgt, self.expr(node.value))]

    def st_AugAssign(self, node):
        op = self.binop_sym(node.op)
        return ["%s %s= %s;" % (self.expr(node.target), op,
                                self.expr(node.value))]

    def st_Return(self, node):
        if node.value is None:
            return ["return;"]
        return ["return %s;" % self.expr(node.value)]

    def st_Pass(self, node):
        return ["/* pass */"]

    def st_Break(self, node):
        return ["break;"]

    def st_Continue(self, node):
        return ["continue;"]

    def st_Global(self, node):
        return ["/* global %s */" % ", ".join(node.names)]

    def st_Delete(self, node):
        return ["/* del %s */" % ", ".join(self.expr(t) for t in node.targets)]

    def st_If(self, node):
        lines = ["if (%s) {" % self.expr(node.test)]
        lines += self.indent_lines(self.suite(node.body))
        if node.orelse:
            # else-if chain
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                inner = self.st_If(node.orelse[0])
                inner[0] = "} else " + inner[0]
                lines += inner
                return lines
            lines.append("} else {")
            lines += self.indent_lines(self.suite(node.orelse))
        lines.append("}")
        return lines

    def st_While(self, node):
        lines = ["while (%s) {" % self.expr(node.test)]
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        if node.orelse:
            lines.append("/* while-else not represented */")
        return lines

    def st_For(self, node):
        target = node.target
        it = node.iter
        # for x in range(...)
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) \
                and it.func.id == "range" and isinstance(target, ast.Name):
            var = target.id
            args = [self.expr(a) for a in it.args]
            if len(args) == 1:
                start, stop, step = "0", args[0], "1"
            elif len(args) == 2:
                start, stop, step = args[0], args[1], "1"
            else:
                start, stop, step = args[0], args[1], args[2]
            head = "for (int %s = %s; %s < %s; %s += %s) {" % (
                var, start, var, stop, var, step)
            lines = [head]
            lines += self.indent_lines(self.suite(node.body))
            lines.append("}")
            return lines
        # for x in <iterable>  -> documented FOR_EACH lowering
        tgt = self.src1(target)
        lines = ["/* for %s in %s: */" % (tgt, self.src1(it))]
        lines.append("FOR_EACH(%s, %s) {" % (tgt, self.expr(it)))
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        return lines

    def st_Raise(self, node):
        what = self.expr(node.exc) if node.exc else ""
        return ["RAISE(%s);" % what]

    def st_Try(self, node):
        lines = ["/* try */ {"]
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        for h in node.handlers:
            etype = self.src1(h.type) if h.type else "..."
            nm = (" as %s" % h.name) if h.name else ""
            lines.append("/* except %s%s */ {" % (etype, nm))
            lines += self.indent_lines(self.suite(h.body))
            lines.append("}")
        if node.orelse:
            lines.append("/* else */ {")
            lines += self.indent_lines(self.suite(node.orelse))
            lines.append("}")
        if node.finalbody:
            lines.append("/* finally */ {")
            lines += self.indent_lines(self.suite(node.finalbody))
            lines.append("}")
        return lines

    def st_With(self, node):
        items = ", ".join(self.src1(i.context_expr) for i in node.items)
        lines = ["/* with %s */ {" % items]
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        return lines

    def st_Import(self, node):
        return ["/* " + self.src1(node) + " */"]

    def st_ImportFrom(self, node):
        return ["/* " + self.src1(node) + " */"]

    def st_FunctionDef(self, node):
        # nested function: emit a marker; bodies of nested funcs are rare here
        return ["/* nested function %s(...) not lifted in this pass */"
                % node.name]

    def st_ClassDef(self, node):
        return ["/* nested class %s not lifted in this pass */" % node.name]

    def suite(self, body):
        out = []
        for stmt in body:
            out += self.stmt(stmt)
        return out

    def indent_lines(self, lines):
        return ["    " + ln for ln in lines]

    # ---- expressions (return a C expression string) ----------------------

    def expr(self, node):
        m = getattr(self, "ex_" + type(node).__name__, None)
        if m is None:
            return "/* %s: %s */ NULL" % (type(node).__name__, self.src1(node))
        try:
            return m(node)
        except Unsupported as e:
            return "/* %s */ NULL" % e
        except Exception as e:
            return "/* expr-error %s */ NULL" % e

    def ex_Name(self, node):
        return node.id

    def ex_Constant(self, node):
        v = node.value
        if v is None:
            return "NULL"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return c_string(v)
        if isinstance(v, bytes):
            return c_string(v.decode("latin-1"))
        if isinstance(v, float):
            return repr(v)
        return str(v)

    def ex_Attribute(self, node):
        # module.attr -> module_attr ; obj.attr -> self->attr or obj.attr
        if isinstance(node.value, ast.Name):
            base = node.value.id
            if base in self.modules:
                return "%s_%s" % (base, node.attr)
            if base == "self":
                return "self->%s" % node.attr
        return "%s.%s" % (self.expr(node.value), node.attr)

    def ex_Call(self, node):
        args = [self.expr(a) for a in node.args]
        for kw in node.keywords:
            if kw.arg is None:
                args.append("/* **%s */" % self.src1(kw.value))
            else:
                args.append("/* %s= */ %s" % (kw.arg, self.expr(kw.value)))
        func = node.func
        # method/attribute call
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and \
                    func.value.id in self.modules:
                # namespaced free function: module_func(args)
                return "%s_%s(%s)" % (func.value.id, func.attr,
                                      ", ".join(args))
            # receiver-first method lowering: meth(recv, args)
            recv = self.expr(func.value)
            allargs = [recv] + args
            return "%s(%s)" % (func.attr, ", ".join(allargs))
        return "%s(%s)" % (self.expr(func), ", ".join(args))

    def ex_BinOp(self, node):
        op = self.binop_sym(node.op)
        return "(%s %s %s)" % (self.expr(node.left), op,
                               self.expr(node.right))

    def ex_BoolOp(self, node):
        op = "&&" if isinstance(node.op, ast.And) else "||"
        return "(" + (" %s " % op).join(self.expr(v)
                                        for v in node.values) + ")"

    def ex_UnaryOp(self, node):
        sym = {ast.Not: "!", ast.USub: "-", ast.UAdd: "+",
               ast.Invert: "~"}[type(node.op)]
        return "(%s%s)" % (sym, self.expr(node.operand))

    def ex_Compare(self, node):
        parts = []
        left = self.expr(node.left)
        cur = left
        for op, comp in zip(node.ops, node.comparators):
            r = self.expr(comp)
            parts.append(self.cmp_expr(cur, op, comp, r))
            cur = r
        return "(" + " && ".join(parts) + ")"

    def cmp_expr(self, left, op, comp_node, right):
        if isinstance(op, ast.In):
            return "IN(%s, %s)" % (left, right)
        if isinstance(op, ast.NotIn):
            return "(!IN(%s, %s))" % (left, right)
        sym = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
               ast.Gt: ">", ast.GtE: ">=", ast.Is: "==", ast.IsNot: "!="}
        s = sym.get(type(op), "/*?*/")
        return "(%s %s %s)" % (left, s, right)

    def ex_Subscript(self, node):
        sl = node.slice
        if isinstance(sl, ast.Slice):
            lo = self.expr(sl.lower) if sl.lower else "0"
            hi = self.expr(sl.upper) if sl.upper else "END"
            return "SLICE(%s, %s, %s)" % (self.expr(node.value), lo, hi)
        return "%s[%s]" % (self.expr(node.value), self.expr(sl))

    def ex_IfExp(self, node):
        return "(%s ? %s : %s)" % (self.expr(node.test),
                                   self.expr(node.body),
                                   self.expr(node.orelse))

    def ex_List(self, node):
        return "/* list[%d] */ NULL" % len(node.elts)

    def ex_Tuple(self, node):
        return "/* tuple(%s) */ NULL" % ", ".join(self.expr(e)
                                                   for e in node.elts)

    def ex_Set(self, node):
        return "/* set[%d] */ NULL" % len(node.elts)

    def ex_Dict(self, node):
        return "/* dict[%d] */ NULL" % len(node.keys)

    def ex_ListComp(self, node):
        return "/* listcomp: %s */ NULL" % self.src1(node)

    def ex_SetComp(self, node):
        return "/* setcomp: %s */ NULL" % self.src1(node)

    def ex_DictComp(self, node):
        return "/* dictcomp: %s */ NULL" % self.src1(node)

    def ex_GeneratorExp(self, node):
        return "/* genexpr: %s */ NULL" % self.src1(node)

    def ex_Lambda(self, node):
        return "/* lambda: %s */ NULL" % self.src1(node)

    def ex_Starred(self, node):
        return "/* *%s */" % self.expr(node.value)

    def ex_JoinedStr(self, node):
        # f-string -> pyfmt("...{}...", expr, expr, ...)
        fmt = []
        exprs = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                fmt.append(str(part.value).replace("%", "%%"))
            elif isinstance(part, ast.FormattedValue):
                fmt.append("{}")
                exprs.append(self.expr(part.value))
        lit = c_string("".join(fmt))
        if exprs:
            return "pyfmt(%s, %s)" % (lit, ", ".join(exprs))
        return "pyfmt(%s)" % lit

    def ex_FormattedValue(self, node):
        return self.expr(node.value)

    # ---- operator symbols -------------------------------------------------

    def binop_sym(self, op):
        return {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
            ast.Mod: "%", ast.FloorDiv: "/", ast.BitOr: "|", ast.BitAnd: "&",
            ast.BitXor: "^", ast.LShift: "<<", ast.RShift: ">>",
            ast.Pow: "POW",
        }.get(type(op), "/*op*/")

    # ---- value-based type guess for declarations -------------------------

    def guess_from_value(self, node):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, int):
                return "int"
            if isinstance(v, str):
                return "char*"
            if isinstance(v, float):
                return "double"
        if isinstance(node, ast.Compare):
            return "bool"
        if isinstance(node, ast.BoolOp):
            return "bool"
        if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
            return OBJ
        return None

    # ---- source helpers ---------------------------------------------------

    def src(self, node):
        try:
            return ast.unparse(node)
        except Exception:
            return type(node).__name__

    def src1(self, node):
        """One-line, comment-safe unparse."""
        s = self.src(node).replace("\n", " ").replace("*/", "* /")
        return s if len(s) <= 120 else s[:117] + "..."


# --------------------------------------------------------------------------
# String literal helper
# --------------------------------------------------------------------------

def c_string(s):
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("\\x%02x" % (ord(ch) & 0xff))
    out.append('"')
    return "".join(out)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def transpile_file(path, out_dir):
    src = open(path, encoding="utf-8").read()
    modname = os.path.splitext(os.path.basename(path))[0]
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        print("  SYNTAX ERROR in %s: %s" % (path, e))
        return None
    out = Transpiler(modname).run(tree)
    out_path = os.path.join(out_dir, modname + ".c")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    return out_path


def print_conventions():
    print(__doc__)
    print("Integer names:", ", ".join(sorted(INT_NAMES)))
    print()
    print("Integer suffixes (after '_'):", ", ".join(INT_SUFFIXES))
    print()
    print("String (char*) names:", ", ".join(sorted(STR_NAMES)))
    print()
    print("Boolean names:", ", ".join(sorted(BOOL_NAMES)))
    print()
    print("Boolean prefixes:", ", ".join(BOOL_PREFIXES))


def main(argv):
    out_dir = "/tmp"
    files = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            out_dir = argv[i + 1]
            i += 2
            continue
        if a in ("--conventions", "-c"):
            print_conventions()
            return
        files.append(a)
        i += 1

    if not files:
        here = os.path.dirname(os.path.abspath(__file__))
        shivyc = os.path.normpath(os.path.join(here, "..", "shivyc"))
        files = sorted(
            os.path.join(shivyc, f)
            for f in os.listdir(shivyc) if f.endswith(".py"))
        print("No files given; defaulting to %d files in %s" %
              (len(files), shivyc))

    os.makedirs(out_dir, exist_ok=True)
    ok = 0
    for path in files:
        res = transpile_file(path, out_dir)
        if res:
            ok += 1
            print("  %-32s -> %s" % (os.path.basename(path), res))
    print("Transpiled %d/%d files into %s" % (ok, len(files), out_dir))


if __name__ == "__main__":
    main(sys.argv[1:])

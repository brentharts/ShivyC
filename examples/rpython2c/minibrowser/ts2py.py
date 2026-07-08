#!/usr/bin/env python3
"""ts2py -- translate a page's TypeScript into typed rpython for native JIT.

TypeScript's type annotations are exactly what py2c/rpython needs, so a
``<script type="typescript">`` is translated to *typed rpython* and compiled to a
native ``.so`` by the same path as ``<script type="rpython">``---not to
JavaScript. Everybody compiles TypeScript to JavaScript; here a typed TS function
becomes native machine code.

This is a dependency-free, pure-Python translator (no Node, no npm, no TypeScript
compiler). It parses the subset page functions use---typed function
declarations, typed locals, the usual control flow and expressions---with its own
tokenizer and a precedence-climbing expression parser, and maps TS types onto
rpython (``number`` -> ``int``, ``boolean`` -> ``bool``, ``string`` -> ``str``,
``void`` -> ``None``, ``T[]`` -> ``list[t]``). Constructs outside the subset raise
Unsupported so the caller can skip the block rather than emit broken code.

    python3 ts2py.py script.ts        # print the translated rpython
"""
import re
import sys


class Unsupported(Exception):
    pass


# ---- tokenizer -----------------------------------------------------------
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<num>\d+\.\d+|\d+)
  | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<op>===|!==|==|!=|<=|>=|&&|\|\||=>|\+\+|--|[-+*/%<>=!&|^(){}\[\];:,.?])
""", re.X | re.S)


def tokenize(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        m = _TOKEN.match(src, i)
        if not m:
            raise Unsupported("cannot tokenize near %r" % src[i:i + 12])
        i = m.end()
        kind = m.lastgroup
        if kind in ("ws", "lc", "bc"):
            continue
        toks.append((kind, m.group()))
    toks.append(("eof", ""))
    return toks


# TS types -> rpython types
_TYPE = {
    "number": "int", "int": "int", "float": "float",
    "string": "str", "boolean": "bool", "bool": "bool",
    "void": "None", "any": "obj",
}
# binary operators -> (python operator, precedence)
_BIN = {
    "||": ("or", 1), "&&": ("and", 2),
    "==": ("==", 3), "!=": ("!=", 3), "===": ("==", 3), "!==": ("!=", 3),
    "<": ("<", 4), ">": (">", 4), "<=": ("<=", 4), ">=": (">=", 4),
    "+": ("+", 5), "-": ("-", 5),
    "*": ("*", 6), "/": ("/", 6), "%": ("%", 6),
    "&": ("&", 4), "|": ("|", 4), "^": ("^", 4),
}


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.p = 0

    # token helpers
    def peek(self):
        return self.toks[self.p]

    def next(self):
        t = self.toks[self.p]
        self.p += 1
        return t

    def at(self, kind, val=None):
        k, v = self.toks[self.p]
        return k == kind and (val is None or v == val)

    def eat(self, kind, val=None):
        if not self.at(kind, val):
            k, v = self.toks[self.p]
            raise Unsupported("expected %s %r, got %s %r"
                              % (kind, val, k, v))
        return self.next()

    # ---- program: a sequence of statements (top level = functions) -------
    def program(self):
        out = []
        while not self.at("eof"):
            self.stmt(0, out)
        return "\n".join(out) + ("\n" if out else "")

    def emit(self, out, indent, text):
        out.append("    " * indent + text)

    def block(self, indent, out):
        self.eat("op", "{")
        start = len(out)
        while not self.at("op", "}"):
            if self.at("eof"):
                raise Unsupported("unterminated block")
            self.stmt(indent, out)
        self.eat("op", "}")
        if len(out) == start:
            self.emit(out, indent, "pass")

    def opt_semi(self):
        if self.at("op", ";"):
            self.next()

    # ---- types -----------------------------------------------------------
    def type_(self):
        name = self.eat("id")[1]
        rt = _TYPE.get(name, name)
        while self.at("op", "["):          # T[] -> list[t]
            self.next()
            self.eat("op", "]")
            rt = "list[%s]" % rt
        return rt

    def opt_type(self):
        if self.at("op", ":"):
            self.next()
            return self.type_()
        return ""

    # ---- statements ------------------------------------------------------
    def stmt(self, indent, out):
        # strip an `export` modifier
        if self.at("id", "export"):
            self.next()
        if self.at("id", "function"):
            return self.func(indent, out)
        if self.at("id", "let") or self.at("id", "const") or self.at("id", "var"):
            self.next()
            name = self.eat("id")[1]
            ann = self.opt_type()
            val = "None"
            if self.at("op", "="):
                self.next()
                val = self.expr()
            self.opt_semi()
            if ann and ann != "None":
                self.emit(out, indent, "%s: %s = %s" % (name, ann, val))
            else:
                self.emit(out, indent, "%s = %s" % (name, val))
            return
        if self.at("id", "if"):
            return self.if_(indent, out)
        if self.at("id", "while"):
            self.next()
            self.eat("op", "(")
            cond = self.expr()
            self.eat("op", ")")
            self.emit(out, indent, "while %s:" % cond)
            self.body(indent + 1, out)
            return
        if self.at("id", "for"):
            return self.for_(indent, out)
        if self.at("id", "return"):
            self.next()
            if self.at("op", ";") or self.at("op", "}"):
                self.emit(out, indent, "return")
            else:
                self.emit(out, indent, "return %s" % self.expr())
            self.opt_semi()
            return
        if self.at("id", "break"):
            self.next()
            self.opt_semi()
            self.emit(out, indent, "break")
            return
        if self.at("id", "continue"):
            self.next()
            self.opt_semi()
            self.emit(out, indent, "continue")
            return
        if self.at("op", "{"):
            self.block(indent, out)
            return
        if self.at("op", ";"):
            self.next()
            return
        # expression statement (incl. assignment, ++/--)
        e = self.expr_stmt()
        self.opt_semi()
        self.emit(out, indent, e)

    def body(self, indent, out):
        # a statement or a brace block as a suite
        if self.at("op", "{"):
            self.block(indent, out)
        else:
            start = len(out)
            self.stmt(indent, out)
            if len(out) == start:
                self.emit(out, indent, "pass")

    def func(self, indent, out):
        self.eat("id", "function")
        name = self.eat("id")[1]
        self.eat("op", "(")
        params = []
        while not self.at("op", ")"):
            pname = self.eat("id")[1]
            ptype = self.opt_type()
            if ptype and ptype != "None":
                params.append("%s: %s" % (pname, ptype))
            else:
                params.append(pname)
            if self.at("op", ","):
                self.next()
        self.eat("op", ")")
        ret = self.opt_type()
        sig = ", ".join(params)
        if ret and ret != "None":
            self.emit(out, indent, "def %s(%s) -> %s:" % (name, sig, ret))
        else:
            self.emit(out, indent, "def %s(%s):" % (name, sig))
        self.block(indent + 1, out)

    def if_(self, indent, out):
        self.eat("id", "if")
        self.eat("op", "(")
        cond = self.expr()
        self.eat("op", ")")
        self.emit(out, indent, "if %s:" % cond)
        self.body(indent + 1, out)
        self._else(indent, out)

    def _else(self, indent, out):
        if not self.at("id", "else"):
            return
        self.next()
        if self.at("id", "if"):             # else if -> elif
            self.eat("id", "if")
            self.eat("op", "(")
            cond = self.expr()
            self.eat("op", ")")
            self.emit(out, indent, "elif %s:" % cond)
            self.body(indent + 1, out)
            self._else(indent, out)
        else:
            self.emit(out, indent, "else:")
            self.body(indent + 1, out)

    def for_(self, indent, out):
        # C-style: for (init; cond; update) body  ->  init; while cond: body; update
        self.eat("id", "for")
        self.eat("op", "(")
        if not self.at("op", ";"):
            self.stmt(indent, out)          # init (declares or expr; eats ';')
        else:
            self.next()
        cond = "True"
        if not self.at("op", ";"):
            cond = self.expr()
        self.eat("op", ";")
        upd = ""
        if not self.at("op", ")"):
            upd = self.expr_stmt()
        self.eat("op", ")")
        self.emit(out, indent, "while %s:" % cond)
        bstart = len(out)
        self.body(indent + 1, out)
        if upd:
            # drop a trailing 'pass' if the body was otherwise empty
            if len(out) == bstart + 1 and out[-1].strip() == "pass":
                out.pop()
            self.emit(out, indent + 1, upd)

    # ---- expressions (precedence climbing) -------------------------------
    def expr_stmt(self):
        # assignment / update / bare expression, returned as a python string
        left = self.expr()
        if self.at("op", "++") or self.at("op", "--"):
            op = self.next()[1]
            return "%s = %s %s 1" % (left, left, "+" if op == "++" else "-")
        if self.at("op", "=") or self._at_augassign():
            op = self.next()[1]
            right = self.expr()
            return "%s %s %s" % (left, op, right)
        return left

    def _at_augassign(self):
        return self.peek()[0] == "op" and self.peek()[1] in (
            "+=", "-=", "*=", "/=", "%=")

    def expr(self, minp=1):
        left = self.unary()
        while True:
            k, v = self.peek()
            if k != "op" or v not in _BIN:
                break
            pyop, prec = _BIN[v]
            if prec < minp:
                break
            self.next()
            right = self.expr(prec + 1)
            left = "(%s %s %s)" % (left, pyop, right)
        if self.at("op", "?"):              # ternary cond ? a : b
            self.next()
            a = self.expr()
            self.eat("op", ":")
            b = self.expr()
            left = "(%s if %s else %s)" % (a, left, b)
        return left

    def unary(self):
        k, v = self.peek()
        if k == "op" and v in ("!", "-", "+"):
            self.next()
            operand = self.unary()
            pref = {"!": "not ", "-": "-", "+": "+"}[v]
            return "(%s%s)" % (pref, operand)
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.at("op", "."):
                self.next()
                e = "%s.%s" % (e, self.eat("id")[1])
            elif self.at("op", "["):
                self.next()
                idx = self.expr()
                self.eat("op", "]")
                e = "%s[%s]" % (e, idx)
            elif self.at("op", "("):
                self.next()
                args = []
                while not self.at("op", ")"):
                    args.append(self.expr())
                    if self.at("op", ","):
                        self.next()
                self.eat("op", ")")
                e = "%s(%s)" % (e, ", ".join(args))
            else:
                break
        return e

    def primary(self):
        k, v = self.peek()
        if k == "num":
            self.next()
            return v
        if k == "str":
            self.next()
            return _pystr(v[1:-1])
        if k == "id":
            self.next()
            if v == "true":
                return "True"
            if v == "false":
                return "False"
            if v == "null" or v == "undefined":
                return "None"
            return v
        if k == "op" and v == "(":
            self.next()
            e = self.expr()
            self.eat("op", ")")
            return "(%s)" % e
        if k == "op" and v == "[":
            self.next()
            items = []
            while not self.at("op", "]"):
                items.append(self.expr())
                if self.at("op", ","):
                    self.next()
            self.eat("op", "]")
            return "[%s]" % ", ".join(items)
        raise Unsupported("unexpected token %s %r" % (k, v))


def _pystr(s):
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def translate(ts_source):
    """TypeScript source -> typed rpython source. Raises Unsupported on a
    construct outside the covered subset."""
    return Parser(tokenize(ts_source)).program()


def main(argv):
    src = open(argv[1]).read() if len(argv) > 1 else sys.stdin.read()
    sys.stdout.write(translate(src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

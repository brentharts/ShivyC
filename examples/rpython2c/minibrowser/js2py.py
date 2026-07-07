#!/usr/bin/env python3
"""js2py -- translate a page's JavaScript into minipy-subset Python.

The minibrowser already runs <script type="python"> on the embedded minipy
interpreter against a live DOM (document/console/window). This makes plain
<script> JavaScript ride that *same* engine: it parses the JS with pyjsparser
(the parser the Js2Py project is built on) and walks the ESTree AST, emitting
minipy Python that calls the same minidom. So `document.getElementById(...)`,
`console.log`, `el.value = ...`, and `onclick` handlers work identically whether
the page author wrote Python or JavaScript.

This is a pragmatic subset (functions, var/let/const, if/while/for, returns,
member/call/assignment expressions, the usual operators with === -> == and
&& -> and), not a full ECMAScript. Unsupported constructs raise Unsupported so
the caller can skip that script rather than emit broken code. Full JS semantics
(type coercion in `+`, hoisting, closures over `var`, prototypes) are out of
scope; the goal is DOM-scripting parity with the Python path.

    python3 js2py.py script.js        # print the translated Python
"""
import sys

try:
    from pyjsparser import parse as _js_parse
except ImportError:                       # optional dependency (like Js2Py)
    _js_parse = None


class Unsupported(Exception):
    pass


# JS globals that map straight onto the minidom names.
_PASSTHROUGH = {"document", "console", "window", "Math"}

_BINOP = {
    "===": "==", "!==": "!=", "==": "==", "!=": "!=",
    "<": "<", ">": ">", "<=": "<=", ">=": ">=",
    "+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
    "&": "&", "|": "|", "^": "^", "<<": "<<", ">>": ">>",
}


class Translator:
    def __init__(self):
        self.lines = []
        self.dict_vars = set()      # vars assigned an object literal -> dicts

    def _note_dict(self, target, value):
        # If `target` (a simple name) is assigned an object literal, remember it
        # so later `target.prop` lowers to `target["prop"]` (JS objects -> dicts).
        if value is not None and value.get("type") == "ObjectExpression" \
                and target.get("type") == "Identifier":
            self.dict_vars.add(target["name"])

    # ---- statements ------------------------------------------------------
    def emit_program(self, node):
        for st in node.get("body", []):
            self.stmt(st, 0)
        return "\n".join(self.lines) + ("\n" if self.lines else "")

    def line(self, indent, text):
        self.lines.append("    " * indent + text)

    def block(self, node, indent):
        body = node.get("body", []) if node.get("type") == "BlockStatement" \
            else [node]
        emitted = False
        for st in body:
            before = len(self.lines)
            self.stmt(st, indent)
            emitted = emitted or len(self.lines) > before
        if not emitted:
            self.line(indent, "pass")

    def stmt(self, node, indent):
        t = node["type"]
        if t == "FunctionDeclaration":
            params = ", ".join(p["name"] for p in node.get("params", []))
            self.line(indent, "def %s(%s):" % (node["id"]["name"], params))
            self.block(node["body"], indent + 1)
        elif t == "VariableDeclaration":
            for d in node["declarations"]:
                name = d["id"]["name"]
                if d.get("init") is not None:
                    self._note_dict(d["id"], d["init"])
                    self.line(indent, "%s = %s" % (name, self.expr(d["init"])))
                else:
                    self.line(indent, "%s = None" % name)
        elif t == "ExpressionStatement":
            e = node["expression"]
            # x++ / x-- as a bare statement
            if e["type"] == "UpdateExpression":
                op = "+" if e["operator"] == "++" else "-"
                self.line(indent, "%s = %s %s 1"
                          % (self.expr(e["argument"]),
                             self.expr(e["argument"]), op))
            else:
                self.line(indent, self.expr(e))
        elif t == "IfStatement":
            self.line(indent, "if %s:" % self.expr(node["test"]))
            self.block(node["consequent"], indent + 1)
            alt = node.get("alternate")
            if alt is not None:
                if alt["type"] == "IfStatement":     # else if -> elif chain
                    self._elif(alt, indent)
                else:
                    self.line(indent, "else:")
                    self.block(alt, indent + 1)
        elif t == "WhileStatement":
            self.line(indent, "while %s:" % self.expr(node["test"]))
            self.block(node["body"], indent + 1)
        elif t == "ForStatement":
            self._for(node, indent)
        elif t == "ReturnStatement":
            arg = node.get("argument")
            self.line(indent, "return %s" % (self.expr(arg) if arg else ""))
        elif t == "BlockStatement":
            self.block(node, indent)
        elif t == "BreakStatement":
            self.line(indent, "break")
        elif t == "ContinueStatement":
            self.line(indent, "continue")
        elif t == "EmptyStatement":
            pass
        else:
            raise Unsupported("statement %s" % t)

    def _elif(self, node, indent):
        self.line(indent, "elif %s:" % self.expr(node["test"]))
        self.block(node["consequent"], indent + 1)
        alt = node.get("alternate")
        if alt is not None:
            if alt["type"] == "IfStatement":
                self._elif(alt, indent)
            else:
                self.line(indent, "else:")
                self.block(alt, indent + 1)

    def _for(self, node, indent):
        # C-style for(init; test; update) body  ->  init; while test: body; update
        init = node.get("init")
        if init is not None:
            if init["type"] == "VariableDeclaration":
                self.stmt(init, indent)
            else:
                self.line(indent, self.expr(init))
        test = node.get("test")
        self.line(indent, "while %s:" % (self.expr(test) if test else "True"))
        self.block(node["body"], indent + 1)
        upd = node.get("update")
        if upd is not None:
            if upd["type"] == "UpdateExpression":
                op = "+" if upd["operator"] == "++" else "-"
                self.line(indent + 1, "%s = %s %s 1"
                          % (self.expr(upd["argument"]),
                             self.expr(upd["argument"]), op))
            else:
                self.line(indent + 1, self.expr(upd))

    # ---- expressions -----------------------------------------------------
    def expr(self, node):
        t = node["type"]
        if t == "Identifier":
            return node["name"]
        if t == "Literal":
            return self._literal(node)
        if t == "MemberExpression":
            obj = self.expr(node["object"])
            if node.get("computed"):
                return "%s[%s]" % (obj, self.expr(node["property"]))
            prop = node["property"]["name"]
            # arr.length / str.length -> len(x)
            if prop == "length":
                return "len(%s)" % obj
            # object-literal var: dot access becomes dict subscript
            if node["object"].get("type") == "Identifier" \
                    and node["object"]["name"] in self.dict_vars:
                return "%s[%s]" % (obj, _pystr(prop))
            return "%s.%s" % (obj, prop)
        if t == "CallExpression":
            call = self._array_method(node)
            if call is not None:
                return call
            args = ", ".join(self.expr(a) for a in node.get("arguments", []))
            return "%s(%s)" % (self.expr(node["callee"]), args)
        if t == "ObjectExpression":
            return self._object(node)
        if t == "NewExpression":
            args = ", ".join(self.expr(a) for a in node.get("arguments", []))
            return "%s(%s)" % (self.expr(node["callee"]), args)
        if t == "BinaryExpression":
            op = _BINOP.get(node["operator"])
            if op is None:
                raise Unsupported("operator %s" % node["operator"])
            return "(%s %s %s)" % (self.expr(node["left"]), op,
                                   self.expr(node["right"]))
        if t == "LogicalExpression":
            op = "and" if node["operator"] == "&&" else "or"
            return "(%s %s %s)" % (self.expr(node["left"]), op,
                                   self.expr(node["right"]))
        if t == "UnaryExpression":
            op = {"!": "not ", "-": "-", "+": "+"}.get(node["operator"])
            if op is None:
                raise Unsupported("unary %s" % node["operator"])
            return "(%s%s)" % (op, self.expr(node["argument"]))
        if t == "AssignmentExpression":
            op = node["operator"]
            left = self.expr(node["left"])
            right = self.expr(node["right"])
            if op == "=":
                return "%s = %s" % (left, right)
            return "%s %s %s" % (left, op, right)   # +=, -=, ...
        if t == "ConditionalExpression":
            return "(%s if %s else %s)" % (self.expr(node["consequent"]),
                                           self.expr(node["test"]),
                                           self.expr(node["alternate"]))
        if t == "ArrayExpression":
            return "[%s]" % ", ".join(self.expr(e) for e in node["elements"])
        if t == "UpdateExpression":
            raise Unsupported("++/-- inside an expression")
        if t == "ThisExpression":
            raise Unsupported("this")
        raise Unsupported("expression %s" % t)

    def _array_method(self, node):
        # Map the common JS array methods onto minipy list ops. Returns the
        # translated call, or None to fall back to a plain method call.
        callee = node["callee"]
        if callee.get("type") != "MemberExpression" or callee.get("computed"):
            return None
        prop = callee["property"]["name"]
        obj = self.expr(callee["object"])
        args = [self.expr(a) for a in node.get("arguments", [])]
        if prop == "push" and len(args) == 1:
            return "%s.append(%s)" % (obj, args[0])
        if prop == "pop" and not args:
            return "%s.pop()" % obj
        if prop == "shift" and not args:
            return "%s.pop(0)" % obj
        if prop == "unshift" and len(args) == 1:
            return "%s.insert(0, %s)" % (obj, args[0])
        return None

    def _object(self, node):
        parts = []
        for p in node.get("properties", []):
            key = p["key"]
            if key["type"] == "Identifier":
                k = _pystr(key["name"])
            elif key["type"] == "Literal":
                v = key.get("value")
                k = _pystr(v if isinstance(v, str) else str(v))
            else:
                raise Unsupported("object key %s" % key["type"])
            parts.append("%s: %s" % (k, self.expr(p["value"])))
        return "{%s}" % ", ".join(parts)

    def _literal(self, node):
        v = node.get("value")
        if v is True:
            return "True"
        if v is False:
            return "False"
        if v is None:
            # could be JS null or a regex; only null is supported
            if node.get("regex"):
                raise Unsupported("regex literal")
            return "None"
        if isinstance(v, str):
            return _pystr(v)
        return repr(v)                       # number


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


def translate(js_source):
    """JS source -> minipy Python source. Raises Unsupported (or ImportError if
    pyjsparser is missing) so the caller can choose to skip the script."""
    if _js_parse is None:
        raise ImportError("pyjsparser not installed (pip install pyjsparser)")
    return Translator().emit_program(_js_parse(js_source))


def main(argv):
    src = open(argv[1]).read() if len(argv) > 1 else sys.stdin.read()
    sys.stdout.write(translate(src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

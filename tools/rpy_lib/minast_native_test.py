#!/usr/bin/env python3
"""Self-test: run rast.py + minast.py (the CPython-`ast`-compatible facade) under
all three minipy executors -- CPython ground truth, the pure-Python ref VM, and
the py2c-compiled native interpreter -- and assert their output is byte-identical.

This is the end-to-end check that minast itself runs *on* minipy: it parses Python
with rast.py, builds the ast-shaped node tree with minast.py, rewrites it with a
NodeTransformer, walks it with a NodeVisitor, and reconstructs source with
unparse -- the exact pipeline py2c drives -- entirely on minipy.

minast.py normally does `from rast import parse_python, is_node`; here rast.py is
concatenated ahead of it (minipy has no module import), so that line is stripped.

Scope: this exercises both the expression pipeline (converter + NodeTransformer +
NodeVisitor + unparse) and statement-level conversion (funcdef/class/if/for/while
bodies) end-to-end on minipy, 3-way. Statement conversion previously tripped a
native-runtime crash -- minast's `if node.children:` guard on an empty child list
mis-evaluated because `truthy()` treated every container as truthy; empty
containers are now correctly falsy, so `def`/`class` conversion runs natively.

    python3 tools/rpy_lib/minast_native_test.py          # run all three, compare
    python3 tools/rpy_lib/minast_native_test.py --keep   # also leave combined file
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RAST = os.path.join(HERE, "rast.py")
MINAST = os.path.join(HERE, "minast.py")
RPY = os.path.join(ROOT, "tools", "rpy.py")

# Expressions spanning what unparse reproduces exactly and what the converter,
# NodeTransformer, and NodeVisitor must all handle: arithmetic, calls with
# *args/keywords, comprehensions, attributes, ternary, compare chains, subscript
# tuples (annotation slices), boolean/unary ops, and type-annotation forms.
SNIPPETS = [
    "a + b * a",
    "f(a, b, *a, k=a)",
    "[a for a in xs if a]",
    "{a: b for a in xs}",
    "a.b.c",
    "a if b else c",
    "a < b <= c",
    "d[a, b]",
    "not a",
    "a and b or a",
    "-a + ~b",
    "list[int]",
    "dict[str, str]",
    "tuple[int, str]",
    "obj.method(a).attr[b]",
]

# Statement-level programs: the funcdef/class forms that exercise the
# `if node.children:` guards (empty return-annotation, empty bases) that the
# truthy() fix unblocked, plus if/for/while suites and nesting.
PROGRAMS = [
    "def f():\n    pass\n",
    "def f() -> int:\n    return 1\n",
    "def f(a, b, *c, k=1):\n    return a + b\n",
    "class C:\n    pass\n",
    "class C(B):\n    x = 1\n",
    "class C(B, D):\n    def m(self):\n        return self.x\n",
    "if a:\n    b = 1\nelse:\n    b = 2\n",
    "for i in xs:\n    total = total + i\n",
    "while a < b:\n    a = a + 1\n",
    "def outer():\n    def inner():\n        return 1\n    return inner\n",
]

DRIVER = '''

class _Renamer(NodeTransformer):
    def visit_Name(self, node):
        if node.id == "a":
            node.id = "Z"
        return node


class _Collector(NodeVisitor):
    def __init__(self):
        self.ids = []
    def visit_Name(self, node):
        self.ids.append(node.id)
        self.generic_visit(node)


def _emit(src):
    tree = parse(src + "\\n")
    value = tree.body[0].value
    print("EXPR " + unparse(value))
    _Renamer().visit(tree)
    print("XFRM " + unparse(tree.body[0].value))
    coll = _Collector()
    coll.visit(tree)
    print("IDS  " + " ".join(coll.ids))


def _emit_prog(src):
    tree = parse(src)
    print("PROG")
    print(unparse(tree))
    _Renamer().visit(tree)
    print("PXFM")
    print(unparse(tree))
    coll = _Collector()
    coll.visit(tree)
    print("PIDS " + " ".join(coll.ids))


%s
'''


def build_combined():
    rast = open(RAST).read()
    minast = open(MINAST).read()
    minast = "\n".join(
        ln for ln in minast.split("\n")
        if not ln.strip().startswith("from rast import"))
    calls = "\n".join("_emit(%r)" % s for s in SNIPPETS)
    calls += "\n" + "\n".join("_emit_prog(%r)" % s for s in PROGRAMS)
    combined = rast + "\n" + minast + "\n" + DRIVER % calls
    fd, path = tempfile.mkstemp(prefix="minast_native_combined_", suffix=".py")
    os.write(fd, combined.encode())
    os.close(fd)
    return path


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout


def main(argv):
    keep = "--keep" in argv
    combined = build_combined()
    try:
        cpython = run([sys.executable, combined])
        ref = run([sys.executable, RPY, "--ref", combined])
        native = run([sys.executable, RPY, combined])
    finally:
        if keep:
            print("combined file:", combined)
        else:
            os.unlink(combined)

    if cpython == ref == native:
        nlines = len(cpython.splitlines())
        print("PASS: cpython == ref == native  (%d snippets, %d programs, %d output lines)"
              % (len(SNIPPETS), len(PROGRAMS), nlines))
        return 0
    print("FAIL: executors disagree")
    for name, out in (("cpython", cpython), ("ref", ref), ("native", native)):
        print("---- %s ----" % name)
        print(out)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

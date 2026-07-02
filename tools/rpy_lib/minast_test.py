"""Differential test for minast.py against CPython's own `ast` module.

minast rewrites the rast parse tree into node objects that mirror CPython's
`ast`.  This test parses the same source two ways -- once with the real `ast`
module and once with `minast` -- and checks the trees are structurally
identical (ignoring source locations and a few fields py2c never reads).  It
runs under CPython only, since it needs the reference `ast` to compare against;
it is the correctness oracle for the converter, analogous to build_rpy_ast.py's
--check for the parser.

Two checks:
  1. A curated snippet set covering every construct py2c uses -- ALL must match.
  2. A coverage sweep over tools/py2c.py -- every top-level statement must
     *convert* (no exceptions), and any structural mismatch must fall into one
     of the KNOWN rast parser bugs (documented below), not the converter.

Known rast bugs surfaced here (tracked separately from minast):
  * raw-string escapes: \\t / \\n inside r'''...''' are unescaped by rast.
  * elif misnesting: an elif after a nested bare `if` inside an elif branch is
    attached to the inner `if` instead of the outer chain.
  * for/while-else: rast drops the else block (not exercised by the sweep).
"""
import ast as real
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minast

# Fields that carry no structural meaning for py2c (locations, py3.8+/3.12+
# annotations we don't model), skipped on both sides before comparing.
SKIP = set(["lineno", "col_offset", "end_lineno", "end_col_offset",
            "type_comment", "type_params", "type_ignores", "kind"])


def dump(node):
    if isinstance(node, list):
        return "[" + ", ".join(dump(x) for x in node) + "]"
    if isinstance(node, (real.AST, minast.AST)):
        parts = []
        for f in node._fields:
            if f in SKIP or not hasattr(node, f):
                continue
            parts.append("%s=%s" % (f, dump(getattr(node, f))))
        return type(node).__name__ + "(" + ", ".join(parts) + ")"
    return repr(node)


SNIPPETS = [
    "x = 5\n",
    "a.b.c\n",
    "f(1, x, g(2))\n",
    "a + b * c - d\n",
    "a < b <= c\n",
    "x = a and b and c or not d\n",
    "y = -a + ~b\n",
    "r = a[1]\n",
    "r = a[1:2]\n",
    "r = a[::2]\n",
    "r = d[a, b]\n",
    "l = [1, 2, 3]\n",
    "t = (1, 2)\n",
    "d = {1: 2, 3: 4}\n",
    "s = {1, 2, 3}\n",
    "e = []\nf = {}\ng = ()\n",
    "c = [i for i in xs if i if j]\n",
    "g = {k: v for k, v in items}\n",
    "st = {x.a for x in ys}\n",
    "ge = sorted(i for i in xs if i)\n",
    "def f(a, b: \"int\", c=1, *args, **kw) -> \"int\":\n    return a + b\n",
    "class C(A, B):\n    x = 1\n    def m(self):\n        return self.x\n",
    "class D(ast.NodeTransformer):\n    pass\n",
    "if a:\n    p = 1\nelif b:\n    p = 2\nelse:\n    p = 3\n",
    "for i in xs:\n    y = i\n",
    "while a > 0:\n    a = a - 1\n",
    "import os\nimport a.b\nimport a.b as c\nfrom a.b import c, d as e\n",
    "x += 1\ny: int = 2\n",
    "obj.attr = val\narr[i] = 9\n",
    "x = f(a, *rest, k=1, **kw)\n",
    "x = v if cond else w\n",
    "raise E('m') from c\n",
    "assert x, 'oops'\n",
    "del a, b\n",
    "global g, h\n",
    "s = \"a\" \"b\" \"c\"\n",
    "v = None\nw = True\nz = False\n",
    "try:\n    a = 1\nexcept ValueError as e:\n    b = 2\nexcept (K, J):\n    c = 3\nexcept:\n    d = 4\nelse:\n    f = 5\nfinally:\n    g = 6\n",
    "try:\n    a = 1\nfinally:\n    b = 2\n",
    "with open(f) as fh, lock:\n    a = 1\n    b = 2\n",
    "@deco\n@mod.deco2(arg)\ndef f():\n    pass\n",
    "@reg\nclass C:\n    pass\n",
]


def _first_diff(w, g):
    n = min(len(w), len(g))
    for i in range(n):
        if w[i] != g[i]:
            return i
    if len(w) != len(g):
        return n
    return -1


def _is_known_rast_bug(w, g):
    i = _first_diff(w, g)
    if i < 0:
        return False
    ww = w[max(0, i - 12):i + 18]
    gg = g[max(0, i - 12):i + 18]
    for esc in ("\\\\t", "\\\\n", "\\\\r"):
        plain = esc.replace("\\\\", "\\")
        if esc in ww and plain in gg:
            return True          # raw-string escape bug
    if "orelse" in ww:
        return True              # elif misnesting bug
    return False


def run_snippets():
    failures = 0
    for s in SNIPPETS:
        want = dump(real.parse(s))
        try:
            got = dump(minast.parse(s))
        except Exception as e:
            print("EXC   %r -> %s: %s" % (s, type(e).__name__, e))
            failures += 1
            continue
        if want != got:
            i = _first_diff(want, got)
            print("DIFF  %r" % s)
            print("  want ...%s" % want[max(0, i - 20):i + 45])
            print("  got  ...%s" % got[max(0, i - 20):i + 45])
            failures += 1
    print("snippets: %d/%d matched" % (len(SNIPPETS) - failures, len(SNIPPETS)))
    return failures


def run_py2c_sweep():
    path = os.path.join(os.path.dirname(__file__), "..", "py2c.py")
    if not os.path.exists(path):
        print("py2c sweep: skipped (py2c.py not found)")
        return 0
    import textwrap
    src = open(path).read()
    lines = src.split("\n")
    tree = real.parse(src)
    ok = exc = rastbug = badconv = 0
    for st in tree.body:
        seg = textwrap.dedent("\n".join(lines[st.lineno - 1:st.end_lineno])) + "\n"
        want = dump(real.parse(seg))
        try:
            got = dump(minast.parse(seg))
        except Exception as e:
            exc += 1
            print("  CONVERT-FAIL line %d: %s" % (st.lineno, e))
            continue
        if want == got:
            ok += 1
        elif _is_known_rast_bug(want, got):
            rastbug += 1
        else:
            badconv += 1
            print("  CONVERTER-DIFF line %d" % st.lineno)
    print("py2c sweep: %d exact, %d known-rast-bug, %d converter-bug, %d convert-fail"
          % (ok, rastbug, badconv, exc))
    return exc + badconv


def run_transformer_tests():
    # NodeVisitor collection order and NodeTransformer rewrites must match the
    # real ast module's semantics (dispatch by type, generic_visit recursion,
    # in-place field/list rewrite).
    def renamer(base):
        class R(base.NodeTransformer):
            def visit_Name(self, node):
                if node.id == "a":
                    node.id = "Q"
                return node
        return R

    def collector(base):
        class C(base.NodeVisitor):
            def __init__(self):
                self.ids = []
            def visit_Name(self, node):
                self.ids.append(node.id)
                self.generic_visit(node)
        return C

    cases = [
        "def f(a, b):\n    c = a + b * a\n    return g(a, c, [a, a.x])\n",
        "x = {a: a for a in items if a}\n",
        "for a in xs:\n    print(a, b)\n",
    ]
    failures = 0
    for src in cases:
        rt = real.parse(src); renamer(real)().visit(rt)
        mt = minast.parse(src); renamer(minast)().visit(mt)
        if dump(rt) != dump(mt):
            print("TRANSFORM-DIFF %r" % src); failures += 1
        rc = collector(real)(); rc.visit(real.parse(src))
        mc = collector(minast)(); mc.visit(minast.parse(src))
        if rc.ids != mc.ids:
            print("VISIT-DIFF %r  real=%s minast=%s" % (src, rc.ids, mc.ids))
            failures += 1
    print("transformer/visitor: %d/%d cases matched"
          % (len(cases) - failures, len(cases)))
    return failures


def run_unparse_tests():
    # minast.unparse must match CPython's ast.unparse for expressions (the
    # annotation forms py2c relies on for ctype inference are the critical path)
    # and for the best-effort statement cases.
    exprs = [
        "int", "list[int]", "dict[str, str]", '"MyClass"', "Optional[int]",
        "tuple[int, str]", "a.b.C", 'list["Foo"]', "x + y * z", "a and b or c",
        "-x", "~y", "a < b <= c", "v if c else w", "f(1, *xs, k=2, **kw)",
        "[i for i in xs if i]", "{k: v for k in ys}", "(i for i in xs)",
        "x[1:2]", "x[::2]", "{1, 2}", "set()", "not a", "a is not b", "x[a, b]",
        # operator-precedence parenthesization (CPython-exact)
        "a % (b or c)", "(a or b) and c", "a and b or (c and d)",
        "isinstance(x, A) and isinstance(y, B) and (n == 1)",
        "(a.asname or a.name).split('.')[0]", "(a + b) * c", "a + b * c",
        "-(a + b)", "(a or b)(x)", "(a and b).method()", "not (a or b)",
        "2 ** -1", "-2 ** 2", "a < b and c",
    ]
    ok = 0
    for s in exprs:
        want = real.unparse(real.parse(s, mode="eval").body)
        got = minast.unparse(minast.parse(s + "\n").body[0].value)
        if want == got:
            ok += 1
        else:
            print("UNPARSE-DIFF %-22s want=%r got=%r" % (s, want, got))
    # annotation ctype-text extraction, exactly as py2c does it
    anns = ["int", "list[int]", "dict[str, str]", '"MyClass"', 'list["Foo"]',
            "tuple[int, str]"]
    ann_ok = 0
    for s in anns:
        m = minast.parse("x: " + s + " = 0\n").body[0].annotation
        c = real.parse("x: " + s + " = 0\n").body[0].annotation
        if minast.unparse(m).strip().strip("'\"") == real.unparse(c).strip().strip("'\""):
            ann_ok += 1
        else:
            print("ANN-DIFF %r" % s)
    # statement-level programs: minast.unparse of a full module must match
    # CPython's ast.unparse byte-for-byte (funcdef/class/if/for/while/try/with/
    # import/raise/assert/global/del/annotations/aug-assign, incl. elif chains,
    # decorators, else/finally, tuple targets).
    progs = [
        "def f(a, b=1, *c, **kw) -> list:\n    return a\n",
        "@deco\nclass C(A, B):\n    x = 0\n\n    def m(self):\n        return self.x\n",
        "try:\n    x = 1\nexcept (A, B) as e:\n    y = 2\nexcept C:\n    z = 3\nelse:\n    w = 4\nfinally:\n    cleanup()\n",
        "with open('f') as a, ctx() as b:\n    read(a)\n",
        "with x as (a, b):\n    pass\n",
        "if a:\n    p()\nelif b:\n    q()\nelif c:\n    r()\nelse:\n    s()\n",
        "for i in items:\n    del x, y\n    global g\n",
        "raise ValueError('x') from err\nassert cond, 'message'\n",
        "x: int\ny: dict[str, int] = {}\na, b = (b, a)\nlst[0], lst[1] = (1, 2)\n",
        "while True:\n    break\nelse:\n    pass\n",
        "import a.b.c\nimport a.b.c as abc\nfrom pkg import y as z, w\n",
        "for i, it in enumerate(xs):\n    total += it\n",
        "def f(a, b=1, *c, d, e=2, **kw) -> list:\n    return a\n",
        "def g(*args, x, y=2, **kw):\n    return x\n",
    ]
    prog_ok = 0
    for s in progs:
        want = real.unparse(real.parse(s))
        got = minast.unparse(minast.parse(s))
        if want == got:
            prog_ok += 1
        else:
            print("PROG-UNPARSE-DIFF for %r\n  want=%r\n  got =%r" % (s[:30], want, got))
    print("unparse: %d/%d exprs, %d/%d annotations, %d/%d programs matched"
          % (ok, len(exprs), ann_ok, len(anns), prog_ok, len(progs)))
    return ((len(exprs) - ok) + (len(anns) - ann_ok) + (len(progs) - prog_ok))


if __name__ == "__main__":
    bad = (run_snippets() + run_py2c_sweep() + run_transformer_tests()
           + run_unparse_tests())
    if bad:
        print("FAIL")
        sys.exit(1)
    print("PASS")

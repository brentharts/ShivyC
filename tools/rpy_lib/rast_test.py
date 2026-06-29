#!/usr/bin/env python3
"""Self-test: run the rast.py Python parser under all three minipy executors
(CPython ground truth, the pure-Python ref VM, and the py2c-compiled native
interpreter) and assert their parse-tree dumps are byte-identical.

This is the first end-to-end demonstration that minipy can compile and run a
non-trivial real-world Python program -- a Python parser -- and get exactly the
same result natively as under CPython.  It exercises classes, methods,
recursion, exceptions, dict/list comprehensions, generator expressions and
heavy string work, all on minipy.

    python3 tools/rpy_lib/rast_test.py            # run all three, compare
    python3 tools/rpy_lib/rast_test.py --keep     # also leave the combined file
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RAST = os.path.join(HERE, "rast.py")
RPY = os.path.join(ROOT, "tools", "rpy.py")

# A spread of Python constructs -- precedence, defs, control flow, containers,
# calls, comparisons -- enough to meaningfully exercise the grammar.
SNIPPETS = [
    "x = 1 + 2 * 3\n",
    "y = (1 + 2) * 3\n",
    "def f(a, b):\n    return a + b\n",
    "if x > 0:\n    y = 1\nelse:\n    y = 2\n",
    "while n:\n    n = n - 1\n",
    "for i in items:\n    total = total + i\n",
    "vals = [1, 2, 3]\n",
    "d = {1: 2, 3: 4}\n",
    "r = f(a, b) + g(c)\n",
    "ok = a == b and c != d\n",
]

# Driver appended to a copy of rast.py: parse each snippet and print a canonical
# pre-order dump of the resulting Node tree.  Kept in minipy's supported subset.
DRIVER = '''

def _dump(node, depth):
    if not is_node(node):
        print(". " * depth + "leaf " + str(node))
        return
    print(". " * depth + node.name)
    for ch in node.children:
        _dump(ch, depth + 1)

def _test(src):
    print("### " + src)
    _dump(parse_python(src), 0)

%s
'''


def build_combined():
    calls = "\n".join("_test(%r)" % s for s in SNIPPETS)
    combined = open(RAST).read() + DRIVER % calls
    fd, path = tempfile.mkstemp(prefix="rast_combined_", suffix=".py")
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
        if not keep:
            os.unlink(combined)
        else:
            print("combined file:", combined)

    ok = True
    if cpython != ref:
        print("MISMATCH: ref VM != CPython"); ok = False
    if cpython != native:
        print("MISMATCH: native != CPython"); ok = False
    lines = cpython.count("\n")
    if ok:
        print("PASS: cpython == ref == native  (%d snippets, %d output lines)"
              % (len(SNIPPETS), lines))
        return 0
    print("--- CPython ---"); print(cpython)
    print("--- native ---"); print(native)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

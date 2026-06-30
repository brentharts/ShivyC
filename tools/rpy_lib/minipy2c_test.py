#!/usr/bin/env python3
"""Self-test: run rast.py + minipy2c.py (the mini Python->C transpiler) under all
three minipy executors -- CPython ground truth, the pure-Python ref VM, and the
py2c-compiled native interpreter -- and assert the emitted C is byte-identical.

This is the end-to-end real-app check for minipy: parse Python with rast.py, then
transpile its AST to C with minipy2c.py, entirely on minipy. It exercises classes,
methods, deep recursion, list work and heavy string building all at once.

    python3 tools/rpy_lib/minipy2c_test.py            # run all three, compare
    python3 tools/rpy_lib/minipy2c_test.py --keep     # also leave the combined file
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RAST = os.path.join(HERE, "rast.py")
M2C = os.path.join(HERE, "minipy2c.py")
RPY = os.path.join(ROOT, "tools", "rpy.py")

# Programs spanning the constructs minipy2c handles: defs, params, recursion,
# while, if/elif/else, assignment (declare vs reassign), calls, comparisons.
SNIPPETS = [
    "def add(a, b):\n    return a + b\n",
    "def fib(n):\n    if n < 2:\n        return n\n    else:\n        return fib(n - 1) + fib(n - 2)\n",
    "def sumto(n):\n    total = 0\n    i = 0\n    while i < n:\n        total = total + i\n        i = i + 1\n    return total\n",
    "def grade(x):\n    if x < 60:\n        r = 0\n    else:\n        r = 1\n    return r\n",
    "def poly(x):\n    return ((x * x) + (x * 3)) + 1\n",
]

DRIVER = '''

def _emit_test(src):
    print("=== " + src)
    print(transpile_source(src))

%s
'''


def build_combined():
    calls = "\n".join("_emit_test(%r)" % s for s in SNIPPETS)
    combined = open(RAST).read() + open(M2C).read() + DRIVER % calls
    fd, path = tempfile.mkstemp(prefix="minipy2c_combined_", suffix=".py")
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

    ok = cpython == ref == native
    if ok:
        nlines = len(cpython.splitlines())
        print("PASS: cpython == ref == native  (%d snippets, %d output lines)"
              % (len(SNIPPETS), nlines))
        return 0
    print("FAIL: executors disagree")
    for name, out in (("cpython", cpython), ("ref", ref), ("native", native)):
        print("---- %s ----" % name)
        print(out)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

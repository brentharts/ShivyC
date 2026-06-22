#!/usr/bin/env python3
"""Compile-speed benchmark: ShivyCX vs gcc.

Measures wall-clock time to compile a C source to an executable, comparing the
ShivyCX compiler against the system gcc on the same input. This answers "how
long does our compiler take versus gcc" and tracks that ratio over time.

The ShivyCX compiler is invoked as a subprocess so the benchmark works for any
build of it:

  * by default it runs the Python-hosted compiler: `python3 -m shivyc.main`;
  * set $SHIVYC to a command (e.g. a native self-hosted binary once one links,
    or `pypy3 -m shivyc.main`) to benchmark that instead.

Usage:
    python3 benchmarks/compile_speed/bench_compile_speed.py [file.c] [-n N]

With no file, a representative ~200-line arithmetic/loop program is generated.
"""
import os
import shlex
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def shivyc_cmd():
    env = os.environ.get("SHIVYC")
    if env:
        return shlex.split(env)
    return [sys.executable, "-m", "shivyc.main"]


def gcc_cmd():
    return shlex.split(os.environ.get("CC", "gcc"))


def _gen_source(path):
    """Write a self-contained C program both compilers accept (no includes:
    ShivyCX has no system headers, so we declare what we use)."""
    lines = ["int putchar(int);", "int collatz(int n) {", "    int steps = 0;",
             "    while (n != 1) {", "        if (n % 2 == 0) n = n / 2;",
             "        else n = 3 * n + 1;", "        steps = steps + 1;",
             "    }", "    return steps;", "}", ""]
    # a pile of simple functions to give the front end real work
    for i in range(40):
        lines += ["int f%d(int x) {" % i,
                  "    int a = x * %d + %d;" % (i + 1, i),
                  "    int b = a;",
                  "    for (int k = 0; k < 8; k = k + 1) { b = b + a * k - k; }",
                  "    return b % 1000 + collatz((x % 27) + 1);", "}", ""]
    lines += ["int main() {", "    int total = 0;"]
    for i in range(40):
        lines.append("    total = total + f%d(total + %d);" % (i, i))
    lines += ["    return total % 256;", "}", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def _time(cmd, runs):
    """Median wall-clock seconds over `runs` (best-of reduces noise)."""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        dt = time.perf_counter() - t0
        if p.returncode != 0:
            return None, (p.stdout + p.stderr)
        times.append(dt)
    times.sort()
    return times[len(times) // 2], None


def main(argv):
    runs = 5
    src = None
    i = 0
    while i < len(argv):
        if argv[i] == "-n" and i + 1 < len(argv):
            runs = int(argv[i + 1]); i += 2
        else:
            src = argv[i]; i += 1

    tmp = tempfile.mkdtemp(prefix="shivyc-bench-")
    if src is None:
        src = os.path.join(tmp, "bench.c")
        _gen_source(src)
        kind = "generated (~200 lines, headerless)"
    else:
        kind = src
    out_sh = os.path.join(tmp, "out_shivyc")
    out_gcc = os.path.join(tmp, "out_gcc")

    print("compile-speed benchmark (median of %d runs)" % runs)
    print("  source : %s" % kind)
    print("  shivyc : %s" % " ".join(shivyc_cmd()))
    print("  gcc    : %s" % " ".join(gcc_cmd()))
    print()

    sh, err = _time(shivyc_cmd() + ["--no-cache", src, "-o", out_sh], runs)
    if sh is None:
        print("shivyc failed to compile the sample:\n" + err[-800:])
        return 1
    gc, err = _time(gcc_cmd() + [src, "-o", out_gcc], runs)
    if gc is None:
        print("gcc failed to compile the sample:\n" + err[-800:])
        return 1

    print("  shivyc : %8.1f ms" % (sh * 1000))
    print("  gcc    : %8.1f ms" % (gc * 1000))
    if gc > 0:
        print("  ratio  : shivyc is %.1fx %s than gcc"
              % ((sh / gc) if sh >= gc else (gc / sh),
                 "slower" if sh >= gc else "faster"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

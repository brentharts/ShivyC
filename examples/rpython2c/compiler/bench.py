#!/usr/bin/env python3
"""Benchmark the rpython lexer kernel: CPython vs ShivyCX-compiled vs gcc.

Verifies all three produce the *same* checksum (so the transpilation is
faithful), then reports wall-clock time and speedup. The kernel is the same
file run three ways -- the point of rewriting compiler hotspots in rpython.

    python3 examples/rpython2c/compiler/bench.py [reps]
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
KERNEL = os.path.join(HERE, "lexer_kernel.py")


def cpython(reps):
    sys.path.insert(0, HERE)
    import lexer_kernel as k
    t = time.time()
    chk = k.run(reps)
    return time.time() - t, chk


def build_and_time(reps):
    # A bench source: the kernel with main() running `reps` repetitions.
    src = open(KERNEL).read().replace("return run(1) % 256",
                                      "return run(%d) %% 256" % reps)
    bsrc = os.path.join("/tmp", "lexbench.py")
    open(bsrc, "w").write(src)

    results = {}
    # ShivyCX backend
    sx = "/tmp/lexbench_sx"
    r = subprocess.run([sys.executable, "-m", "shivyc.main", "--no-cache",
                        bsrc, "-o", sx], cwd=REPO, capture_output=True)
    if r.returncode == 0:
        t = time.time()
        rc = subprocess.run([sx]).returncode
        results["ShivyCX"] = (time.time() - t, rc)

    # gcc -O2 (transpile, then compile the generated C)
    outdir = "/tmp/lexbench_c"
    subprocess.run([sys.executable, os.path.join(REPO, "tools", "py2c.py"),
                    bsrc, "--out", outdir], capture_output=True)
    gcc = "/tmp/lexbench_gcc"
    cc = subprocess.run(["gcc", "-O2", "-w",
                         os.path.join(outdir, "lexbench.c"),
                         os.path.join(outdir, "shivyc_rt.c"), "-o", gcc],
                        capture_output=True)
    if cc.returncode == 0:
        t = time.time()
        rc = subprocess.run([gcc]).returncode
        results["gcc -O2"] = (time.time() - t, rc)
    return results


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    print("lexer kernel benchmark (%d reps)\n" % reps)

    py_t, chk = cpython(reps)
    exit_code = chk % 256
    print("checksum = %d   (exit code %d)\n" % (chk, exit_code))

    rows = [("CPython", py_t, exit_code)]
    for name, (t, rc) in build_and_time(reps).items():
        ok = "ok" if rc == exit_code else "MISMATCH(%d)" % rc
        rows.append((name, t, rc, ok))

    print("%-10s %10s %8s   %s" % ("backend", "time(s)", "speedup", "result"))
    for row in rows:
        name, t = row[0], row[1]
        speed = "%.1fx" % (py_t / t) if t > 0 else "-"
        extra = row[3] if len(row) > 3 else "reference"
        print("%-10s %10.3f %8s   %s" % (name, t, speed, extra))


if __name__ == "__main__":
    main()

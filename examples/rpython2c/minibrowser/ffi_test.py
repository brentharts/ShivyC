#!/usr/bin/env python3
"""Native proof that the browser can run a page's JIT'd rpython at run time.

Builds `ffi_probe.py` (transpiled by py2c and linked with mb_ffi.c + -ldl) and
runs it: it dlopens a JIT-compiled `.so` and calls its `calc_sum` symbol through
a run-time pointer, all in native code. This is the mechanism the embedded
interpreter's ctypes will use to invoke <script type="rpython"> blocks.

    python3 ffi_test.py

Exits 0 on PASS. (Separate from jit_test.py, which proves the same pipeline via
CPython + real ctypes; this one proves the *native* run-time FFI path.)
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PY2C = os.path.join(ROOT, "tools", "py2c.py")
RPY_CTYPES = os.path.join(ROOT, "tools", "rpy_lib", "rpy_ctypes.py")
WORK = "/tmp/mb_ffi_test"


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
    return r.returncode == 0


def main():
    os.makedirs(WORK, exist_ok=True)
    # 1. JIT-compile a trivial rpython block to WORK/jit.foo.so
    with open(os.path.join(WORK, "foo.py"), "w") as fh:
        fh.write("def calc_sum(a: int, b: int) -> int:\n    return a + b\n")
    assert sh([sys.executable, PY2C, os.path.join(WORK, "foo.py"),
               "--out", WORK]), "py2c(foo) failed"
    assert sh(["cc", "-O2", "-w", "-shared", "-fPIC",
               os.path.join(WORK, "foo.c"), "-o",
               os.path.join(WORK, "jit.foo.so"), "-lm"]), "gcc(.so) failed"

    # 2. Transpile + link the native FFI probe (rpython + mb_ffi.c, -ldl)
    bdir = os.path.join(WORK, "probe")
    os.makedirs(bdir, exist_ok=True)
    for f in ("ffi_probe.py",):
        open(os.path.join(bdir, f), "w").write(open(os.path.join(HERE, f)).read())
    open(os.path.join(bdir, "rpy_ctypes.py"), "w").write(open(RPY_CTYPES).read())
    print("transpiling + linking native FFI probe...")
    assert sh([sys.executable, PY2C, os.path.join(bdir, "ffi_probe.py"),
               os.path.join(bdir, "rpy_ctypes.py"), "--out", bdir]), \
        "py2c(probe) failed"
    csrc = [c for c in glob.glob(os.path.join(bdir, "*.c"))
            if os.path.basename(c) != "rpy_ctypes.c"]   # marker module, not C
    app = os.path.join(bdir, "ffi_app")
    assert sh(["cc", "-O2", "-w", "-I", bdir] + csrc +
              [os.path.join(HERE, "mb_ffi.c"), "-o", app, "-ldl", "-lm"]), \
        "link(probe) failed"

    # 3. Run it
    r = subprocess.run([app], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    assert "OK native runtime FFI" in r.stdout, "native FFI probe did not pass"
    print("ffi_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

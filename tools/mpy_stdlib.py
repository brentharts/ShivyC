#!/usr/bin/env python3
"""Stress-test tools/py2c.py against MicroPython's pure-Python standard library.

This clones (or reuses) the MicroPython repo plus the micropython-lib
``python-stdlib`` tree, then for every stdlib module it:

  1. transpiles the ``.py`` to C with ``py2c`` (mp-bridge runtime),
  2. compiles the generated ``.c`` with the C compiler, and
  3. partial-links every object that compiled into one relocatable object,
     which surfaces any cross-module symbol clashes.

It prints a three-stage report (transpiled / compiled / linked) with the top
failure reasons. This is a coverage probe, not a correctness test: many stdlib
modules lean on CPython/MicroPython internals py2c does not model, so failures
are expected and informative.

Usage:
    python3 tools/mpy_stdlib.py                 # clone + run, summary report
    python3 tools/mpy_stdlib.py --stdlib-dir D  # use an existing python-stdlib
    python3 tools/mpy_stdlib.py -v              # list every module's outcome
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import py2c  # noqa: E402  (tools/ on path)

MPY_URL = "https://github.com/OpenSourceJesus/micropython"
MPYLIB_URL = "https://github.com/micropython/micropython-lib"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _has_py(d: Path) -> bool:
    return d.exists() and any(d.rglob("*.py"))


def ensure_stdlib(work: Path) -> Path:
    """Return a python-stdlib directory, cloning what is needed."""
    mpy = work / "micropython"
    if not mpy.exists():
        print("cloning %s ..." % MPY_URL)
        r = run(["git", "clone", "--depth", "1", MPY_URL, str(mpy)])
        if r.returncode != 0:
            print(r.stderr.strip()[-400:])
    # python-stdlib lives in the micropython-lib submodule; the pinned commit
    # is often unreachable from a shallow clone, so fall back to a direct clone
    # of micropython-lib at its default branch.
    sub = mpy / "lib" / "micropython-lib" / "python-stdlib"
    if _has_py(sub):
        return sub
    mpylib = work / "micropython-lib"
    if not _has_py(mpylib / "python-stdlib"):
        print("cloning %s ..." % MPYLIB_URL)
        r = run(["git", "clone", "--depth", "1", MPYLIB_URL, str(mpylib)])
        if r.returncode != 0:
            print(r.stderr.strip()[-400:])
    return mpylib / "python-stdlib"


def is_module(py_path: Path) -> bool:
    """True for a real stdlib module (not packaging metadata or tests)."""
    name = py_path.name
    if name == "manifest.py" or name == "setup.py":
        return False
    p = py_path.as_posix()
    if "/test" in p or name.startswith("test_") or "/examples/" in p:
        return False
    return True


def short_err(err: str) -> str:
    err = (err or "").strip()
    last = err.splitlines()[-1] if err else "unknown error"
    return last[:160]


def short_cc_err(stderr: str) -> str:
    for line in stderr.splitlines():
        if "error:" in line:
            return line.split("error:", 1)[1].strip()[:160]
    return (stderr.strip().splitlines() or ["?"])[-1][:160]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Stress-test py2c against the MicroPython stdlib.")
    ap.add_argument("--stdlib-dir", help="existing python-stdlib path")
    ap.add_argument("--work", default="/tmp/mpy_stdlib_work",
                    help="scratch dir for clones")
    ap.add_argument("--out", default="/tmp/mpy_stdlib_out",
                    help="dir for generated C/objects")
    ap.add_argument("--cc", default="gcc")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list every module outcome")
    args = ap.parse_args(argv)

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    stdlib = Path(args.stdlib_dir) if args.stdlib_dir else ensure_stdlib(work)
    if not _has_py(stdlib):
        print("no python-stdlib .py files found under %s" % stdlib)
        return 1

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    py2c.write_runtime(str(out), mp_bridge=True)
    rt_o = out / "shivyc_rt.o"
    rt = run([args.cc, "-c", str(out / "shivyc_rt.c"), "-I", str(out),
              "-o", str(rt_o)])
    if rt.returncode != 0:
        print("runtime failed to compile:\n" + rt.stderr[-800:])
        return 1

    files = [p for p in sorted(stdlib.rglob("*.py")) if is_module(p)]
    transpiled, t_fail = [], []
    compiled, c_fail = [], []
    objs = []
    for p in files:
        rel = p.relative_to(stdlib).as_posix()
        cpath, err = py2c.transpile_file(str(p), str(out), str(stdlib))
        if cpath is None:
            t_fail.append((rel, short_err(err)))
            continue
        transpiled.append(rel)
        opath = cpath[:-2] + ".o"
        cc = run([args.cc, "-c", cpath, "-I", str(out), "-o", opath])
        if cc.returncode != 0:
            c_fail.append((rel, short_cc_err(cc.stderr)))
            continue
        compiled.append(rel)
        objs.append(opath)

    # Stage 3: partial-link every compiled object into one relocatable object.
    # This is where duplicate/clashing symbols across modules would surface.
    link_ok, link_msg = True, ""
    combined = out / "stdlib_combined.o"
    if objs:
        ld = run(["ld", "-r", "-o", str(combined), *objs])
        if ld.returncode != 0:
            link_ok, link_msg = False, short_cc_err(ld.stderr)

    total = len(files)
    print("\n=== MicroPython stdlib via ShivyCX py2c ===")
    print("stdlib dir : %s" % stdlib)
    print("modules    : %d" % total)
    print("transpiled : %d/%d" % (len(transpiled), total))
    print("compiled   : %d/%d" % (len(compiled), total))
    if objs:
        print("linked     : %s (%d objects -> %s)"
              % ("ok" if link_ok else "FAIL: " + link_msg,
                 len(objs), combined.name))

    if args.verbose:
        print("\n--- compiled ---")
        for r in compiled:
            print("  ok    %s" % r)
    # Always show why things failed -- that is the useful signal.
    if t_fail:
        print("\n--- transpile failures (%d) ---" % len(t_fail))
        for r, e in t_fail:
            print("  %-32s %s" % (r, e))
    if c_fail:
        print("\n--- compile failures (%d) ---" % len(c_fail))
        for r, e in c_fail:
            print("  %-32s %s" % (r, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())

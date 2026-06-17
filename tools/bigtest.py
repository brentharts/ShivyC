#!/usr/bin/env python3
"""Generic ShivyCX compile-checker for large external codebases.

A single configurable driver behind the `test_cpython` / `test_bsd` style
Makefile targets. It compiles a set of C sources through ShivyCX and reports a
per-file pass/fail summary.

Each file is compiled in its own ShivyCX subprocess: ShivyCX carries global
state across compilations, so a single long-lived interpreter would produce
false results. The subprocesses reuse whichever interpreter runs this script
(PyPy3 when invoked from the Makefile), which is what helps the slow, large
translation units.

Two preprocessing modes:
  * default      -- ShivyCX does everything (its own preprocessor + headers).
                    Pair with --musl to use the packaged musl libc.
  * --gcc-pp     -- run gcc -E first (forwarding -I/-D), then compile the
                    preprocessed unit. Use for codebases that need a full set
                    of system headers ShivyCX does not bundle.

To keep routine regression runs fast, source files larger than --max-kb are
skipped by default (they are the slow ones); pass --include-large for the full
set. -I and -D are forwarded straight through, so Makefile targets can tweak
includes/defines simply.

Usage:
    pypy3 tools/bigtest.py SRC_DIR GLOB [GLOB ...] [options]

Examples:
    # CPython object model, against musl, skipping the big files:
    pypy3 tools/bigtest.py cpython-tinier 'Objects/*.c' --musl \\
        -I . -I Include -I Include/internal \\
        -D Py_BUILD_CORE -D thread_local=_Thread_local

    # 2.11BSD userland utilities:
    pypy3 tools/bigtest.py 2.11BSD-riscv 'bin/**/*.c' 'usr.bin/**/*.c' -I include

Exit status is the number of files ShivyCX failed to compile (0 = all passed).
"""

import argparse
import glob as globmod
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ENV = dict(os.environ, PYTHONPATH=ROOT +
            (os.pathsep + os.environ["PYTHONPATH"]
             if os.environ.get("PYTHONPATH") else ""))


def resolve_dirs(dirs, base):
    """Resolve include dirs relative to the codebase root unless absolute."""
    out = []
    for d in dirs:
        out.append(d if os.path.isabs(d) else os.path.join(base, d))
    return out


def collect(src_dir, patterns):
    files = []
    for pat in patterns:
        files.extend(globmod.glob(os.path.join(src_dir, pat), recursive=True))
    return sorted(set(f for f in files if f.endswith(".c")))


def _first_error(text):
    text = _ANSI.sub("", text)
    for line in text.split("\n"):
        if "error:" in line:
            return line.split("error:", 1)[1].strip()
    return text.strip().split("\n")[0] if text.strip() else "(unknown error)"


def gcc_preprocess(src, incs, defines, out_path):
    cmd = ["gcc", "-E", "-std=c99"]
    for d in incs:
        cmd += ["-I", d]
    for d in defines:
        cmd += ["-D", d]
    cmd += [src, "-o", out_path]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        first = proc.stderr.decode("utf-8", "replace").strip().split("\n")[0]
        return False, first
    return True, ""


def compile_shivyc(src, obj_path, incs, defines, use_musl):
    """Compile one file in a fresh ShivyCX subprocess.

    A subprocess (rather than calling shivyc.main in-process) is deliberate:
    ShivyCX keeps global state across compilations, so reusing one interpreter
    across files yields false results. The same interpreter running this script
    is reused (PyPy3 when invoked from the Makefile)."""
    cmd = [sys.executable, "-m", "shivyc.main", "-c", src, "-o", obj_path]
    for d in incs:
        cmd += ["-I", d]
    for d in defines:
        cmd += ["-D", d]
    if use_musl:
        cmd.append("--musl")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=_ENV)
    if proc.returncode == 0:
        return None
    return _first_error(proc.stdout.decode("utf-8", "replace"))


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("src_dir", help="root of the codebase checkout")
    p.add_argument("globs", nargs="+", help="source globs relative to SRC_DIR "
                   "(recursive ** supported)")
    p.add_argument("-I", dest="includes", action="append", default=[],
                   metavar="DIR", help="include dir (relative to SRC_DIR)")
    p.add_argument("-D", dest="defines", action="append", default=[],
                   metavar="NAME[=VAL]", help="preprocessor define")
    p.add_argument("--musl", action="store_true",
                   help="compile against the packaged musl libc")
    p.add_argument("--gcc-pp", action="store_true",
                   help="preprocess with gcc -E before compiling")
    p.add_argument("--max-kb", type=int, default=64,
                   help="skip .c files larger than this many KB (0 = no limit)")
    p.add_argument("--include-large", action="store_true",
                   help="do not skip large files")
    p.add_argument("--limit", type=int, default=0,
                   help="compile at most this many files (0 = all)")
    p.add_argument("-j", "--jobs", type=int, default=1,
                   help="compile this many files in parallel (0 = one per CPU)")
    p.add_argument("--quiet", action="store_true",
                   help="only print failures, skips, and the summary")
    p.add_argument("--list", action="store_true",
                   help="list selected files (and sizes) without compiling")
    opts = p.parse_args()

    src_dir = os.path.abspath(opts.src_dir)
    if not os.path.isdir(src_dir):
        print("codebase not found at %s -- run the matching 'make install_*'"
              % src_dir)
        return 2

    incs = resolve_dirs(opts.includes, src_dir)
    files = collect(src_dir, opts.globs)
    if not files:
        print("no .c files matched %s in %s" % (opts.globs, src_dir))
        return 2

    limit = float("inf") if opts.max_kb <= 0 or opts.include_large \
        else opts.max_kb * 1024

    if opts.list:
        for f in files:
            kb = os.path.getsize(f) / 1024.0
            mark = "skip" if os.path.getsize(f) > limit else "    "
            print("%s %6.1fKB  %s" % (mark, kb, os.path.relpath(f, src_dir)))
        return 0

    # Partition into work (to compile) and skipped (too large), honoring --limit.
    workdir = tempfile.mkdtemp(prefix="bigtest_")
    work = []
    skipped = 0
    for src in files:
        if os.path.getsize(src) > limit:
            skipped += 1
            if not opts.quiet:
                print("skip %s (%.0fKB > %dKB)"
                      % (os.path.relpath(src, src_dir),
                         os.path.getsize(src) / 1024.0, opts.max_kb))
            continue
        if opts.limit and len(work) >= opts.limit:
            break
        work.append(src)

    def process(src):
        """Compile one file; return (name, error-or-None)."""
        name = os.path.relpath(src, src_dir)
        base = os.path.splitext(os.path.basename(src))[0]
        # Object/temp names are made unique per source so parallel jobs and
        # same-named files in different dirs do not collide.
        uniq = base + "_" + str(abs(hash(src)) % 10000000)
        obj_path = os.path.join(workdir, uniq + ".o")
        to_compile, incs_used, pp_defines = src, incs, opts.defines
        if opts.gcc_pp:
            pp_path = os.path.join(workdir, uniq + ".pp.c")
            ok, perr = gcc_preprocess(src, incs, opts.defines, pp_path)
            if not ok:
                return name, "gcc -E: " + perr
            to_compile, incs_used, pp_defines = pp_path, [], []
        return name, compile_shivyc(to_compile, obj_path, incs_used,
                                    pp_defines, opts.musl)

    jobs = opts.jobs if opts.jobs > 0 else (os.cpu_count() or 1)
    passed = failed = 0
    failures = []
    print("== ShivyCX compile-check: %s [%s] (%d files%s) =="
          % (os.path.basename(src_dir), ", ".join(opts.globs), len(files),
             ", %d jobs" % jobs if jobs > 1 else ""))

    def record(name, err):
        nonlocal passed, failed
        if err is None:
            passed += 1
            if not opts.quiet:
                print("ok   %s" % name)
        else:
            failed += 1
            failures.append((name, err))
            print("FAIL %-30s %s" % (name, err))

    if jobs > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = [pool.submit(process, s) for s in work]
            for fut in as_completed(futs):
                record(*fut.result())
    else:
        for src in work:
            record(*process(src))

    summary = "-- %d passed, %d failed" % (passed, failed)
    if skipped:
        summary += ", %d skipped (>%dKB)" % (skipped, opts.max_kb)
    print(summary + " --")
    if failures and opts.quiet:
        for name, err in sorted(failures):
            print("   %-30s %s" % (name, err))
    return failed


if __name__ == "__main__":
    sys.exit(main())

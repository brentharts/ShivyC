#!/usr/bin/env python3
"""Compile-check micropython sources through ShivyCX.

For each selected C source it runs gcc's preprocessor (to expand the system and
generated micropython headers, which ShivyCX does not fully bundle) and then
compiles the preprocessed translation unit with ShivyCX, reporting whether
ShivyCX accepts it.

Sources are compiled in-process so that, under PyPy3, the JIT stays warm across
files -- much faster than spawning a fresh interpreter per file.

Usage:
    pypy3 tools/mpy_test.py CATEGORY [--mpy-dir DIR] [--quiet]

Categories: core, objects, modules, emitters, port, all (see CATEGORIES).
Exit status is the number of files ShivyCX failed to compile (0 = all passed).
"""

import argparse
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import shivyc.main  # noqa: E402
from shivyc.errors import error_collector  # noqa: E402

# Category -> list of globs, relative to the micropython repo root. `port` is
# the objcore port's own translation units; the rest carve up the `py/` core.
CATEGORIES = {
    "objects": ["py/obj*.c"],
    "modules": ["py/mod*.c"],
    "emitters": ["py/emit*.c"],
    "port": ["ports/objcore/main.c", "ports/objcore/hal.c"],
    # Everything in py/ that is not an obj*/mod*/emit* file: the VM, parser,
    # lexer, gc, runtime, qstr machinery, etc.
    "core": ["py/*.c"],
}
_CORE_EXCLUDE = ("obj", "mod", "emit")


class _Args:
    """Minimal stand-in for parsed CLI arguments (see shivyc/main.py)."""

    show_reg_alloc_perf = False
    variables_on_stack = False
    simd_pack_globals = False
    stackless_calls = False
    metamorphic = False
    opt_level = 0
    compile_only = True
    include_dirs = []
    defines = []

    def __init__(self, files, output_name):
        self.files = files
        self.output_name = output_name


def select_files(category, mpy_dir):
    """Return the sorted list of source files for the given category."""
    if category == "all":
        cats = ["core", "objects", "modules", "port"]
    else:
        cats = [category]

    files = []
    for cat in cats:
        for pattern in CATEGORIES[cat]:
            for path in glob.glob(os.path.join(mpy_dir, pattern)):
                base = os.path.basename(path)
                if cat == "core" and base.startswith(_CORE_EXCLUDE):
                    continue
                files.append(path)
    return sorted(set(files))


def preprocess(src, mpy_dir, out_path):
    """Run gcc -E on `src`; return (ok, error_text)."""
    port = os.path.join(mpy_dir, "ports", "objcore")
    cmd = [
        "gcc", "-E", "-std=c99", "-DNDEBUG",
        "-I", port, "-I", mpy_dir, "-I", os.path.join(port, "build"),
        src, "-o", out_path,
    ]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return False, proc.stderr.decode("utf-8", "replace").strip().split("\n")[0]
    return True, ""


def compile_shivyc(pp_path, obj_path):
    """Compile a preprocessed TU with ShivyCX in-process; return error or None."""
    error_collector.clear()
    error_collector.show = lambda: True  # suppress console printing
    args = _Args([pp_path], [obj_path])
    shivyc.main.get_arguments = lambda: args
    try:
        shivyc.main.main()
    except SystemExit:
        pass
    except Exception as e:  # pragma: no cover - defensive
        return "shivyc crashed: %s" % e
    for issue in error_collector.issues:
        if not issue.warning:
            return issue.descrip
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("category", choices=sorted(CATEGORIES) + ["all"])
    parser.add_argument("--mpy-dir", default=os.path.join(ROOT, "micropython"),
                        help="path to the micropython checkout")
    parser.add_argument("--quiet", action="store_true",
                        help="only print failures and the summary")
    opts = parser.parse_args()

    mpy_dir = os.path.abspath(opts.mpy_dir)
    if not os.path.isdir(mpy_dir):
        print("micropython not found at %s -- run 'make install_micropython'"
              % mpy_dir)
        return 2

    files = select_files(opts.category, mpy_dir)
    if not files:
        print("no source files matched category '%s' in %s"
              % (opts.category, mpy_dir))
        return 2

    workdir = tempfile.mkdtemp(prefix="mpy_test_")
    passed = failed = 0
    failures = []
    print("== ShivyCX micropython compile-check: %s (%d files) =="
          % (opts.category, len(files)))
    for src in files:
        name = os.path.relpath(src, mpy_dir)
        base = os.path.splitext(os.path.basename(src))[0]
        pp_path = os.path.join(workdir, base + ".pp.c")
        obj_path = os.path.join(workdir, base + ".o")

        ok, pp_err = preprocess(src, mpy_dir, pp_path)
        if not ok:
            failed += 1
            failures.append((name, "gcc -E: " + pp_err))
            print("FAIL %-32s gcc -E: %s" % (name, pp_err))
            continue

        err = compile_shivyc(pp_path, obj_path)
        if err is None:
            passed += 1
            if not opts.quiet:
                print("ok   %s" % name)
        else:
            failed += 1
            failures.append((name, err))
            print("FAIL %-32s %s" % (name, err))

    print("-- %d passed, %d failed (of %d) --" % (passed, failed, len(files)))
    if failures and opts.quiet:
        for name, err in failures:
            print("   %-32s %s" % (name, err))
    return failed


if __name__ == "__main__":
    sys.exit(main())

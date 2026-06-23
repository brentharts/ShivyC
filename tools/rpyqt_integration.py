"""rpyqt -- py2c-side integration for the PyQt-compatible widget layer.

The rpython library lives in `rpy_lib/rpyqt.py`. It is self-contained: it binds
the generated Wayland runtime (`rwl_run`) and defines the five `rw_*` hooks the
runtime calls, without importing the rwayland rpython module. So when a source
imports `rpyqt`, py2c

  1. bundles `rpy_lib/rpyqt.py` into the translation unit, and
  2. emits the same app-agnostic Wayland runtime that rwayland uses
     (`rwayland_rt.{h,c}` + scanned xdg-shell), reusing tools/rwayland.py.

The end user writes only PyQt-shaped rpython; every line of C -- widgets, font,
event loop, Wayland glue -- is generated. Link with `-lwayland-client`.
"""

import os
import sys
import ast

import rwayland_integration as _rwayland   # reuse the runtime emitter

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "rpyqt.py")


def imports_rpyqt(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name == "rpyqt" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "rpyqt":
            return True
    return False


def needed(files):
    return any(imports_rpyqt(f) for f in files if f.endswith(".py"))


def bundle(files):
    """Append the bundled rpy_lib/rpyqt.py iff some input imports it."""
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        if needed(files):
            sys.stderr.write(
                "py2c: a source imports `rpyqt` but the bundled library %s is "
                "missing; the generated C will not compile. Restore "
                "tools/rpy_lib/rpyqt.py.\n" % LIB_FILE)
        return files
    if any(os.path.basename(f) == "rpyqt.py" for f in files):
        return files
    if needed(files):
        files.append(LIB_FILE)
    return files


def emit_runtime(out_dir, files):
    """Emit the Wayland runtime when an input imports rpyqt."""
    if not needed(files):
        return []
    notes = _rwayland.write_runtime(out_dir, files)
    return ["(via rpyqt) " + n for n in notes]

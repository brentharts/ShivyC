"""rpy_plot -- py2c-side integration for the bundled matplotlib-style plotter.

The rpython library lives in `rpy_lib/rpy_plot.py` (a POD-class SVG line-chart
renderer ported from matplotlib-micropython). This module lets py2c and ShivyCX
auto-bundle it: when a source does `import rpy_plot` / `from rpy_plot import
...`, the library file is appended to the translation unit so it is co-compiled
and linked. Mirrors tools/rpy_torch.py exactly -- no heavyweight dependency, just
one more rpython source compiled alongside the user's program.
"""

import os
import ast

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "rpy_plot.py")


def imports_plot(path):
    """True if the rpython source at `path` imports the rpy_plot module."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "rpy_plot" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "rpy_plot":
            return True
    return False


def bundle(files):
    """Return `files`, with the bundled rpy_plot library appended iff some
    input imports it and it isn't already present. Idempotent and best-effort."""
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        return files
    if any(os.path.basename(f) == "rpy_plot.py" for f in files):
        return files
    if any(imports_plot(f) for f in files):
        files.append(LIB_FILE)
    return files

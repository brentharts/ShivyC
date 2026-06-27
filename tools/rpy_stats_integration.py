"""rpy_stats -- py2c-side integration for the bundled statistics mini-library.

The rpython library lives in `rpy_lib/rpy_stats.py` (pure scalar-list numeric
functions ported from the stdlib `statistics`). This module lets py2c and
ShivyCX auto-bundle it on `import rpy_stats` / `from rpy_stats import ...`, the
same mechanism as tools/rpy_torch.py and tools/rpy_plot_integration.py.
"""

import os
import ast

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "rpy_stats.py")


def imports_stats(path):
    """True if the rpython source at `path` imports the rpy_stats module."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "rpy_stats" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "rpy_stats":
            return True
    return False


def bundle(files):
    """Return `files`, with the bundled rpy_stats library appended iff some
    input imports it and it isn't already present. Idempotent and best-effort."""
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        return files
    if any(os.path.basename(f) == "rpy_stats.py" for f in files):
        return files
    if any(imports_stats(f) for f in files):
        files.append(LIB_FILE)
    return files

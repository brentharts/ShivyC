"""rpy_torch -- py2c-side integration for the bundled mini-PyTorch library.

The actual rpython library lives in `rpy_lib/rpy_torch.py` (POD layers + fused
numpy kernels). This module lets py2c and ShivyCX auto-bundle it: when a source
does `import rpy_torch` / `from rpy_torch import ...`, the library file is added
to the translation unit so it is co-compiled and linked, and its directory is
registered for module resolution. No heavyweight dependency is pulled in -- it is
just one more rpython source compiled alongside the user's program.
"""

import os
import ast

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "rpy_torch.py")


def imports_torch(path):
    """True if the rpython source at `path` imports the rpy_torch module."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "rpy_torch" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "rpy_torch":
            return True
    return False


def bundle(files):
    """Return `files`, with the bundled rpy_torch library appended iff some
    input imports it and it isn't already present. Idempotent and best-effort."""
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        return files
    if any(os.path.basename(f) == "rpy_torch.py" for f in files):
        return files
    if any(imports_torch(f) for f in files):
        files.append(LIB_FILE)
    return files

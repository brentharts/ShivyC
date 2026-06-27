"""rpy_bisect -- py2c-side integration for the bundled bisect mini-library.

Auto-bundles `rpy_lib/rpy_bisect.py` into the translation unit on
`import rpy_bisect` / `from rpy_bisect import ...`, the same mechanism as
tools/rpy_torch.py and the other rpy_lib shims.
"""

import os
import ast

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "rpy_bisect.py")


def imports_bisect(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "rpy_bisect" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "rpy_bisect":
            return True
    return False


def bundle(files):
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        return files
    if any(os.path.basename(f) == "rpy_bisect.py" for f in files):
        return files
    if any(imports_bisect(f) for f in files):
        files.append(LIB_FILE)
    return files

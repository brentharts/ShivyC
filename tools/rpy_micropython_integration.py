"""rpy micropython -- py2c-side integration for the first-class `micropython`
library. Auto-bundles `rpy_lib/micropython.py` into the translation unit on
`import micropython` / `from micropython import ...`, the same mechanism as
tools/rpy_hashlib_integration.py and the other rpy_lib shims.
"""
import os
import ast
LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpy_lib")
LIB_FILE = os.path.join(LIB_DIR, "micropython.py")


def imports_micropython(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "micropython" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.module == "micropython":
            return True
    return False


def bundle(files):
    files = list(files)
    if not os.path.isfile(LIB_FILE):
        return files
    if any(os.path.basename(f) == "micropython.py" for f in files):
        return files
    if any(imports_micropython(f) for f in files):
        files.append(LIB_FILE)
    return files

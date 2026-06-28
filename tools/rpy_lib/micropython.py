"""First-class MicroPython access from rpython (ShivyCX).

Under CPython these wrap the Python builtins, so a program that uses them also
runs untranslated. Under py2c they lower to the MicroPython bridge (rpy_exec /
rpy_eval), which links the MicroPython core -- giving the *compiled* program a
real Python interpreter at run time. This is the foundation for `mrpy`, a
MicroPython front end that runs `.py` scripts the way `python3 script.py` does.
"""


def exec_(src: "str") -> None:
    """Run a block of Python statements (the builtin exec)."""
    exec(src)


def run_source(src: "str") -> None:
    """Run a whole script's source through the interpreter."""
    exec(src)


def run_file(path: "str") -> None:
    """Read a .py file from disk and run it, like `python3 path`.

    The file is read with ordinary file I/O (compiled to C), so no MicroPython
    filesystem is required; only the source string crosses into the interpreter.
    """
    src = open(path, encoding="utf-8").read()
    exec(src)


def eval_int(src: "str") -> "int":
    """Evaluate a Python expression to an int (no boxing)."""
    v: int = eval(src)
    return v


def eval_float(src: "str") -> "float":
    v: float = eval(src)
    return v


def eval_str(src: "str") -> "str":
    v: str = eval(src)
    return v


def eval_bool(src: "str") -> "bool":
    v: bool = eval(src)
    return v

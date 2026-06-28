# `mrpy` — a MicroPython front end built through ShivyCX
- https://github.com/OpenSourceJesus/micropython

`mrpy script.py` runs a Python script the way `python3 script.py` does, but the
front end itself is written in rpython, compiled to C by py2c, and linked against
the MicroPython core. It reads the script with ordinary (compiled-to-C) file I/O
and hands the source to the embedded interpreter via the first-class
`micropython` library — so no MicroPython filesystem is required.

```python
import sys
import micropython

def main() -> "int":
    if len(sys.argv) < 2:
        print("usage: mrpy <script.py>")
        return 1
    micropython.run_file(sys.argv[1])   # read in C, exec on MicroPython
    return 0
```

`run_file` lowers to a `fopen`/`fread` loop plus `rpy_exec(src)`, which parses the
source as a module (`MP_PARSE_FILE_INPUT`), compiles it, and runs it. Uncaught
exceptions print a CPython-style traceback.

## Building

```sh
EMB=<micropython>/examples/embedding/micropython_embed   # built once via embed.mk
EX=<micropython>/examples/embedding
python3 tools/py2c.py examples/rpython2c/mrpy/mrpy.py --out build   # bundles micropython.py
echo '#include "py/mpconfig.h"' > build/mpconfigstdlib.h
cd build
INC="-I. -I$EX -I$EMB -I$EMB/port -fno-common"
gcc $INC -c mrpy.c micropython.c mp_stdlib_bridge.c shivyc_rt.c
gcc *.o $(find $EMB -name '*.o') -lm -o mrpy
./mrpy somescript.py
```

## Validated

`./mrpy script.py` produces **byte-identical** output to `python3 script.py` for a
script using classes/methods, `%`-formatting, list comprehensions, `range`,
`dict.values()`, `sum`, `try/except`, and the `if __name__ == "__main__":` guard.
(`%`-formatting needs `MICROPY_PY_BUILTINS_STR_OP_MODULO` enabled in the
MicroPython config.)

## Running py2c.py itself

The headline goal is `mrpy py2c.py` — bootstrapping the compiler's front end on
MicroPython. py2c.py imports `ast`, `os`, `re`, `sys`, and `pathlib` and uses the
full CPython-3 grammar, so it needs a MicroPython build that ships those modules
and a filesystem (the OpenSourceJesus fork's full build, where this has been
tested). The **minimal embed** amalgamation used for the demos above omits those
modules, so it can run ordinary scripts but not py2c.py. The front end and the
`exec`/file-read mechanism are identical in both builds; only the linked module
set differs.

## Foundation for AOT / JIT

With MicroPython first-class, the same `exec`/import path is where ShivyCX can
hook ahead-of-time compilation (transpile a module to C at import) and a JIT
(compile hot functions through the backend), instead of always interpreting.

## Limitations
* The script's own `sys.argv` is not yet forwarded into the interpreter (forwarding
  needs the `sys` module enabled); scripts that read `sys.argv` see only what the
  MicroPython build sets up.
* Dynamic `import` of `.py` files from disk depends on the MicroPython build's
  import/VFS support; `run_file` (read-in-C + exec) avoids that dependency.

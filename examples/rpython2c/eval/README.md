# Typed `eval` via embedded MicroPython

rpython is statically compiled, but `eval("...")` needs a real Python parser and
evaluator at run time. ShivyCX provides this by **linking the program against the
MicroPython core** and calling its embedding API. The bridge parses the string as
an expression (`MP_PARSE_EVAL_INPUT`), compiles it, runs it, and returns the
result.

## Typed vs untyped

```python
x        = eval("6 * 7")   # -> obj  (boxed; rpy_eval)
answer: int   = eval("6 * 7")   # -> long   (rpy_eval_int)
ratio:  float = eval("22/7")    # -> double (rpy_eval_float)
name:   str   = eval("'a'+'b'") # -> char*  (rpy_eval_str)
ok:     bool  = eval("1 < 2")   # -> bool   (rpy_eval_bool)
```

With an annotation the translator emits a typed extractor that reads the value
directly out of the MicroPython result object — no `obj` box, no tag dispatch —
so eval results flow straight into tight C scalars. Untyped `eval` boxes the
result into the universal rpython `obj`.

## Building (linking MicroPython)

`eval` makes py2c emit the MicroPython bridge (`mp_stdlib_bridge.c`) automatically.
Link the generated C against a MicroPython build — e.g. the **embed** port:

```sh
# 1. one-time: generate the MicroPython embed amalgamation
cd <micropython>/examples/embedding && make -f micropython_embed.mk
EMB=<micropython>/examples/embedding/micropython_embed
EX=<micropython>/examples/embedding         # provides mpconfigport.h

# 2. transpile the rpython program (auto-emits the bridge)
python3 tools/py2c.py examples/rpython2c/eval/eval_demo.py --out build
echo '#include "py/mpconfig.h"' > build/mpconfigstdlib.h

# 3. compile + link generated C + bridge + MicroPython core (+ -lm for floats)
cd build
INC="-I. -I$EX -I$EMB -I$EMB/port -fno-common"
gcc $INC -c eval_demo.c shivyc_rt.c mp_stdlib_bridge.c
gcc *.o $(find $EMB -name '*.o') -lm -o eval_demo
./eval_demo        # -> 42 / 3.142857 / hello from eval / True
```

(Float eval needs `MICROPY_PY_BUILTINS_FLOAT` enabled in the MicroPython config;
the full stdlib build of the OpenSourceJesus/micropython fork enables it.)

## Limitations
* Typed extraction covers scalars: `int`, `float`, `str`, `bool`. Untyped `eval`
  of a **container** (list/dict) currently wraps the raw MicroPython object as an
  opaque `obj` — fine to pass back into the bridge, but not yet traversable as a
  native rpython list. Scalar eval is the supported, validated path.
* The evaluated expression runs in a fresh MicroPython context; it cannot see the
  rpython program's local variables.

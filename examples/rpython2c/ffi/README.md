# rpython FFI — a transpilable `ctypes` subset

`rpy_ctypes.py` (in `tools/rpy_lib/`) is the FFI bridge for the rpython dialect.
It lets a program call into a shared library through a small, familiar `ctypes`
surface, and py2c resolves every dynamic lookup to a **direct C call** at
transpile time. Nothing is `dlopen`'d at runtime — the lookup becomes a static
symbol that the linker resolves.

## What's supported

```python
import ctypes
lib = ctypes.CDLL("libm.so.6")      # records a link target (compile-time)
lib.pow.restype  = ctypes.c_double  # the extern's C return type
lib.pow.argtypes = [ctypes.c_double, ctypes.c_double]
r = lib.pow(2.0, 10.0)              # -> `pow(2.0, 10.0)` in C
f = lib.sqrt                        # bind the lookup to a local
s = f(r)                            # -> `sqrt(...)` in C
```

py2c tracks the `CDLL` handle and each `lib.symbol` attribute as a compile-time
constant, then:

- emits a real prototype, e.g. `extern double pow(double, double);`,
- lowers `lib.symbol(args)` (and a bound `f = lib.symbol; f(args)`) to a direct
  call `symbol(args)`, coercing each argument to its declared `argtypes`,
- drops the `CDLL` / `restype` / `argtypes` statements (they produce no code),
- and relies on the linker for the symbol (ShivyCX links `-lc -lm`; other shared
  objects link by name).

Type markers map to C as you'd expect: `c_int -> int`, `c_double -> double`,
`c_float -> float`, `c_char_p -> char*`, `c_void_p -> void*`,
`c_ubyte -> unsigned char`, `c_long -> long`, and so on.

## One source, two implementations

The same file runs under CPython, where `rpy_ctypes` delegates to the genuine,
dynamically-loading `ctypes`. That makes the transpiled FFI directly
cross-validatable against the real thing:

```python
import sys
if sys.implementation.name == 'shivyc':
    import rpy_ctypes as ctypes
else:
    import ctypes
```

## `ffi_math.py`

Calls `pow`, `sqrt`, and `cbrt` from libm and returns `35`. It produces the
identical result three ways — `make testtorch` checks the first two:

```
python3 -m shivyc.main --no-cache ffi_math.py -o /tmp/ffi && /tmp/ffi; echo $?  # 35 (ShivyCX)
# gcc path via tools/py2c.py + gcc -lm                                          # 35
python3 ffi_math.py; echo $?                                                     # 35 (real ctypes)
```

## Scope

This is a deliberately minimal subset: `CDLL`, the scalar type markers,
per-function `restype` / `argtypes`, direct calls, and `f = lib.symbol`
bindings. Struct/array argument marshalling, callbacks, `byref`/pointer
out-parameters, and `errno` handling are out of scope for now — the goal is to
turn the common "load a library and call its functions" pattern into clean,
statically-linked C.

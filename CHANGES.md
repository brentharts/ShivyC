# rpython FFI / ctypes bridge — change summary

Builds on `b224c42 "rpy 8bit quantized neural networks"`. Five files (three new).
**`tools/rpy_lib/rpy_ctypes.py` and the `examples/rpython2c/ffi/` directory are
new — they need `git add`.** Only the transpiler (`tools/py2c.py`) changed this
turn; the live ShivyCX compiler (`shivyc/*.py`) was untouched.

## What it does

A small, transpilable `ctypes` subset turns the common "load a library and call
its functions" pattern into direct, statically-linked C — the dynamic lookup is
resolved at transpile time, nothing is `dlopen`'d at runtime:

    import ctypes
    libm = ctypes.CDLL("libm.so.6")
    libm.pow.restype  = ctypes.c_double
    libm.pow.argtypes = [ctypes.c_double, ctypes.c_double]
    r = libm.pow(2.0, 10.0)        # -> `pow(2.0, 10.0)` in C
    f = libm.sqrt                  # bind the lookup to a local
    s = f(r)                       # -> `sqrt(...)` in C

py2c tracks the `CDLL` handle and each `lib.symbol` attribute as a compile-time
constant, emits a real prototype (`extern double pow(double, double);`), lowers
the calls to direct C calls (coercing args to the declared `argtypes`), drops
the `CDLL`/`restype`/`argtypes` statements (no code), and leaves the symbol to
the linker (ShivyCX links `-lc -lm`).

## Files

* **`tools/rpy_lib/rpy_ctypes.py`** (new): the ctypes subset — scalar type
  markers (`c_int`, `c_double`, `c_char_p`, ...) and `CDLL`. Under CPython it
  delegates to the real `ctypes`, so the same source cross-validates against the
  genuine dynamic-loading implementation.
* **`tools/py2c.py`**:
  - `_CTYPES_TYPEMAP` (ctypes marker -> C type).
  - `_scan_ctypes(tree)` (called from `run` after `collect_imports`): tracks
    `CDLL` handles, `lib.symbol` bindings, `restype`/`argtypes`, the symbols
    actually called, and the statement-ids that emit no C.
  - `ctypes_call_symbol` / `_emit_ctypes_call`: lower a tracked call to
    `symbol(args)`.
  - `ctypes_externs` + an emit hook in `emit_forward_decls` for the prototypes.
  - `value_ctype` returns a call's `restype`.
  - Config statements are skipped in `stmt`, `toplevel`, and
    `collect_module_globals`; bound FFI names are excluded from local hoisting
    (so no shadowing `obj` is declared).
  - Every hook is a no-op when no ctypes import is present.
* **`examples/rpython2c/ffi/ffi_math.py`** (new): libm `pow`/`sqrt`/`cbrt` via
  the subset; returns `35`.
* **`examples/rpython2c/ffi/README.md`** (new).
* **`Makefile`**: `ffi_math.py` added to the `rpython` and `testtorch` targets.

## Verification (no regressions)

* `ffi_math.py` returns **35** under all three: ShivyCX, gcc (`-lm`), and CPython
  (real ctypes) — same source.
* unit tests `FAILED (errors=29)` — unchanged
* `selfhost test` -> 3 OK — unchanged
* `make rpython` all pass (incl. ffi_math=35; simd_kernels=55, torch_mlp=4,
  torch_mlp_f32=4, quant_mlp=50, fusion=97, neural_net=199, ...)
* `make testtorch / testfast / testpromote / testpgo / testfuse` -> PASS
  (testtorch checks ffi_math on gcc **and** ShivyCX)
* gcc coverage 45/60 — unchanged

## Scope

Minimal by design: `CDLL`, scalar type markers, per-function
`restype`/`argtypes`, direct calls, and `f = lib.symbol` bindings. Struct/array
marshalling, callbacks, `byref`/pointer out-params, and `errno` are future work.

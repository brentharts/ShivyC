"""FFI to a shared library through the rpython `ctypes` subset.

`ctypes.CDLL` and each `lib.symbol` lookup are resolved at transpile time: a
`lib.symbol(args)` call lowers to a *direct* C call `symbol(args)` with a real
`extern` prototype, and the named library is linked (ShivyCX links `-lc -lm`).
Nothing is dlopen'd at runtime -- the dynamic lookup becomes a static symbol.

The import switch keeps the same source runnable under CPython (where it uses
the genuine, dynamically-loading ctypes), so the FFI lowering can be
cross-validated against the real thing:

    import sys
    if sys.implementation.name == 'shivyc':
        import rpy_ctypes as ctypes
    else:
        import ctypes

Run:
    python3 -m shivyc.main --no-cache ffi_math.py -o /tmp/ffi && /tmp/ffi
    echo $?      # 35
    python3 ffi_math.py; echo $?    # 35  (same source, real ctypes)
"""
import sys
if sys.implementation.name == 'shivyc':
    import rpy_ctypes as ctypes
else:
    import ctypes

libm = ctypes.CDLL("libm.so.6")

# Declaring restype/argtypes gives py2c the exact C prototype to emit.
libm.pow.restype = ctypes.c_double
libm.pow.argtypes = [ctypes.c_double, ctypes.c_double]
libm.sqrt.restype = ctypes.c_double
libm.sqrt.argtypes = [ctypes.c_double]
libm.cbrt.restype = ctypes.c_double
libm.cbrt.argtypes = [ctypes.c_double]


def main() -> int:
    p = libm.pow(2.0, 10.0)              # 2^10            = 1024.0
    s = libm.sqrt(p)                     # sqrt(1024)      = 32.0
    root = libm.cbrt                     # bind the lookup to a local
    c = root(27.0)                       # cbrt(27)        = 3.0
    return int(s) + int(c)               # 35


if __name__ == "__main__":
    sys.exit(main())

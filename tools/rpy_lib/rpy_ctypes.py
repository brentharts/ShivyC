"""A minimal `ctypes` subset that py2c.py transpiles to direct C extern calls.

This is the FFI bridge for the rpython dialect. Under ShivyCX the ctypes calls
are resolved entirely at *transpile time* -- nothing is loaded dynamically at
runtime:

    import ctypes
    libm = ctypes.CDLL("libm.so.6")          # records a link target
    libm.pow.restype  = ctypes.c_double      # the extern's C return type
    libm.pow.argtypes = [ctypes.c_double,    # the extern's C parameter types
                         ctypes.c_double]
    r = libm.pow(2.0, 10.0)                   # -> `pow(2.0, 10.0)` in C

py2c tracks the `CDLL` handle and each `lib.symbol` attribute as a compile-time
constant, emits a real prototype `extern double pow(double, double);`, and
lowers the call to a direct C call. The named library's symbols are resolved by
the linker (ShivyCX links `-lc -lm`; other shared objects are linked by name).
A `func = lib.symbol` binding is tracked too, so `func(...)` calls the symbol.

The same source runs under CPython, where this module simply delegates to the
real `ctypes`, so an rpython FFI example can be cross-validated against the
genuine dynamic-loading implementation:

    import sys
    if sys.implementation.name == 'shivyc':
        import rpy_ctypes as ctypes
    else:
        import ctypes

Only the documented subset is supported by the transpiler: `CDLL`, the scalar
type markers below, per-function `restype` / `argtypes`, and direct calls.
"""

try:                                    # pragma: no cover - host CPython path
    import ctypes as _ct

    c_void = None
    c_bool = _ct.c_bool
    c_char = _ct.c_char
    c_byte = _ct.c_byte
    c_ubyte = _ct.c_ubyte
    c_short = _ct.c_short
    c_ushort = _ct.c_ushort
    c_int = _ct.c_int
    c_uint = _ct.c_uint
    c_long = _ct.c_long
    c_ulong = _ct.c_ulong
    c_float = _ct.c_float
    c_double = _ct.c_double
    c_char_p = _ct.c_char_p
    c_void_p = _ct.c_void_p
    CDLL = _ct.CDLL
except ImportError:                     # pragma: no cover
    # Pure marker fallback (real ctypes always ships with CPython, so this is
    # only a safety net). py2c never executes this module; it reads the names.
    c_void = c_bool = c_char = c_byte = c_ubyte = c_short = c_ushort = None
    c_int = c_uint = c_long = c_ulong = c_float = c_double = None
    c_char_p = c_void_p = None

    class CDLL(object):
        def __init__(self, name):
            self.name = name

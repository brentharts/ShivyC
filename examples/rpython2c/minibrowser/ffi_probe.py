"""Native runtime-FFI proof: transpiled RPython dlopens a JIT'd .so and calls a
symbol at run time via the mb_ffi shim -- the mechanism the embedded interpreter
will use to run a page's <script type="rpython"> from minipy. Built + run by
ffi_test.py; also CPython-cross-checkable via rpy_ctypes -> real ctypes."""
import sys
if sys.implementation.name == 'shivyc':
    import rpy_ctypes as ctypes
else:
    import ctypes

_ffi = ctypes.CDLL("mb_ffi")
_ffi.mb_dlopen.restype = ctypes.c_long
_ffi.mb_dlopen.argtypes = [ctypes.c_char_p]
_ffi.mb_dlsym.restype = ctypes.c_long
_ffi.mb_dlsym.argtypes = [ctypes.c_long, ctypes.c_char_p]
_ffi.mb_call2i.restype = ctypes.c_int
_ffi.mb_call2i.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_int]


def main() -> int:
    h = _ffi.mb_dlopen("/tmp/mb_ffi_test/jit.foo.so")
    if h == 0:
        print("dlopen failed")
        return 1
    fn = _ffi.mb_dlsym(h, "calc_sum")
    if fn == 0:
        print("dlsym failed")
        return 1
    r = _ffi.mb_call2i(fn, 1, 2)
    if r == 3:
        print("OK native runtime FFI: calc_sum(1,2)=3")
        return 0
    print("FAIL native runtime FFI")
    return 1

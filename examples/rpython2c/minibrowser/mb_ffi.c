/* mb_ffi -- runtime-FFI shim for the embedded interpreter's ctypes.

   The minibrowser JIT-compiles a page's <script type="rpython"> blocks to native
   .so files (see jitc.py). To call into one at run time the interpreter needs to
   dlopen it and call a symbol resolved to a pointer at run time -- something the
   ctypes *static* bridge (compile-time symbol, link-time resolution) can't do.

   These few C functions are linked into the browser binary, so transpiled
   RPython reaches them through the static bridge (they are known symbols), while
   the target .so is loaded dynamically here. mb_callNi call a symbol pointer as
   int(int,...) -- matching JIT'd `int f(int, ...)` blocks. Link with -ldl. */
#include <dlfcn.h>

long mb_dlopen(const char *path) {
    return (long)dlopen(path, RTLD_NOW | RTLD_LOCAL);
}
long mb_dlsym(long handle, const char *name) {
    return (long)dlsym((void *)handle, name);
}
int mb_call0i(long fn) {
    return ((int (*)(void))fn)();
}
int mb_call1i(long fn, int a) {
    return ((int (*)(int))fn)(a);
}
int mb_call2i(long fn, int a, int b) {
    return ((int (*)(int, int))fn)(a, b);
}
int mb_call3i(long fn, int a, int b, int c) {
    return ((int (*)(int, int, int))fn)(a, b, c);
}
int mb_call5i(long fn, int a, int b, int c, int d, int e) {
    return ((int (*)(int, int, int, int, int))fn)(a, b, c, d, e);
}

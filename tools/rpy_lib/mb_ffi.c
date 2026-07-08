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
#include <stdlib.h>
#include <stdio.h>

/* Load a blit-ready image cache file (written by mb_imgcache.py) into a native
   buffer the QImage widget blits into the window framebuffer. Returned buffer
   layout is [u32 w][u32 h][w*h u32 pixels], so one call yields both the size
   (buf[0], buf[1]) and the pixels (buf+2). Returns 0 on any failure -- the
   widget then falls back to an "[img]" placeholder. The pixel format matches
   the framebuffer (little-endian 0xAARRGGBB), so no conversion happens here. */
long mb_image_load(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    unsigned hdr[3];
    if (fread(hdr, 4, 3, f) != 3 || hdr[0] != 0x494D4731u) {  /* 'IMG1' */
        fclose(f);
        return 0;
    }
    unsigned w = hdr[1], h = hdr[2];
    /* Guard against absurd sizes so a corrupt header can't request a huge or
       overflowing allocation. */
    if (w == 0 || h == 0 || w > 20000u || h > 20000u) { fclose(f); return 0; }
    size_t n = (size_t)w * (size_t)h;
    unsigned *buf = (unsigned *)malloc((n + 2) * sizeof(unsigned));
    if (!buf) { fclose(f); return 0; }
    buf[0] = w;
    buf[1] = h;
    if (fread(buf + 2, sizeof(unsigned), n, f) != n) {
        free(buf);
        fclose(f);
        return 0;
    }
    fclose(f);
    return (long)buf;
}
void mb_image_free(long buf) {
    free((void *)buf);
}

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

/* Whole-frame canvas shader: allocate a w*h ARGB buffer once, then fill it in a
   single native call per frame (no per-pixel FFI). mb_render_call invokes a
   JIT'd `int render(unsigned *buf, int w, int h, int t, int mx, int my)`. */
long mb_canvas_alloc(int n) {
    return (long)calloc((size_t)(n < 0 ? 0 : n), 4);
}
int mb_render_call(long fn, long buf, int w, int h, int t, int mx, int my) {
    return ((int (*)(unsigned *, int, int, int, int, int))fn)(
        (unsigned *)buf, w, h, t, mx, my);
}

/* Native page code -> DOM. A JIT'd module (loaded with the browser linked
   -rdynamic) calls these back into the embedded interpreter to mutate the
   page's document by element handle. mpy_call_is runs a page-level minipy entry
   (__set_text / __set_value) and is a symbol in the browser binary. */
extern int mpy_call_is(const char *name, int i, const char *s);
extern char *mpy_call_i_s(const char *name, int i);
extern int mpy_call_i(const char *name, int i);
extern int mpy_call_iss(const char *name, int i, const char *s1,
                        const char *s2);
int mb_dom_set_text(int handle, const char *text) {
    return mpy_call_is("__set_text", handle, text);
}
int mb_dom_set_value(int handle, const char *text) {
    return mpy_call_is("__set_value", handle, text);
}
const char *mb_dom_get_value(int handle) {
    return mpy_call_i_s("__get_value", handle);
}
const char *mb_dom_get_text(int handle) {
    return mpy_call_i_s("__get_text", handle);
}
/* Feature: native reads a numeric value, removes an element, or creates a
   labelled child element, by handle. */
int mb_dom_get_int(int handle) {
    return mpy_call_i("__get_int", handle);
}
int mb_dom_remove(int handle) {
    return mpy_call_i("__remove", handle);
}
int mb_dom_create_child(int parent, const char *tag, const char *text) {
    return mpy_call_iss("__create_child", parent, tag, text);
}

/* Pointer-aware calls so a page can hold a native object: value args are passed
   64-bit (pointers survive); the *l variants return int, the *p variants return
   a pointer (as long). Used when a page declares ctypes c_void_p arg/restypes
   for a native function that makes or takes a class instance. */
int  mb_call0l(long fn) { return ((int (*)(void))fn)(); }
int  mb_call1l(long fn, long a) { return ((int (*)(long))fn)(a); }
int  mb_call2l(long fn, long a, long b) {
    return ((int (*)(long, long))fn)(a, b);
}
int  mb_call3l(long fn, long a, long b, long c) {
    return ((int (*)(long, long, long))fn)(a, b, c);
}
long mb_call0p(long fn) { return (long)((void *(*)(void))fn)(); }
long mb_call1p(long fn, long a) { return (long)((void *(*)(long))fn)(a); }
long mb_call2p(long fn, long a, long b) {
    return (long)((void *(*)(long, long))fn)(a, b);
}
long mb_call3p(long fn, long a, long b, long c) {
    return (long)((void *(*)(long, long, long))fn)(a, b, c);
}

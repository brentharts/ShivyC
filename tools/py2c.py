#!/usr/bin/env python3
"""py2c.py -- a source-to-source transpiler from the ShivyCX compiler's Python
source into C, specialized to the ShivyCX code base (not general Python).

See the NAMING CONVENTIONS section below: where a type is not annotated and not
easily recovered, the transpiler guesses it from the variable's name. This is
sound here because ShivyCX evolves slowly and follows consistent conventions.

OBJECT MODEL (why this can be smaller/faster than a PyObject*-based approach)
---------------------------------------------------------------------------
The codebase has no __getattr__/__setattr__, no metaclasses, no eval/exec, no
runtime class creation, and a fixed per-class attribute set. So we do NOT need
CPython's dict-based attributes or slot dispatch. Instead:

  Tier 0  concrete C types from inference (int, char*, struct*) -- no `obj`.
  Tier 1  each class -> a real C struct whose first member is an `Obj` header
          carrying a `TypeInfo*` (name + base chain + vtable). This gives:
            * isinstance  -> walk the base chain (isinstance_of)
            * virtual call -> one indirect call through a vtable slot
            * attributes  -> compile-time struct offsets (no dict)
          Subclass structs repeat inherited fields *first*, so a common prefix
          lays out identically across a family (header at 0, then base fields),
          making base<->derived pointer casts valid.
  Tier 2  a tagged `obj` word, used only where a value is genuinely dynamic
          (e.g. MemSpot.base which the source documents as `str | Spot`).

Memory: the whole compile is one arena (aalloc); freed in one shot. No
refcounting, no GC -- a batch compiler can do this; CPython cannot.

The C runtime (shivyc_rt.h / shivyc_rt.c) is embedded in this file as raw
strings and written to the output directory at transpile time, so a generated
module compiles with just `cc module.c shivyc_rt.c`.


NAMING CONVENTIONS (the contract for ShivyCX contributors)
----------------------------------------------------------
  * int   : index, idx, i, j, k, n, n1, n2, count, size, offset, chunk, num,
            length, len, pos, position, line, lineno, col, column, start, end,
            depth, level, width, height, amount, total, addr, address, byte,
            bytes, bits, rbp_offset, spot_size; or a name ending in
            _size/_offset/_count/_index/_len/_num/_idx.
  * char* : name, text, s, string, msg, message, filename, fname, func_name,
            tag, rep, content, spelling, label, identifier, prog, code,
            asm_code, asm_str, text_repr, mangled, suffix, prefix; or a name
            ending in _str.
  * bool  : names starting is_/has_/can_/should_/was_/use_/allow_, or exactly
            defined/ok/found/done/wide/signed/unsigned/const/volatile/valid/
            empty/present/enabled/success.
  * self  : pointer to the enclosing class' struct.
  * A Capitalized annotation / 'ForwardRef' (e.g. Spot, 'Spot') -> `Spot*`.
  Everything else falls back to the generic `obj`. A `: type` annotation always
  overrides the name-based guess.

Usage:
    python3 py2c.py                 # transpile every ../shivyc/*.py -> /tmp/*.c
    python3 py2c.py spots.py        # transpile the given file(s) -> /tmp
    python3 py2c.py --out DIR ...   # choose a different output directory
    python3 py2c.py --conventions   # print the naming-convention rules
    python3 py2c.py --stdlib-dir DIR --out DIR
                                    # batch-transpile python-stdlib to C
"""
import ast
import os
import re
import sys
from pathlib import Path


# ==========================================================================
# python-stdlib module index (micropython-lib layout)
# ==========================================================================

_STDLIB_INDEX_CACHE: dict[str, dict[str, str]] = {}


def py_modname_from_path(path, stdlib_root):
    """Dotted Python module name for a file under python-stdlib, or None."""
    if not stdlib_root:
        return None
    ap = os.path.abspath(path)
    for mod, p in build_stdlib_index(stdlib_root).items():
        if os.path.abspath(p) == ap:
            return mod
    return None


def build_stdlib_index(stdlib_root):
    """Map dotted Python module names to .py source paths."""
    stdlib_root = os.fspath(stdlib_root)
    if stdlib_root in _STDLIB_INDEX_CACHE:
        return _STDLIB_INDEX_CACHE[stdlib_root]
    index = {}
    root = Path(stdlib_root)
    for pkg_dir in sorted(root.iterdir()):
        if not pkg_dir.is_dir():
            continue
        manifest = pkg_dir / "manifest.py"
        if not manifest.is_file():
            continue
        package = None
        module_files = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            m = re.match(r'package\("([^"]+)"\)', line)
            if m:
                package = m.group(1)
            m = re.match(r'module\("([^"]+)"\)', line)
            if m:
                module_files.append(m.group(1))
        if package:
            for py in pkg_dir.rglob("*.py"):
                if py.name == "manifest.py":
                    continue
                rel = py.relative_to(pkg_dir)
                if not rel.parts or rel.parts[0] != package:
                    continue
                if py.name == "__init__.py":
                    mod = ".".join(rel.parts[:-1])
                else:
                    mod = ".".join(rel.with_suffix("").parts)
                index[mod] = str(py)
        for mf in module_files:
            path = pkg_dir / mf
            if path.is_file():
                index[Path(mf).stem] = str(path)
    _STDLIB_INDEX_CACHE[stdlib_root] = index
    return index


MICROPYTHON_TOP = Path(__file__).resolve().parents[1]
DEFAULT_STDLIB = MICROPYTHON_TOP / "lib" / "micropython-lib" / "python-stdlib"


# ==========================================================================
# Embedded C runtime (written to the output dir at transpile time)
# ==========================================================================

RUNTIME_H = r'''#ifndef SHIVYC_RT_H
#define SHIVYC_RT_H
/* ShivyCX transpiler runtime -- generated by tools/py2c.py */
#include <stdbool.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

typedef char* str;

/* ---- Tier 2: generic dynamic value (used only where the type is open) ---- */
typedef struct Obj Obj;
enum { T_NONE, T_INT, T_BOOL, T_STR, T_OBJ, T_LIST, T_DICT, T_FUNC, T_FLOAT };
typedef struct { unsigned char tag; union { long i; str s; Obj* o; double d; } u; } obj;

#define OBJ_NONE    ((obj){T_NONE,{0}})
#define OBJ_INT(x)  ((obj){T_INT,{.i=(long)(x)}})
#define OBJ_BOOL(x) ((obj){T_BOOL,{.i=(long)(x)}})
#define OBJ_STR(x)  ((obj){T_STR,{.s=(str)(x)}})
#define OBJ_OBJ(x)  ((obj){T_OBJ,{.o=(Obj*)(x)}})
#define OBJ_FLOAT(x) ((obj){T_FLOAT,{.d=(double)(x)}})
#define IS_OBJ(v)   ((v).tag==T_OBJ)
#define IS_NONE(v)  ((v).tag==T_NONE)
#define IS_FLOAT(v) ((v).tag==T_FLOAT)
#define AS_OBJ(v)   ((v).u.o)
#define AS_INT(v)   ((v).u.i)
#define AS_STR(v)   ((v).u.s)
#define AS_FLOAT(v) ((v).u.d)

/* ---- Tier 1: object header -- first member of every transpiled struct ---- */
/* `type` points at a per-module TypeInfo; kept void* here so this header is
   module-agnostic. Each module casts it to its own TypeInfo. The base chain
   used by isinstance lives at a fixed offset, so isinstance_of works on any
   module's TypeInfo as long as {name; base;} are the first two members. */
struct Obj { const void* type; };

typedef struct TypeInfoHdr {
    const char* name;
    const struct TypeInfoHdr* base;
} TypeInfoHdr;

static inline bool isinstance_of(Obj* o, const void* t) {
    const TypeInfoHdr* want = (const TypeInfoHdr*)t;
    const TypeInfoHdr* k = o ? (const TypeInfoHdr*)o->type : NULL;
    for (; k; k = k->base) if (k == want) return true;
    return false;
}
/* isinstance on an obj-word: true only if it boxes a matching Obj* */
#define OBJ_ISINST(v, t) (IS_OBJ(v) && isinstance_of((v).u.o, (t)))

/* fetch a transpiled value's TypeInfo (cast to the module's TypeInfo type) */
#define TYPEINFO(T, o) ((const T*)((Obj*)(o))->type)

/* ---- first-class functions: a callable obj (T_FUNC) is a closure: a uniform
   function pointer plus a captured-environment obj. All transpiled functions
   used as values get a trampoline of this shape that unpacks the arg list. -- */
typedef obj (*ClosureFn)(obj env, obj args);   /* args is a T_LIST of arguments */
typedef struct Closure { Obj _hdr; ClosureFn fn; obj env; } Closure;
obj  make_closure(ClosureFn fn, obj env);
obj  call_closure(obj f, obj args);
obj  identity__tramp(obj env, obj args);
obj  call_obj(obj f, int n, ...);              /* convenience: builds arg list  */

/* ---- arena: bump-allocate; free the whole compile at once (no refcount) -- */
void* aalloc(size_t n);
void  afree(void* p, size_t n);   /* manual reclaim for Python `del` (see .c) */
void  arena_reset(void);

/* micropython-lib manifest.py packaging metadata (no runtime effect in C) */
void metadata(void);
void module(const char* name);
void require(const char* name);
void package(const char* name);

/* ---- small python-ish helpers ---- */
str  pystr(obj v);                 /* str(x)                                  */
str  pyrepr(obj v);                 /* repr() — quotes strings inside containers */
str  pyfmt(int n, const char* fmt, ...); /* f-strings: "{}..." + obj args     */
str  pyconcat(str a, str b);       /* "x" + y                                 */
bool truthy(obj v);                /* Python truthiness of a Tier-2 value     */

/* ---- lists: growable obj vector, represented as a tagged obj (T_LIST) ----- */
typedef struct { obj* data; int len; int cap; } List;
obj  list_new(void);
obj  list_of(int n, ...);          /* [a, b, c] literal                       */
obj  varg_list(int n, va_list ap); /* collect C varargs (obj) into a list     */
void list_append(obj lst, obj v);
void list_insert(obj lst, long i, obj v);
void list_extend(obj lst, obj it);      /* append all elements of it           */
obj  list_get(obj lst, long i);    /* supports negative indices               */
void list_set(obj lst, long i, obj v);
long pylen(obj v);                 /* len(list) or len(str)                   */
obj  index_obj(obj container, long i);  /* container[i] for list/str          */
bool obj_eq(obj a, obj b);         /* == on Tier-2 values                     */
bool pycontains(obj container, obj v);  /* v in container                     */
void pyprint(obj v);               /* print(x)                                */

/* ---- dicts: insertion-ordered obj->obj map, tagged obj (T_DICT) ----------- */
typedef struct { obj key; obj val; } DEnt;
typedef struct { DEnt* e; int len; int cap; } Dict;
obj  dict_new(void);
obj  dict_of(int n, ...);          /* {k1:v1, k2:v2, ...} (2n varargs)         */
void dict_set(obj d, obj k, obj v);
obj  dict_get(obj d, obj k, obj dflt);
bool dict_contains(obj d, obj k);
obj  dict_pop(obj d, obj k, obj dflt);
obj  dict_setdefault(obj d, obj k, obj dflt);
void dict_update(obj d, obj other);
obj  dict_keys(obj d);
obj  dict_values(obj d);
obj  dict_items(obj d);            /* list of [k, v] pairs                     */

obj  subscript(obj container, obj key);       /* container[key] (list/dict/str) */
void subscript_set(obj container, obj key, obj v);  /* container[key] = v       */

/* ---- arithmetic / comparison on Tier-2 values ---- */
obj  obj_add(obj a, obj b);        /* +  (int, str concat, list concat)       */
obj  obj_sub(obj a, obj b);
obj  obj_mul(obj a, obj b);        /* *  (int, str/list repeat)               */
obj  obj_fdiv(obj a, obj b);       /* // */
obj  obj_mod(obj a, obj b);
obj  obj_neg(obj a);               /* unary -  */
obj  obj_invert(obj a);            /* unary ~  */
obj  obj_bin(char op, obj a, obj b);  /* &|^ and << >>                        */
long ipow(long base, long exp);       /* integer ** (exp >= 0)                */
obj  obj_pow(obj a, obj b);           /* Tier-2 **                            */
long obj_cmp(obj a, obj b);        /* <0,0,>0 for < <= > >=                   */
obj  pyrange(long lo, long hi, long step);

/* ---- string methods (operate on char*) ---- */
bool str_startswith(str s, str p);
bool str_endswith(str s, str p);
str  str_strip(str s, int mode);   /* 0 both, 1 left, 2 right                 */
obj  str_split(str s, str sep);    /* sep NULL -> split on whitespace runs    */
obj  str_partition(str s, str sep);/* (before, sep_or_'', after) as a 3-list  */
obj  str_splitlines(str s);
str  str_replace(str s, str a, str b);
long str_find(str s, str sub, bool last);
bool str_isdigit(str s);
bool str_isalpha(str s);
bool str_isspace(str s);
bool str_isalnum(str s);
str  str_lower(str s);
str  str_upper(str s);
str  pyjoin(str sep, obj it);

/* ---- common builtins ---- */
obj  pyenumerate(obj it, long start);   /* list of [i, x]                     */
obj  pyzip(obj a, obj b);               /* list of [x, y] (shortest)          */
obj  pysorted(obj it);                  /* natural-order sort (obj_cmp)        */
void list_sort(obj lst);                /* in-place natural sort               */
obj  pymax(obj it, obj dflt, bool has_dflt);
obj  pymin(obj it, obj dflt, bool has_dflt);
obj  pysum(obj it, obj start);
obj  pyreversed(obj it);
obj  pyset(obj it);                     /* dedup iterable into a list-set      */
#define PY_SLICE_END 0x7fffffffffffffffL
obj  py_slice(obj seq, long lo, long hi);  /* str or list slice, Python rules  */
str  char_at(str s, long i);            /* s[i] on a string -> 1-char string   */
void set_add(obj s, obj v);             /* add to a list-set if absent         */
void list_remove(obj lst, obj v);       /* remove first matching element       */
obj  list_index(obj lst, obj v);        /* position of first match (else abort) */
obj  list_pop(obj lst);                 /* remove & return last element        */
obj  float_to_bits(obj val, long size); /* IEEE-754 float -> int bit pattern   */
obj  float_fromhex(str s);              /* float.fromhex: strtod (hex floats)  */
obj  pyfloat(obj v);                    /* float(x): str/int/float -> double    */
void list_assign_slice(obj dst, obj src); /* dst[:] = src (replace contents)   */
void list_set_slice(obj dst, long lo, long hi, obj src); /* dst[lo:hi] = src   */
obj  obj_augop(obj a, char op, obj b);  /* x op= y for obj (arith / set ops)   */
void del_item(obj c, obj k);            /* del c[k] for dict (key) or list (i) */
obj  pylist(obj it);                    /* shallow copy iterable into a list   */
long pyord(obj c);
str  pychr(long i);
long pyint(obj v);
long py_int_base(str s, long base);     /* int(s, base) incl 0x/0b/0o prefix   */
long pyabs(long x);

#endif /* SHIVYC_RT_H */
'''

MP_BRIDGE_H = r'''#ifndef MP_STDLIB_BRIDGE_H
#define MP_STDLIB_BRIDGE_H
#include "shivyc_rt.h"
obj mp_call_import(const char* mod, const char* attr, int n, ...);
obj mp_call_method(obj recv, const char* attr, int n, ...);
obj mp_call_obj(obj fun, obj args, obj kwargs);
bool mp_hasattr(obj recv, const char* attr);
obj mp_getattr(obj recv, const char* attr, obj dflt);
obj mp_getattr_obj(obj recv, obj attr, obj dflt);
#endif
'''

MP_BRIDGE_C = r'''#include "mpconfigstdlib.h"
#include "mp_stdlib_bridge.h"
#include <stdarg.h>
#include <string.h>
#include "py/runtime.h"
#include "py/obj.h"
#include "py/objstr.h"
#include "py/objlist.h"
#include "py/qstr.h"

static mp_obj_t obj_to_mp(obj v) {
    switch (v.tag) {
        case T_NONE: return mp_const_none;
        case T_INT: return mp_obj_new_int(v.u.i);
        case T_BOOL: return mp_obj_new_bool((bool)v.u.i);
        case T_STR: return mp_obj_new_str(v.u.s, v.u.s ? strlen(v.u.s) : 0);
        case T_FLOAT: return mp_obj_new_float(v.u.d);
        case T_LIST: {
            List* l = (List*)v.u.o;
            mp_obj_t items[l->len];
            for (int i = 0; i < l->len; i++) items[i] = obj_to_mp(l->data[i]);
            return mp_obj_new_list(l->len, items);
        }
        default: return mp_const_none;
    }
}

static obj mp_to_obj(mp_obj_t v) {
    if (v == mp_const_none) return OBJ_NONE;
    if (v == mp_const_true) return OBJ_BOOL(1);
    if (v == mp_const_false) return OBJ_BOOL(0);
    if (mp_obj_is_integer(v)) return OBJ_INT(mp_obj_get_int(v));
    if (mp_obj_is_str(v)) {
        size_t len; const char* s = mp_obj_str_get_data(v, &len);
        char* out = aalloc(len + 1);
        memcpy(out, s, len); out[len] = 0;
        return OBJ_STR(out);
    }
    if (mp_obj_is_float(v)) return OBJ_FLOAT(mp_obj_get_float(v));
    return OBJ_OBJ((Obj*)v);
}

obj mp_call_import(const char* mod, const char* attr, int n, ...) {
    qstr qm = qstr_from_str(mod);
    mp_obj_t module = mp_import_name(qm, MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_NEW_SMALL_INT(0));
    qstr qa = qstr_from_str(attr);
    mp_obj_t fun = mp_load_attr(module, qa);
    mp_obj_t args[n];
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) args[i] = obj_to_mp(va_arg(ap, obj));
    va_end(ap);
    return mp_to_obj(mp_call_function_n_kw(fun, n, 0, args));
}

obj mp_call_method(obj recv, const char* attr, int n, ...) {
    mp_obj_t dest[4];
    mp_obj_t o = obj_to_mp(recv);
    qstr qa = qstr_from_str(attr);
    mp_load_method(o, qa, dest);
    mp_obj_t args[n];
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) args[i] = obj_to_mp(va_arg(ap, obj));
    va_end(ap);
    return mp_to_obj(mp_call_method_n_kw(n, 0, args));
}

obj mp_call_obj(obj fun, obj args, obj kwargs) {
    mp_obj_t f = obj_to_mp(fun);
    List* l = (List*)args.u.o;
    size_t n = l ? (size_t)l->len : 0;
    Dict* kd = (kwargs.tag == T_DICT) ? (Dict*)kwargs.u.o : NULL;
    size_t nkw = kd ? (size_t)kd->len : 0;
    mp_obj_t call_args[n + 2 * nkw];
    for (size_t i = 0; i < n; i++) {
        call_args[i] = obj_to_mp(l->data[i]);
    }
    for (size_t i = 0; i < nkw; i++) {
        call_args[n + 2 * i] = obj_to_mp(kd->e[i].key);
        call_args[n + 2 * i + 1] = obj_to_mp(kd->e[i].val);
    }
    return mp_to_obj(mp_call_function_n_kw(f, n, nkw, call_args));
}

bool mp_hasattr(obj recv, const char* attr) {
    mp_obj_t o = obj_to_mp(recv);
    qstr qa = qstr_from_str(attr);
    mp_obj_t dest[2];
    mp_load_method_protected(o, qa, dest, false);
    return dest[0] != MP_OBJ_NULL;
}

obj mp_getattr(obj recv, const char* attr, obj dflt) {
    mp_obj_t o = obj_to_mp(recv);
    qstr qa = qstr_from_str(attr);
    mp_obj_t dest[2];
    mp_load_method_protected(o, qa, dest, false);
    if (dest[0] == MP_OBJ_NULL) return dflt;
    return mp_to_obj(dest[0]);
}

obj mp_getattr_obj(obj recv, obj attr, obj dflt) {
    mp_obj_t o = obj_to_mp(recv);
    mp_obj_t a = obj_to_mp(attr);
    size_t len; const char* s = mp_obj_str_get_data(a, &len);
    char buf[256];
    if (len >= sizeof buf) len = sizeof buf - 1;
    memcpy(buf, s, len); buf[len] = 0;
    return mp_getattr(recv, buf, dflt);
}
'''

RUNTIME_C = r'''/* ShivyCX transpiler runtime -- generated by tools/py2c.py */
#include <ctype.h>
#include "shivyc_rt.h"

/* One big region for the whole compilation. Tune as needed. */
static char   g_arena[1u << 26];
static size_t g_ap = 0;

/* Manual reclaim for Python `del`. A `del x` on a heap object becomes
 * afree(x, sizeof *x): the block is pushed onto a per-size free list and reused
 * by the next aalloc of that size. This keeps the fast bump allocator while
 * letting ShivyCX hand back large intermediate structures mid-compile (no
 * refcounting, no GC). arena_reset() drops the bump pointer and every free
 * list at once. afree ignores any pointer not allocated here (string literals,
 * stack, NULL), so `del` on a borrowed value is harmless. */
#define ARENA_ALIGN   16u
#define ARENA_BUCKETS 4096u   /* sizes 16..65536 bytes are pooled for reuse */
static void* g_free[ARENA_BUCKETS];

void* aalloc(size_t n) {
    size_t a = (n + (ARENA_ALIGN - 1u)) & ~(size_t)(ARENA_ALIGN - 1u);
    if (a == 0) a = ARENA_ALIGN;
    size_t bucket = a / ARENA_ALIGN;
    if (bucket < ARENA_BUCKETS && g_free[bucket]) {  /* reuse a del'd block */
        void* p = g_free[bucket];
        g_free[bucket] = *(void**)p;
        return p;
    }
    if (g_ap + a > sizeof g_arena) { fprintf(stderr, "arena exhausted\n"); abort(); }
    void* p = &g_arena[g_ap];
    g_ap += a;
    return p;
}

void afree(void* p, size_t n) {
    if (!p) return;
    char* cp = (char*)p;
    if (cp < g_arena || cp >= g_arena + sizeof g_arena) return;  /* not ours */
    size_t a = (n + (ARENA_ALIGN - 1u)) & ~(size_t)(ARENA_ALIGN - 1u);
    if (a == 0) a = ARENA_ALIGN;
    size_t bucket = a / ARENA_ALIGN;
    if (bucket == 0 || bucket >= ARENA_BUCKETS) return;  /* too big to pool */
    *(void**)p = g_free[bucket];
    g_free[bucket] = p;
}

void arena_reset(void) { g_ap = 0; memset(g_free, 0, sizeof g_free); }

void metadata(void) {}
void module(const char* name) { (void)name; }
void require(const char* name) { (void)name; }
void package(const char* name) { (void)name; }

/* ---- first-class function support ---- */
static const TypeInfoHdr CLOSURE_TYPE = { "function", NULL };
obj make_closure(ClosureFn fn, obj env) {
    Closure* c = (Closure*)aalloc(sizeof(Closure));
    c->_hdr.type = &CLOSURE_TYPE;
    c->fn = fn; c->env = env;
    return (obj){T_FUNC, {.o = (Obj*)c}};
}
obj call_closure(obj f, obj args) {
    Closure* c = (Closure*)f.u.o;
    return c->fn(c->env, args);
}
obj identity__tramp(obj env, obj args) {
    (void)env;
    return pylen(args) ? index_obj(args, 0) : OBJ_NONE;
}
obj call_obj(obj f, int n, ...) {
    obj args = list_new();
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) list_append(args, va_arg(ap, obj));
    va_end(ap);
    return call_closure(f, args);
}


obj float_to_bits(obj val, long size) {
    /* IEEE-754 reinterpret of a float initializer to its integer bit pattern,
       for an assembler .int/.quad directive. Integer initializers (the common
       case) pass through unchanged. */
    if (val.tag != T_FLOAT) return val;
    if (size == 4) {
        float f = (float)val.u.d;
        unsigned int u;
        memcpy(&u, &f, sizeof u);
        return OBJ_INT((long)u);
    }
    double d = val.u.d;
    unsigned long u;
    memcpy(&u, &d, sizeof u);
    return OBJ_INT((long)u);
}
obj float_fromhex(str s) {
    /* float.fromhex: C99 strtod parses hex-float syntax ("0x1.8p3") directly. */
    return OBJ_FLOAT(strtod(s, NULL));
}
obj pyfloat(obj v) {
    /* float(x): parse a string, or widen an int/bool; floats pass through. */
    if (v.tag == T_STR) return OBJ_FLOAT(strtod(v.u.s, NULL));
    if (v.tag == T_FLOAT) return v;
    return OBJ_FLOAT((double)((v.tag == T_INT || v.tag == T_BOOL) ? v.u.i : 0));
}

str pystr(obj v) {
    char* b;
    switch (v.tag) {
        case T_INT:  b = aalloc(24); sprintf(b, "%ld", v.u.i); return b;
        case T_FLOAT: b = aalloc(32); sprintf(b, "%g", v.u.d); return b;
        case T_BOOL: return v.u.i ? "True" : "False";
        case T_STR:  return v.u.s ? v.u.s : "";
        case T_NONE: return "None";
        case T_LIST: {
            List* l = (List*)v.u.o;
            size_t cap = 3;
            for (int i = 0; i < l->len; i++) cap += strlen(pyrepr(l->data[i])) + 2;
            b = aalloc(cap); char* p = b; *p++ = '[';
            for (int i = 0; i < l->len; i++) {
                if (i) { *p++ = ','; *p++ = ' '; }
                str s = pyrepr(l->data[i]); size_t n = strlen(s);
                memcpy(p, s, n); p += n;
            }
            *p++ = ']'; *p = 0; return b;
        }
        case T_DICT: {
            Dict* d = (Dict*)v.u.o;
            size_t cap = 3;
            for (int i = 0; i < d->len; i++)
                cap += strlen(pyrepr(d->e[i].key)) + strlen(pyrepr(d->e[i].val)) + 4;
            b = aalloc(cap); char* p = b; *p++ = '{';
            for (int i = 0; i < d->len; i++) {
                if (i) { *p++ = ','; *p++ = ' '; }
                str k = pyrepr(d->e[i].key); size_t kn = strlen(k);
                memcpy(p, k, kn); p += kn; *p++ = ':'; *p++ = ' ';
                str vv = pyrepr(d->e[i].val); size_t vn = strlen(vv);
                memcpy(p, vv, vn); p += vn;
            }
            *p++ = '}'; *p = 0; return b;
        }
        default:     b = aalloc(24); sprintf(b, "<obj %p>", (void*)v.u.o); return b;
    }
}

str pyrepr(obj v) {
    if (v.tag == T_STR) {
        size_t n = v.u.s ? strlen(v.u.s) : 0;
        char* b = aalloc(n + 3);
        b[0] = '\''; if (v.u.s) memcpy(b + 1, v.u.s, n);
        b[n + 1] = '\''; b[n + 2] = 0; return b;
    }
    return pystr(v);
}

str pyfmt(int n, const char* fmt, ...) {
    (void)n;
    va_list ap; va_start(ap, fmt);
    char* out = aalloc(strlen(fmt) + 256);
    char* p = out; const char* f = fmt;
    while (*f) {
        if (f[0] == '{' && f[1] == '}') {
            obj a = va_arg(ap, obj);
            str s = pystr(a);
            size_t l = strlen(s);
            memcpy(p, s, l); p += l;
            f += 2;
        } else {
            *p++ = *f++;
        }
    }
    *p = 0;
    va_end(ap);
    return out;
}

str pyconcat(str a, str b) {
    size_t la = a ? strlen(a) : 0, lb = b ? strlen(b) : 0;
    char* out = aalloc(la + lb + 1);
    if (a) memcpy(out, a, la);
    if (b) memcpy(out + la, b, lb);
    out[la + lb] = 0;
    return out;
}

bool in_str(str needle, const str* hay, int n) {
    for (int i = 0; i < n; i++) if (!strcmp(needle, hay[i])) return true;
    return false;
}

bool truthy(obj v) {
    switch (v.tag) {
        case T_NONE: return false;
        case T_INT:
        case T_BOOL: return v.u.i != 0;
        case T_STR:  return v.u.s && v.u.s[0];
        case T_LIST: return ((List*)v.u.o)->len != 0;
        case T_DICT: return ((Dict*)v.u.o)->len != 0;
        default:     return v.u.o != NULL;
    }
}

/* ---- lists ---- */
obj list_new(void) {
    List* l = aalloc(sizeof *l);
    l->len = 0; l->cap = 4;
    l->data = aalloc(sizeof(obj) * l->cap);
    obj r; r.tag = T_LIST; r.u.o = (Obj*)l; return r;
}
void list_append(obj lst, obj v) {
    List* l = (List*)lst.u.o;
    if (l->len == l->cap) {
        int nc = l->cap * 2;
        obj* nd = aalloc(sizeof(obj) * nc);
        memcpy(nd, l->data, sizeof(obj) * l->len);
        l->data = nd; l->cap = nc;
    }
    l->data[l->len++] = v;
}
void list_insert(obj lst, long i, obj v) {
    List* l = (List*)lst.u.o;
    if (i < 0) i += l->len;
    if (i < 0) i = 0;
    if (i > l->len) i = l->len;
    if (l->len == l->cap) {
        int nc = l->cap ? l->cap * 2 : 4;
        obj* nd = aalloc(sizeof(obj) * nc);
        memcpy(nd, l->data, sizeof(obj) * l->len);
        l->data = nd; l->cap = nc;
    }
    for (long j = l->len; j > i; j--)
        l->data[j] = l->data[j - 1];
    l->data[i] = v;
    l->len++;
}
void list_extend(obj lst, obj it) {
    long n = pylen(it);
    for (long i = 0; i < n; i++) list_append(lst, index_obj(it, i));
}
void list_assign_slice(obj dst, obj src) {
    /* dst[:] = src  -- replace all of dst's contents with src's, in place.
       Snapshot src first so the operation is safe even if src aliases dst. */
    long n = pylen(src);
    obj* tmp = (obj*)aalloc((n ? n : 1) * sizeof(obj));
    for (long i = 0; i < n; i++) tmp[i] = index_obj(src, i);
    ((List*)dst.u.o)->len = 0;
    for (long i = 0; i < n; i++) list_append(dst, tmp[i]);
}
void list_set_slice(obj dst, long lo, long hi, obj src) {
    /* dst[lo:hi] = src  -- splice src's elements in place of dst[lo:hi],
       resizing as needed. Snapshots src and the surviving tail first so the
       operation is safe even if src aliases dst. */
    List* d = (List*)dst.u.o;
    long n = d->len;
    if (lo < 0) lo += n;
    if (hi < 0) hi += n;
    if (lo < 0) lo = 0;
    if (lo > n) lo = n;
    if (hi < lo) hi = lo;
    if (hi > n) hi = n;
    long m = pylen(src), tail = n - hi;
    obj* sp = (obj*)aalloc((m ? m : 1) * sizeof(obj));
    for (long i = 0; i < m; i++) sp[i] = index_obj(src, i);
    obj* tl = (obj*)aalloc((tail ? tail : 1) * sizeof(obj));
    for (long i = 0; i < tail; i++) tl[i] = d->data[hi + i];
    d->len = lo;                              /* keep head [0:lo] */
    for (long i = 0; i < m; i++) list_append(dst, sp[i]);
    for (long i = 0; i < tail; i++) list_append(dst, tl[i]);
}
obj list_of(int n, ...) {
    obj r = list_new();
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) list_append(r, va_arg(ap, obj));
    va_end(ap);
    return r;
}
obj varg_list(int n, va_list ap) {
    obj r = list_new();
    for (int i = 0; i < n; i++) list_append(r, va_arg(ap, obj));
    return r;
}
obj list_get(obj lst, long i) {
    List* l = (List*)lst.u.o;
    if (i < 0) i += l->len;
    if (i < 0 || i >= l->len) return OBJ_NONE;
    return l->data[i];
}
void list_set(obj lst, long i, obj v) {
    List* l = (List*)lst.u.o;
    if (i < 0) i += l->len;
    if (i >= 0 && i < l->len) l->data[i] = v;
}
long pylen(obj v) {
    if (v.tag == T_LIST) return ((List*)v.u.o)->len;
    if (v.tag == T_DICT) return ((Dict*)v.u.o)->len;
    if (v.tag == T_STR)  return v.u.s ? (long)strlen(v.u.s) : 0;
    return 0;
}
obj index_obj(obj container, long i) {
    if (container.tag == T_LIST) return list_get(container, i);
    if (container.tag == T_DICT) {   /* iterating a dict yields its keys */
        Dict* d = (Dict*)container.u.o;
        if (i < 0) i += d->len;
        if (i < 0 || i >= d->len) return OBJ_NONE;
        return d->e[i].key;
    }
    if (container.tag == T_STR) {
        long n = (long)strlen(container.u.s);
        if (i < 0) i += n;
        if (i < 0 || i >= n) return OBJ_NONE;
        char* c = aalloc(2); c[0] = container.u.s[i]; c[1] = 0;
        return OBJ_STR(c);
    }
    return OBJ_NONE;
}
bool obj_eq(obj a, obj b) {
    if (a.tag != b.tag) {
        if ((a.tag == T_INT && b.tag == T_BOOL) ||
            (a.tag == T_BOOL && b.tag == T_INT)) return a.u.i == b.u.i;
        return false;
    }
    switch (a.tag) {
        case T_NONE: return true;
        case T_INT:
        case T_BOOL: return a.u.i == b.u.i;
        case T_STR:  return strcmp(a.u.s, b.u.s) == 0;
        default:     return a.u.o == b.u.o;
    }
}
bool pycontains(obj container, obj v) {
    if (container.tag == T_LIST) {
        List* l = (List*)container.u.o;
        for (int i = 0; i < l->len; i++) if (obj_eq(l->data[i], v)) return true;
        return false;
    }
    if (container.tag == T_DICT) return dict_contains(container, v);
    if (container.tag == T_STR && v.tag == T_STR)
        return strstr(container.u.s, v.u.s) != NULL;
    return false;
}
void pyprint(obj v) { printf("%s\n", pystr(v)); }

/* ---- dicts ---- */
obj dict_new(void) {
    Dict* d = aalloc(sizeof *d);
    d->len = 0; d->cap = 8; d->e = aalloc(sizeof(DEnt) * d->cap);
    obj r; r.tag = T_DICT; r.u.o = (Obj*)d; return r;
}
static int dict_find(Dict* d, obj k) {
    for (int i = 0; i < d->len; i++) if (obj_eq(d->e[i].key, k)) return i;
    return -1;
}
void dict_set(obj dd, obj k, obj v) {
    Dict* d = (Dict*)dd.u.o;
    int i = dict_find(d, k);
    if (i >= 0) { d->e[i].val = v; return; }
    if (d->len == d->cap) {
        int nc = d->cap * 2; DEnt* ne = aalloc(sizeof(DEnt) * nc);
        memcpy(ne, d->e, sizeof(DEnt) * d->len); d->e = ne; d->cap = nc;
    }
    d->e[d->len].key = k; d->e[d->len].val = v; d->len++;
}
obj dict_of(int n, ...) {
    obj r = dict_new();
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) { obj k = va_arg(ap, obj); obj v = va_arg(ap, obj); dict_set(r, k, v); }
    va_end(ap); return r;
}
obj dict_get(obj dd, obj k, obj dflt) {
    Dict* d = (Dict*)dd.u.o; int i = dict_find(d, k);
    return i >= 0 ? d->e[i].val : dflt;
}
bool dict_contains(obj dd, obj k) { return dict_find((Dict*)dd.u.o, k) >= 0; }
obj dict_pop(obj dd, obj k, obj dflt) {
    Dict* d = (Dict*)dd.u.o; int i = dict_find(d, k);
    if (i < 0) return dflt;
    obj v = d->e[i].val;
    for (int j = i; j < d->len - 1; j++) d->e[j] = d->e[j + 1];
    d->len--; return v;
}
obj dict_setdefault(obj dd, obj k, obj dflt) {
    Dict* d = (Dict*)dd.u.o; int i = dict_find(d, k);
    if (i >= 0) return d->e[i].val;
    dict_set(dd, k, dflt); return dflt;
}
void dict_update(obj dd, obj other) {
    if (other.tag != T_DICT) return;
    Dict* o = (Dict*)other.u.o;
    for (int i = 0; i < o->len; i++) dict_set(dd, o->e[i].key, o->e[i].val);
}
obj dict_keys(obj dd) {
    Dict* d = (Dict*)dd.u.o; obj r = list_new();
    for (int i = 0; i < d->len; i++) list_append(r, d->e[i].key);
    return r;
}
obj dict_values(obj dd) {
    Dict* d = (Dict*)dd.u.o; obj r = list_new();
    for (int i = 0; i < d->len; i++) list_append(r, d->e[i].val);
    return r;
}
obj dict_items(obj dd) {
    Dict* d = (Dict*)dd.u.o; obj r = list_new();
    for (int i = 0; i < d->len; i++)
        list_append(r, list_of(2, d->e[i].key, d->e[i].val));
    return r;
}
obj subscript(obj container, obj key) {
    if (container.tag == T_DICT) return dict_get(container, key, OBJ_NONE);
    if (container.tag == T_LIST) return list_get(container, AS_INT(key));
    if (container.tag == T_STR)  return index_obj(container, AS_INT(key));
    return OBJ_NONE;
}
void subscript_set(obj container, obj key, obj v) {
    if (container.tag == T_DICT) { dict_set(container, key, v); return; }
    if (container.tag == T_LIST) { list_set(container, AS_INT(key), v); return; }
}

/* ---- arithmetic / comparison on Tier-2 values ---- */
static long as_num(obj v) { return (v.tag == T_INT || v.tag == T_BOOL) ? v.u.i : 0; }

obj obj_add(obj a, obj b) {
    if (a.tag == T_STR && b.tag == T_STR) return OBJ_STR(pyconcat(a.u.s, b.u.s));
    if (a.tag == T_LIST && b.tag == T_LIST) {
        obj r = list_new();
        List* la = (List*)a.u.o; List* lb = (List*)b.u.o;
        for (int i = 0; i < la->len; i++) list_append(r, la->data[i]);
        for (int i = 0; i < lb->len; i++) list_append(r, lb->data[i]);
        return r;
    }
    return OBJ_INT(as_num(a) + as_num(b));
}
obj obj_sub(obj a, obj b) { return OBJ_INT(as_num(a) - as_num(b)); }
obj obj_mul(obj a, obj b) {
    if (a.tag == T_STR && b.tag == T_INT) {
        long n = b.u.i; size_t l = strlen(a.u.s);
        char* o = aalloc(l * (n < 0 ? 0 : n) + 1); o[0] = 0;
        for (long i = 0; i < n; i++) memcpy(o + i * l, a.u.s, l);
        o[l * (n < 0 ? 0 : n)] = 0; return OBJ_STR(o);
    }
    return OBJ_INT(as_num(a) * as_num(b));
}
obj obj_fdiv(obj a, obj b) { long d = as_num(b); return OBJ_INT(d ? as_num(a) / d : 0); }
obj obj_mod(obj a, obj b) { long d = as_num(b); return OBJ_INT(d ? as_num(a) % d : 0); }
obj obj_neg(obj a) { return OBJ_INT(-as_num(a)); }
obj obj_invert(obj a) { return OBJ_INT(~as_num(a)); }
long ipow(long base, long exp) {
    long r = 1;
    while (exp > 0) { if (exp & 1) r *= base; base *= base; exp >>= 1; }
    return r;
}
obj obj_pow(obj a, obj b) { return OBJ_INT(ipow(as_num(a), as_num(b))); }
obj obj_bin(char op, obj a, obj b) {
    long x = as_num(a), y = as_num(b), r = 0;
    switch (op) {
        case '&': r = x & y; break; case '|': r = x | y; break;
        case '^': r = x ^ y; break; case 'l': r = x << y; break;
        case 'r': r = x >> y; break;
    }
    return OBJ_INT(r);
}
long obj_cmp(obj a, obj b) {
    if (a.tag == T_STR && b.tag == T_STR) return strcmp(a.u.s, b.u.s);
    long x = as_num(a), y = as_num(b);
    return x < y ? -1 : (x > y ? 1 : 0);
}
obj pyrange(long lo, long hi, long step) {
    obj r = list_new();
    if (step > 0) for (long i = lo; i < hi; i += step) list_append(r, OBJ_INT(i));
    else if (step < 0) for (long i = lo; i > hi; i += step) list_append(r, OBJ_INT(i));
    return r;
}

/* ---- string methods ---- */
bool str_startswith(str s, str p) {
    size_t ls = strlen(s), lp = strlen(p);
    return lp <= ls && memcmp(s, p, lp) == 0;
}
bool str_endswith(str s, str p) {
    size_t ls = strlen(s), lp = strlen(p);
    return lp <= ls && memcmp(s + ls - lp, p, lp) == 0;
}
str str_strip(str s, int mode) {
    size_t n = strlen(s); size_t a = 0, b = n;
    if (mode != 2) while (a < b && isspace((unsigned char)s[a])) a++;
    if (mode != 1) while (b > a && isspace((unsigned char)s[b - 1])) b--;
    char* o = aalloc(b - a + 1); memcpy(o, s + a, b - a); o[b - a] = 0; return o;
}
obj str_split(str s, str sep) {
    obj r = list_new();
    if (!sep || !sep[0]) {                 /* whitespace */
        size_t i = 0, n = strlen(s);
        while (i < n) {
            while (i < n && isspace((unsigned char)s[i])) i++;
            size_t j = i;
            while (j < n && !isspace((unsigned char)s[j])) j++;
            if (j > i) { char* o = aalloc(j - i + 1); memcpy(o, s + i, j - i); o[j - i] = 0; list_append(r, OBJ_STR(o)); }
            i = j;
        }
        return r;
    }
    size_t sl = strlen(sep); const char* p = s; const char* q;
    while ((q = strstr(p, sep)) != NULL) {
        size_t len = q - p; char* o = aalloc(len + 1); memcpy(o, p, len); o[len] = 0;
        list_append(r, OBJ_STR(o)); p = q + sl;
    }
    list_append(r, OBJ_STR((str)p));
    return r;
}
obj str_partition(str s, str sep) {
    /* str.partition: (head, sep, tail) at the first occurrence of sep, else
       (s, "", "").  Returned as a 3-element list for tuple unpacking. */
    obj r = list_new();
    const char* q = (sep && sep[0]) ? strstr(s, sep) : NULL;
    if (!q) {
        list_append(r, OBJ_STR(s));
        list_append(r, OBJ_STR(""));
        list_append(r, OBJ_STR(""));
        return r;
    }
    size_t hl = q - s;
    char* head = aalloc(hl + 1); memcpy(head, s, hl); head[hl] = 0;
    list_append(r, OBJ_STR(head));
    list_append(r, OBJ_STR(sep));
    list_append(r, OBJ_STR((str)(q + strlen(sep))));
    return r;
}
obj str_splitlines(str s) {
    obj r = list_new(); const char* p = s; const char* start = s;
    for (; *p; p++) if (*p == '\n') {
        size_t len = p - start; char* o = aalloc(len + 1); memcpy(o, start, len); o[len] = 0;
        list_append(r, OBJ_STR(o)); start = p + 1;
    }
    if (*start) { list_append(r, OBJ_STR((str)start)); }
    return r;
}
str str_replace(str s, str a, str b) {
    size_t la = strlen(a); if (!la) return s;
    size_t lb = strlen(b), ls = strlen(s); int cnt = 0;
    for (const char* p = s; (p = strstr(p, a)); p += la) cnt++;
    char* o = aalloc(ls + (lb > la ? (lb - la) : 0) * cnt + 1); char* w = o; const char* p = s; const char* q;
    while ((q = strstr(p, a))) { memcpy(w, p, q - p); w += q - p; memcpy(w, b, lb); w += lb; p = q + la; }
    strcpy(w, p); return o;
}
long str_find(str s, str sub, bool last) {
    const char* hit = NULL;
    if (!last) { const char* q = strstr(s, sub); return q ? (long)(q - s) : -1; }
    for (const char* p = s; (p = strstr(p, sub)); p++) hit = p;
    return hit ? (long)(hit - s) : -1;
}
bool str_isdigit(str s) { if (!*s) return false; for (; *s; s++) if (!isdigit((unsigned char)*s)) return false; return true; }
bool str_isalpha(str s) { if (!*s) return false; for (; *s; s++) if (!isalpha((unsigned char)*s)) return false; return true; }
bool str_isspace(str s) { if (!*s) return false; for (; *s; s++) if (!isspace((unsigned char)*s)) return false; return true; }
bool str_isalnum(str s) { if (!*s) return false; for (; *s; s++) if (!isalnum((unsigned char)*s)) return false; return true; }
str str_lower(str s) { size_t n = strlen(s); char* o = aalloc(n + 1); for (size_t i = 0; i < n; i++) o[i] = tolower((unsigned char)s[i]); o[n] = 0; return o; }
str str_upper(str s) { size_t n = strlen(s); char* o = aalloc(n + 1); for (size_t i = 0; i < n; i++) o[i] = toupper((unsigned char)s[i]); o[n] = 0; return o; }
str pyjoin(str sep, obj it) {
    long n = pylen(it); if (n <= 0) return "";
    size_t sl = strlen(sep), tot = 0;
    str* parts = aalloc(sizeof(str) * n);
    for (long i = 0; i < n; i++) { parts[i] = pystr(index_obj(it, i)); tot += strlen(parts[i]); }
    char* o = aalloc(tot + sl * (n - 1) + 1); char* w = o;
    for (long i = 0; i < n; i++) { if (i) { memcpy(w, sep, sl); w += sl; } size_t l = strlen(parts[i]); memcpy(w, parts[i], l); w += l; }
    *w = 0; return o;
}

/* ---- builtins ---- */
obj pyenumerate(obj it, long start) {
    obj r = list_new(); long n = pylen(it);
    for (long i = 0; i < n; i++) list_append(r, list_of(2, OBJ_INT(start + i), index_obj(it, i)));
    return r;
}
obj pyzip(obj a, obj b) {
    obj r = list_new();
    long n = pylen(a), m = pylen(b); if (m < n) n = m;
    for (long i = 0; i < n; i++)
        list_append(r, list_of(2, index_obj(a, i), index_obj(b, i)));
    return r;
}
static int cmp_obj_qsort(const void* a, const void* b) {
    long c = obj_cmp(*(const obj*)a, *(const obj*)b);
    return c < 0 ? -1 : (c > 0 ? 1 : 0);
}
void list_sort(obj lst) {
    if (lst.tag != T_LIST) return;
    List* l = (List*)lst.u.o;
    qsort(l->data, l->len, sizeof(obj), cmp_obj_qsort);
}
obj pysorted(obj it) {
    obj r = pylist(it); list_sort(r); return r;
}
obj pymax(obj it, obj dflt, bool has_dflt) {
    long n = pylen(it); if (n == 0) return has_dflt ? dflt : OBJ_NONE;
    obj best = index_obj(it, 0);
    for (long i = 1; i < n; i++) { obj v = index_obj(it, i); if (obj_cmp(v, best) > 0) best = v; }
    return best;
}
obj pymin(obj it, obj dflt, bool has_dflt) {
    long n = pylen(it); if (n == 0) return has_dflt ? dflt : OBJ_NONE;
    obj best = index_obj(it, 0);
    for (long i = 1; i < n; i++) { obj v = index_obj(it, i); if (obj_cmp(v, best) < 0) best = v; }
    return best;
}
obj pysum(obj it, obj start) {
    obj acc = start; long n = pylen(it);
    for (long i = 0; i < n; i++) acc = obj_add(acc, index_obj(it, i));
    return acc;
}
obj pyreversed(obj it) {
    obj r = list_new(); long n = pylen(it);
    for (long i = n - 1; i >= 0; i--) list_append(r, index_obj(it, i));
    return r;
}
obj pylist(obj it) {
    obj r = list_new(); long n = pylen(it);
    for (long i = 0; i < n; i++) list_append(r, index_obj(it, i));
    return r;
}
obj pyset(obj it) {
    obj r = list_new(); long n = pylen(it);
    for (long i = 0; i < n; i++) { obj v = index_obj(it, i); if (!pycontains(r, v)) list_append(r, v); }
    return r;
}
void set_add(obj s, obj v) { if (!pycontains(s, v)) list_append(s, v); }
void list_remove(obj lst, obj v) {
    if (lst.tag != T_LIST) return;
    List* l = (List*)lst.u.o;
    for (long i = 0; i < l->len; i++) {
        if (obj_eq(l->data[i], v)) {
            for (long j = i; j + 1 < l->len; j++) l->data[j] = l->data[j + 1];
            l->len--;
            return;
        }
    }
}
obj list_pop(obj lst) {
    if (lst.tag != T_LIST) return OBJ_NONE;
    List* l = (List*)lst.u.o;
    if (l->len == 0) return OBJ_NONE;
    return l->data[--l->len];
}
obj list_index(obj lst, obj v) {
    if (lst.tag == T_LIST) {
        List* l = (List*)lst.u.o;
        for (long i = 0; i < l->len; i++)
            if (obj_eq(l->data[i], v)) return OBJ_INT(i);
    }
    fprintf(stderr, "list.index: value not found\n"); abort();
}
obj obj_augop(obj a, char op, obj b) {
    int set_like = (a.tag == T_LIST || b.tag == T_LIST);
    if (op == '+') return obj_add(a, b);
    if (set_like && op == '|') {                 /* set union */
        obj r = pyset(a); long n = pylen(b);
        for (long i = 0; i < n; i++) set_add(r, index_obj(b, i));
        return r;
    }
    if (set_like && op == '&') {                 /* intersection */
        obj r = list_new(); long n = pylen(a);
        for (long i = 0; i < n; i++) {
            obj v = index_obj(a, i);
            if (pycontains(b, v) && !pycontains(r, v)) list_append(r, v);
        }
        return r;
    }
    if (set_like && op == '-') {                 /* difference */
        obj r = list_new(); long n = pylen(a);
        for (long i = 0; i < n; i++) {
            obj v = index_obj(a, i);
            if (!pycontains(b, v) && !pycontains(r, v)) list_append(r, v);
        }
        return r;
    }
    return obj_bin(op, a, b);
}
void del_item(obj c, obj k) {
    if (c.tag == T_DICT) { dict_pop(c, k, OBJ_NONE); return; }
    if (c.tag == T_LIST) {
        List* l = (List*)c.u.o;
        long i = as_num(k);
        if (i < 0) i += l->len;
        if (i >= 0 && i < l->len) {
            for (long j = i; j + 1 < l->len; j++) l->data[j] = l->data[j + 1];
            l->len--;
        }
    }
}

str char_at(str s, long i) {
    long n = (long)strlen(s);
    if (i < 0) i += n;
    char* o = aalloc(2);
    o[0] = (i >= 0 && i < n) ? s[i] : 0;
    o[1] = 0;
    return o;
}
static long clamp_index(long i, long n) {
    if (i < 0) i += n;
    if (i < 0) i = 0;
    if (i > n) i = n;
    return i;
}
obj py_slice(obj seq, long lo, long hi) {
    if (seq.tag == T_STR) {
        const char* s = seq.u.s ? seq.u.s : "";
        long n = (long)strlen(s);
        long a = clamp_index(lo, n), b = (hi == PY_SLICE_END) ? n
                                          : clamp_index(hi, n);
        if (b < a) b = a;
        char* o = aalloc(b - a + 1);
        memcpy(o, s + a, b - a); o[b - a] = 0;
        return OBJ_STR(o);
    }
    long n = pylen(seq);
    long a = clamp_index(lo, n), b = (hi == PY_SLICE_END) ? n
                                      : clamp_index(hi, n);
    obj r = list_new();
    for (long i = a; i < b; i++) list_append(r, index_obj(seq, i));
    return r;
}
long pyord(obj c) { return (c.tag == T_STR && c.u.s) ? (unsigned char)c.u.s[0] : (long)as_num(c); }
str pychr(long i) { char* o = aalloc(2); o[0] = (char)i; o[1] = 0; return o; }
long pyint(obj v) {
    if (v.tag == T_INT || v.tag == T_BOOL) return v.u.i;
    if (v.tag == T_STR) return strtol(v.u.s, NULL, 0);
    return 0;
}
long pyabs(long x) { return x < 0 ? -x : x; }
long py_int_base(str s, long base) {
    const char* p = s ? s : "";
    while (isspace((unsigned char)*p)) p++;
    int neg = 0;
    if (*p == '+' || *p == '-') { neg = (*p == '-'); p++; }
    if (base == 16 && p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) p += 2;
    else if (base == 2 && p[0] == '0' && (p[1] == 'b' || p[1] == 'B')) p += 2;
    else if (base == 8 && p[0] == '0' && (p[1] == 'o' || p[1] == 'O')) p += 2;
    long v = strtol(p, NULL, (int)(base == 0 ? 0 : base));
    return neg ? -v : v;
}
'''


# ==========================================================================
# Type inference from naming conventions
# ==========================================================================

INT_NAMES = {
    "index", "idx", "i", "j", "k", "n", "n1", "n2", "count", "size",
    "offset", "chunk", "num", "length", "len", "pos", "position", "line",
    "lineno", "col", "column", "start", "end", "depth", "level", "width",
    "height", "amount", "total", "addr", "address", "byte", "bytes", "bits",
    "rbp_offset", "spot_size",
}
INT_SUFFIXES = ("size", "offset", "count", "index", "len", "num", "idx")

STR_NAMES = {
    "name", "text", "s", "string", "msg", "message", "filename", "fname",
    "func_name", "tag", "rep", "content", "spelling", "label", "identifier",
    "prog", "code", "asm_code", "asm_str", "text_repr", "mangled", "suffix",
    "prefix",
}
STR_SUFFIXES = ("str",)

BOOL_NAMES = {
    "defined", "ok", "found", "done", "wide", "signed", "unsigned", "const",
    "volatile", "valid", "empty", "present", "enabled", "success",
}
BOOL_PREFIXES = ("is_", "has_", "can_", "should_", "was_", "use_", "allow_")

OBJ = "obj"

KNOWN_CLASSES = {}      # name -> ClassInfo
VTABLE_METHODS = set()  # method names that are virtual somewhere in the module
_XMOD_CACHE = {}        # dotted module name -> imported-symbol registry


def ann_text_to_ctype(text):
    text = text.strip().strip("'\"")
    simple = {"int": "int", "bool": "bool", "None": "void", "str": "char*",
              "float": "double", "bytes": "char*"}
    if text in simple:
        return simple[text]
    head = text.split("[", 1)[0]
    if head in ("List", "list", "Dict", "dict", "Set", "set", "Tuple",
                "tuple", "Optional", "Any", "object"):
        return OBJ
    if text and (text[0].isupper() or text[0] == "_") and text.isidentifier():
        return text + "*"
    return None


def ann_elem_ctype(ann):
    """Element C type of a `List[T]`/`list[T]` annotation, e.g. List[ILValue]
    -> 'ILValue*'. None if `ann` is not a typed-list annotation. Used to give a
    concrete type to the loop variable of `for x in <annotated list>` (and to
    subscripts of it) so member access on the elements resolves."""
    if ann is None:
        return None
    try:
        text = ast.unparse(ann).strip().strip("'\"")
    except Exception:
        return None
    for pfx in ("List[", "list["):
        if text.startswith(pfx) and text.endswith("]"):
            return ann_text_to_ctype(text[len(pfx):-1])
    return None


def ann_to_ctype(ann):
    if ann is None:
        return None
    try:
        return ann_text_to_ctype(ast.unparse(ann))
    except Exception:
        return None


def infer_from_name(name):
    if name == "self":
        return None
    low = name.lower()
    if low in INT_NAMES:
        return "int"
    if low in STR_NAMES:
        return "char*"
    if low in BOOL_NAMES:
        return "bool"
    if low.startswith(BOOL_PREFIXES):
        return "bool"
    tail = low.rsplit("_", 1)[-1]
    if tail in INT_SUFFIXES and not name.startswith("_"):
        return "int"
    if tail in STR_SUFFIXES:
        return "char*"
    return None


def infer_type(name, ann=None):
    return ann_to_ctype(ann) or infer_from_name(name) or OBJ


def optional_param_names(fn):
    """Params with a literal `None` default and no annotation are Optional,
    hence must be boxed as `obj` (None has to be representable)."""
    names = set()
    defaults = fn.args.defaults
    if defaults:
        for arg, default in zip(fn.args.args[-len(defaults):], defaults):
            if arg.annotation is None and isinstance(default, ast.Constant) \
                    and default.value is None:
                names.add(arg.arg)
    return names


def _param_used_as_container(fn, name):
    """True if `name` is iterated, membership-tested, subscripted, or mutated as
    a collection inside `fn` -- strong evidence it isn't the scalar its name
    suggests (e.g. a set parameter called `defined`)."""
    for n in ast.walk(fn):
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) \
                and n.iter.id == name:
            return True
        if isinstance(n, ast.Compare):
            for op, cmp in zip(n.ops, n.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and \
                        isinstance(cmp, ast.Name) and cmp.id == name:
                    return True
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                and n.value.id == name:
            return True
        if isinstance(n, ast.comprehension) and isinstance(n.iter, ast.Name) \
                and n.iter.id == name:
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == name and n.func.attr in (
                    "append", "add", "update", "extend", "pop", "remove",
                    "keys", "values", "items", "get", "discard"):
            return True
    return False


def _param_used_as_object(fn, name):
    """True if `name` is accessed via an attribute that isn't a string or
    container method (e.g. `identifier.content`) -- evidence it's an object,
    not the scalar string/int its name suggests."""
    KNOWN = {"strip", "lstrip", "rstrip", "upper", "lower", "replace", "split",
             "partition",
             "splitlines", "startswith", "endswith", "isdigit", "isalpha",
             "isspace", "isalnum", "find", "rfind", "join", "encode", "decode",
             "format", "count", "index", "append", "add", "update", "extend",
             "pop", "remove", "keys", "values", "items", "get", "discard",
             "sort", "copy", "setdefault"}
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                and n.value.id == name and n.attr not in KNOWN:
            return True
    return False


def _param_used_in_isinstance(fn, name):
    """True if `name` is the subject of isinstance(...)."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and \
                n.func.id == "isinstance" and n.args and \
                isinstance(n.args[0], ast.Name) and n.args[0].id == name:
            return True
    return False


def _param_assigned_from_call(fn, name, call_names):
    """True if `name` is assigned from a call like input()."""
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if isinstance(t, ast.Name) and t.id == name and \
                    isinstance(n.value, ast.Call):
                f = n.value.func
                if isinstance(f, ast.Name) and f.id in call_names:
                    return True
                if isinstance(f, ast.Attribute) and f.attr in call_names:
                    return True
    return False


def _param_used_in_str_compare(fn, name):
    """True if `name` is compared with ==/!= (likely a str in stdlib code)."""
    for n in ast.walk(fn):
        if not isinstance(n, ast.Compare):
            continue
        sides = [n.left] + list(n.comparators)
        if any(isinstance(s, ast.Name) and s.id == name for s in sides):
            return True
    return False


def arg_ctype(fn, arg):
    if arg.annotation is not None:
        return ann_to_ctype(arg.annotation) or OBJ
    if arg.arg in optional_param_names(fn):
        return OBJ
    if _param_used_in_isinstance(fn, arg.arg):
        return OBJ
    if _param_assigned_from_call(fn, arg.arg, {"input", "raw_input"}):
        return OBJ
    if _param_used_in_str_compare(fn, arg.arg):
        return OBJ
    guess = infer_from_name(arg.arg)
    if guess in ("bool", "int") and _param_used_as_container(fn, arg.arg):
        return OBJ                  # usage contradicts the scalar name guess
    if guess in ("bool", "int", "char*") and _param_used_as_object(fn, arg.arg):
        return OBJ                  # attribute access -> it's an object
    return guess or OBJ


# ==========================================================================
# Class model
# ==========================================================================

class ClassInfo:
    def __init__(self, node):
        self.node = node
        self.name = node.name
        self.csym = node.name       # C base symbol (module-qualified if the
                                    # class name collides across modules)
        self.base = None
        self.base_name = None
        self.methods = {}
        self.static_methods = set()  # names decorated @staticmethod (no self)
        self.classmethod_methods = set()  # @classmethod (cls as first arg)
        self.property_methods = set()  # @property: direct call, not vtable slot
        self.own_fields = []
        self.const_dicts = {}
        self.class_statics = {}     # class-level obj constants (lists / dicts
                                    # the const_dict fast-path can't specialize)
        self.class_attrs = {}       # class-level scalar defaults (instance flds)
        bases = [b for b in node.bases if isinstance(b, ast.Name)]
        if bases:
            self.base_name = bases[0].id

    def root(self):
        c = self
        while c.base:
            c = c.base
        return c

    def full_fields(self):
        out = []
        if self.base:
            out.extend(self.base.full_fields())
        seen = {f[0] for f in out}
        for f in self.own_fields:
            if f[0] not in seen:
                out.append(f)
                seen.add(f[0])
        return out

    def field_ctype(self, cn):
        for n, t in self.full_fields():
            if n == cn:
                return t
        return None

    def find_method_owner(self, mname):
        c = self
        while c:
            if mname in c.methods:
                return c
            c = c.base
        return None


def _const_value(node):
    """Python constant value of `node` (int/str/bool, incl. negatives), else
    None."""
    if isinstance(node, ast.Constant) and isinstance(node.value,
                                                     (int, str, bool, bytes)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
            and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)):
        return -node.operand.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "const" and len(node.args) == 1:
        return _const_value(node.args[0])
    return None


def _assigned_names(fn):
    """Names truly *bound* (rebound) anywhere in fn. A subscript/attribute
    target (a[i]=..., a.x=...) mutates an existing object rather than binding a
    new local, so the base name is NOT counted -- it stays a free variable."""
    out = set()

    def bind_target(t):
        if isinstance(t, ast.Name):
            out.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                bind_target(e)
        elif isinstance(t, ast.Starred):
            bind_target(t.value)
        # Subscript / Attribute targets intentionally ignored (mutation)

    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                bind_target(t)
        elif isinstance(n, ast.AnnAssign):
            bind_target(n.target)
        elif isinstance(n, (ast.AugAssign,)):
            if isinstance(n.target, ast.Name):
                out.add(n.target.id)
        elif isinstance(n, ast.For):
            bind_target(n.target)
        elif isinstance(n, ast.comprehension):
            bind_target(n.target)
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            bind_target(n.optional_vars)
    return out


def _free_vars(sub, enclosing_names):
    """Enclosing locals that `sub` reads but does not itself bind/param."""
    params = {a.arg for a in sub.args.args}
    bound = params | _assigned_names(sub)
    used = {n.id for n in ast.walk(sub)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return [nm for nm in sorted(used & enclosing_names) if nm not in bound]


class _CallRewriter(ast.NodeTransformer):
    """Rewrite calls to a lifted nested function: rename + prepend captures."""
    def __init__(self, name_map):
        self.name_map = name_map        # original name -> (mangled, captures)

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        if isinstance(f, ast.Name) and f.id in self.name_map:
            mangled, captures = self.name_map[f.id]
            node.func = ast.Name(id=mangled, ctx=ast.Load())
            node.args = [ast.Name(id=c, ctx=ast.Load()) for c in captures] \
                + node.args
        return node


class _ValueUseReplacer(ast.NodeTransformer):
    """Replace a *value* use of a lifted nested function (the bare name, not a
    call — those are already rewritten) with a closure-construction marker, so
    `callback(emitting)` builds a real closure carrying the captured env."""
    def __init__(self, vmap):
        self.vmap = vmap                # orig name -> (mangled, captures)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.vmap:
            mangled, captures = self.vmap[node.id]
            return ast.Call(
                func=ast.Name(id="__closure_env__", ctx=ast.Load()),
                args=[ast.Constant(value=mangled)] +
                     [ast.Name(id=c, ctx=ast.Load()) for c in captures],
                keywords=[])
        return node


class _NameRenamer(ast.NodeTransformer):
    """Rename references to a single local name within an expression."""
    def __init__(self, old, new):
        self.old, self.new = old, new

    def visit_Name(self, node):
        if node.id == self.old:
            return ast.copy_location(ast.Name(id=self.new, ctx=node.ctx), node)
        return node


def rewrite_module_lambdas(tree):
    """Module-level `name = lambda ...` -> `def name(...): return ...`."""
    new_body = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name) \
                and isinstance(stmt.value, ast.Lambda):
            lam = stmt.value
            name = stmt.targets[0].id
            fn = ast.FunctionDef(name=name, args=lam.args,
                                 body=[ast.Return(value=lam.body)],
                                 decorator_list=[], returns=None)
            new_body.append(ast.copy_location(fn, stmt))
        else:
            new_body.append(stmt)
    tree.body = new_body
    ast.fix_missing_locations(tree)
    return tree


def rewrite_class_lambdas(tree):
    """A class-level `name = lambda self_, *args: body` is, in Python, just a
    method (a function stored as a class attribute binds as one). Rewrite each
    such assignment into a real method def so it participates in normal method
    dispatch -- including polymorphic override across subclasses."""
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        new_body = []
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                    and isinstance(stmt.targets[0], ast.Name) \
                    and isinstance(stmt.value, ast.Lambda):
                lam = stmt.value
                name = stmt.targets[0].id
                body = lam.body
                if lam.args.args and lam.args.args[0].arg != "self":
                    # the first parameter is the (conventionally `_`) self slot
                    body = _NameRenamer(lam.args.args[0].arg, "self").visit(body)
                    lam.args.args[0] = ast.arg(arg="self")
                fn = ast.FunctionDef(name=name, args=lam.args,
                                     body=[ast.Return(value=body)],
                                     decorator_list=[], returns=None)
                new_body.append(ast.copy_location(fn, stmt))
            else:
                new_body.append(stmt)
        cls.body = new_body
    ast.fix_missing_locations(tree)
    return tree


def _is_dunder_main_guard(node):
    """True for `if __name__ == "__main__":` -- the script-entry idiom. The C
    program has its own entry point, so this block is skipped when transpiling."""
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__")


def lift_nested_functions(tree):
    """Closure-convert one level of nested functions to file scope.

    Each nested def is moved to module scope with a mangled name and its
    captured enclosing locals prepended as parameters; all calls (including
    recursive ones) are rewritten to pass those captures. Functions that
    rebind a captured variable via `nonlocal` are left in place (unsupported).
    """
    lifted = []
    specs = {}

    def process(fn, prefix, cls=None):
        encl = {a.arg for a in fn.args.args} | _assigned_names(fn)
        if fn.args.vararg:
            encl.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            encl.add(fn.args.kwarg.arg)
        nested = [s for s in fn.body if isinstance(s, ast.FunctionDef)]
        if not nested:
            return
        name_map = {}
        orig_defaults = {}              # sub.name -> original-param default nodes
        for sub in nested:
            if any(isinstance(n, ast.Nonlocal) for n in ast.walk(sub)):
                continue                # rebinds enclosing var; can't lift
            captures = _free_vars(sub, encl)
            mangled = "%s__%s" % (prefix, sub.name)
            name_map[sub.name] = (mangled, captures)
            np = len(sub.args.args)
            nd = len(sub.args.defaults)
            orig_defaults[sub.name] = [None] * (np - nd) + list(sub.args.defaults)
        if not name_map:
            return
        rewriter = _CallRewriter(name_map)
        # drop the nested defs from the parent body, rewrite remaining calls
        fn.body = [s for s in fn.body if not (isinstance(s, ast.FunctionDef)
                                              and s.name in name_map)]
        rewriter.visit(fn)
        my_subs = []
        for sub in nested:
            if sub.name not in name_map:
                continue
            mangled, captures = name_map[sub.name]
            real_defs = orig_defaults[sub.name]
            sub.name = mangled
            # captured locals default to Tier-2 obj (their real type coerces in
            # at the call site); a name-based guess like defined->bool is wrong.
            # `self` keeps its class so member/static access still resolves.
            bound = {a.arg for a in sub.args.args}
            if sub.args.vararg:
                bound.add(sub.args.vararg.arg)
            if sub.args.kwarg:
                bound.add(sub.args.kwarg.arg)
            bound.update(a.arg for a in sub.args.kwonlyargs)
            cap_args = []
            for c in captures:
                cap_name = c
                if cap_name in bound:
                    cap_name = c + "__cap"
                    _NameRenamer(c, cap_name).visit(sub)
                ann = ast.Name(id=(cls if (c == "self" and cls) else "object"),
                               ctx=ast.Load())
                cap_args.append(ast.arg(arg=cap_name, annotation=ann))
            sub.args.args = cap_args + sub.args.args
            rewriter.visit(sub)         # rewrite recursive/sibling calls
            sub.decorator_list = []
            lifted.append(sub)
            my_subs.append(sub)
            # a value use of this fn (passed as an argument, stored, returned)
            # needs a real closure; register a spec so a trampoline is emitted.
            specs[mangled] = (sub, len(captures), real_defs)
            process(sub, mangled, cls)  # handle deeper nesting
        # any remaining bare references to a lifted name are value uses
        repl = _ValueUseReplacer(dict(name_map))
        repl.visit(fn)
        for sub in my_subs:
            repl.visit(sub)

    for top in list(tree.body):
        if isinstance(top, ast.FunctionDef):
            process(top, top.name)
        elif isinstance(top, ast.ClassDef):
            for item in top.body:
                if isinstance(item, ast.FunctionDef):
                    process(item, "%s_%s" % (top.name, item.name), top.name)
    tree.body = lifted + tree.body
    ast.fix_missing_locations(tree)
    return specs


def convert_block_closures(tree):
    """Closure-convert nested functions the call-rewriting lift cannot handle:
    those defined inside a block (for/while/if/with/try) and used as a
    first-class value (e.g. asm_gen's `get_reg`, defined per-command inside a
    loop and handed to `command.make_asm`).

    Each such function is moved to file scope with its captured enclosing
    locals prepended as parameters; the original `def f(...)` is replaced by
    `f = __closure_env__("mangled", cap0, cap1, ...)`, which the emitter lowers
    to a make_closure carrying the captured values. The classic capture-via-
    default idiom (`_i=i`) is honoured: such params are folded into the
    captured environment (their value is the default expression at def time),
    while params with constant/no defaults (`pref=None`) stay caller-supplied.

    Returns {mangled: (lifted_node, n_caps, real_default_nodes)}.
    """
    specs = {}
    lifted = []
    used = {f.name for f in tree.body if isinstance(f, ast.FunctionDef)}

    def process(fn, prefix, cls):
        toplevel = {id(s) for s in fn.body}   # handled by the other lift
        encl = {a.arg for a in fn.args.args} | _assigned_names(fn)
        if fn.args.vararg:
            encl.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            encl.add(fn.args.kwarg.arg)

        class T(ast.NodeTransformer):
            def visit_FunctionDef(self, sub):
                if sub is fn or id(sub) in toplevel:
                    self.generic_visit(sub)
                    return sub
                if any(isinstance(n, ast.Nonlocal) for n in ast.walk(sub)):
                    return sub                 # rebinds enclosing var; skip
                return self.closure(sub)

            def closure(self, sub):
                params = sub.args.args
                defaults = sub.args.defaults
                dmap = {len(params) - len(defaults) + k: d
                        for k, d in enumerate(defaults)}
                defcap, real, real_defs = [], [], []
                for idx, p in enumerate(params):
                    d = dmap.get(idx)
                    if d is not None and not isinstance(d, ast.Constant):
                        defcap.append((p.arg, d))      # `_i=i` -> capture
                    else:
                        real.append(p)
                        real_defs.append(d)
                # free vars read in the BODY (not in default expressions)
                bound = {a.arg for a in params} | _assigned_names(sub)
                body_used = set()
                for st in sub.body:
                    for n in ast.walk(st):
                        if isinstance(n, ast.Name) and \
                                isinstance(n.ctx, ast.Load):
                            body_used.add(n.id)
                fv = [nm for nm in sorted(body_used & encl) if nm not in bound]
                cap_names = list(fv) + [nm for nm, _ in defcap]
                cap_vals = [ast.Name(id=nm, ctx=ast.Load()) for nm in fv] + \
                    [d for _, d in defcap]
                n_caps = len(cap_names)
                orig_name = sub.name
                mangled = "%s__%s" % (prefix, orig_name)
                base, k = mangled, 2
                while mangled in used:    # avoid colliding with the call-rewrite
                    mangled = "%s__%d" % (base, k)   # lift or a same-named sibling
                    k += 1
                used.add(mangled)
                new_args = []
                for nm in cap_names:
                    ann = ast.Name(id=(cls if (nm == "self" and cls)
                                       else "object"), ctx=ast.Load())
                    new_args.append(ast.arg(arg=nm, annotation=ann))
                sub.name = mangled
                sub.args.args = new_args + real
                sub.args.defaults = []
                sub.args.kwonlyargs = []
                sub.args.kw_defaults = []
                sub.decorator_list = []
                self.generic_visit(sub)        # handle any deeper nesting
                lifted.append(sub)
                specs[mangled] = (sub, n_caps, real_defs)
                marker = ast.Call(
                    func=ast.Name(id="__closure_env__", ctx=ast.Load()),
                    args=[ast.Constant(value=mangled)] + cap_vals,
                    keywords=[])
                return ast.Assign(
                    targets=[ast.Name(id=orig_name, ctx=ast.Store())],
                    value=marker)

        T().visit(fn)

    for top in list(tree.body):
        if isinstance(top, ast.FunctionDef):
            process(top, top.name, None)
        elif isinstance(top, ast.ClassDef):
            for item in top.body:
                if isinstance(item, ast.FunctionDef):
                    process(item, "%s_%s" % (top.name, item.name), top.name)
    tree.body = lifted + tree.body
    ast.fix_missing_locations(tree)
    return specs


def _const_dict_specializable(dnode):
    """Whether emit_const_dict has a fast-path for this dict literal
    (str -> list[str], or int -> str). Others become obj class-statics."""
    keys, vals = dnode.keys, dnode.values
    if not keys:
        return False
    str_keys = all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                   for k in keys)
    int_keys = all(isinstance(k, ast.Constant) and isinstance(k.value, int)
                   for k in keys)
    list_vals = all(isinstance(v, ast.List) for v in vals)
    str_vals = all(isinstance(v, ast.Constant) and isinstance(v.value, str)
                   for v in vals)
    return (str_keys and list_vals) or (int_keys and str_vals)


def collect_classes(tree):
    classes = {}
    order = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            ci = ClassInfo(node)
            classes[ci.name] = ci
            order.append(ci)
    for ci in order:
        if ci.base_name in classes:
            ci.base = classes[ci.base_name]
    for ci in order:
        for item in ci.node.body:
            if isinstance(item, ast.FunctionDef):
                ci.methods[item.name] = item
                if any(isinstance(d, ast.Name) and d.id == "staticmethod"
                       for d in item.decorator_list):
                    ci.static_methods.add(item.name)
                if any(isinstance(d, ast.Name) and d.id == "classmethod"
                       for d in item.decorator_list):
                    ci.classmethod_methods.add(item.name)
                if any(isinstance(d, ast.Name) and d.id == "property"
                       for d in item.decorator_list):
                    ci.property_methods.add(item.name)
            elif isinstance(item, ast.Assign) and len(item.targets) == 1 \
                    and isinstance(item.targets[0], ast.Name):
                nm, val = item.targets[0].id, item.value
                if isinstance(val, ast.Dict) and _const_dict_specializable(val):
                    ci.const_dicts[nm] = val
                elif isinstance(val, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
                    ci.class_statics[nm] = val
                elif isinstance(val, ast.Name) and val.id in ci.methods:
                    pass  # method alias, e.g. __rmul__ = __mul__
                elif nm.startswith("__") and nm.endswith("__"):
                    pass  # dunder method names are not instance fields
                else:
                    # class-level scalar (e.g. `comm = False`, `name = "add"`,
                    # `FInst_s = None`): a per-class default, possibly overridden
                    # in subclasses -> becomes an instance field set in the ctor
                    ci.class_attrs[nm] = val
        ci.own_fields = discover_fields(ci.node, set(ci.methods))
        for nm in ci.class_attrs:           # ensure they get a struct slot
            if not any(f == nm for f, _ in ci.own_fields):
                ci.own_fields.append((nm, OBJ))
        for nm in ci.class_statics:         # class statics accessed as self.X
            if not any(f == nm for f, _ in ci.own_fields):
                ci.own_fields.append((nm, OBJ))
    vt = set()
    for ci in order:
        root = ci.root()
        for mname in ci.methods:
            if mname == "__init__" or (mname.startswith("__") and
                                       mname.endswith("__")):
                continue
            if mname in ci.static_methods:   # @staticmethod: not virtual
                continue
            if mname in ci.property_methods:  # @property: not virtual
                continue
            if mname in root.methods:
                vt.add(mname)
    discover_fields_from_ctor_locals(tree, classes)
    return classes, order, vt


def _ctor_class_name(val, classes):
    """Class name from `Cls()` / `Cls(...)` if `Cls` is a known local class."""
    if not isinstance(val, ast.Call):
        return None
    f = val.func
    if isinstance(f, ast.Name) and f.id in classes:
        return f.id
    return None


def discover_fields_from_ctor_locals(tree, classes):
    """Discover instance fields from `var = Cls(); var.attr = ...` patterns."""

    def add_field(clsname, attr):
        ci = classes.get(clsname)
        if ci is None:
            return
        if not any(f == attr for f, _ in ci.own_fields):
            ci.own_fields.append((attr, OBJ))

    def scan_scope(body):
        locals_types = {}
        for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and \
                    isinstance(sub.targets[0], ast.Name):
                cls = _ctor_class_name(sub.value, classes)
                if cls:
                    locals_types[sub.targets[0].id] = cls
        for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
            if not isinstance(sub, ast.Assign):
                continue
            for tgt in sub.targets:
                if isinstance(tgt, ast.Attribute) and \
                        isinstance(tgt.value, ast.Name):
                    vn = tgt.value.id
                    if vn == "self":
                        continue
                    if vn in locals_types:
                        add_field(locals_types[vn], tgt.attr)

    scan_scope(tree.body)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            scan_scope(node.body)


def discover_fields(classnode, method_names=None):
    fields = []
    seen = set()
    method_names = method_names or set()

    def add(name, ctype):
        if name not in seen:
            seen.add(name)
            fields.append((name, ctype))

    for item in classnode.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target,
                                                          ast.Name) \
                and not isinstance(item.value, ast.Dict):
            add(item.target.id, infer_type(item.target.id, item.annotation))
    for item in classnode.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        opt = optional_param_names(item)
        param_ann = {a.arg: a.annotation
                     for a in item.args.args if a.annotation is not None}
        for sub in ast.walk(item):
            targets, ann = [], None
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, ast.AnnAssign):
                targets, ann = [sub.target], sub.annotation
            elif isinstance(sub, ast.Attribute) and \
                    isinstance(sub.value, ast.Name) and sub.value.id == "self":
                # reads of self.attr in methods (e.g. abstract base expects
                # subclass-provided _iv, block_size, digest_size)
                if sub.attr not in method_names:
                    add(sub.attr, infer_type(sub.attr, None))
            else:
                continue
            for tgt in targets:
                # `self.a, self.b = ...` -> the target is a Tuple/List of
                # Attributes; flatten so each self.<attr> is discovered.
                subtgts = (tgt.elts if isinstance(tgt, (ast.Tuple, ast.List))
                           else [tgt])
                for st in subtgts:
                    if isinstance(st, ast.Attribute) and \
                            isinstance(st.value, ast.Name) and \
                            st.value.id == "self":
                        # self.x = <optional param>  ->  obj
                        if ann is None and isinstance(sub, ast.Assign) and \
                                isinstance(sub.value, ast.Name) and \
                                sub.value.id in opt:
                            add(st.attr, OBJ)
                        # self.x = <annotated param>  ->  the param's type
                        elif ann is None and isinstance(sub, ast.Assign) and \
                                isinstance(sub.value, ast.Name) and \
                                sub.value.id in param_ann:
                            add(st.attr, infer_type(st.attr,
                                                    param_ann[sub.value.id]))
                        else:
                            add(st.attr, infer_type(st.attr, ann))
    return fields


# ==========================================================================
# Transpiler
# ==========================================================================

class Unsupported(Exception):
    pass


C_KEYWORDS = {"int", "char", "short", "long", "float", "double", "void",
              "struct", "union", "enum", "const", "register", "static",
              "return", "if", "else", "while", "for", "switch", "default",
              "auto", "extern", "signed", "unsigned", "volatile", "goto",
              # runtime type/identifier names that must not be shadowed
              "obj", "Obj", "List", "Dict", "TypeInfo"}

# Method names that collide with per-class `TypeInfo <Class>_type` symbols.
METHOD_TYPEINFO_COLLISION = frozenset({"type"})

# Runtime API symbols that Python stdlib modules may reuse as function names.
RUNTIME_API_NAMES = {
    "make_closure", "call_closure", "subscript", "subscript_set",
    "dict_get", "dict_set", "dict_new", "list_new", "list_of", "truthy",
    "obj_add", "obj_neg", "aalloc", "identity__tramp", "call_obj",
}

# Module-level helper functions whose Python body is not transpilable (they use
# the stdlib `struct` module) but which the runtime provides directly. Their
# `def` is skipped and calls are lowered to the runtime function.
RUNTIME_INTRINSICS = {"_float_to_bits"}

# Bare names routed through mp_call_import("builtins", ...) in stdlib mode.
STDLIB_BUILTINS = {
    "open", "next", "globals", "locals", "type", "__import__", "dir", "round",
    "iter", "callable", "hex", "oct", "bin", "hash", "id", "input", "eval",
    "exec", "compile", "format", "help", "memoryview", "bytearray", "bytes",
    "super", "property", "staticmethod", "classmethod", "object",
    "map", "filter", "zip", "enumerate", "reversed", "sorted", "sum", "min",
    "max", "any", "all", "print", "len", "range", "list", "dict", "set",
    "divmod", "issubclass", "setattr", "tuple", "hasattr", "getattr",
    "int", "float", "bool", "str", "Ellipsis",
    "complex",
    "frozenset",
}

# Exception / warning types resolved from the builtins module.
EXCEPTION_NAMES = {
    "BaseException", "Exception", "StopIteration", "StopAsyncIteration",
    "GeneratorExit", "SystemExit", "KeyboardInterrupt",
    "ArithmeticError", "AssertionError", "AttributeError", "BufferError",
    "EOFError", "ImportError", "LookupError", "MemoryError", "NameError",
    "OSError", "ReferenceError", "RuntimeError", "SyntaxError",
    "SystemError", "TypeError", "ValueError", "Warning", "UserWarning",
    "DeprecationWarning", "PendingDeprecationWarning", "RuntimeWarning",
    "FutureWarning", "ImportWarning", "UnicodeWarning", "BytesWarning",
    "ResourceWarning", "BlockingIOError", "BrokenPipeError", "ChildProcessError",
    "ConnectionError", "ConnectionAbortedError", "ConnectionRefusedError",
    "ConnectionResetError", "FileExistsError", "FileNotFoundError",
    "InterruptedError", "IsADirectoryError", "NotADirectoryError",
    "PermissionError", "ProcessLookupError", "TimeoutError",
    "IndexError", "KeyError", "ModuleNotFoundError", "NotImplementedError",
    "OverflowError", "RecursionError", "UnicodeError", "UnicodeDecodeError",
    "UnicodeEncodeError", "UnicodeTranslateError", "ZeroDivisionError",
    "EnvironmentError", "IOError", "WindowsError", "FloatingPointError",
    "IndentationError", "TabError", "UnboundLocalError",
}

TYPEINFO_RESERVED = {"name", "base"}

# stdio/unistd macros that collide with common Python module-level names.
C_STDIO_MACRO_NAMES = frozenset({
    "SEEK_SET", "SEEK_CUR", "SEEK_END", "EOF",
})


def cname(name):
    return name + "_" if name in C_KEYWORDS or name in RUNTIME_API_NAMES else name


def method_cname(mname):
    return mname + "_" if mname in METHOD_TYPEINFO_COLLISION else mname


def vslot_name(mname):
    n = cname(mname)
    return "vt_" + n if n in TYPEINFO_RESERVED else n


def c_mod_slug(name):
    return name.replace(".", "_").replace("-", "_")


_AMBIG_CACHE = {}


def ambiguous_class_names(base_dir):
    """Class names defined in more than one shivyc module. Such names cannot use
    a bare C symbol (it would collide at link time, e.g. tree.Mult vs the il_cmd
    Mult), so they are module-qualified. Computed once over the whole package so
    that every separately-compiled module agrees, even ones that import neither
    side of a collision."""
    if base_dir in _AMBIG_CACHE:
        return _AMBIG_CACHE[base_dir]
    name2mods = {}
    pkg = os.path.join(base_dir or ".", "shivyc")
    for dp, _dirs, fns in os.walk(pkg):
        if "musl" in dp.split(os.sep):
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            mod = os.path.relpath(p, base_dir or ".")[:-3].replace(os.sep, ".")
            try:
                t = ast.parse(open(p, encoding="utf-8").read())
            except Exception:
                continue
            for n in t.body:
                if isinstance(n, ast.ClassDef):
                    name2mods.setdefault(n.name, set()).add(mod)
    amb = {n for n, mods in name2mods.items() if len(mods) > 1}
    _AMBIG_CACHE[base_dir] = amb
    return amb


def class_csym(name, modname, ambiguous):
    """C base symbol for class `name` defined in `modname`: bare when unique
    across the package, else module-qualified so collisions get distinct
    symbols."""
    if name in ambiguous and modname:
        return "%s__%s" % (c_mod_slug(modname), name)
    return name


class Transpiler:
    def __init__(self, modname, base_dir=None, stdlib_root=None,
                 py_modname=None):
        self.modname = modname
        self.cmod = c_mod_slug(modname)   # C-safe form (e.g. for _init)
        self.py_modname = py_modname      # dotted module name (stdlib only)
        self.base_dir = base_dir    # repo dir containing the shivyc/ package
        self.stdlib_root = os.fspath(stdlib_root) if stdlib_root else None
        self.stdlib_index = build_stdlib_index(self.stdlib_root) \
            if self.stdlib_root else {}
        self.lines = []
        self.cur_class = None
        self.modules = set()
        self.import_alias = {}      # alias -> full dotted module name
        self.from_imports = {}      # imported name -> full dotted module name
        self.star_import_mods = []  # modules imported via `from X import *`
        self.mod_global_types = {}  # module global name -> ctype
        self.used_imports = set()   # (modname, name) actually referenced
        self.mod_const_types = {}
        self.func_nodes = {}
        self.singletons = []
        self.singleton_names = {}   # var -> ClassName (module-level instances)
        self.str_sets = {}          # module-level set/list of string literals
        self.func_returns = {}      # top-level function name -> return ctype
        self.func_values_needed = set()  # functions used as first-class values
        self.class_values_needed = set()  # classes used as constructor values
        self.func_params = {}       # top-level function name -> [param ctypes]
        self.scope = {}             # local/param name -> ctype (per function)
        self.narrowed = {}          # name -> ctype, active in an isinstance block
        self.hoisted = set()        # locals declared at function top
        self.cur_ret = OBJ          # current function's return ctype
        self.loop_n = 0             # unique-id counter for generated loops
        self.indent = 0

    def emit(self, line=""):
        self.lines.append(("    " * self.indent + line) if line else "")

    def _fs(self):
        """File-local linkage (unused; stdlib uses module-qualified symbols)."""
        return ""

    def _msym(self, name):
        """Module-qualified C symbol for stdlib shared-library linkage."""
        if self.stdlib_root:
            return "%s__%s" % (self.cmod, cname(name))
        return cname(name)

    def fnsym(self, name):
        """C symbol for a module-level function."""
        if self.stdlib_root and name in self.func_nodes:
            return "%s__%s" % (self.cmod, cname(name))
        return cname(name)

    def pname(self, name):
        """C parameter name: avoid typedef / keyword collisions."""
        if name in C_KEYWORDS or name in RUNTIME_API_NAMES \
                or name in self.class_typedef_names:
            return name + "_"
        return name

    def lid(self, name):
        """C identifier for a local/param in the current function."""
        if name in self.scope:
            return self.pname(name)
        return cname(name)

    # ---- driver ----------------------------------------------------------

    def run(self, tree):
        global KNOWN_CLASSES, VTABLE_METHODS
        rewrite_class_lambdas(tree)
        if self.stdlib_root:
            rewrite_module_lambdas(tree)
        self.closure_specs = lift_nested_functions(tree)
        self.closure_specs.update(convert_block_closures(tree))
        self.closure_values_needed = set()
        self.classes, self.class_order, vt = collect_classes(tree)
        self.ambiguous = ambiguous_class_names(self.base_dir)
        for ci in self.class_order:     # qualify local colliding class symbols
            ci.csym = class_csym(ci.name, self.modname, self.ambiguous)
        self.class_typedef_names = {ci.csym for ci in self.class_order}
        KNOWN_CLASSES = self.classes
        VTABLE_METHODS = vt
        self.build_owner_maps()
        self.collect_imports(tree)
        # cross-module class registry: clsname -> (ClassInfo, modname)
        self.xclasses = {}
        for modname in set(self.import_alias.values()) | \
                set(self.from_imports.values()):
            reg = self.load_xmod(modname)
            if reg:
                for cn, ci in reg["classes"].items():
                    ci.csym = class_csym(cn, modname, self.ambiguous)
                    self.xclasses.setdefault(cn, (ci, modname))
        # transitively pull in imported base classes (e.g. a subclass module
        # imports ILCommand from il_cmds.base) so the hierarchy root is known
        for _ in range(8):           # fixpoint; depth is tiny in practice
            added = False
            for cn, (ci, mod) in list(self.xclasses.items()):
                bn = ci.base_name
                if not bn or bn in self.xclasses:
                    continue
                src = self.load_xmod(mod)["imports"].get(bn)
                if not src:
                    continue
                breg = self.load_xmod(src)
                if breg and bn in breg["classes"]:
                    self.xclasses.setdefault(bn, (breg["classes"][bn], src))
                    added = True
            if not added:
                break
        self.used_xmethods = {}     # (clsname, method) -> return ctype
        self.xstructs_needed = set()  # imported classes whose fields are read
        self.xvt_needed = set()     # imported modules needing a VT struct emitted
        self.xtype_externs = set()  # imported TypeInfo singletons (Cls_type)
        self.xvtable_impls = set()  # (clsname, method) imported vtable slot impls
        self.xconstdict_externs = set()  # (clsname, dict) imported const-dict fns
        self.xclass_module = {}     # imported class name -> its module
        for cn, (ci, mod) in self.xclasses.items():
            self.xclass_module[cn] = mod
        self.xmethod_owners = {}    # method name -> [imported ClassInfo] (sep.
                                    # from method_owners so runtime-helper guards
                                    # are unaffected)
        for cn, (ci, _m) in self.xclasses.items():
            if cn in self.classes:
                continue
            for fn, _ in ci.own_fields:
                self.field_owners.setdefault(fn, []).append(ci)
            for m in ci.methods:
                if m != "__init__":
                    self.xmethod_owners.setdefault(m, []).append(ci)
        self.link_cross_module_hierarchy(vt)
        # cross-module-hierarchy dispatch: imported roots whose hierarchy spans
        # modules. Any of the root's interface methods can be dispatched through
        # the root module's (canonical) vtable layout.
        self.hierarchy_method = {}   # method -> root module (for xvcall)
        for cn, (ci, mod) in self.xclasses.items():
            if ci.base is not None:          # not a root
                continue
            subs = [c for c, (c2, _m) in self.xclasses.items()
                    if c2 is not ci and c2.root() is ci]
            if subs:
                for m in ci.methods:
                    if not (m.startswith("__") and m.endswith("__")):
                        self.hierarchy_method.setdefault(m, mod)
            else:
                # An imported polymorphic root whose subclasses live in modules
                # this one never imports -- e.g. asm_gen calls the ILCommand
                # interface (inputs/outputs/targets/...) on IL commands it
                # receives but never constructs. The root's own module emits the
                # canonical vtable, so dispatch its virtual methods through that.
                reg = self.load_xmod(mod)
                if reg:
                    for m in reg["vt"]:
                        self.hierarchy_method.setdefault(m, mod)
        self.collect_module_globals(tree)

        self.prelude()
        # forward typedefs so the TypeInfo vtable can mention class pointers
        for ci in self.class_order:
            self.emit("typedef struct %s %s;" % (ci.csym, ci.csym))
        # imported classes used as local field types need a typedef too (and
        # their struct body, via xstructs_needed, for member access). This is
        # transitive: an imported field type (ILValue) may itself have imported
        # field types (CType) -- and those may live in a module this one never
        # imported, so we resolve and load them on demand.
        imported_field_types = set()
        # work items: (ClassInfo, its_module_name)
        work = [(ci, self.modname) for ci in self.class_order]
        seen_cls = set()
        while work:
            ci, ci_mod = work.pop()
            if ci.name in seen_cls:
                continue
            seen_cls.add(ci.name)
            for _fn, ft in ci.full_fields():
                if not ft.endswith("*"):
                    continue
                base = ft[:-1]
                if base in self.classes or base == ci.name:
                    continue
                if base not in self.xclasses:
                    # resolve `base`'s defining module via ci's imports, then
                    # register the WHOLE module's classes (so sibling subclasses
                    # are available for downcasts / polymorphic dispatch).
                    reg = self.load_xmod(ci_mod) if ci_mod else None
                    src = reg["imports"].get(base) if reg else None
                    breg = self.load_xmod(src) if src else None
                    if breg and base in breg["classes"]:
                        for bn, bci in breg["classes"].items():
                            self.xclasses.setdefault(bn, (bci, src))
                            self.xclass_module.setdefault(bn, src)
                    else:
                        continue
                if base not in imported_field_types:
                    imported_field_types.add(base)
                    bci, bmod = self.xclasses[base]
                    work.append((bci, bmod))
        # virtual-return typing: a method whose annotated return is a leaf
        # class may have its (obj-ABI) result recovered as a typed pointer at a
        # call site in this module (see ex_Call), so ensure that class's struct
        # is declared. Scan reachable method return annotations up front, since
        # struct typedefs are emitted here, before any method body.
        for _ci in list(self.classes.values()) + \
                [c for (c, _m) in self.xclasses.values()]:
            for _fn in _ci.methods.values():
                _rt = ann_to_ctype(_fn.returns) if _fn.returns else None
                if _rt and self._is_class_ptr(_rt):
                    _cn = _rt[:-1]
                    if _cn not in self.classes and _cn in self.xclasses \
                            and self._class_is_leaf(_cn):
                        imported_field_types.add(_cn)
        for c in sorted(imported_field_types):
            self.emit("typedef struct %s %s;" % (c, c))
            self.xstructs_needed.add(c)
        if self.class_order:
            self.emit()
        self.emit_typeinfo_struct()
        for ci in self.class_order:
            self.emit("extern const TypeInfo %s_type;" % ci.csym)
        if self.class_order:
            self.emit()
        for ci in self.class_order:
            self.emit_struct(ci)
        self.extern_idx = len(self.lines)   # cross-module externs go here
        self.emit_forward_decls(tree)
        for ci in self.class_order:
            self.emit_class_impl(ci)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                continue
            if _is_dunder_main_guard(node):
                continue            # script-entry guard; C entry is separate
            self.toplevel(node)
        self.emit_module_init()
        self.emit_trampolines()
        # insert cross-module extern declarations at the reserved point
        externs = self.build_externs()
        tramps = ["static obj %s__tramp(obj, obj);" % self.fnsym(fn)
                  for fn in sorted(self.func_values_needed)]
        tramps += ["static obj %s__tramp(obj, obj);" % cname(m)
                   for m in sorted(self.closure_values_needed)]
        tramps += ["static obj %s__ctortramp(obj, obj);" % cls
                   for cls in sorted(set(self.class_values_needed) |
                                     {ci.csym for ci in self.class_order})]
        self.lines[self.extern_idx:self.extern_idx] = externs + tramps
        return "\n".join(self.lines) + "\n"

    def struct_body_lines(self, ci):
        """Tag definition `struct C { ... };` for an imported class whose
        fields are accessed (the typedef is forward-declared separately)."""
        out = ["struct %s {" % ci.csym, "    Obj _hdr;"]
        ff = ci.full_fields()
        if not ff:
            out.append("    char _empty;")
        for fn, ft in ff:
            out.append("    %s %s;" % (ft, self.fnsym(fn)))
        out.append("};")
        return out

    def _load_xclass_anywhere(self, cls):
        """Best-effort: register imported class `cls` by searching the modules
        reachable from this one's imports (transitively). Used when a concrete
        pointer type (e.g. CType* inferred from a `.ctype` access) names a class
        whose defining module was never directly imported here."""
        if cls in self.xclasses or cls in self.classes:
            return cls in self.xclasses
        roots = set(self.import_alias.values()) | set(self.from_imports.values())
        seen, work = set(), list(roots)
        while work:
            mod = work.pop()
            if mod in seen:
                continue
            seen.add(mod)
            reg = self.load_xmod(mod)
            if not reg:
                continue
            if cls in reg["classes"]:
                src = mod
                breg = reg
                for bn, bci in breg["classes"].items():
                    self.xclasses.setdefault(bn, (bci, src))
                    self.xclass_module.setdefault(bn, src)
                return True
            work += list(reg["imports"].values())
        return False

    def _load_missing_xclass(self, base, from_mod):
        """Ensure imported class `base` is registered in xclasses, loading its
        whole defining module (resolved via `from_mod`'s imports) on demand.
        Returns True if `base` is available afterwards."""
        if base in self.classes:
            return False
        if base in self.xclasses:
            return True
        reg = self.load_xmod(from_mod) if from_mod else None
        src = reg["imports"].get(base) if reg else None
        breg = self.load_xmod(src) if src else None
        if breg and base in breg["classes"]:
            for bn, bci in breg["classes"].items():
                self.xclasses.setdefault(bn, (bci, src))
                self.xclass_module.setdefault(bn, src)
            return True
        return False

    def build_externs(self):
        classes, funcs, singles, globs = set(), {}, {}, {}
        for (mod, name) in sorted(self.used_imports):
            kind, info = self.resolve_import(name, mod)
            if kind == "class" and name not in self.classes:
                classes.add(name)
            elif kind == "func" and name not in self.func_params:
                funcs[name] = ann_to_ctype(info.returns) or OBJ
            elif kind == "singleton":
                singles[name] = info
                if info not in self.classes:
                    classes.add(info)
            elif kind == "global":
                globs[name] = info
        for (cls, meth) in self.used_xmethods:
            if cls not in self.classes:
                classes.add(cls)
        needed = {c for c in self.xstructs_needed
                  if c in self.xclasses and c not in self.classes}
        classes |= needed
        # transitive field-type dependencies of accessed struct bodies. A body
        # like ILValue's names CType*, which may live in a module this one never
        # imported -- load it on demand so its forward typedef can be emitted.
        dep_work = list(needed)
        dep_seen = set()
        while dep_work:
            c = dep_work.pop()
            if c in dep_seen or c not in self.xclasses:
                continue
            dep_seen.add(c)
            ci, cmod = self.xclasses[c]
            for _, ft in ci.full_fields():
                if not ft.endswith("*"):
                    continue
                base = ft[:-1]
                if base in self.classes:
                    continue
                if base not in self.xclasses and \
                        not self._load_missing_xclass(base, cmod):
                    continue
                classes.add(base)
                dep_work.append(base)
        vt_fwd = set()              # classes named only in VT slot signatures
        for mod in self.xvt_needed:
            reg = self.load_xmod(mod)
            for m in reg["vt"]:
                ret, params = self.ximported_method_sig(mod, m)
                for ct in [ret] + params:
                    base = ct.rstrip("*")
                    if base in self.xclasses and base not in self.classes:
                        vt_fwd.add(base)
        if not (classes or funcs or singles or globs or self.used_xmethods or
                self.xvt_needed or self.xtype_externs or self.xvtable_impls or
                self.xconstdict_externs):
            return []
        out = ["/* ---- cross-module imports (extern declarations) ---- */"]
        for c in sorted(classes | (vt_fwd - classes)):  # forward typedefs first
            cs = self.xcsym(c)
            out.append("typedef struct %s %s;" % (cs, cs))
        for c in sorted(needed):            # full layout for accessed classes
            out += self.struct_body_lines(self.xclasses[c][0])
        for c in sorted(classes):
            # a class that is also a cross-module hierarchy base gets a full
            # `extern const TypeInfo c_type;` below; emitting a TypeInfoHdr one
            # here too would conflict, so emit only the constructor for it.
            cs = self.xcsym(c)
            if c not in self.xtype_externs:
                out.append("extern const TypeInfoHdr %s_type;" % cs)
            out.append("extern %s* %s_new();" % (cs, cs))
        for n in sorted(funcs):
            out.append("extern %s %s();" % (funcs[n], n))
        for n in sorted(singles):
            out.append("extern %s* %s;" % (singles[n], cname(n)))
        for n in sorted(globs):
            out.append("extern %s %s;" % (globs[n], cname(n)))
        for (cls, meth) in sorted(self.used_xmethods):
            ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                           if cls in self.xclasses else None)
            fn = ci.methods.get(meth) if ci else None
            if fn is not None and meth == "__init__":
                ip = self._init_param_list(fn, skip_self=True)
                plist = ", ".join(["%s* self" % self.xcsym(cls)] + ip)
                out.append("extern void %s_%s(%s);" % (self.xcsym(cls), meth,
                                                       plist))
            else:
                out.append("extern %s %s_%s();" % (
                    self.used_xmethods[(cls, meth)], self.xcsym(cls), meth))
        for (cls, meth) in sorted(self.xvtable_impls):  # imported vtable slots
            if (cls, meth) not in self.used_xmethods:
                out.append("extern %s %s_%s();" % (
                    self.ximported_method_sig(self.xclass_module.get(cls), meth)[0]
                    if self.xclass_module.get(cls) else OBJ,
                    self.xcsym(cls), meth))
        for cls in sorted(self.xtype_externs):          # imported TypeInfo
            out.append("extern const TypeInfo %s_type;" % self.xcsym(cls))
        for (cls, d) in sorted(self.xconstdict_externs):  # imported const-dicts
            out.append("extern str %s_%s();" % (self.xcsym(cls), d))
        for mod in sorted(self.xvt_needed):     # replicated TypeInfo layouts
            reg = self.load_xmod(mod)
            vt = self.vt_struct_name(mod)
            out.append("typedef struct %s {" % vt)
            out.append("    const char* name;")
            out.append("    const struct %s* base;" % vt)
            for m in sorted(reg["vt"]):
                ret, params = self.ximported_method_sig(mod, m)
                out.append("    %s (*%s)(%s);" % (
                    ret, vslot_name(m), ", ".join(["Obj*"] + params)))
            out.append("} %s;" % vt)
        out.append("")
        return out

    def emit_forward_decls(self, tree):
        """Prototypes for top-level functions, plus file-scope declarations for
        module-level globals (initialized later in <module>_init)."""
        protos = []
        for ci in self.class_order:         # method + constructor prototypes
            if "__init__" not in ci.methods:
                ni = self._nearest_init(ci)  # inherit-only ctor still gets _new
                if ni is not None:
                    _, fn = ni
                    if fn.args.vararg:
                        vn = fn.args.vararg.arg
                        cargs = "int _n_%s, ..." % vn
                    else:
                        cargs = ", ".join(self.param_list(fn, skip_self=True) +
                                          self._kwonly_param_list(fn)) or "void"
                    protos.append("%s* %s_new(%s);" % (ci.csym, ci.csym, cargs))
                else:
                    protos.append("%s* %s_new(void);" % (ci.csym, ci.csym))
            for mname, fn in ci.methods.items():
                if mname.startswith("__") and mname.endswith("__") \
                        and mname not in ("__init__", "__enter__", "__exit__"):
                    continue
                ret = self._c_ret(fn)
                if mname in ci.static_methods:   # @staticmethod: no self
                    sp = self.param_list(fn, skip_self=False)
                    protos.append("%s %s_%s(%s);" % (
                        ret, ci.csym, method_cname(mname),
                        ", ".join(sp) if sp else "void"))
                    continue
                params = self.param_list(fn, skip_self=True)
                if mname == "__init__":
                    init_params = self._init_param_list(fn, skip_self=True)
                    plist = ", ".join(["%s* self" % ci.csym] + init_params)
                    protos.append("void %s___init__(%s);" % (ci.csym, plist))
                    if fn.args.vararg:
                        vn = fn.args.vararg.arg
                        cargs = "int _n_%s, ..." % vn
                    else:
                        cargs = ", ".join(self.param_list(fn, skip_self=True) +
                                          self._kwonly_param_list(fn)) or "void"
                    protos.append("%s* %s_new(%s);" % (ci.csym, ci.csym, cargs))
                elif mname in VTABLE_METHODS:
                    vparams = self._vtable_c_param_list(fn)
                    protos.append("%s %s_%s(Obj* self_%s);" % (
                        ret, ci.csym, method_cname(mname),
                        (", " + ", ".join(vparams)) if vparams else ""))
                else:
                    plist = ", ".join(["%s* self" % ci.csym] + params)
                    protos.append("%s %s_%s(%s);" % (ret, ci.csym, method_cname(mname), plist))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and \
                    node.name not in RUNTIME_INTRINSICS and \
                    self.func_nodes.get(node.name) is node:
                protos.append(self.func_signature(node) + ";")
        if protos:
            self.emit("/* forward declarations */")
            for p in protos:
                self.emit(p)
            self.emit()
        if self.mod_globals:
            self.emit("/* module-level globals (init in %s_init) */"
                      % self.modname)
            for name, ctype, kind, val in self.mod_globals:
                if kind == "const":     # literal: define with initializer here
                    if name in C_STDIO_MACRO_NAMES:
                        self.emit("#undef %s" % name)
                    self.emit("%s %s = %s;" % (ctype, self._msym(name),
                                               self.expr(val)))
                else:                   # complex: declare now, init in _init()
                    self.emit("%s %s;" % (ctype, self._msym(name)))
            self.emit()
        statics = [(ci, nm) for ci in self.class_order
                   for nm in ci.class_statics]
        if statics:
            self.emit("/* class-level statics (obj; init in %s_init) */"
                      % self.modname)
            for ci, nm in statics:
                self.emit("obj %s_%s;" % (ci.csym, cname(nm)))
            self.emit()

    def func_signature(self, node):
        ret = ann_to_ctype(node.returns) or OBJ
        params = self.param_list(node, skip_self=False)
        plist = ", ".join(params) if params else "void"
        return "%s %s(%s)" % (ret, self.fnsym(node.name), plist)

    def resolve_import_module(self, node):
        """Absolute dotted module name for an ImportFrom node."""
        if node.level == 0:
            return node.module or ""
        if not self.py_modname:
            return node.module or ""
        parts = self.py_modname.split(".")
        base = parts[:max(0, len(parts) - node.level)]
        if node.module:
            base.extend(node.module.split("."))
        return ".".join(base)

    def collect_imports(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    alias = (a.asname or a.name).split(".")[0]
                    self.modules.add(alias)
                    self.import_alias[alias] = a.name
            elif isinstance(node, ast.ImportFrom):
                abs_mod = self.resolve_import_module(node)
                for a in node.names:
                    if a.name == "*":
                        if abs_mod:
                            self.star_import_mods.append(abs_mod)
                            reg = self.load_xmod(abs_mod)
                            if reg:
                                for n in reg["funcs"]:
                                    self.from_imports.setdefault(n, abs_mod)
                                for n in reg["classes"]:
                                    self.from_imports.setdefault(n, abs_mod)
                                for n in reg["globals"]:
                                    self.from_imports.setdefault(n, abs_mod)
                                for n in reg["consts"]:
                                    self.from_imports.setdefault(n, abs_mod)
                    else:
                        self.from_imports[a.asname or a.name] = abs_mod

    def ccls(self, name):
        """C base symbol for a class referenced by `name` in local context:
        the local class if one exists, else an imported class, else the bare
        name. (Explicitly cross-module sites use the xclass's .csym directly.)"""
        ci = self.classes.get(name)
        if ci is not None:
            return ci.csym
        ent = self.xclasses.get(name)
        if ent is not None:
            return ent[0].csym
        return class_csym(name, None, self.ambiguous)

    def ann_ctype(self, ann):
        """Annotation -> C type, extending ann_to_ctype with dotted
        `module.Class` hints (e.g. `il_gen.ILValue`, `decl_nodes.Root`).

        The dotted form is resolved to the referenced class's pointer type as a
        *bare* `Class*` so the usual ClassInfo lookup and emit-time csym
        qualification both keep working. Ambiguous class names are only typed
        when the dotted module is the local one; an ambiguous name imported from
        another module is declined (left as obj) because a bare `Class*` would
        bind to the wrong same-named class."""
        if ann is None:
            return None
        base = ann_to_ctype(ann)
        if base is not None:
            return base
        try:
            text = ast.unparse(ann).strip().strip("'\"")
        except Exception:
            return None
        if "[" in text or "." not in text:
            return None
        alias, _, cls = text.rpartition(".")
        if not (cls and (cls[0].isupper() or cls[0] == "_")
                and cls.isidentifier()):
            return None
        if cls not in self.classes and cls not in self.xclasses:
            return None                  # not a class whose fields we can resolve
        if cls in self.ambiguous:
            # bare `Class*` resolves locally; only safe if that is what the
            # dotted module actually names.
            if self.import_alias.get(alias) != self.modname:
                return None
        return cls + "*"

    def arg_ctype_q(self, fn, arg):
        """arg_ctype, but honoring dotted `module.Class` parameter annotations."""
        if arg.annotation is not None:
            t = self.ann_ctype(arg.annotation)
            if t is not None:
                return t
        return arg_ctype(fn, arg)

    def _is_class_ptr(self, ct):
        """True if `ct` is a pointer to a (local or imported) class struct."""
        return bool(ct) and ct.endswith("*") and ct != OBJ \
            and (ct[:-1] in self.classes or ct[:-1] in self.xclasses)

    def _logical_ret(self, fn):
        """Logical (typing) return type of a method's call result. A class
        return annotation is exposed to callers only when the class is a leaf
        (no subclasses) -- a non-leaf base could be any subclass at runtime, so
        statically typing the result to the base would make subclass-field
        access unsound; those stay obj. Scalars/char* pass through unchanged."""
        rt = (ann_to_ctype(fn.returns) or OBJ) if fn is not None else OBJ
        if rt and rt != OBJ and rt.endswith("*") and rt[0].isupper():
            cls = rt[:-1]
            if cls not in self.classes and cls not in self.xclasses:
                self._load_xclass_anywhere(cls)
            if (cls in self.classes or cls in self.xclasses) \
                    and self._class_is_leaf(cls):
                return rt
            return OBJ          # non-leaf or unresolvable here
        return rt               # scalars, char*

    def _c_ret(self, fn):
        """C/ABI return type of a method: a class-pointer return is emitted as
        `obj` so every dispatch path (direct, vtable, cross-module hierarchy)
        shares one uniform ABI -- even when the class is not imported in this
        module. The typed pointer is recovered at the call site via
        `_logical_ret` + an AS_OBJ cast (see ex_Call)."""
        rt = (ann_to_ctype(fn.returns) or OBJ) if fn is not None else OBJ
        if rt and rt != OBJ and rt.endswith("*") and rt[0].isupper():
            return OBJ
        return rt

    _CONTAINER_METHODS = {
        "strip", "lstrip", "rstrip", "upper", "lower", "replace", "split",
        "partition", "splitlines", "startswith", "endswith", "isdigit",
        "isalpha", "isspace", "isalnum", "find", "rfind", "join", "encode",
        "decode", "format", "count", "index", "append", "add", "update",
        "extend", "pop", "remove", "keys", "values", "items", "get", "discard",
        "sort", "copy", "setdefault", "insert", "clear", "reverse", "next"}

    def _exclusive_vt_module(self, attr):
        """Imported module whose canonical vtable provides `attr` when `attr` is
        defined in *exactly one* reachable module's class hierarchy and nowhere
        else (not locally, not in another module). Such a method is
        unambiguously that hierarchy's -- e.g. the CType predicates
        is_void/is_integral/is_pointer/... live only in `ctypes` -- so any call
        to it can dispatch through that module's vtable, correct for a non-leaf
        base and uniform across typed-pointer and bare-obj receivers. Cached."""
        cache = self.__dict__.setdefault("_excl_vt_cache", {})
        if attr in cache:
            return cache[attr]
        res = None
        # never hijack builtin / container method names (pop, get, append, ...)
        # which collide with list/dict/str operations on unrelated receivers.
        if attr in self._CONTAINER_METHODS:
            cache[attr] = None
            return None
        if not any(attr in ci.methods for ci in self.classes.values()):
            roots = set(self.import_alias.values()) | \
                set(self.from_imports.values())
            seen, work, defining, vt_has = set(), list(roots), set(), set()
            while work:
                mod = work.pop()
                if mod in seen:
                    continue
                seen.add(mod)
                reg = self.load_xmod(mod)
                if not reg:
                    continue
                if any(attr in ci.methods
                       for ci in reg["classes"].values()):
                    defining.add(mod)
                if attr in reg["vt"]:
                    vt_has.add(mod)
                work += list(reg["imports"].values())
            if len(defining) == 1:
                m = next(iter(defining))
                if m in vt_has:
                    res = m
        cache[attr] = res
        return res

    def _ximported_logical_ret(self, mod, mname):
        """Logical (leaf-class) return type of imported method `mname` defined
        in `mod`, used to type cross-module-dispatched call results whose slot
        ABI is obj. Returns obj when absent/non-leaf/non-class."""
        reg = self.load_xmod(mod)
        if reg:
            for ci in reg["classes"].values():
                fn = ci.methods.get(mname)
                if fn is not None:
                    return self._logical_ret(fn)
        return OBJ

    def ctype_csym(self, ft):
        """Rewrite a C type so a pointer to an ambiguous class uses that class's
        qualified symbol (e.g. 'Mult*' -> 'shivyc_..._Mult*')."""
        if ft.endswith("*") and ft[:-1] in self.ambiguous:
            return self.ccls(ft[:-1]) + "*"
        return ft

    def xcsym(self, name):
        """C base symbol for an *imported* class name (the cross-module one,
        even when a local class shares the name)."""
        ent = self.xclasses.get(name)
        if ent is not None:
            return ent[0].csym
        return class_csym(name, self.xclass_module.get(name), self.ambiguous)

    def load_xmod(self, modname):
        """Parse an imported shivyc module and register its public symbols."""
        cache = _XMOD_CACHE
        if modname in cache:
            return cache[modname]
        cache[modname] = None       # guard against import cycles
        reg = {"classes": {}, "funcs": {}, "singletons": {}, "vt": set(),
               "order": [], "imports": {}, "consts": {}, "globals": {}}
        path = None
        if self.base_dir and modname.startswith("shivyc"):
            path = os.path.join(self.base_dir, *modname.split(".")) + ".py"
        elif modname in self.stdlib_index:
            path = self.stdlib_index[modname]
        if path:
            try:
                t = ast.parse(open(path, encoding="utf-8").read())
                classes, order, vt = collect_classes(t)
                amb = ambiguous_class_names(self.base_dir)
                for cn, ci in classes.items():
                    ci.csym = class_csym(cn, modname, amb)
                reg["classes"] = classes
                reg["order"] = order
                reg["vt"] = vt
                for n in t.body:        # module-level constant globals
                    if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                            and isinstance(n.targets[0], ast.Name):
                        val = _const_value(n.value)
                        if val is not None:
                            reg["consts"][n.targets[0].id] = val
                imp_src = list(t.body)
                for n in t.body:        # descend into `if TYPE_CHECKING:`
                    if isinstance(n, ast.If) and isinstance(n.test, ast.Name) \
                            and n.test.id == "TYPE_CHECKING":
                        imp_src += list(n.body)
                for n in imp_src:        # name -> defining module (for base res.)
                    if isinstance(n, ast.ImportFrom) and n.module:
                        for a in n.names:
                            reg["imports"][a.asname or a.name] = n.module
                    elif isinstance(n, ast.Import):
                        for a in n.names:
                            reg["imports"][a.asname or a.name] = a.name
                for n in t.body:
                    if isinstance(n, ast.FunctionDef):
                        reg["funcs"][n.name] = n
                    elif isinstance(n, ast.Assign) and len(n.targets) == 1 \
                            and isinstance(n.targets[0], ast.Name) \
                            and isinstance(n.value, ast.Call):
                        f = n.value.func
                        cls = None
                        if isinstance(f, ast.Name):
                            if f.id in classes or (f.id[:1].isupper()):
                                cls = f.id
                        elif isinstance(f, ast.Attribute) and f.attr[:1].isupper():
                            cls = f.attr
                        if cls:
                            reg["singletons"][n.targets[0].id] = cls
                    elif isinstance(n, ast.Assign) and len(n.targets) == 1 \
                            and isinstance(n.targets[0], ast.Name) \
                            and isinstance(n.value, (ast.List, ast.Dict,
                                ast.Set, ast.Tuple, ast.BinOp, ast.ListComp,
                                ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                        # module-level obj global (e.g. xmm_arg_regs = [...] or
                        # registers = caller_saved + callee_saved), emitted
                        # unprefixed in its module as `obj <name>;`
                        reg["globals"][n.targets[0].id] = OBJ
            except (OSError, SyntaxError):
                pass
        cache[modname] = reg
        return reg

    def resolve_import(self, name, modname):
        """('class'|'func'|'singleton'|None, info) for `name` in `modname`."""
        reg = self.load_xmod(modname)
        if not reg:
            return (None, None)
        if name in reg["classes"]:
            return ("class", reg["classes"][name])
        if name in reg["funcs"]:
            return ("func", reg["funcs"][name])
        if name in reg["singletons"]:
            return ("singleton", reg["singletons"][name])
        if name in reg["globals"]:
            return ("global", reg["globals"][name])
        if name in reg["consts"]:
            return ("const", reg["consts"][name])
        return (None, None)

    def xref(self, name, modname):
        """Resolve an imported name; record it for extern emission."""
        kind, info = self.resolve_import(name, modname)
        if kind:
            self.used_imports.add((modname, name))
        return kind, info

    def build_owner_maps(self):
        """Which class declares each attribute/method. Because attributes are a
        fixed per-class set in this codebase, a `.attr` read off an untyped obj
        usually identifies the element's type uniquely -> a real struct offset."""
        self.field_owners = {}
        self.method_owners = {}
        for ci in self.class_order:
            for fn, _ in ci.own_fields:
                self.field_owners.setdefault(fn, []).append(ci)
            for m in ci.methods:
                if m == "__init__":
                    continue
                self.method_owners.setdefault(m, []).append(ci)

    def link_cross_module_hierarchy(self, local_vt):
        """When local classes extend an *imported* base, link the chain so
        find_method_owner/root() walk across the module boundary, and adopt the
        hierarchy root's full virtual interface as the canonical vtable layout
        (so every module in the hierarchy emits a byte-identical TypeInfo)."""
        global VTABLE_METHODS
        self.vt_root = None          # imported root ClassInfo of the hierarchy
        self.vt_root_mod = None
        for ci in self.class_order:  # link a local class's imported base
            if ci.base is None and ci.base_name in self.xclasses \
                    and ci.base_name not in self.classes:
                ci.base = self.xclasses[ci.base_name][0]
        for cn, (ci, _m) in self.xclasses.items():   # transitive imported links
            if ci.base is None and ci.base_name in self.xclasses:
                ci.base = self.xclasses[ci.base_name][0]
        roots = {}                   # external roots of local classes
        for ci in self.class_order:
            r = ci.root()
            if r is not ci and r.name in self.xclasses \
                    and r.name not in self.classes:
                roots[r.name] = r
        if len(roots) != 1:
            return                   # no / ambiguous external hierarchy
        rname, r = next(iter(roots.items()))
        self.vt_root = r
        self.vt_root_mod = self.xclass_module.get(rname)
        canon = {m for m in r.methods if not (m.startswith("__")
                                              and m.endswith("__"))}
        # The layout must match the root module's TypeInfo *exactly*, so it is
        # precisely the root's virtual interface. Module-private helpers stay
        # off the vtable (they are self-calls on concrete types).
        VTABLE_METHODS = canon

    def is_ancestor(self, a, b):
        c = b
        while c:
            if c is a:
                return True
            c = c.base
        return False

    def _resolve_owner(self, owners):
        if not owners:
            return None
        if len(owners) == 1:
            return owners[0]
        # if one owner is a base of all others, casting to it is offset-safe
        for cand in owners:
            if all(self.is_ancestor(cand, o) for o in owners):
                return cand
        return None

    def resolve_attr_owner(self, attr):
        return self._resolve_owner(self.field_owners.get(attr, []))

    def resolve_method_owner(self, attr):
        return self._resolve_owner(self.method_owners.get(attr, []))

    def resolve_xmethod_owner(self, attr):
        """Imported class declaring `attr`, only if it is the SOLE definer.

        Multiple definers means the method is overridden (polymorphic); calling
        any one statically would dispatch to the wrong override at runtime, and
        cross-module vtables aren't available, so we decline rather than
        miscompile."""
        if attr in self.method_owners:     # a local method of the same name wins
            return None
        if attr in self.hierarchy_method:  # virtual via a canonical cross-module
            return None                    # vtable; never bind to one (base) impl
        owners = self.xmethod_owners.get(attr, [])
        return owners[0] if len(owners) == 1 else None

    @staticmethod
    def vt_struct_name(modname):
        return "VT_" + modname.replace(".", "_")

    def _class_is_leaf(self, clsname):
        """True if no known class (local or imported) derives from clsname."""
        for ci in self.classes.values():
            if ci.base_name == clsname:
                return False
        for cn, (ci, _m) in self.xclasses.items():
            if ci.base_name == clsname:
                return False
        return True

    def resolve_xvirtual(self, attr):
        """A polymorphic imported method `attr` is dispatchable via a
        cross-module vtable iff all its definers live in ONE module and it is
        virtual there. Returns that module name, else None."""
        if attr in self.method_owners or attr in VTABLE_METHODS:
            return None
        owners = self.xmethod_owners.get(attr, [])
        if len(owners) < 2:                # single/zero -> handled elsewhere
            return None
        mods = {self.xclass_module.get(o.name) for o in owners}
        if len(mods) != 1:
            return None
        mod = next(iter(mods))
        reg = self.load_xmod(mod)
        return mod if reg and attr in reg["vt"] else None

    def ximported_method_sig(self, mod, mname):
        """(ret_ctype, [param_ctypes]) for method `mname` in imported `mod`,
        replicating that module's emitted slot signature."""
        reg = self.load_xmod(mod)
        for ci in reg["order"]:
            fn = ci.methods.get(mname)
            if fn:
                ret = self._c_ret(fn)
                params = [arg_ctype(fn, a) for a in fn.args.args[1:]]
                return ret, params
        return OBJ, []

    def ximported_method_fn(self, mod, mname):
        """The AST FunctionDef for method `mname` in imported `mod`, or None
        (used to recover default-argument values for vtable calls)."""
        reg = self.load_xmod(mod)
        for ci in reg["order"]:
            fn = ci.methods.get(mname)
            if fn:
                return fn
        return None

    def xvcall(self, mod, recv_node, mname, arg_nodes):
        """Cross-module virtual call: index the defining module's TypeInfo
        layout (replicated locally as a VT struct) through the object header."""
        fn = self.ximported_method_fn(mod, mname)
        if self.stdlib_root:
            return self._mp_method_call_args(recv_node, mname, arg_nodes, fn)
        if self._method_has_varargs(fn) or \
                len(arg_nodes) > len(self.ximported_method_sig(mod, mname)[1]):
            return self._mp_method_call_args(recv_node, mname, arg_nodes, fn)
        self.xvt_needed.add(mod)
        xo = self.vtable_recv(recv_node)
        ret, pct = self.ximported_method_sig(mod, mname)
        fn = self.ximported_method_fn(mod, mname)
        defs = self.defaults_for(fn, True) if fn else None
        cargs = self.coerce_args(pct, arg_nodes, defs)
        vt = self.vt_struct_name(mod)
        return "((const %s*)(%s)->type)->%s(%s)" % (
            vt, xo, vslot_name(mname), ", ".join([xo] + cargs))

    def ctor_class(self, call):
        """If `call` constructs a known class (local/imported/alias), return its
        name (marking the import used); else None."""
        if not isinstance(call, ast.Call):
            return None
        f = call.func
        if isinstance(f, ast.Name):
            if f.id in self.classes:
                return f.id
            if f.id in self.from_imports:
                kind, _ = self.xref(f.id, self.from_imports[f.id])
                if kind == "class":
                    return f.id
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id in self.import_alias:
            kind, _ = self.xref(f.attr, self.import_alias[f.value.id])
            if kind == "class":
                return f.attr
        return None

    def _register_mod_global(self, name, ctype, kind, val):
        """First binding -> file-scope decl; later bindings -> module init."""
        if name in self.mod_global_names:
            self.mod_init_stmts.append(ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store())], value=val))
            return
        if kind not in ("const", "singleton") and \
                ctype not in ("char*", "int", "bool"):
            ctype = OBJ
        self.mod_globals.append((name, ctype, kind, val))
        self.mod_global_names.add(name)
        self.mod_global_types[name] = ctype

    def _register_assign_global(self, name, val):
        """Register one name from a module-level assignment."""
        if isinstance(val, (ast.Set, ast.List)) and val.elts and \
                all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in val.elts):
            self.str_sets[name] = [e.value for e in val.elts]
        cls = self.ctor_class(val)
        if cls:
            self.singleton_names[name] = cls
            self._register_mod_global(name, cls + "*", "singleton", val)
        elif isinstance(val, ast.Name) and val.id in self.func_nodes:
            self.func_values_needed.add(val.id)
            self._register_mod_global(name, OBJ, "expr", val)
        elif isinstance(val, ast.Name):
            self._register_mod_global(name, OBJ, "expr", val)
        elif isinstance(val, ast.Attribute):
            self._register_mod_global(name, OBJ, "expr", val)
        elif isinstance(val, (ast.List, ast.Dict, ast.Set, ast.Tuple,
                              ast.BinOp, ast.ListComp, ast.DictComp,
                              ast.SetComp, ast.Call)):
            if isinstance(val, ast.Tuple) and val.elts and \
                    all(isinstance(e, ast.Name) for e in val.elts):
                tnames = [e.id for e in val.elts]
                if all(t in STDLIB_BUILTINS or t in self.BUILTIN_TYPE_TAGS
                       for t in tnames):
                    self.tuple_type_globals[name] = tnames
            ct = self.value_ctype(val) or OBJ
            if ct not in ("char*", "int", "bool"):
                ct = OBJ
            self._register_mod_global(name, ct, "expr", val)
        elif isinstance(val, ast.Constant) and isinstance(val.value, (str, bytes)):
            self._register_mod_global(name, OBJ, "expr", val)
        else:
            ct = self.value_ctype(val)
            if ct and ct != OBJ:
                self.mod_const_types[name] = ct
                if isinstance(val, ast.Constant):
                    kind = "const" if name not in self.func_nodes else "expr"
                    self._register_mod_global(name, ct, kind, val)
                elif isinstance(val, ast.Name):
                    self._register_mod_global(name, ct, "expr", val)

    def collect_module_globals(self, tree):
        # mod_globals: ordered [(name, ctype, kind, value_node)] needing a
        # file-scope declaration + deferred init in <module>_init().
        self.mod_globals = []
        self.mod_global_names = set()
        self.tuple_type_globals = {}  # name -> [type names] for isinstance(x, T)
        self.mod_const_types = {}   # module-level constant name -> ctype
        self.mod_init_stmts = []
        for node in tree.body:
            if isinstance(node, ast.Try):
                for stmt in node.body:
                    if isinstance(stmt, ast.ImportFrom):
                        for alias in stmt.names:
                            if alias.name == "*":
                                continue
                            nm = alias.asname or alias.name.split(".")[0]
                            if nm not in self.mod_global_names:
                                self._register_mod_global(nm, OBJ, "expr",
                                                          ast.Constant(value=None))
                    elif isinstance(stmt, ast.Import):
                        for alias in stmt.names:
                            nm = alias.asname or alias.name.split(".")[0]
                            if nm not in self.mod_global_names:
                                self._register_mod_global(nm, OBJ, "expr",
                                                          ast.Constant(value=None))
                for handler in node.handlers:
                    for stmt in handler.body:
                        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                                and isinstance(stmt.targets[0], ast.Name) \
                                and isinstance(stmt.value, ast.Constant) \
                                and stmt.value.value is None:
                            nm = stmt.targets[0].id
                            if nm not in self.mod_global_names:
                                self._register_mod_global(nm, OBJ, "expr",
                                                          stmt.value)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self.func_returns[node.name] = ann_to_ctype(node.returns) or OBJ
                self.func_params[node.name] = [arg_ctype(node, a)
                                               for a in node.args.args]
                self.func_nodes[node.name] = node
            if not isinstance(node, ast.Assign):
                continue
            val = node.value
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for i, tgt in enumerate(node.targets):
                if not isinstance(tgt, ast.Name):
                    continue
                if i == 0:
                    self._register_assign_global(tgt.id, val)
                else:
                    self._register_mod_global(tgt.id, OBJ, "expr",
                                              ast.Name(id=names[0]))
        # module-level statements that aren't declarations (attribute/subscript
        # assignments, aug-assignments, bare calls) run at import time, so they
        # are deferred into <module>_init() rather than emitted at file scope.
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and not isinstance(node.targets[0], ast.Name):
                self.mod_init_stmts.append(node)
            elif isinstance(node, ast.AugAssign):
                self.mod_init_stmts.append(node)
            elif isinstance(node, ast.Expr):
                self.mod_init_stmts.append(node)
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                self.mod_init_stmts.append(node)

    def prelude(self):
        bar = "/* " + "=" * 66 + " */"
        self.emit(bar)
        if self.stdlib_root:
            self.emit("/*  Transpiled from python-stdlib/%s by tools/py2c.py */"
                      % self.modname)
        else:
            self.emit("/*  Transpiled from shivyc/%s.py by tools/py2c.py        */"
                      % self.modname)
        self.emit("/*  Object model: arena + per-class vtable (see py2c.py).  */")
        self.emit(bar)
        self.emit('#include "shivyc_rt.h"')
        if self.stdlib_root:
            self.emit('#include "mp_stdlib_bridge.h"')
        self.emit()

    def _shallow_copy(self, node):
        """copy.copy(x) on a class instance -> a fresh arena node bit-copied
        from the source (shallow copy), keeping x's type. Returns None when the
        argument's type isn't a known struct pointer (caller falls back)."""
        if len(node.args) != 1:
            return None
        ct = self.value_ctype(node.args[0])
        if ct and ct.endswith("*") and ct not in ("char*", "void*", OBJ):
            struct = ct[:-1].strip()
            e = self.expr(node.args[0])
            return ("({ %s _cp = aalloc(sizeof(%s)); *_cp = *(%s); _cp; })"
                    % (ct, struct, e))
        return None

    def _mp_import_call(self, mod, attr, node):
        # copy.copy(x) on a class instance is a shallow struct copy, so a
        # self-hosted ShivyCX needs no dynamic `copy` module.
        if mod == "copy" and attr == "copy":
            cc = self._shallow_copy(node)
            if cc:
                return cc
        nargs = len(node.args)
        wrapped = [self.wrap_obj(a) for a in node.args]
        sm, sa = c_string(mod), c_string(attr)
        if nargs == 0:
            return "mp_call_import(%s, %s, 0)" % (sm, sa)
        if nargs == 1:
            return "mp_call_import(%s, %s, 1, %s)" % (sm, sa, wrapped[0])
        if nargs == 2:
            return "mp_call_import(%s, %s, 2, %s, %s)" % (
                sm, sa, wrapped[0], wrapped[1])
        return "mp_call_import(%s, %s, %d, %s)" % (
            sm, sa, nargs, ", ".join(wrapped))

    def _mp_method_call(self, recv, attr, node):
        nargs = len(node.args)
        wrapped = [self.wrap_obj(a) for a in node.args]
        r = self.wrap_obj(recv) if not isinstance(recv, str) else recv
        sa = c_string(attr)
        if nargs == 0:
            return "mp_call_method(%s, %s, 0)" % (r, sa)
        if nargs == 1:
            return "mp_call_method(%s, %s, 1, %s)" % (r, sa, wrapped[0])
        if nargs == 2:
            return "mp_call_method(%s, %s, 2, %s, %s)" % (
                r, sa, wrapped[0], wrapped[1])
        return "mp_call_method(%s, %s, %d, %s)" % (
            r, sa, nargs, ", ".join(wrapped))

    def emit_typeinfo_struct(self):
        self.emit("/* Per-module type descriptor: header + vtable slots. */")
        self.emit("typedef struct TypeInfo {")
        self.indent += 1
        self.emit("const char* name;")
        self.emit("const struct TypeInfo* base;")
        for m in sorted(VTABLE_METHODS):
            self.emit(self.vslot_signature(m) + ";")
        self.indent -= 1
        self.emit("} TypeInfo;")
        self.emit()
        self.emit("#define TYPE(o) (TYPEINFO(TypeInfo, (o)))")
        self.emit()

    def vslot_signature(self, mname):
        ret, params, _ = self.method_proto(mname)
        return "%s (*%s)(%s)" % (ret, vslot_name(mname), ", ".join(["Obj*"] + params))

    def method_proto(self, mname):
        ret, params, fndef = OBJ, [], None
        n_kwonly = 0
        for ci in self.class_order:
            fn = ci.methods.get(mname)
            if not fn:
                continue
            ret = self._c_ret(fn)
            p = self._method_proto_params(fn)
            if len(p) > len(params):
                params = list(p)
                fndef = fn
            elif len(p) == len(params) and fndef is None:
                fndef = fn
            n_kwonly = max(n_kwonly, len(fn.args.kwonlyargs))
        # canonical methods not overridden locally: take the imported root's
        # signature so the slot layout matches the defining module exactly.
        if fndef is None and getattr(self, "vt_root", None) is not None:
            fn = self.vt_root.methods.get(mname)
            if fn is not None:
                ret = self._c_ret(fn)
                params = self._method_proto_params(fn)
                fndef = fn
                n_kwonly = max(n_kwonly, len(fn.args.kwonlyargs))
        has_vararg = False
        has_kwarg = False
        for ci in self.class_order:
            fn = ci.methods.get(mname)
            if fn and fn.args.vararg:
                has_vararg = True
            if fn and fn.args.kwarg:
                has_kwarg = True
        if fndef is None and getattr(self, "vt_root", None) is not None:
            fn = self.vt_root.methods.get(mname)
            if fn is not None and fn.args.vararg:
                has_vararg = True
            if fn is not None and fn.args.kwarg:
                has_kwarg = True
        params = list(params) + [OBJ] * n_kwonly
        if has_vararg:
            params.append(OBJ)
        if has_kwarg:
            params.append(OBJ)
        return ret, params, fndef

    def _canon_vtable_param_names(self, fn, n):
        """Parameter names for the first `n` vtable positional slots of `fn`."""
        pos = fn.args.args[1:]
        out = []
        for i in range(n):
            if i < len(pos):
                out.append(self.pname(pos[i].arg))
            else:
                out.append("_vtpad%d" % i)
        return out

    def _method_proto_params(self, fn):
        """Positional params only (vtable slots share a uniform positional ABI)."""
        return [arg_ctype(fn, a) for a in fn.args.args[1:]]

    def _vtable_kwonly_union(self, mname):
        n = 0
        for ci in self.class_order:
            fn = ci.methods.get(mname)
            if fn:
                n = max(n, len(fn.args.kwonlyargs))
        return n

    def _vtable_vararg_union(self, mname):
        for ci in self.class_order:
            fn = ci.methods.get(mname)
            if fn and fn.args.vararg:
                return True
        return False

    def _vtable_kwarg_union(self, mname):
        for ci in self.class_order:
            fn = ci.methods.get(mname)
            if fn and fn.args.kwarg:
                return fn.args.kwarg.arg
        return None

    def _vtable_c_param_list(self, fn):
        _, canon, _ = self.method_proto(fn.name)
        n_kw_union = self._vtable_kwonly_union(fn.name)
        n_vararg = 1 if self._vtable_vararg_union(fn.name) else 0
        n_kwarg = 1 if self._vtable_kwarg_union(fn.name) else 0
        npos_canon = len(canon) - n_kw_union - n_vararg - n_kwarg
        npos = max(npos_canon, max(0, len(fn.args.args) - 1))
        names = self._canon_vtable_param_names(fn, npos)
        parts = []
        for i in range(npos):
            if i < len(fn.args.args) - 1:
                arg = fn.args.args[i + 1]
                ct = self.arg_ctype_q(fn, arg)
                if fn.name in VTABLE_METHODS and self._is_class_ptr(ct):
                    ct = OBJ
                parts.append("%s %s" % (self.ctype_csym(ct), self.pname(arg.arg)))
            elif i < npos_canon:
                parts.append("%s %s" % (self.ctype_csym(canon[i]), cname(names[i])))
            else:
                parts.append("obj %s" % cname(names[i]))
        for a in fn.args.kwonlyargs:
            parts.append("obj %s" % self.pname(a.arg))
        for j in range(len(fn.args.kwonlyargs), n_kw_union):
            parts.append("obj _vtkw%d" % j)
        if fn.args.vararg:
            parts.append("obj %s" % self.pname(fn.args.vararg.arg))
        elif n_vararg:
            parts.append("obj _vtvarargs")
        if fn.args.kwarg:
            parts.append("obj %s" % self.pname(fn.args.kwarg.arg))
        elif n_kwarg:
            parts.append("obj _vtkwargs")
        return parts

    # ---- struct emission -------------------------------------------------

    def emit_struct(self, ci):
        bn = (" : " + ci.base_name) if ci.base_name else ""
        self.emit("/* class %s%s */" % (ci.name, bn))
        self.emit("typedef struct %s {" % ci.csym)
        self.indent += 1
        self.emit("Obj _hdr;")
        ff = ci.full_fields()
        if not ff:
            self.emit("char _empty;")
        for fn, ft in ff:
            self.emit("%s %s;" % (self.ctype_csym(ft), self.fnsym(fn)))
        self.indent -= 1
        self.emit("} %s;" % ci.csym)
        self.emit()

    # ---- class implementation -------------------------------------------

    def emit_class_impl(self, ci):
        prev = self.cur_class
        self.cur_class = ci
        for dname, dnode in ci.const_dicts.items():
            self.emit_const_dict(ci, dname, dnode)
        if "__init__" in ci.methods:
            self.emit_constructor(ci, ci.methods["__init__"])
        else:
            ni = self._nearest_init(ci)     # inherit __init__ but get own _new
            if ni is not None:
                owner, fn = ni
                self.emit_inherited_constructor(ci, owner, fn)
            else:
                self.emit_default_constructor(ci)
        for mname, fn in ci.methods.items():
            if mname == "__init__":
                continue
            if mname.startswith("__") and mname.endswith("__"):
                if mname not in ("__enter__", "__exit__"):
                    self.emit("/* %s.%s: dunder not lowered in this pass */"
                              % (ci.name, mname))
                    self.emit()
                    continue
            self.emit_method(ci, fn, virtual=(mname in VTABLE_METHODS))
        self.emit_vtable(ci)
        self.cur_class = prev

    def emit_const_dict(self, ci, dname, dnode):
        keys, vals = dnode.keys, dnode.values
        all_str_keys = all(isinstance(k, ast.Constant) and
                           isinstance(k.value, str) for k in keys)
        all_int_keys = all(isinstance(k, ast.Constant) and
                           isinstance(k.value, int) for k in keys)
        list_vals = all(isinstance(v, ast.List) for v in vals)
        const_str_vals = all(isinstance(v, ast.Constant) and
                             isinstance(v.value, str) for v in vals)
        if all_str_keys and list_vals:
            self.emit("/* const dict %s.%s : str -> list[str] */" %
                      (ci.name, dname))
            self.emit("str %s_%s(str key, int i) {" % (ci.name, dname))
            self.indent += 1
            for k, v in zip(keys, vals):
                items = ", ".join(c_string(e.value) for e in v.elts
                                  if isinstance(e, ast.Constant))
                self.emit('if (!strcmp(key, %s)) { static const char* _r[] = {%s}; return (str)_r[i]; }'
                          % (c_string(k.value), items))
            self.emit('return (str)"";')
            self.indent -= 1
            self.emit("}")
            self.emit()
            return
        if all_int_keys and const_str_vals:
            self.emit("/* const dict %s.%s : int -> str */" % (ci.name, dname))
            self.emit("str %s_%s_get(long key, str dflt) {" %
                      (ci.name, dname))
            self.indent += 1
            for k, v in zip(keys, vals):
                self.emit("if (key == %d) return %s;" %
                          (k.value, c_string(v.value)))
            self.emit("return dflt;")
            self.indent -= 1
            self.emit("}")
            self.emit()
            return
        self.emit("/* const dict %s.%s: shape not lowered */" %
                  (ci.name, dname))
        self.emit()

    def _resolved_class_attrs(self, ci):
        """attr -> value AST node for `ci`, walking root->leaf so a subclass
        override wins over an inherited default."""
        chain = []
        c = ci
        while c:
            chain.append(c)
            c = c.base
        res = {}
        for c in reversed(chain):
            for k, v in c.class_attrs.items():
                res[k] = v
        return res

    def _resolve_class_default(self, ci, attr, seen=None):
        """Resolve a class-level attribute's default value, following bare-Name
        references to sibling class attributes (so `all_registers =
        alloc_registers` resolves through to alloc_registers's own default).
        Returns an AST value node, or None if `attr` has no class default."""
        seen = seen if seen is not None else set()
        attrs = self._resolved_class_attrs(ci)
        val = attrs.get(attr)
        if isinstance(val, ast.Name) and val.id in attrs and val.id not in seen:
            seen.add(val.id)
            return self._resolve_class_default(ci, val.id, seen)
        return val

    def _lookup_imported_const(self, name, ci=None):
        """Resolve a bare Name to a C literal from an imported/base module."""
        mods = set(self.from_imports.values()) | set(self.import_alias.values())
        if ci:
            c = ci
            while c:
                bn = c.name
                if bn in self.xclasses:
                    mods.add(self.xclasses[bn][1])
                c = c.base
        for mod in mods:
            if not mod:
                continue
            reg = self.load_xmod(mod)
            if reg and name in reg.get("consts", {}):
                return self.const_literal(reg["consts"][name])
        return None

    def _expr_class_default(self, ci, dflt, ft):
        """Emit a class-attribute default value, resolving cross-module consts."""
        if isinstance(dflt, ast.Name) and dflt.id not in self.scope \
                and dflt.id not in self.mod_global_names:
            lit = self._lookup_imported_const(dflt.id, ci)
            if lit is not None:
                if ft == OBJ:
                    if lit in ("0", "1") and lit == "1":
                        return "OBJ_BOOL(true)"
                    if lit in ("0", "1"):
                        return "OBJ_BOOL(false)"
                    if lit.isdigit() or (lit.startswith("-") and lit[1:].isdigit()):
                        return "OBJ_INT(%s)" % lit
                    return "OBJ_STR(%s)" % lit
                return lit
        s = self.expr(dflt)
        if ft and ft != OBJ:
            return self.coerce_to(ft, dflt, s)
        return self.wrap_obj(dflt) if ft == OBJ or not ft else s

    def emit_class_attr_init(self, ci):
        """Set the instance fields backing class-level scalar attributes to this
        class's most-derived value (polymorphic class data made per-instance)."""
        attrs = self._resolved_class_attrs(ci)
        if not attrs:
            return
        prev = self.cur_class
        self.cur_class = ci
        for nm, val in sorted(attrs.items()):
            # a default that names a sibling class attr resolves to that
            # sibling's own default value, not a (nonexistent) bare local.
            dflt = self._resolve_class_default(ci, nm)
            ft = ci.field_ctype(nm)
            sval = self._expr_class_default(ci, dflt, ft)
            self.emit("self->%s = %s;" % (cname(nm), sval))
        self.cur_class = prev

    def emit_class_static_instance_init(self, ci):
        """Copy class-level list/dict statics into instance fields when the
        struct declares a slot (Python: self._iv finds the class variable)."""
        for nm in ci.class_statics:
            if any(fn == nm for fn, _ in ci.full_fields()):
                self.emit("self->%s = %s_%s;" % (
                    cname(nm), ci.csym, cname(nm)))

    def _nearest_init(self, ci):
        """(owner, __init__ fn) for the nearest class in ci's chain that defines
        __init__, or None."""
        c = ci
        while c:
            if "__init__" in c.methods:
                return c, c.methods["__init__"]
            c = c.base
        return None

    def emit_constructor(self, ci, fn):
        self.enter_scope(fn, skip_self=True)
        init_params = self._init_param_list(fn, skip_self=True)
        init_sig = ", ".join(["%s* self" % ci.csym] + init_params)
        argnames = [self.pname(a.arg) for a in fn.args.args[1:]]
        if fn.args.vararg:
            argnames.append(self.pname(fn.args.vararg.arg))
        if fn.args.kwarg:
            argnames.append(self.pname(fn.args.kwarg.arg))
        for a in fn.args.kwonlyargs:
            argnames.append(self.pname(a.arg))
        self.emit("void %s___init__(%s) {" % (ci.csym, init_sig))
        self.indent += 1
        self.emit_hoisted_body(fn.body)
        self.indent -= 1
        self.emit("}")
        self.emit()
        if fn.args.vararg:
            vn = fn.args.vararg.arg
            plist = "int _n_%s, ..." % vn
        else:
            plist = ", ".join(self.param_list(fn, skip_self=True) +
                              self._kwonly_param_list(fn)) or "void"
        self.emit("%s* %s_new(%s) {" % (ci.csym, ci.csym, plist))
        self.indent += 1
        self.emit("%s* self = aalloc(sizeof *self);" % ci.csym)
        self.emit("((Obj*)self)->type = &%s_type;" % ci.csym)
        if fn.args.vararg:
            vn = fn.args.vararg.arg
            self.emit("va_list _ap; va_start(_ap, _n_%s);" % vn)
            self.emit("obj %s = varg_list(_n_%s, _ap);" % (cname(vn), vn))
            self.emit("va_end(_ap);")
        self.emit("%s___init__(%s);" % (ci.csym,
                                        ", ".join(["self"] + argnames)))
        self.emit_class_attr_init(ci)
        self.emit_class_static_instance_init(ci)
        self.emit("return self;")
        self.indent -= 1
        self.emit("}")
        self.emit()

    def emit_default_constructor(self, ci):
        """Classes with no __init__ still need a _new for ctor calls."""
        self.emit("%s* %s_new(void) {" % (ci.csym, ci.csym))
        self.indent += 1
        self.emit("%s* self = aalloc(sizeof *self);" % ci.csym)
        self.emit("((Obj*)self)->type = &%s_type;" % ci.csym)
        self.emit_class_attr_init(ci)
        self.emit_class_static_instance_init(ci)
        self.emit("return self;")
        self.indent -= 1
        self.emit("}")
        self.emit()

    def emit_inherited_constructor(self, ci, owner, fn):
        """A concrete class with no own __init__ still needs its own _new so it
        sets its own type pointer and its own class-attr values; it delegates
        construction to the nearest inherited __init__."""
        if fn.args.vararg:
            vn = fn.args.vararg.arg
            plist = "int _n_%s, ..." % vn
        else:
            plist = ", ".join(self.param_list(fn, skip_self=True) +
                              self._kwonly_param_list(fn)) or "void"
        argnames = [self.pname(a.arg) for a in fn.args.args[1:]]
        if fn.args.kwarg:
            argnames.append(self.pname(fn.args.kwarg.arg))
        for a in fn.args.kwonlyargs:
            argnames.append(self.pname(a.arg))
        self.emit("%s* %s_new(%s) {" % (ci.csym, ci.csym, plist))
        self.indent += 1
        self.emit("%s* self = aalloc(sizeof *self);" % ci.csym)
        self.emit("((Obj*)self)->type = &%s_type;" % ci.csym)
        if owner.name not in self.classes:
            self.xstructs_needed.add(owner.name)
            if "__init__" in owner.methods:
                self.used_xmethods[(owner.name, "__init__")] = "void"
        self.emit("%s___init__((%s*)self%s);" % (
            owner.csym, owner.csym,
            (", " + ", ".join(argnames)) if argnames else ""))
        self.emit_class_attr_init(ci)
        self.emit_class_static_instance_init(ci)
        self.emit("return self;")
        self.indent -= 1
        self.emit("}")
        self.emit()

    def emit_method(self, ci, fn, virtual):
        static = fn.name in ci.static_methods
        classmethod = fn.name in getattr(ci, "classmethod_methods", set())
        self.enter_scope(fn, skip_self=not static)
        ret = self._c_ret(fn)
        self.cur_ret = ret
        params = self.param_list(fn, skip_self=not static)
        if static:                          # @staticmethod: no receiver at all
            plist = ", ".join(params) if params else "void"
            self.emit("%s %s_%s(%s) {" % (ret, ci.csym, method_cname(fn.name), plist))
            self.indent += 1
        elif virtual:
            vparams = self._vtable_c_param_list(fn)
            self.emit("%s %s_%s(Obj* self_%s) {" % (
                ret, ci.csym, method_cname(fn.name),
                (", " + ", ".join(vparams)) if vparams else ""))
            self.indent += 1
            _, canon, _ = self.method_proto(fn.name)
            n_kw_union = self._vtable_kwonly_union(fn.name)
            n_vararg = 1 if self._vtable_vararg_union(fn.name) else 0
            n_kwarg = 1 if self._vtable_kwarg_union(fn.name) else 0
            npos_canon = len(canon) - n_kw_union - n_vararg - n_kwarg
            npos = max(npos_canon, max(0, len(fn.args.args) - 1))
            pad_names = self._canon_vtable_param_names(fn, npos)
            if classmethod:
                self.emit("(void)self_;")
                cls_nm = fn.args.args[0].arg
                self.emit("obj %s = make_closure(&%s__ctortramp, OBJ_NONE);" % (
                    cname(cls_nm), ci.csym))
                self.scope[cls_nm] = OBJ
            else:
                self.emit("%s* self = (%s*)self_;" % (ci.csym, ci.csym))
                self.emit("(void)self;")
            for i in range(max(0, len(fn.args.args) - 1), npos):
                self.emit("(void)%s;" % cname(pad_names[i]))
            for j in range(len(fn.args.kwonlyargs), n_kw_union):
                self.emit("(void)_vtkw%d;" % j)
            if n_vararg and not fn.args.vararg:
                self.emit("(void)_vtvarargs;")
            if n_kwarg and not fn.args.kwarg:
                self.emit("(void)_vtkwargs;")
            if fn.args.kwarg:
                self.scope[fn.args.kwarg.arg] = OBJ
        else:
            plist = ["%s* self" % ci.csym] + params
            self.emit("%s %s_%s(%s) {" % (ret, ci.csym, method_cname(fn.name),
                                          ", ".join(plist)))
            self.indent += 1
        if fn.args.vararg:
            if virtual:
                self.scope[fn.args.vararg.arg] = OBJ
            else:
                self._emit_vararg_setup(fn)
        self.emit_hoisted_body(fn.body)
        self.indent -= 1
        self.emit("}")
        self.emit()

    def emit_vtable(self, ci):
        # Only a base that is a known class in this module gets a type pointer;
        # external/builtin bases (e.g. Exception) become NULL.
        base = "NULL"
        if ci.base:
            base = "&%s_type" % ci.base.csym
            if ci.base.name not in self.classes:        # imported base type
                self.xtype_externs.add(ci.base.name)
        slots = []
        for m in sorted(VTABLE_METHODS):
            owner = ci.find_method_owner(m)
            if owner and owner.name not in self.classes:  # imported impl
                self.xvtable_impls.add((owner.name, m))
            slots.append(".%s = %s" % (vslot_name(m), ("%s_%s" % (owner.csym, method_cname(m)))
                                       if owner else "NULL"))
        init = ", ".join([".name = %s" % c_string(ci.name),
                          ".base = (const struct TypeInfo*)%s" % base] + slots)
        self.emit("const TypeInfo %s_type = { %s };" % (ci.csym, init))
        self.emit()

    def _emit_vararg_setup(self, fn):
        """Materialize *args into a list local at function entry."""
        if not fn.args.vararg:
            return
        vn = fn.args.vararg.arg
        cn = cname(vn)
        self.scope[vn] = OBJ
        if fn.name in getattr(self, "closure_specs", {}):
            return
        self.hoisted.add(vn)
        self.emit("va_list _ap_%s; va_start(_ap_%s, _n_%s);" % (vn, vn, vn))
        self.emit("obj %s = varg_list(_n_%s, _ap_%s);" % (cn, vn, vn))
        self.emit("va_end(_ap_%s);" % vn)

    def param_list(self, fn, skip_self):
        params = []
        args = fn.args.args[1:] if skip_self else fn.args.args
        for arg in args:
            ct = self.arg_ctype_q(fn, arg)
            if fn.name in VTABLE_METHODS and self._is_class_ptr(ct):
                ct = OBJ
            params.append("%s %s" % (self.ctype_csym(ct), self.pname(arg.arg)))
        if fn.args.kwarg:
            params.append("obj %s" % cname(fn.args.kwarg.arg))
        if fn.args.vararg:
            if fn.name in getattr(self, "closure_specs", {}):
                params.append("obj %s" % cname(fn.args.vararg.arg))
            else:
                params.append("int _n_%s" % fn.args.vararg.arg)
                params.append("...")
        return params

    def _init_param_list(self, fn, skip_self):
        """__init__ parameter list: *vararg becomes a single obj list param."""
        params = []
        args = fn.args.args[1:] if skip_self else fn.args.args
        for arg in args:
            ct = self.arg_ctype_q(fn, arg)
            if fn.name in VTABLE_METHODS and self._is_class_ptr(ct):
                ct = OBJ
            params.append("%s %s" % (self.ctype_csym(ct), self.pname(arg.arg)))
        if fn.args.kwarg:
            params.append("obj %s" % cname(fn.args.kwarg.arg))
        if fn.args.vararg:
            params.append("obj %s" % cname(fn.args.vararg.arg))
        params.extend(self._kwonly_param_list(fn))
        return params

    def _kwonly_param_list(self, fn):
        return ["obj %s" % self.pname(a.arg) for a in fn.args.kwonlyargs]

    def enter_scope(self, fn, skip_self):
        self.scope = {}
        self.hoisted = set()
        self.narrowed = {}          # name -> ctype, active in an isinstance block
        self.elem_types = {}        # list var -> element ctype (from List[T])
        args = fn.args.args[1:] if skip_self else fn.args.args
        for arg in args:
            ct = self.arg_ctype_q(fn, arg)
            if fn.name in VTABLE_METHODS and self._is_class_ptr(ct):
                # boxed `obj` param (vtable ABI) with a known concrete class:
                # keep it obj, but narrow so member/method access resolves to
                # the typed struct (unwrapping with AS_OBJ at each use).
                self.scope[arg.arg] = OBJ
                self.narrowed[arg.arg] = ct
                cls = ct[:-1]
                if cls in self.xclasses and cls not in self.classes:
                    self.xstructs_needed.add(cls)   # typed casts need its struct
            else:
                self.scope[arg.arg] = ct
            et = ann_elem_ctype(arg.annotation)
            if et:
                self.elem_types[arg.arg] = et
        if fn.args.vararg:
            self.scope[fn.args.vararg.arg] = OBJ
        if fn.args.kwarg:
            self.scope[fn.args.kwarg.arg] = OBJ
        ko = fn.args.kwonlyargs
        kd = fn.args.kw_defaults
        for i, arg in enumerate(ko):
            di = i - (len(ko) - len(kd))
            dflt = kd[di] if di >= 0 else None
            ct = self.arg_ctype_q(fn, arg)
            if dflt is not None and ct in ("int", "bool", "char*") and \
                    self.value_ctype(dflt) == OBJ:
                ct = OBJ
            self.scope[arg.arg] = ct or OBJ

    def iter_elem_ctype(self, node):
        """Element ctype of an iterable expression when known from a List[T]
        annotation (a bare list Name, or self.<field> declared List[T])."""
        if isinstance(node, ast.BoolOp):           # `xs or []` defaulting idiom
            for v in node.values:
                et = self.iter_elem_ctype(v)
                if et:
                    return et
            return None
        if isinstance(node, ast.Name):
            return self.elem_types.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)\
                and node.value.id == "self" and self.cur_class:
            return getattr(self.cur_class, "field_elem_types", {}).get(node.attr)
        return None

    def hoist_locals(self, body):
        """Find all assigned locals and their types, to declare at function top
        (Python has function scope; C blocks would otherwise lose them)."""
        order = []
        types = {}

        def consider(name, ctype):
            if name in self.scope:
                return
            if name not in types:
                order.append(name)
                types[name] = ctype
            elif ctype == OBJ or types[name] == OBJ:
                types[name] = OBJ
            elif types[name] != ctype:
                types[name] = OBJ

        # pre-scan typed-list annotations so a `for x in <list>` below can give
        # x the element type even when the list is a local declared earlier.
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.AnnAssign) and \
                        isinstance(sub.target, ast.Name):
                    et = ann_elem_ctype(sub.annotation)
                    if et:
                        self.elem_types[sub.target.id] = et

        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            if isinstance(sub.value, ast.Name) and \
                                    sub.value.id in STDLIB_BUILTINS:
                                ct = OBJ
                            else:
                                ct = self.value_ctype(sub.value) or \
                                    infer_from_name(t.id) or OBJ
                            consider(t.id, ct)
                        elif isinstance(t, (ast.Tuple, ast.List)):
                            for el in t.elts:
                                if isinstance(el, ast.Name) and el.id != "_":
                                    consider(el.id, OBJ)
                elif isinstance(sub, ast.AnnAssign) and \
                        isinstance(sub.target, ast.Name):
                    consider(sub.target.id,
                             infer_type(sub.target.id, sub.annotation))
                elif isinstance(sub, ast.For):
                    if isinstance(sub.target, ast.Name):
                        is_range = isinstance(sub.iter, ast.Call) and \
                            isinstance(sub.iter.func, ast.Name) and \
                            sub.iter.func.id == "range"
                        et = None if is_range else self.iter_elem_ctype(sub.iter)
                        consider(sub.target.id,
                                 "int" if is_range else (et or OBJ))
                    elif isinstance(sub.target, (ast.Tuple, ast.List)):
                        for el in sub.target.elts:
                            if isinstance(el, ast.Name) and el.id != "_":
                                consider(el.id, OBJ)
        return [(n, types[n]) for n in order]

    def emit_hoisted_body(self, body):
        for name, ct in self.hoist_locals(body):
            self.scope[name] = ct
            self.hoisted.add(name)
            self.emit("%s %s;" % (self.ctype_csym(ct), cname(name)))
        self.emit_body(body)

    # ---- top level (non-class) -------------------------------------------

    def toplevel(self, node):
        if getattr(self, "mod_init_stmts", None) and node in self.mod_init_stmts:
            return                          # emitted inside <module>_init()
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            doc = node.value.value.strip().splitlines()
            if doc:
                self.emit("/* " + doc[0] + " */")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self.emit("/* " + self.src1(node) + " */")
        elif isinstance(node, ast.FunctionDef):
            if node.name not in RUNTIME_INTRINSICS:
                if self.func_nodes.get(node.name) is not node:
                    self.emit("/* %s: superseded by later definition */"
                              % node.name)
                else:
                    self.func_def(node)
                    self.emit()
        elif isinstance(node, ast.Assign):
            self.toplevel_assign(node)
        elif isinstance(node, ast.AnnAssign):
            for ln in self.st_AnnAssign(node):
                self.emit(ln)
        elif isinstance(node, ast.AugAssign):
            for ln in self.stmt(node):
                self.emit(ln)
        else:
            self.emit("/* top-level: " + self.src1(node) + " */")

    def toplevel_assign(self, node):
        if all(isinstance(t, ast.Name) and t.id in self.mod_global_names
               for t in node.targets):
            for t in node.targets:
                self.emit("/* module global %s -- initialized in %s_init() */"
                          % (t.id, self.modname))
            return
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in self.mod_global_names:
            self.emit("/* module global %s -- initialized in %s_init() */"
                      % (node.targets[0].id, self.modname))
            return
        for ln in self.assign(node, toplevel=True):
            self.emit(ln)

    def _coerce_obj_to(self, expr, ct):
        """Coerce a Tier-2 obj expression `expr` to C type `ct`."""
        if ct == "int":
            return "AS_INT(%s)" % expr
        if ct == "bool":
            return "truthy(%s)" % expr
        if ct == "char*":
            return "AS_STR(%s)" % expr
        if ct.endswith("*") and ct != OBJ:
            return "(%s)AS_OBJ(%s)" % (ct, expr)
        return expr

    def _ctortramp_new_args(self, init):
        """Build a ctor-trampoline argument list with defaults and **kwargs."""
        pct = [arg_ctype(init, a) for a in init.args.args[1:]]
        pos_defs = self.defaults_for(init, True)
        n_pos = len(pct)
        out = []
        for i, ct in enumerate(pct):
            dflt = pos_defs[i] if i < len(pos_defs) else None
            if dflt is not None:
                raw = "(pylen(args) > %d ? index_obj(args, %d) : %s)" % (
                    i, i, self.wrap_obj(dflt))
            else:
                raw = "(pylen(args) > %d ? index_obj(args, %d) : OBJ_NONE)" % (
                    i, i)
            if ct == "int":
                raw = "AS_INT(%s)" % raw
            elif ct == "bool":
                raw = "truthy(%s)" % raw
            elif ct == "char*":
                raw = "AS_STR(%s)" % raw
            elif ct.endswith("*") and ct != OBJ:
                raw = "(%s)AS_OBJ(%s)" % (ct, raw)
            out.append(raw)
        ko = init.args.kwonlyargs
        kd = init.args.kw_defaults
        for j, _a in enumerate(ko):
            di = j - (len(ko) - len(kd))
            dflt = self.wrap_obj(kd[di]) if di >= 0 else "OBJ_NONE"
            idx = n_pos + j
            out.append("(pylen(args) > %d ? index_obj(args, %d) : %s)" % (
                idx, idx, dflt))
        if init.args.kwarg:
            idx = n_pos + len(ko)
            out.append("(pylen(args) > %d ? index_obj(args, %d) : dict_new())" % (
                idx, idx))
        return out

    def _emit_ctortramp(self, cls, ci, init):
        """Emit static obj Class__ctortramp(obj env, obj args)."""
        if init is None:
            self.emit("static obj %s__ctortramp(obj env, obj args) {" % cls)
            self.indent += 1
            self.emit("(void)env; (void)args;")
            self.emit("return OBJ_OBJ(%s_new());" % cls)
            self.indent -= 1
            self.emit("}")
            self.emit()
            return
        if init.args.vararg and len(init.args.args) == 1:
            prev = self.cur_class
            self.cur_class = ci
            self.emit("static obj %s__ctortramp(obj env, obj args) {" % cls)
            self.indent += 1
            self.emit("(void)env;")
            self.emit("%s* self = aalloc(sizeof *self);" % ci.csym)
            self.emit("((Obj*)self)->type = &%s_type;" % ci.csym)
            self.emit("%s___init__(self, args);" % ci.csym)
            self.emit_class_attr_init(ci)
            self.emit_class_static_instance_init(ci)
            self.emit("return OBJ_OBJ(self);")
            self.indent -= 1
            self.emit("}")
            self.emit()
            self.cur_class = prev
            return
        nargs = self._ctortramp_new_args(init)
        self.emit("static obj %s__ctortramp(obj env, obj args) {" % cls)
        self.indent += 1
        self.emit("(void)env; (void)args;")
        self.emit("return OBJ_OBJ(%s_new(%s));" % (ci.csym, ", ".join(nargs)))
        self.indent -= 1
        self.emit("}")
        self.emit()

    def emit_trampolines(self):
        """Uniform-signature wrappers for functions used as first-class values:
        unpack the arg list, coerce to the real parameter types, call, and box
        the result back to a Tier-2 obj."""
        # closure-converted nested functions: captures come from `env`, the
        # caller-supplied params from `args` (filling defaults when short).
        for mangled in sorted(self.closure_values_needed):
            node, n_caps, real_defs = self.closure_specs[mangled]
            ret = ann_to_ctype(node.returns) or OBJ
            parts = []
            for i in range(n_caps):
                pct_i = arg_ctype(node, node.args.args[i])
                parts.append(self._coerce_obj_to("index_obj(env, %d)" % i,
                                                 pct_i))
            for j in range(n_caps, len(node.args.args)):
                param = node.args.args[j]
                k = j - n_caps
                d = real_defs[k] if k < len(real_defs) else None
                pct_j = arg_ctype(node, param)
                if d is None:
                    raw = "index_obj(args, %d)" % k
                else:
                    dflt = self.wrap_obj(d)
                    raw = "(pylen(args) > %d ? index_obj(args, %d) : %s)" % (
                        k, k, dflt)
                parts.append(self._coerce_obj_to(raw, pct_j))
            if node.args.kwarg:
                parts.append("dict_new()")
            if node.args.vararg:
                parts.append("args")
            call = "%s(%s)" % (self.fnsym(mangled), ", ".join(parts))
            self.emit("static obj %s__tramp(obj env, obj args) {" %
                      cname(mangled))
            self.indent += 1
            self.emit("(void)env; (void)args;")
            if ret == "void":
                self.emit("%s; return OBJ_NONE;" % call)
            elif ret == "int":
                self.emit("return OBJ_INT(%s);" % call)
            elif ret == "bool":
                self.emit("return OBJ_BOOL(%s);" % call)
            elif ret == "char*":
                self.emit("return OBJ_STR(%s);" % call)
            elif ret.endswith("*") and ret != OBJ:
                self.emit("return OBJ_OBJ(%s);" % call)
            else:
                self.emit("return %s;" % call)
            self.indent -= 1
            self.emit("}")
            self.emit()
        for fn in sorted(self.func_values_needed):
            node = self.func_nodes[fn]
            pct = self.func_params.get(fn, [])
            ret = self.func_returns.get(fn, OBJ)
            args = []
            for i, ct in enumerate(pct):
                a = "index_obj(args, %d)" % i
                if ct == "int":
                    a = "AS_INT(%s)" % a
                elif ct == "bool":
                    a = "truthy(%s)" % a
                elif ct == "char*":
                    a = "AS_STR(%s)" % a
                elif ct.endswith("*") and ct != OBJ:
                    a = "(%s)AS_OBJ(%s)" % (ct, a)
                args.append(a)
            call = "%s(%s)" % (self.fnsym(fn), ", ".join(args))
            self.emit("static obj %s__tramp(obj env, obj args) {" % self.fnsym(fn))
            self.indent += 1
            self.emit("(void)env; (void)args;")
            if ret == "void":
                self.emit("%s; return OBJ_NONE;" % call)
            elif ret == OBJ:
                self.emit("return %s;" % call)
            elif ret == "int":
                self.emit("return OBJ_INT(%s);" % call)
            elif ret == "bool":
                self.emit("return OBJ_BOOL(%s);" % call)
            elif ret == "char*":
                self.emit("return OBJ_STR(%s);" % call)
            elif ret.endswith("*"):
                self.emit("return OBJ_OBJ(%s);" % call)
            else:
                self.emit("return %s;" % call)
            self.indent -= 1
            self.emit("}")
            self.emit()
        for cls in sorted(set(self.class_values_needed) |
                          {ci.csym for ci in self.class_order}):
            ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                           if cls in self.xclasses else None)
            ni = self._nearest_init(ci) if ci else None
            init = ni[1] if ni else (ci.methods.get("__init__") if ci else None)
            self._emit_ctortramp(cls, ci, init)

    def emit_module_init(self):
        self.emit("/* Initialize module-level globals (Python import-time). */")
        self.emit("void %s_init(void) {" % (self.cmod))
        self.indent += 1
        if not self.mod_globals:
            self.emit("/* none */")
        for name, ctype, kind, val in self.mod_globals:
            if kind == "const":             # already defined at file scope
                continue
            if kind == "singleton":
                cls = ctype[:-1]
                if cls in self.classes:
                    init = self.classes[cls].methods.get("__init__")
                    defs = self.defaults_for(init, True) if init else None
                    args = self.coerce_args(
                        self.init_param_ctypes(self.classes[cls]), val.args,
                        defs)
                else:                       # imported class -> unchecked args
                    args = [self.wrap_obj(a) if False else self.expr(a)
                            for a in val.args]
                self.emit("%s = %s_new(%s);" % (self._msym(name), self.ccls(cls),
                                                ", ".join(args)))
            else:
                lit = _const_value(val)
                if ctype == OBJ and isinstance(lit, (str, bytes)):
                    s = lit.decode("latin1") if isinstance(lit, bytes) else lit
                    self.emit("%s = OBJ_STR(%s);" % (self._msym(name), c_string(s)))
                else:
                    self.emit("%s = %s;" % (self._msym(name),
                        self.coerce_to(ctype, val, self.expr(val))))
        for ci in self.class_order:         # class-level statics
            prev = self.cur_class
            self.cur_class = ci
            for nm, val in ci.class_statics.items():
                self.emit("%s_%s = %s;" % (ci.csym, cname(nm),
                                           self.expr(val)))
            self.cur_class = prev
        for stmt in getattr(self, "mod_init_stmts", []):  # deferred top-level
            for ln in self.stmt(stmt):
                self.emit(ln)
        self.indent -= 1
        self.emit("}")

    def func_def(self, node):
        self.enter_scope(node, skip_self=False)
        ret = ann_to_ctype(node.returns) or OBJ
        self.cur_ret = ret
        params = self.param_list(node, skip_self=False)
        plist = ", ".join(params) if params else "void"
        self.emit("%s {" % self.func_signature(node).rstrip(";"))
        self.indent += 1
        self._emit_vararg_setup(node)
        self.emit_hoisted_body(node.body)
        self.indent -= 1
        self.emit("}")

    def emit_body(self, body):
        if not body:
            self.emit("/* pass */")
            return
        for stmt in body:
            for ln in self.stmt(stmt):
                self.emit(ln)

    # ---- statements ------------------------------------------------------

    def stmt(self, node):
        m = getattr(self, "st_" + type(node).__name__, None)
        if m is None:
            return ["/* stmt %s: %s */" % (type(node).__name__,
                                           self.src1(node))]
        try:
            return m(node)
        except Unsupported:
            raise
        except Exception as e:
            if self.stdlib_root:
                raise Unsupported(str(e)) from e
            return ["/* transpile-error (%s): %s */" % (e, self.src1(node))]

    def st_Expr(self, node):
        v = node.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            first = v.value.strip().splitlines()
            return ["/* " + first[0] + " */"] if first else []
        return [self.expr(v) + ";"]

    def st_Assign(self, node):
        return self.assign(node)

    def _subscript_container_is_obj(self, node):
        if self.is_obj_word(node) or self.value_ctype(node) == OBJ:
            return True
        if isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
            return True
        return False

    def assign(self, node, toplevel=False):
        rhs = self.expr(node.value)
        lines = []
        for tgt in node.targets:
            if isinstance(tgt, (ast.Tuple, ast.List)):
                # a, b = expr  ->  bind each element from the materialized rhs
                self.loop_n += 1
                tmp = "_u%d" % self.loop_n
                lines.append("obj %s = %s;" % (tmp, rhs))
                for i, el in enumerate(tgt.elts):
                    if isinstance(el, ast.Name) and el.id == "_":
                        continue
                    src = "index_obj(%s, %d)" % (tmp, i)
                    if isinstance(el, ast.Name):
                        if el.id not in self.scope and el.id not in self.hoisted:
                            self.scope[el.id] = OBJ
                            lines.append("obj %s = %s;" % (self.lid(el.id), src))
                        else:
                            t = self.scope.get(el.id, OBJ)
                            lines.append("%s = %s;" % (self.lid(el.id),
                                                       self.unwrap_obj(t, src)))
                    elif isinstance(el, ast.Subscript):
                        lines.append("subscript_set(%s, %s, %s);" % (
                            self.expr(el.value), self.wrap_obj(el.slice), src))
                    elif isinstance(el, ast.Attribute):
                        lines.append(self._emit_attr_assign(el, node.value))
                    else:
                        t = self.target_ctype(el) or OBJ
                        lines.append("%s = %s;" % (self.expr(el),
                                                   self.unwrap_obj(t, src)))
                continue
            if isinstance(tgt, ast.Subscript):
                # dst[:] = src  -- replace the list's contents in place.
                if isinstance(tgt.slice, ast.Slice) and \
                        tgt.slice.lower is None and tgt.slice.upper is None \
                        and tgt.slice.step is None:
                    vct = self.value_ctype(tgt.value)
                    if vct == "char*":
                        lines.append("strcpy(%s, AS_STR(%s));" % (
                            self.expr(tgt.value), self.wrap_obj(node.value)))
                    else:
                        lines.append("list_assign_slice(%s, %s);" % (
                            self.expr(tgt.value), self.wrap_obj(node.value)))
                    continue
                # dst[lo:hi] = src  -- splice (step must be absent)
                if isinstance(tgt.slice, ast.Slice) and tgt.slice.step is None:
                    lo = self.coerce_to("int", tgt.slice.lower,
                                        self.expr(tgt.slice.lower)) \
                        if tgt.slice.lower is not None else "0"
                    hi = self.coerce_to("int", tgt.slice.upper,
                                        self.expr(tgt.slice.upper)) \
                        if tgt.slice.upper is not None else "pylen(%s)" % \
                        self.expr(tgt.value)
                    lines.append("list_set_slice(%s, %s, %s, %s);" % (
                        self.expr(tgt.value), lo, hi, self.wrap_obj(node.value)))
                    continue
                if self._subscript_container_is_obj(tgt.value) or \
                        isinstance(tgt.value, ast.Call):
                    lines.append("subscript_set(%s, %s, %s);" % (
                        self.expr(tgt.value), self.wrap_obj(tgt.slice),
                        self.wrap_obj(node.value)))
                else:
                    lines.append("%s = %s;" % (self.expr(tgt), rhs))
                continue
            if isinstance(tgt, ast.Name):
                if tgt.id in self.mod_global_types and tgt.id not in self.scope:
                    lines.append("%s = %s;" % (
                        self._msym(tgt.id),
                        self.coerce_to(self.mod_global_types[tgt.id],
                                       node.value, rhs)))
                    continue
                # already declared in this scope?  ->  plain reassignment
                if tgt.id in self.scope and not toplevel:
                    lines.append("%s = %s;" % (
                        self.lid(tgt.id),
                        self.coerce_to(self.scope[tgt.id], node.value, rhs)))
                    continue
                # the value's actual C type wins over the name guess
                vct = self.value_ctype(node.value)
                ctype = vct or infer_from_name(tgt.id) or OBJ
                if vct == OBJ and infer_from_name(tgt.id) in ("char*", "int", "bool"):
                    ctype = OBJ
                if not toplevel:
                    self.scope[tgt.id] = ctype
                lines.append("%s %s = %s;" % (ctype, cname(tgt.id), rhs))
            else:
                if isinstance(tgt, ast.Attribute):
                    lines.append(self._emit_attr_assign(tgt, node.value))
                else:
                    lines.append("%s = %s;" % (
                        self.expr(tgt),
                        self.coerce_to(self.target_ctype(tgt), node.value, rhs)))
        return lines

    def wrap_for_assign(self, value_node, rendered):
        if self.value_ctype(value_node) in ("int", "bool", "char*"):
            return self.wrap_obj(value_node)
        return rendered

    def _attr_assign_needs_setattr(self, tgt):
        """Attribute store that cannot lower to a struct field offset."""
        if not isinstance(tgt, ast.Attribute):
            return False
        if isinstance(tgt.value, ast.Name) and tgt.value.id == "self" \
                and self.cur_class:
            if self.cur_class.field_ctype(tgt.attr) is not None:
                return False
        bt = self.value_ctype(tgt.value)
        if bt and bt.endswith("*") and bt != OBJ:
            if self._class_has_field(bt[:-1], tgt.attr):
                return False
            return True
        if self.is_obj_word(tgt.value) or bt == OBJ or \
                isinstance(tgt.value, ast.Call):
            return True
        return False

    def _attr_is_property(self, tgt):
        if not isinstance(tgt, ast.Attribute):
            return False
        bt = self.value_ctype(tgt.value)
        if not (bt and bt.endswith("*") and bt != OBJ):
            return False
        cls = bt[:-1]
        ci = self.classes.get(cls) or \
            (self.xclasses[cls][0] if cls in self.xclasses else None)
        return ci is not None and tgt.attr in ci.property_methods

    def _emit_attr_assign(self, tgt, value_node):
        val = self.wrap_obj(value_node)
        if self._attr_is_property(tgt) or self._attr_assign_needs_setattr(tgt):
            return 'mp_call_import("builtins", "setattr", 3, %s, %s, %s);' % (
                self.wrap_obj(tgt.value), c_string(tgt.attr), val)
        t = self.target_ctype(tgt) or OBJ
        raw = self.expr(value_node)
        return "%s = %s;" % (self.expr(tgt), self.coerce_to(t, value_node, raw))

    def value_ctype(self, node):
        """Best-effort C type of an expression, when determinable."""
        if isinstance(node, ast.Call) and len(node.args) == 1:
            f = node.func
            is_copy = (
                (isinstance(f, ast.Attribute) and f.attr == "copy"
                 and isinstance(f.value, ast.Name) and f.value.id == "copy")
                or (isinstance(f, ast.Name) and f.id == "copy"
                    and self.from_imports.get("copy") == "copy"))
            if is_copy:
                return self.value_ctype(node.args[0])  # shallow copy keeps type
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "__closure_env__":
            return OBJ                  # make_closure(...) yields a Tier-2 obj
        t = self.static_type(node)
        if t:
            return t
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add) and (self.looks_str(node.left) or
                                                 self.looks_str(node.right)):
                return "char*"
            lt = self.value_ctype(node.left)
            rt = self.value_ctype(node.right)
            if lt in ("int", "bool") and rt in ("int", "bool"):
                return "int"
            return OBJ  # obj arithmetic yields a Tier-2 obj
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp)):
            return OBJ
        if isinstance(node, ast.IfExp):
            bt = self.value_ctype(node.body)
            return bt if bt == self.value_ctype(node.orelse) else OBJ
        if isinstance(node, ast.BoolOp):
            types = [self.value_ctype(v) for v in node.values]
            return types[0] if len(set(types)) == 1 and \
                types[0] in ("int", "bool", "char*") else OBJ
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Slice):
                return OBJ
            if isinstance(node.value, ast.Subscript):
                inner = node.value
                if isinstance(inner.value, ast.Attribute) and \
                        self.const_dict_owner(inner.value) is not None:
                    return "char*"
            if self.is_obj_word(node.value) or \
                    self.value_ctype(node.value) == OBJ:
                return OBJ
            if self.value_ctype(node.value) == "char*":
                return "char*"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return "bool"
            if isinstance(node.op, ast.USub):
                lt = self.value_ctype(node.operand)
                if self.is_obj_word(node.operand) or lt == OBJ:
                    return OBJ
                if lt and lt.endswith("*") and lt not in ("char*", OBJ):
                    return OBJ
            return self.value_ctype(node.operand)
        if isinstance(node, ast.IfExp):
            return self.value_ctype(node.body) or self.value_ctype(node.orelse)
        if isinstance(node, ast.Subscript) and \
                isinstance(node.value, ast.Subscript):
            inner = node.value
            if isinstance(inner.value, ast.Attribute) and \
                    isinstance(inner.value.value, ast.Name) and \
                    inner.value.value.id == "self" and self.cur_class and \
                    inner.value.attr in self.cur_class.const_dicts:
                return "char*"
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                if f.id == "isinstance":
                    return "bool"
                if f.id in ("any", "all"):
                    return "bool"
                if f.id in ("chr", "repr"):
                    return "char*"
                if f.id == "str":
                    if self.stdlib_root and len(node.args) != 1:
                        return OBJ
                    return "char*"
                if f.id == "getattr" and len(node.args) >= 2 and \
                        isinstance(node.args[1], ast.Constant) and \
                        isinstance(node.args[1].value, str):
                    owner = self.resolve_attr_owner(node.args[1].value)
                    return owner.field_ctype(node.args[1].value) if owner \
                        else OBJ
                if f.id in ("len", "ord", "int", "abs", "const"):
                    return "int" if f.id != "const" else (
                        self.value_ctype(node.args[0]) if node.args else "int")
                if f.id == "bool":
                    return "bool"
                if f.id == "float":
                    return OBJ          # pyfloat(...) yields a Tier-2 obj
                if f.id in ("range", "sorted", "list", "dict", "set",
                            "reversed", "enumerate", "max", "min", "sum",
                            "zip", "map", "filter", "vars"):
                    return OBJ
                if f.id in self.mod_global_types:
                    return self.mod_global_types[f.id]
                if f.id in self.classes:
                    return f.id + "*"
                if f.id in self.func_returns:
                    return self.func_returns[f.id]
                # calling an obj-typed local/param (first-class function) -> obj
                if f.id in self.scope and self.scope[f.id] == OBJ \
                        and f.id not in self.func_params \
                        and f.id not in self.classes:
                    return OBJ
                if f.id in self.from_imports:
                    kind, info = self.resolve_import(f.id,
                                                     self.from_imports[f.id])
                    if kind == "class":
                        return cname(f.id) + "*"
                    if kind == "func":
                        return ann_to_ctype(info.returns) or OBJ
            if isinstance(f, ast.Attribute):
                # float.fromhex("0x..") -> a Tier-2 float obj
                if f.attr == "fromhex" and isinstance(f.value, ast.Name) \
                        and f.value.id == "float":
                    return OBJ
                # a method dispatched through an imported module's vtable (see
                # ex_Call's _exclusive_vt_module): report its logical return --
                # obj/scalar for predicates (matching the obj slot so boolean
                # lowering wraps truthy), or a leaf class pointer for class
                # returns (so ex_Call recovers the typed pointer via AS_OBJ).
                if not (isinstance(f.value, ast.Name) and (
                        f.value.id in self.import_alias
                        or f.value.id in self.modules
                        or f.value.id in self.classes
                        or f.value.id in self.xclasses)):
                    _xm = self._exclusive_vt_module(f.attr)
                    if _xm is not None:
                        return self._ximported_logical_ret(_xm, f.attr)
                # isinstance-narrowed receiver: report the concrete method's
                # return type, matching ex_Call's narrowing dispatch.
                if isinstance(f.value, ast.Name) and \
                        f.value.id in self.narrowed:
                    cls = self.narrowed[f.value.id][:-1]
                    ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                                   if cls in self.xclasses
                                                   else None)
                    if ci is not None and self._class_is_leaf(cls):
                        owner = ci.find_method_owner(f.attr)
                        if owner is None and f.attr in ci.methods:
                            owner = ci
                        if owner is not None and f.attr in owner.methods:
                            m = owner.methods[f.attr]
                            return self._logical_ret(m)
                if isinstance(f.value, ast.Name) and \
                        f.value.id in self.import_alias:
                    kind, info = self.resolve_import(
                        f.attr, self.import_alias[f.value.id])
                    if kind == "class":
                        return cname(f.attr) + "*"
                    if kind == "func":
                        return ann_to_ctype(info.returns) or OBJ
                    if kind == "global":
                        return OBJ
                    if self.stdlib_root:
                        return OBJ
                if isinstance(f.value, ast.Name) and \
                        f.value.id in self.modules and self.stdlib_root:
                    return OBJ
                if f.attr == "get" and isinstance(f.value, ast.Attribute) \
                        and isinstance(f.value.value, ast.Name) \
                        and f.value.value.id == "self" and self.cur_class \
                        and f.value.attr in self.cur_class.const_dicts:
                    return "char*"
                if f.attr in VTABLE_METHODS:
                    return self._logical_ret(self.method_proto(f.attr)[2])
                if f.attr not in self.method_owners:
                    if f.attr in ("startswith", "endswith", "isdigit",
                                  "isalpha", "isspace", "isalnum"):
                        return "bool"
                    if f.attr in ("strip", "lstrip", "rstrip", "replace",
                                  "lower", "upper", "encode", "join"):
                        return "char*"
                    if f.attr in ("split", "splitlines", "keys", "values",
                                  "items", "get", "pop", "setdefault"):
                        return OBJ
                    if f.attr in ("find", "rfind"):
                        return "int"
                # method call on a concrete class instance (local or imported)
                bt = self.value_ctype(f.value)
                if bt and bt.endswith("*") and bt != OBJ:
                    cls = bt[:-1]
                    ci = self.classes.get(cls) or \
                        (self.xclasses[cls][0] if cls in self.xclasses else None)
                    if ci:
                        owner = ci.find_method_owner(f.attr)
                        if owner:
                            m = owner.methods.get(f.attr)
                            return self._logical_ret(m) if m else OBJ
                # method call on an untyped obj -> unique local/imported owner
                if self.is_obj_word(f.value) or bt == OBJ:
                    # a cross-module-hierarchy-dispatched method (e.g. make_il)
                    # is resolved through its hierarchy root's canonical return,
                    # not an arbitrary (unannotated) local/imported override, so
                    # the typed result matches the xvcall dispatch.
                    if f.attr in self.hierarchy_method:
                        return self._ximported_logical_ret(
                            self.hierarchy_method[f.attr], f.attr)
                    owner = self.resolve_method_owner(f.attr) or \
                        self.resolve_xmethod_owner(f.attr)
                    if owner:
                        m = owner.methods.get(f.attr)
                        return self._logical_ret(m) if m else OBJ
                    xmod = self.resolve_xvirtual(f.attr)
                    if xmod:
                        return self._ximported_logical_ret(xmod, f.attr)
                    if f.attr in self.hierarchy_method:
                        return self._ximported_logical_ret(
                            self.hierarchy_method[f.attr], f.attr)
            # calling a *complex* obj-valued expression (closure/ctor returned
            # by another call, a subscript, etc.) yields obj; Name/Attribute
            # funcs are already handled above and must not fall through here
            if not isinstance(f, (ast.Name, ast.Attribute)) and \
                    (self.value_ctype(f) == OBJ or self.is_obj_word(f)):
                return OBJ
        return self.guess_from_value(node)

    def st_AnnAssign(self, node):
        ctype = infer_type(getattr(node.target, "id", "x"), node.annotation)
        if isinstance(node.target, ast.Name):
            et = ann_elem_ctype(node.annotation)
            if et:
                self.elem_types[node.target.id] = et
        tgt = self.expr(node.target)
        if node.value is None:
            return ["%s %s;" % (ctype, tgt)]
        already = isinstance(node.target, ast.Name) and \
            node.target.id in self.scope
        decl = "" if (already or not isinstance(node.target, ast.Name)) \
            else (ctype + " ")
        return ["%s%s = %s;" % (decl, tgt, self.expr(node.value))]

    AUG_OP_CHAR = {ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.Div: '/',
                   ast.FloorDiv: '/', ast.Mod: '%', ast.BitOr: '|',
                   ast.BitAnd: '&', ast.BitXor: '^', ast.LShift: '<',
                   ast.RShift: '>'}

    def st_AugAssign(self, node):
        # `c[i] += v` on an obj container: read-modify-write via subscript_set,
        # since `subscript(...)` is an rvalue and cannot be assigned to.
        if isinstance(node.target, ast.Subscript) and \
                (self.is_obj_word(node.target.value) or
                 self.value_ctype(node.target.value) == OBJ or
                 isinstance(node.target.value, ast.Call)):
            cont = self.expr(node.target.value)
            idx = self.wrap_obj(node.target.slice)
            ch = self.AUG_OP_CHAR.get(type(node.op), '+')
            cur = "subscript(%s, %s)" % (cont, idx)
            return ["subscript_set(%s, %s, obj_augop(%s, '%c', %s));" % (
                cont, idx, cur, ch, self.wrap_obj(node.value))]
        tgt = self.expr(node.target)
        tt = self.target_ctype(node.target) or self.value_ctype(node.target)
        if tt == OBJ or self.is_obj_word(node.target):
            ch = self.AUG_OP_CHAR.get(type(node.op), '+')
            return ["%s = obj_augop(%s, '%c', %s);" % (
                tgt, tgt, ch, self.wrap_obj(node.value))]
        if tt and tt.endswith("*") and tt[:-1] in self.classes:
            ch = self.AUG_OP_CHAR.get(type(node.op), '+')
            return ["%s = (%s)AS_OBJ(obj_augop(OBJ_OBJ(%s), '%c', %s));" % (
                tgt, tt, tgt, ch, self.wrap_obj(node.value))]
        if isinstance(node.op, ast.Add) and tt == "char*":
            return ["%s = pyconcat(%s, %s);" % (tgt, tgt,
                                                self.as_str(node.value))]
        return ["%s %s= %s;" % (tgt, self.binop_sym(node.op),
                                self.coerce_to(tt, node.value,
                                               self.expr(node.value)))]

    def st_Return(self, node):
        if node.value is None:
            ret = getattr(self, "cur_ret", OBJ)
            if ret == OBJ or ret == "obj":
                return ["return OBJ_NONE;"]
            return ["return;"]
        ret = getattr(self, "cur_ret", OBJ)
        return ["return %s;" % self.coerce_to(ret, node.value,
                                              self.expr(node.value))]

    def st_Assert(self, node):
        test = self.bool_expr(node.test)
        if node.msg:
            msg = self.wrap_obj(node.msg)
            return ['if (!(%s)) { fprintf(stderr, "AssertionError: %%s\\n", AS_STR(%s)); abort(); }' % (test, msg)]
        return ['if (!(%s)) { fprintf(stderr, "AssertionError\\n"); abort(); }' % test]

    def st_Pass(self, node):
        return ["/* pass */"]

    def st_Break(self, node):
        return ["break;"]

    def st_Continue(self, node):
        return ["continue;"]

    def st_Global(self, node):
        return ["/* global %s */" % ", ".join(node.names)]

    def st_Delete(self, node):
        out = []
        for t in node.targets:
            if isinstance(t, ast.Subscript) and not isinstance(t.slice,
                                                               ast.Slice):
                out.append("del_item(%s, %s);" % (self.wrap_obj(t.value),
                                                  self.wrap_obj(t.slice)))
                continue
            # `del obj` on a heap object (a class instance / struct pointer)
            # hands its storage back to the arena's free list so a later
            # allocation of the same size reuses it -- manual memory management
            # in the ShivyCX source, no refcounting. Borrowed/scalar values
            # (char* strings, obj, int, ...) keep the bulk-reclaim no-op;
            # afree itself also ignores anything not on the arena.
            ct = None
            if isinstance(t, (ast.Name, ast.Attribute)):
                try:
                    ct = self.value_ctype(t)
                except Exception:
                    ct = None
            if ct and ct.endswith("*") and ct not in ("char*", "void*"):
                expr = self.expr(t)
                out.append("afree(%s, sizeof(*(%s)));" % (expr, expr))
                continue
            # no-op (arena reclaims in bulk); use source text, never the
            # generated C, so inner /* */ can't break the comment
            try:
                txt = ast.unparse(t)
            except Exception:
                txt = "?"
            out.append("/* del %s */" % txt.replace("*/", "* /"))
        return out

    def st_Import(self, node):
        return ["/* " + self.src1(node) + " */"]

    def st_ImportFrom(self, node):
        return ["/* " + self.src1(node) + " */"]

    def st_Raise(self, node):
        what = self.src1(node.exc) if node.exc else ""
        return ['fprintf(stderr, "raise %s\\n"); abort();' %
                what.replace('"', '\\"')]

    def _isinstance_class(self, ref):
        """Resolve an isinstance() 2nd-arg class reference to a known class
        name (local or imported), or None for tuples / unknown types."""
        if isinstance(ref, ast.Name):
            n = ref.id
        elif isinstance(ref, ast.Attribute):
            n = ref.attr                # e.g. value_cmds.AddrOf -> AddrOf
        else:
            return None                 # tuple-of-types: ambiguous, don't narrow
        if n in self.classes or n in self.xclasses:
            return n
        return None

    def _narrowings(self, test):
        """[(name, 'Cls*')] implied true by `test`: a bare isinstance(name,Cls)
        or an `and`-chain of them. Negation / `or` yield nothing."""
        out = []
        conds = test.values if isinstance(test, ast.BoolOp) and \
            isinstance(test.op, ast.And) else [test]
        for c in conds:
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) \
                    and c.func.id == "isinstance" and len(c.args) == 2 \
                    and isinstance(c.args[0], ast.Name):
                cls = self._isinstance_class(c.args[1])
                if cls:
                    out.append((c.args[0].id, cls + "*"))
        return out

    def st_If(self, node):
        # `if TYPE_CHECKING:` guards type-only imports; emit nothing for it
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            return []
        lines = ["if (%s) {" % self.bool_expr(node.test)]
        narrows = self._narrowings(node.test)
        saved = {n: self.narrowed.get(n) for n, _ in narrows}
        for n, ct in narrows:
            self.narrowed[n] = ct
        lines += self.indent_lines(self.suite(node.body))
        for n in saved:                 # restore: narrowing is block-scoped
            if saved[n] is None:
                self.narrowed.pop(n, None)
            else:
                self.narrowed[n] = saved[n]
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                inner = self.st_If(node.orelse[0])
                inner[0] = "} else " + inner[0]
                return lines + inner
            lines.append("} else {")
            lines += self.indent_lines(self.suite(node.orelse))
        lines.append("}")
        return lines

    def st_While(self, node):
        lines = ["while (%s) {" % self.bool_expr(node.test)]
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        return lines

    def st_For(self, node):
        it, tgt = node.iter, node.target
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) \
                and it.func.id == "range" and isinstance(tgt, ast.Name):
            v = cname(tgt.id)
            a = [self.coerce_to("int", x, self.expr(x)) for x in it.args]
            if len(a) == 1:
                lo, hi, stp = "0", a[0], "1"
            elif len(a) == 2:
                lo, hi, stp = a[0], a[1], "1"
            else:
                lo, hi, stp = a[0], a[1], a[2]
            decl = "" if tgt.id in self.hoisted else "int "
            lines = ["for (%s%s = %s; %s < %s; %s += %s) {" %
                     (decl, v, lo, v, hi, v, stp)]
            lines += self.indent_lines(self.suite(node.body))
            lines.append("}")
            return lines
        lines = ["/* for %s in %s: */" % (self.src1(tgt), self.src1(it))]
        if isinstance(tgt, (ast.Name, ast.Tuple, ast.List)):
            self.loop_n += 1
            itv = "_it%d" % self.loop_n
            idx = "_k%d" % self.loop_n
            if isinstance(tgt, ast.Name) and tgt.id != "_" \
                    and tgt.id not in self.hoisted:
                lines.append("obj %s;" % self.lid(tgt.id))
                self.scope[tgt.id] = OBJ
                self.hoisted.add(tgt.id)
            lines.append("{ obj %s = %s;" % (itv, self.wrap_obj(it)))
            lines.append("  for (long %s = 0; %s < pylen(%s); %s++) {" %
                         (idx, idx, itv, idx))
            binds = self.bind_target(tgt, "index_obj(%s, %s)" % (itv, idx))
            for b in binds:
                lines.append("    " + b)
            lines += self.indent_lines(self.indent_lines(self.suite(node.body)))
            lines.append("  }")
            lines.append("}")
            return lines
        lines.append("FOR_EACH(%s, %s) {" % (self.src1(tgt), self.expr(it)))
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        return lines

    def bind_target(self, target, src, force_decl=False):
        """C statements binding `target` (Name or Tuple/List) from obj `src`."""
        out = []
        if isinstance(target, ast.Name):
            if target.id == "_":
                return ["(void)(%s);" % src]
            fresh = force_decl or target.id not in self.hoisted
            if fresh:
                # a freshly declared `obj name` shadows any outer binding of the
                # same name; record its type as obj so value_ctype is correct
                self.scope[target.id] = OBJ
            elif target.id not in self.scope:
                self.scope[target.id] = OBJ
            decl = "obj " if fresh else ""
            rhs = src
            declared = self.scope.get(target.id)
            if not fresh and declared and declared != OBJ:
                # `src` is an obj element; coerce to the target's hoisted C type
                if declared == "char*":
                    rhs = "AS_STR(%s)" % src
                elif declared == "int":
                    rhs = "AS_INT(%s)" % src
                elif declared == "bool":
                    rhs = "truthy(%s)" % src
                elif declared.endswith("*"):
                    rhs = "(%s)AS_OBJ(%s)" % (declared, src)
            out.append("%s%s = %s;" % (decl, cname(target.id), rhs))
        elif isinstance(target, (ast.Tuple, ast.List)):
            self.loop_n += 1
            tmp = "_e%d" % self.loop_n
            out.append("obj %s = %s;" % (tmp, src))
            for i, el in enumerate(target.elts):
                out += self.bind_target(el, "index_obj(%s, %d)" % (tmp, i),
                                        force_decl=force_decl)
        return out

    def lower_comp(self, node, kind):
        """Lower a comprehension/genexpr to a GCC statement-expression loop.
        kind is 'list' (list/set/genexpr) or 'dict'."""
        saved = dict(self.scope)
        self.loop_n += 1
        acc = "_acc%d" % self.loop_n
        init = "dict_new()" if kind == "dict" else "list_new()"
        gens = node.generators

        def build(i):
            if i == len(gens):
                if kind == "dict":
                    return "dict_set(%s, %s, %s);" % (
                        acc, self.wrap_obj(node.key), self.wrap_obj(node.value))
                return "list_append(%s, %s);" % (acc, self.wrap_obj(node.elt))
            g = gens[i]
            self.loop_n += 1
            it = "_it%d" % self.loop_n
            idx = "_k%d" % self.loop_n
            binds = self.bind_target(g.target, "index_obj(%s, %s)" % (it, idx),
                                     force_decl=True)
            guard_open, guard_close = "", ""
            if g.ifs:
                conds = " && ".join("(%s)" % self.bool_expr(c) for c in g.ifs)
                guard_open, guard_close = "if (%s) { " % conds, " }"
            inner = build(i + 1)
            body = " ".join(binds) + " " + guard_open + inner + guard_close
            return "{ obj %s = %s; for (long %s = 0; %s < pylen(%s); %s++) " \
                   "{ %s } }" % (it, self.wrap_obj(g.iter), idx, idx, it, idx,
                                 body)

        body = build(0)
        self.scope = saved
        return "({ obj %s = %s; %s %s; })" % (acc, init, body, acc)

    def st_Try(self, node):
        lines = ["/* try */ {"]
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        for h in node.handlers:
            et = self.src1(h.type) if h.type else "..."
            # `raise` aborts with no stack unwinding, so a handler can never be
            # reached: if the try body raises it has already aborted, and if it
            # completes there is no exception. Keep the body for readability but
            # guard it so it never executes (and still type-checks / compiles).
            lines.append("if (0) { /* except %s */" % et)
            binds = []
            if h.name:
                en = cname(h.name)
                if h.name not in self.scope:
                    self.scope[h.name] = OBJ
                binds.append("obj %s = OBJ_NONE;" % en)
            lines += self.indent_lines(binds + self.suite(h.body))
            lines.append("}")
        if node.finalbody:
            lines.append("/* finally */ {")
            lines += self.indent_lines(self.suite(node.finalbody))
            lines.append("}")
        return lines

    def st_With(self, node):
        items = ", ".join(self.src1(i.context_expr) for i in node.items)
        lines = ["/* with %s */ {" % items]
        binds = []
        for it in node.items:
            tv = it.optional_vars
            if isinstance(tv, ast.Name):
                binds.append("obj %s = %s;" % (
                    cname(tv.id), self.expr(it.context_expr)))
            else:
                binds.append("%s;" % self.expr(it.context_expr))
        lines += self.indent_lines(binds)
        lines += self.indent_lines(self.suite(node.body))
        lines.append("}")
        return lines

    def st_FunctionDef(self, node):
        return ["/* nested function %s not lifted */" % node.name]

    def suite(self, body):
        out = []
        for s in body:
            out += self.stmt(s)
        return out

    def indent_lines(self, lines):
        return ["    " + ln for ln in lines]

    # ---- expressions -----------------------------------------------------

    def expr(self, node):
        m = getattr(self, "ex_" + type(node).__name__, None)
        if m is None:
            return "/* %s: %s */ OBJ_NONE" % (type(node).__name__,
                                              self.src1(node))
        try:
            return m(node)
        except Unsupported:
            raise
        except Exception as e:
            if self.stdlib_root:
                raise Unsupported(str(e)) from e
            return "/* expr-error %s */ OBJ_NONE" % e

    def ex_Name(self, node):
        if self.stdlib_root and node.id == "__name__" and node.id not in self.scope:
            return "OBJ_STR(%s)" % c_string(self.modname)
        if self.stdlib_root and node.id in EXCEPTION_NAMES and \
                node.id not in self.scope:
            return "mp_getattr(mp_call_import(\"builtins\", \"\", 0), %s, OBJ_NONE)" % (
                c_string(node.id))
        # a top-level function used as a *value* (not called) becomes a closure
        if node.id in self.func_nodes and node.id not in self.scope:
            self.func_values_needed.add(node.id)
            return "make_closure(&%s__tramp, OBJ_NONE)" % self.fnsym(node.id)
        # a class used as a *value* becomes a constructor closure
        if node.id in self.classes and node.id not in self.scope:
            self.class_values_needed.add(node.id)
            return "make_closure(&%s__ctortramp, OBJ_NONE)" % node.id
        # an imported module global / singleton referenced by bare name: make
        # sure it gets an extern declaration emitted by build_externs
        if node.id in self.from_imports and node.id not in self.scope:
            mod = self.from_imports[node.id]
            kind, info = self.xref(node.id, mod)
            if kind == "const":
                # a module-level constant (e.g. an error-message string): inline
                if self.stdlib_root and isinstance(info, bool):
                    return "OBJ_BOOL(%s)" % ("true" if info else "false")
                if self.stdlib_root and isinstance(info, int):
                    return "OBJ_INT(%d)" % info
                if self.stdlib_root and isinstance(info, str):
                    return "OBJ_STR(%s)" % c_string(info)
                return self.const_literal(info)
            if kind in ("singleton", "func", "class"):
                self.used_imports.add((mod, node.id))
                if kind == "class":
                    self.class_values_needed.add(node.id)
                    return "make_closure(&%s__ctortramp, OBJ_NONE)" % node.id
                if kind == "func" and self.stdlib_root:
                    return "mp_call_import(%s, %s, 0)" % (
                        c_string(mod), c_string(node.id))
            if kind == "global" and self.stdlib_root:
                return "mp_call_import(%s, %s, 0)" % (
                    c_string(mod), c_string(node.id))
            if self.stdlib_root:
                return "mp_call_import(%s, %s, 0)" % (
                    c_string(mod), c_string(node.id))
        if self.stdlib_root and node.id in STDLIB_BUILTINS and \
                node.id not in self.scope:
            return "mp_call_import(\"builtins\", %s, 0)" % c_string(node.id)
        if self.stdlib_root and node.id in self.import_alias and \
                node.id not in self.scope:
            return "mp_call_import(%s, %s, 0)" % (
                c_string(self.import_alias[node.id]), c_string(""))
        if node.id == "self" and node.id not in self.scope:
            return "self"
        if node.id in self.mod_global_names and node.id not in self.scope:
            return self._msym(node.id)
        if node.id not in self.scope and self.star_import_mods and \
                self.stdlib_root and node.id not in self.from_imports:
            mod = self.star_import_mods[-1]
            return "mp_call_import(%s, %s, 0)" % (c_string(mod), c_string(node.id))
        if self.stdlib_root and node.id not in self.scope:
            for mod in set(self.from_imports.values()) | \
                    set(self.import_alias.values()) | \
                    set(self.star_import_mods):
                reg = self.load_xmod(mod)
                if not reg:
                    continue
                if node.id in reg.get("globals", {}) or \
                        node.id in reg.get("consts", {}):
                    return "mp_call_import(%s, %s, 0)" % (
                        c_string(mod), c_string(node.id))
            for _cls, (_ci, mod) in self.xclasses.items():
                reg = self.load_xmod(mod)
                if reg and node.id in reg.get("globals", {}):
                    return "mp_call_import(%s, %s, 0)" % (
                        c_string(mod), c_string(node.id))
        if node.id in self.scope:
            return self.lid(node.id)
        return cname(node.id)

    def const_literal(self, v):
        """Render a Python constant (int/str/bool) as a C literal."""
        if v is True:
            return "1"
        if v is False:
            return "0"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return c_string(v)
        return "OBJ_NONE"

    def ex_Constant(self, node):
        v = node.value
        if v is None:
            return "OBJ_NONE"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return c_string(v)
        if isinstance(v, bytes):
            return c_string(v.decode("latin-1"))
        if isinstance(v, float):
            return repr(v)
        return str(v)

    def _class_has_field(self, cls, field):
        """True if class `cls` (local or imported), including bases, declares
        `field` as a real struct member."""
        ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                       if cls in self.xclasses else None)
        if ci is None:
            return False
        try:
            return any(fn == field for fn, _ in ci.full_fields())
        except Exception:
            return any(fn == field for fn, _ in ci.own_fields)

    def _field_owner_subclass(self, base_cls, attr):
        """If `attr` is not declared on `base_cls` but is an own field of
        exactly one of its (transitive) subclasses, return that subclass name.
        Used to faithfully downcast a base pointer to the concrete type the
        Python code assumes (e.g. CType* whose `.signed` lives on IntegerCType).
        """
        pool = list(self.classes.values()) + \
            [ci for ci, _m in self.xclasses.values()]
        cands = set()
        for ci in pool:
            if ci.name == base_cls:
                continue
            c, is_sub = ci, False
            while c is not None:
                if c.name == base_cls:
                    is_sub = True
                    break
                c = c.base
            if is_sub and any(fn == attr for fn, _ in ci.own_fields):
                cands.add(ci.name)
        return next(iter(cands)) if len(cands) == 1 else None

    def _class_ptr_expr(self, node, cls):
        """Render `node` as a `cls*`. A Tier-2 obj (e.g. an isinstance-narrowed
        variable) must be unwrapped with AS_OBJ; a real pointer casts directly."""
        cls = self.ccls(cls)            # qualify ambiguous class names
        e = self.expr(node)
        if self.is_obj_word(node):
            return "(%s*)AS_OBJ(%s)" % (cls, e)
        return "(%s*)(%s)" % (cls, e)

    def ex_Attribute(self, node):
        if node.attr == "__name__" and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "__class__":
            return "TYPE(%s)->name" % self.obj_ptr(node.value.value)
        # type(x).__name__  ->  TYPE(x)->name
        if node.attr == "__name__" and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "type" and node.value.args:
            return "TYPE(%s)->name" % self.obj_ptr(node.value.args[0])
        # type(self).<class-attr>  ->  the attribute's class-level *default*
        # (used to reset an instance field to the original, e.g. restoring the
        # full register list). Resolved by following sibling-name defaults.
        if isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "type" and len(node.value.args) == 1 \
                and isinstance(node.value.args[0], ast.Name) \
                and node.value.args[0].id == "self" and self.cur_class:
            dflt = self._resolve_class_default(self.cur_class, node.attr)
            if dflt is not None:
                return self.wrap_obj(dflt)
        if node.attr == "buffer" and self.stdlib_root and \
                (self.is_obj_word(node.value) or
                 self.value_ctype(node.value) == OBJ):
            return "mp_getattr(%s, %s, OBJ_NONE)" % (
                self.wrap_obj(node.value), c_string(node.attr))
        if self.stdlib_root and isinstance(node.value, ast.Attribute):
            bt = self.value_ctype(node.value)
            if not (bt and bt.endswith("*") and bt != OBJ and
                    self._class_has_field(bt[:-1], node.attr)):
                return "mp_getattr(%s, %s, OBJ_NONE)" % (
                    self.wrap_obj(node.value), c_string(node.attr))
        if isinstance(node.value, ast.Call) and self.stdlib_root:
            return "mp_getattr(%s, %s, OBJ_NONE)" % (
                self.wrap_obj(node.value), c_string(node.attr))
        if isinstance(node.value, ast.Name):
            base = node.value.id
            if base in self.import_alias:
                modname = self.import_alias[base]
                consts = self.load_xmod(modname).get("consts", {})
                if node.attr in consts:
                    return self.const_literal(consts[node.attr])
                kind, info = self.xref(node.attr, modname)
                if kind == "func" and self.stdlib_root:
                    return "mp_call_import(%s, %s, 0)" % (
                        c_string(modname), c_string(node.attr))
                if kind == "global" and self.stdlib_root:
                    return "mp_call_import(%s, %s, 0)" % (
                        c_string(modname), c_string(node.attr))
                if kind in ("singleton", "func", "global"):
                    return cname(node.attr)      # bare exported symbol
                if kind == "class":
                    self.class_values_needed.add(node.attr)
                    return "make_closure(&%s__ctortramp, OBJ_NONE)" % node.attr
                if base in self.modules:
                    mod = self.import_alias.get(base, base)
                    if self.stdlib_root:
                        return "mp_getattr(mp_call_import(%s, %s, 0), %s, OBJ_NONE)" % (
                            c_string(mod), c_string(""), c_string(node.attr))
                    return "%s_%s" % (base, node.attr)
            if base in self.modules:
                mod = self.import_alias.get(base, base)
                if self.stdlib_root:
                    return "mp_getattr(mp_call_import(%s, %s, 0), %s, OBJ_NONE)" % (
                        c_string(mod), c_string(""), c_string(node.attr))
                return "%s_%s" % (base, node.attr)
            if base == "self":
                if self.cur_class:
                    owner = self.static_owner(self.cur_class, node.attr)
                    if owner:
                        return "%s_%s" % (owner.csym, cname(node.attr))
                    if node.attr in self.cur_class.methods:
                        ff = [f for f, _ in self.cur_class.full_fields()]
                        if node.attr not in ff:
                            if node.attr in self.cur_class.property_methods or \
                                    node.attr not in VTABLE_METHODS:
                                return "%s_%s(self)" % (self.cur_class.csym,
                                                        method_cname(node.attr))
                            if self.stdlib_root:
                                return "mp_getattr(%s, %s, OBJ_NONE)" % (
                                    "OBJ_OBJ(self)", c_string(node.attr))
                            return self.vcall(ast.Name(id="self", ctx=node.ctx),
                                              node.attr, [])
                    return "self->%s" % cname(node.attr)
                # cur_class is None (e.g. a lifted nested function): `self` is a
                # typed param, so fall through to the concrete-pointer path.
            # Class.STATIC (local or imported)
            ci = self.classes.get(base) or (self.xclasses[base][0]
                                            if base in self.xclasses else None)
            if ci is not None:
                owner = self.static_owner(ci, node.attr)
                if owner:
                    return "%s_%s" % (owner.csym, cname(node.attr))
            # isinstance-narrowed variable: access the field through its proven
            # concrete class (only when that class really declares the field).
            if base in self.narrowed:
                cls = self.narrowed[base][:-1]
                if self._class_has_field(cls, node.attr):
                    if cls in self.xclasses and cls not in self.classes:
                        self.xstructs_needed.add(cls)
                    return "(%s)->%s" % (self._class_ptr_expr(node.value, cls),
                                         cname(node.attr))
        # access through a concrete class pointer:  p.attr -> (p)->attr
        bt = self.value_ctype(node.value)
        if bt and bt.endswith("*") and bt != OBJ:
            cls = bt[:-1]
            sci = self.classes.get(cls) or (self.xclasses[cls][0]
                                            if cls in self.xclasses else None)
            if self.stdlib_root and sci is not None and \
                    cls in self.xclasses and cls not in self.classes and \
                    not self._class_has_field(cls, node.attr) and \
                    node.attr not in getattr(sci, "static_methods", set()):
                return "mp_getattr(%s, %s, OBJ_NONE)" % (
                    self.wrap_obj(node.value), c_string(node.attr))
            if sci is not None:
                owner = self.static_owner(sci, node.attr)
                if owner:                       # class static via a typed ptr
                    return "%s_%s" % (owner.csym, cname(node.attr))
                if node.attr in sci.methods:
                    ff = [f for f, _ in sci.full_fields()]
                    if node.attr not in ff:
                        recv = self.expr(node.value)
                        if node.attr in sci.property_methods or \
                                node.attr not in VTABLE_METHODS:
                            return "%s_%s(%s)" % (sci.csym, node.attr, recv)
                        return self.vcall(node.value, node.attr, [])
            if cls not in self.xclasses and cls not in self.classes:
                self._load_xclass_anywhere(cls)
            if cls in self.xclasses:
                self.xstructs_needed.add(cls)
            if sci is not None and not self._class_has_field(cls, node.attr):
                sub = self._field_owner_subclass(cls, node.attr)
                if sub:
                    if sub in self.xclasses and sub not in self.classes:
                        self.xstructs_needed.add(sub)
                    return "((%s*)(%s))->%s" % (
                        self.ccls(sub), self.expr(node.value), cname(node.attr))
            return "(%s)->%s" % (self.expr(node.value), cname(node.attr))
        # reading an attribute off a Tier-2 obj: resolve the element's class by
        # which class declares this attribute, then offset into its struct.
        if self.is_obj_word(node.value) or self.value_ctype(node.value) == OBJ:
            owner = self.resolve_attr_owner(node.attr)
            if owner:
                if owner.name in self.xclasses and \
                        owner.name not in self.classes:
                    self.xstructs_needed.add(owner.name)
                return "((%s*)AS_OBJ(%s))->%s" % (
                    owner.csym, self.expr(node.value), cname(node.attr))
            if self.stdlib_root:
                return "mp_getattr(%s, %s, OBJ_NONE)" % (
                    self.wrap_obj(node.value), c_string(node.attr))
            return "OBJ_NONE /* %s.%s */" % (self.src1(node.value), node.attr)
        return "%s.%s" % (self.expr(node.value), cname(node.attr))

    def ex_Call(self, node):
        s = self._ex_call_inner(node)
        # virtual-return typing: an instance method's annotated leaf-class
        # return is emitted with an obj ABI (see _c_ret); recover the typed
        # pointer here so chained member/method access on the result resolves.
        # Module/alias/class-qualified calls and constructors return real
        # pointers already and are skipped.
        f = node.func
        if isinstance(f, ast.Attribute) and not self.ctor_class(node):
            fv = f.value
            qualified = isinstance(fv, ast.Name) and (
                fv.id in self.import_alias or fv.id in self.modules
                or fv.id in self.classes or fv.id in self.xclasses)
            if not qualified:
                rt = self.value_ctype(node)
                if self._is_class_ptr(rt):
                    cls = rt[:-1]
                    if cls in self.xclasses and cls not in self.classes:
                        self.xstructs_needed.add(cls)
                    return "(%s)AS_OBJ(%s)" % (rt, s)
        return s

    def _lower_vararg_local_call(self, fn, fndef, node):
        """Lower a call to a module-local function with *args / **kwargs."""
        n_reg = len(fndef.args.args)
        pos = node.args[:n_reg]
        var = node.args[n_reg:]
        kw = self._lower_call_kwargs(node)
        defs = self.defaults_for(fndef, False)
        creg = self.coerce_args(self.func_params[fn][:n_reg], pos, defs)
        wvar = [self.wrap_obj(a) for a in var]
        parts = list(creg) + [kw, str(len(wvar))] + wvar
        return "%s(%s)" % (self.fnsym(fn), ", ".join(parts))

    def _lower_starred_local_call(self, fn, fndef, node):
        """Expand `f(*seq, ...)` when `f` is a module-local function."""
        if not node.args or not isinstance(node.args[0], ast.Starred):
            return None
        star_val = node.args[0].value
        rest = node.args[1:]
        nparams = len(fndef.args.args)
        nkw = len([k for k in node.keywords if k.arg])
        npos = max(0, nparams - len(rest) - nkw)
        if npos < 0:
            return None
        star_expr = self.expr(star_val)
        unpacked = ["subscript(%s, OBJ_INT(%d))" % (star_expr, i)
                    for i in range(npos)]
        merged = list(unpacked) + list(rest)
        if node.keywords:
            merged = self._merge_keyword_args(fndef, merged, node.keywords)
        defs = self.defaults_for(fndef, False)
        cargs = self.coerce_args(self.func_params[fn], merged, defs)
        return "%s(%s)" % (self.fnsym(fn), ", ".join(cargs))

    def _ex_call_inner(self, node):
        func = node.func
        argstrs = [self.expr(a) for a in node.args]

        if isinstance(func, ast.Lambda):
            clo = self.expr(func)
            wargs = [self.wrap_obj(a) for a in node.args]
            if wargs:
                return "call_obj(%s, %d, %s)" % (clo, len(wargs),
                                                 ", ".join(wargs))
            return "call_closure(%s, list_new())" % clo

        if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and \
                func.func.id == "type" and len(func.args) == 1:
            recv = func.args[0]
            rt = self.value_ctype(recv)
            if isinstance(recv, ast.Name) and recv.id == "self" and self.cur_class:
                ci = self.cur_class
            elif rt and rt.endswith("*") and rt != OBJ:
                cls = rt[:-1]
                ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                               if cls in self.xclasses else None)
            else:
                ci = None
            if ci is not None:
                nargs = [self.wrap_obj(a) for a in node.args]
                if nargs:
                    return "OBJ_OBJ(%s_new(%s))" % (ci.csym, ", ".join(nargs))
                init = ci.methods.get("__init__")
                nparams = len(init.args.args) - 1 if init else 0
                if nparams or (init and init.args.kwarg):
                    cargs = self._pad_ctor_kwargs(init, [])
                    return "OBJ_OBJ(%s_new(%s))" % (ci.csym, ", ".join(cargs))
                return "OBJ_OBJ(%s_new())" % ci.csym

        if isinstance(func, ast.Call):
            inner = self.expr(func)
            args = [self.wrap_obj(a) for a in node.args]
            if args:
                return "mp_call_obj(%s, list_of(%d, %s), dict_new())" % (
                    inner, len(args), ", ".join(args))
            return "mp_call_obj(%s, list_new(), dict_new())" % inner

        if isinstance(func, ast.Name):
            fn = func.id
            if fn == "__closure_env__":
                # closure-converted nested function used as a value: bundle the
                # captured values into an env list and build the closure.
                mangled = node.args[0].value
                self.closure_values_needed.add(mangled)
                caps = node.args[1:]
                env = "list_of(%d, %s)" % (
                    len(caps), ", ".join(self.wrap_obj(c) for c in caps)) \
                    if caps else "list_new()"
                return "make_closure(&%s__tramp, %s)" % (cname(mangled), env)
            if fn == "const" and node.args:
                return self.expr(node.args[0])
            if fn == "isinstance" and len(node.args) == 2:
                return self.lower_isinstance(node.args[0], node.args[1])
            if fn == "_float_to_bits" and len(node.args) == 2:
                size = node.args[1]
                sz = self.expr(size) if self.value_ctype(size) in \
                    ("int", "bool") else "AS_INT(%s)" % self.wrap_obj(size)
                return "float_to_bits(%s, %s)" % (
                    self.wrap_obj(node.args[0]), sz)
            if fn == "str":
                if len(node.args) == 1:
                    return "pystr(%s)" % self.wrap_obj(node.args[0])
                if self.stdlib_root and node.args:
                    return self._mp_import_call("builtins", "str", node)
            if fn == "len" and len(node.args) == 1:
                return "pylen(%s)" % self.wrap_obj(node.args[0])
            if fn == "print":
                if node.args:
                    return "pyprint(%s)" % self.wrap_obj(node.args[0])
                return 'pyprint(OBJ_STR(""))'
            if fn in ("any", "all") and len(node.args) == 1:
                return self.lower_any_all(fn, node.args[0])
            if fn == "bool" and len(node.args) == 1:
                return self.bool_expr(node.args[0])
            if fn == "range":
                a = [self.coerce_to("int", x, self.expr(x))
                     for x in node.args]
                if len(a) == 1:
                    return "pyrange(0, %s, 1)" % a[0]
                if len(a) == 2:
                    return "pyrange(%s, %s, 1)" % (a[0], a[1])
                if len(a) == 3:
                    return "pyrange(%s, %s, %s)" % (a[0], a[1], a[2])
            if fn == "zip" and len(node.args) == 2:
                return "pyzip(%s, %s)" % (self.wrap_obj(node.args[0]),
                                          self.wrap_obj(node.args[1]))
            if fn == "enumerate" and node.args:
                start = self.expr(node.args[1]) if len(node.args) > 1 else "0"
                return "pyenumerate(%s, %s)" % (self.wrap_obj(node.args[0]),
                                                start)
            if fn == "sorted" and node.args:
                return "pysorted(%s)" % self.wrap_obj(node.args[0])
            if fn == "reversed" and node.args:
                return "pyreversed(%s)" % self.wrap_obj(node.args[0])
            if fn in ("max", "min"):
                kw = {k.arg: k.value for k in node.keywords}
                if "default" in kw:
                    dflt, has = self.wrap_obj(kw["default"]), "true"
                else:
                    dflt, has = "OBJ_NONE", "false"
                if len(node.args) == 1:
                    it = self.wrap_obj(node.args[0])
                else:
                    it = "list_of(%d, %s)" % (len(node.args),
                        ", ".join(self.wrap_obj(x) for x in node.args))
                return "py%s(%s, %s, %s)" % (fn, it, dflt, has)
            if fn == "sum" and node.args:
                start = self.wrap_obj(node.args[1]) if len(node.args) > 1 \
                    else "OBJ_INT(0)"
                return "pysum(%s, %s)" % (self.wrap_obj(node.args[0]), start)
            if fn == "set":
                return "pyset(%s)" % self.wrap_obj(node.args[0]) if node.args \
                    else "list_new() /* set() */"
            if fn == "list":
                return "pylist(%s)" % self.wrap_obj(node.args[0]) if node.args \
                    else "list_new()"
            if fn == "dict":
                if not node.args:
                    return "dict_new()"
                if self.stdlib_root:
                    return self._mp_import_call("builtins", "dict", node)
                return "dict_new() /* dict(arg) unsupported */"
            if fn == "ord" and node.args:
                return "pyord(%s)" % self.wrap_obj(node.args[0])
            if fn == "chr" and node.args:
                return "pychr(%s)" % self.as_long(node.args[0])
            if fn == "int":
                if len(node.args) == 2:
                    return "py_int_base(%s, %s)" % (self.as_str(node.args[0]),
                                                    self.expr(node.args[1]))
                return "pyint(%s)" % self.wrap_obj(node.args[0]) if node.args \
                    else "0"
            if fn == "abs" and node.args:
                return "pyabs(%s)" % self.as_long(node.args[0])
            if fn == "float" and node.args:
                return "pyfloat(%s)" % self.wrap_obj(node.args[0])
            if fn == "repr" and node.args:
                return "pyrepr(%s)" % self.wrap_obj(node.args[0])
            if fn == "vars":
                return "dict_new() /* vars() unsupported */"
            if fn == "hasattr" and len(node.args) == 2 and \
                    isinstance(node.args[1], ast.Constant) and \
                    isinstance(node.args[1].value, str):
                # Structural approximation (yields a C bool): an object "has" an
                # attribute iff it is an instance of the class that declares it.
                # Dynamically-set attributes (no declared owner) read as absent.
                owner = self.resolve_attr_owner(node.args[1].value)
                if owner:
                    return self.lower_isinstance(
                        node.args[0], ast.Name(id=owner.name))
                if self.stdlib_root:
                    return "mp_hasattr(%s, %s)" % (
                        self.wrap_obj(node.args[0]),
                        c_string(node.args[1].value))
                return "0 /* hasattr: dynamic attr, unsupported */"
            if fn == "hasattr" and len(node.args) == 2 and self.stdlib_root \
                    and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                return "mp_hasattr(%s, %s)" % (
                    self.wrap_obj(node.args[0]), c_string(node.args[1].value))
            if fn == "getattr" and len(node.args) >= 2 and \
                    isinstance(node.args[1], ast.Constant) and \
                    isinstance(node.args[1].value, str):
                attr = node.args[1].value
                dflt = self.wrap_obj(node.args[2]) if len(node.args) > 2 \
                    else "OBJ_NONE"
                owner = self.resolve_attr_owner(attr)
                if owner:
                    if owner.name in self.xclasses and \
                            owner.name not in self.classes:
                        self.xstructs_needed.add(owner.name)
                    return "((%s*)AS_OBJ(%s))->%s" % (
                        owner.csym, self.wrap_obj(node.args[0]), cname(attr))
                if self.stdlib_root:
                    return "mp_getattr(%s, %s, %s)" % (
                        self.wrap_obj(node.args[0]), c_string(attr), dflt)
                return dflt
            if fn == "getattr" and len(node.args) >= 2:
                if self.stdlib_root and isinstance(node.args[1], ast.Constant) \
                        and isinstance(node.args[1].value, str):
                    dflt = self.wrap_obj(node.args[2]) if len(node.args) > 2 \
                        else "OBJ_NONE"
                    return "mp_getattr(%s, %s, %s)" % (
                        self.wrap_obj(node.args[0]),
                        c_string(node.args[1].value), dflt)
                if self.stdlib_root:
                    dflt = self.wrap_obj(node.args[2]) if len(node.args) > 2 \
                        else "OBJ_NONE"
                    return "mp_getattr_obj(%s, %s, %s)" % (
                        self.wrap_obj(node.args[0]),
                        self.wrap_obj(node.args[1]), dflt)
                return self.wrap_obj(node.args[2]) if len(node.args) > 2 \
                    else "OBJ_NONE /* getattr: dynamic attr, unsupported */"
            if fn in self.classes:
                ci = self.classes[fn]
                init = ci.methods.get("__init__")
                if init and init.args.vararg:
                    n_regular = len(init.args.args) - 1
                    regular_args = node.args[:n_regular]
                    var_args = node.args[n_regular:]
                    defs = self.defaults_for(init, True)
                    cargs = self.coerce_args(
                        self.init_param_ctypes(ci), regular_args, defs)
                    wrapped = [self.wrap_obj(a) for a in var_args]
                    if not wrapped:
                        return "%s_new(0)" % ci.csym
                    return "%s_new(%d, %s)" % (
                        ci.csym, len(wrapped), ", ".join(wrapped))
                defs = self.defaults_for(init, True) if init else None
                merged = self._merge_keyword_args(init, node.args,
                                                  node.keywords, True) \
                    if init and node.keywords else node.args
                cargs = self.coerce_args(
                    self.init_param_ctypes(ci), merged, defs)
                cargs = self._pad_ctor_kwargs(init, cargs)
                return "%s_new(%s)" % (ci.csym, ", ".join(cargs))
            if fn in self.func_params:
                fndef = self.func_nodes[fn]
                if fndef.args.vararg:
                    return self._lower_vararg_local_call(fn, fndef, node)
                if any(isinstance(a, ast.Starred) for a in node.args):
                    starcall = self._lower_starred_local_call(fn, fndef, node)
                    if starcall:
                        return starcall
                merged = self._merge_keyword_args(fndef, node.args,
                                                  node.keywords) \
                    if node.keywords else node.args
                defs = self.defaults_for(fndef, False)
                cargs = self.coerce_args(self.func_params[fn], merged, defs)
                return "%s(%s)" % (self.fnsym(fn), ", ".join(cargs))
            if fn in self.from_imports:
                kind, info = self.xref(fn, self.from_imports[fn])
                if kind == "class":
                    init = info.methods.get("__init__")
                    pct = [arg_ctype(init, a) for a in init.args.args[1:]] \
                        if init else []
                    defs = self.defaults_for(init, True) if init else None
                    merged = self._merge_keyword_args(init, node.args,
                                                      node.keywords, True) \
                        if init and node.keywords else node.args
                    cargs = self.coerce_args(pct, merged, defs)
                    cargs = self._pad_ctor_kwargs(init, cargs)
                    return "%s_new(%s)" % (info.csym, ", ".join(cargs))
                if kind == "func":
                    pct = [arg_ctype(info, a) for a in info.args.args]
                    defs = self.defaults_for(info, False)
                    merged = self._merge_keyword_args(info, node.args,
                                                      node.keywords) \
                        if node.keywords else node.args
                    cargs = self.coerce_args(pct, merged, defs)
                    return "%s(%s)" % (fn, ", ".join(cargs))
                if self.stdlib_root:
                    return self._mp_import_call(self.from_imports[fn], fn, node)
            if fn in self.mod_global_types and self.mod_global_types[fn] == OBJ \
                    and fn not in self.scope:
                args = [self.wrap_obj(a) for a in node.args]
                if args:
                    return "call_obj(%s, %d, %s)" % (self._msym(fn), len(args),
                                                     ", ".join(args))
                return "call_closure(%s, list_new())" % self._msym(fn)
            if self.stdlib_root and fn in STDLIB_BUILTINS:
                return self._mp_import_call("builtins", fn, node)
            if fn in self.from_imports and fn not in self.scope \
                    and fn not in self.func_params and fn not in self.func_nodes:
                return self._mp_import_call(self.from_imports[fn], fn, node)
            if fn not in self.scope and fn not in self.from_imports \
                    and self.star_import_mods and self.stdlib_root \
                    and fn not in self.func_nodes and fn not in self.classes:
                return self._mp_import_call(self.star_import_mods[-1], fn, node)
            if fn in ("metadata", "module", "require", "package"):
                args = [self.expr(a) for a in node.args]
                if fn == "metadata":
                    return "metadata()"
                return "%s(%s)" % (fn, ", ".join(args))
            if self.stdlib_root and fn in EXCEPTION_NAMES:
                return "mp_getattr(mp_call_import(\"builtins\", \"\", 0), %s, OBJ_NONE)" % (
                    c_string(fn))
            # calling an obj-typed local/param: a first-class function value
            if fn in self.scope and self.scope[fn] == OBJ \
                    and fn not in self.func_params and fn not in self.classes:
                varcall = self._lower_varcall(self.fnsym(fn), node)
                if varcall:
                    return varcall
                args = [self.wrap_obj(a) for a in node.args]
                if args:
                    return "call_obj(%s, %d, %s)" % (self.fnsym(fn), len(args),
                                                     ", ".join(args))
                return "call_closure(%s, list_new())" % self.fnsym(fn)

        if isinstance(func, ast.Attribute) and is_super_call(func.value):
            base = self.cur_class.base if self.cur_class else None
            if base is None:
                # base is external/builtin (e.g. Exception): nothing to chain to
                return "(void)0"
            bname = base.name
            if func.attr == "__init__":
                init = base.methods.get("__init__")
                pct = self.init_param_ctypes(base) if base else []
                defs = self.defaults_for(init, True) if init else None
                cargs = self.coerce_args(pct, node.args, defs)
                if bname not in self.classes:       # imported base: extern decls
                    self.xstructs_needed.add(bname)
                    self.used_xmethods[(bname, "__init__")] = "void"
                return "%s___init__((%s*)self%s)" % (
                    base.csym, base.csym,
                    (", " + ", ".join(cargs)) if cargs else "")
            if func.attr in VTABLE_METHODS:
                return "%s_%s((Obj*)self%s)" % (
                    base.csym, func.attr,
                    (", " + ", ".join(argstrs)) if argstrs else "")
            return "%s_%s((%s*)self%s)" % (
                base.csym, func.attr, base.csym,
                (", " + ", ".join(argstrs)) if argstrs else "")

        if isinstance(func, ast.Attribute):
            # float.fromhex("0x1.8p3") -- C's strtod parses hex float literals.
            if func.attr == "fromhex" and isinstance(func.value, ast.Name) \
                    and func.value.id == "float" and node.args:
                return "float_fromhex(%s)" % self.as_str(node.args[0])
            # const-dict .get() (e.g. self.size_map.get(...)) takes priority
            if func.attr == "get" and isinstance(func.value, ast.Attribute) \
                    and isinstance(func.value.value, ast.Name) \
                    and func.value.value.id == "self" and self.cur_class \
                    and func.value.attr in self.cur_class.const_dicts:
                d = func.value.attr
                k = argstrs[0]
                dflt = argstrs[1] if len(argstrs) > 1 else '""'
                return "%s_%s_get(%s, %s)" % (self.cur_class.name, d, k, dflt)
            # a method defined in exactly one imported hierarchy (e.g. the
            # CType predicates is_void/is_integral/...) dispatches through that
            # module's canonical vtable -- correct for a non-leaf base and
            # uniform whether the receiver is a typed pointer or a bare obj.
            xm = self._exclusive_vt_module(func.attr)
            if xm is not None and not self.ctor_class(node):
                fv = func.value
                if not (isinstance(fv, ast.Name) and (
                        fv.id in self.import_alias or fv.id in self.modules
                        or fv.id in self.classes or fv.id in self.xclasses)):
                    return self.xvcall(xm, func.value, func.attr, node.args)
            # method call on a concrete class instance (local or imported) is
            # resolved before the generic list/dict/str heuristics so that an
            # instance method named append/add/get/pop/etc. wins.
            rcv = self.method_on_instance(func, node)
            if rcv is not None:
                return rcv
            # list methods on an obj/list receiver
            if func.attr == "append" and len(node.args) == 1:
                return "list_append(%s, %s)" % (self.expr(func.value),
                                                self.wrap_obj(node.args[0]))
            if func.attr == "extend" and len(node.args) == 1 and \
                    func.attr not in self.method_owners:
                return "list_extend(%s, %s)" % (self.wrap_obj(func.value),
                                                self.wrap_obj(node.args[0]))
            if func.attr == "add" and len(node.args) == 1 and \
                    func.attr not in self.method_owners:
                return "set_add(%s, %s)" % (self.wrap_obj(func.value),
                                            self.wrap_obj(node.args[0]))
            if func.attr == "remove" and len(node.args) == 1 and \
                    func.attr not in self.method_owners:
                return "list_remove(%s, %s)" % (self.wrap_obj(func.value),
                                                self.wrap_obj(node.args[0]))
            if func.attr == "pop" and not node.args and \
                    not self._local_method_accepts_argc("pop", 0):
                return "list_pop(%s)" % self.wrap_obj(func.value)
            if func.attr == "index" and len(node.args) == 1 and \
                    func.attr not in self.method_owners:
                return "list_index(%s, %s)" % (self.wrap_obj(func.value),
                                               self.wrap_obj(node.args[0]))
            if func.attr == "sort":
                return "list_sort(%s)" % self.expr(func.value)
            if func.attr == "reverse":
                return "/* .reverse() omitted */ (void)0"
            # string methods (guard against user methods of the same name)
            if func.attr in self.STR_METHODS and \
                    func.attr not in self.method_owners:
                r = self.lower_str_method(func, node)
                if r is not None:
                    return r
            # dict methods on an obj receiver
            if func.attr == "items" and not node.args:
                return "dict_items(%s)" % self.wrap_obj(func.value)
            if func.attr == "keys" and not node.args:
                return "dict_keys(%s)" % self.wrap_obj(func.value)
            if func.attr == "values" and not node.args:
                return "dict_values(%s)" % self.wrap_obj(func.value)
            if func.attr == "update" and len(node.args) == 1:
                return "dict_update(%s, %s)" % (self.wrap_obj(func.value),
                                                self.wrap_obj(node.args[0]))
            if func.attr == "setdefault":
                k = self.wrap_obj(node.args[0])
                d = self.wrap_obj(node.args[1]) if len(node.args) > 1 \
                    else "OBJ_NONE"
                return "dict_setdefault(%s, %s, %s)" % (
                    self.wrap_obj(func.value), k, d)
            if func.attr == "pop" and node.args:
                k = self.wrap_obj(node.args[0])
                d = self.wrap_obj(node.args[1]) if len(node.args) > 1 \
                    else "OBJ_NONE"
                return "dict_pop(%s, %s, %s)" % (self.wrap_obj(func.value), k, d)
            if func.attr == "get" and node.args:
                k = self.wrap_obj(node.args[0])
                d = self.wrap_obj(node.args[1]) if len(node.args) > 1 \
                    else "OBJ_NONE"
                return "dict_get(%s, %s, %s)" % (self.wrap_obj(func.value), k, d)
            if isinstance(func.value, ast.Name) and \
                    func.value.id in self.import_alias:
                modname = self.import_alias[func.value.id]
                kind, info = self.xref(func.attr, modname)
                if kind == "class":
                    init = info.methods.get("__init__")
                    pct = [arg_ctype(init, a) for a in init.args.args[1:]] \
                        if init else []
                    defs = self.defaults_for(init, True) if init else None
                    merged = self._merge_keyword_args(init, node.args,
                                                      node.keywords, True) \
                        if init and node.keywords else node.args
                    cargs = self.coerce_args(pct, merged, defs)
                    cargs = self._pad_ctor_kwargs(init, cargs)
                    return "%s_new(%s)" % (info.csym, ", ".join(cargs))
                if kind == "func":
                    pct = [arg_ctype(info, a) for a in info.args.args]
                    defs = self.defaults_for(info, False)
                    merged = self._merge_keyword_args(info, node.args,
                                                      node.keywords) \
                        if node.keywords else node.args
                    cargs = self.coerce_args(pct, merged, defs)
                    return "%s(%s)" % (cname(func.attr), ", ".join(cargs))
                if self.stdlib_root:
                    return self._mp_import_call(modname, func.attr, node)
                if not modname.startswith("shivyc"):
                    if modname == "copy" and func.attr == "copy":
                        cc = self._shallow_copy(node)
                        if cc:
                            return cc
                    for a in node.args:
                        self.expr(a)
                    return "OBJ_NONE /* %s.%s(...) unsupported */" % (
                        func.value.id, func.attr)
                if func.value.id in self.modules:
                    mod = self.import_alias.get(func.value.id, func.value.id)
                    if self.stdlib_root:
                        return self._mp_import_call(mod, func.attr, node)
                    return "%s_%s(%s)" % (func.value.id, func.attr,
                                          ", ".join(argstrs))
            # a method name that collides between a local class method and an
            # imported polymorphic hierarchy (e.g. `make_asm`: ASMCode defines
            # a 0-arg make_asm, while ILCommand's is make_asm(spotmap, ...)).
            # When the call's arity cannot fit any local method, it must be the
            # hierarchy method -> dispatch through the cross-module vtable.
            if func.attr in self.hierarchy_method and \
                    not self._local_method_accepts_argc(func.attr,
                                                         len(node.args)):
                return self.xvcall(self.hierarchy_method[func.attr],
                                   func.value, func.attr, node.args)
            if func.attr in VTABLE_METHODS:
                return self.vcall(func.value, func.attr, node.args)
            # concrete class. Safe to devirtualize only for a leaf class, so no
            # subclass can override the method at runtime.
            if isinstance(func.value, ast.Name) and \
                    func.value.id in self.narrowed:
                cls = self.narrowed[func.value.id][:-1]
                ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                               if cls in self.xclasses else None)
                if ci is not None and self._class_is_leaf(cls):
                    owner = ci.find_method_owner(func.attr)
                    if owner is None and func.attr in ci.methods:
                        owner = ci
                    if owner is not None and func.attr in owner.methods:
                        m = owner.methods[func.attr]
                        if owner.name in self.xclasses and \
                                owner.name not in self.classes:
                            self.used_xmethods[(owner.name, func.attr)] = \
                                self._c_ret(m)
                        return self._format_direct_method_call(
                            owner, m, func.value, node.args)
            # non-virtual method on a concrete class pointer
            bt = self.value_ctype(func.value)
            if bt and bt.endswith("*") and bt != OBJ and bt[:-1] in self.classes:
                ci = self.classes[bt[:-1]]
                owner = ci.find_method_owner(func.attr)
                if owner:
                    m = owner.methods.get(func.attr)
                    if m is not None:
                        return self._format_direct_method_call(
                            owner, m, func.value, node.args)
            # method on a concrete *imported* class pointer (e.g. narrowed by
            # isinstance). Safe to devirtualize only when the static class is a
            # leaf, so no subclass can override the method at runtime.
            if bt and bt.endswith("*") and bt != OBJ and bt[:-1] in self.xclasses \
                    and self._class_is_leaf(bt[:-1]):
                cls = bt[:-1]
                ci = self.xclasses[cls][0]
                m = ci.methods.get(func.attr)
                if m is not None:
                    ret = self._c_ret(m)
                    self.used_xmethods[(cls, func.attr)] = ret
                    pct = [arg_ctype(m, a) for a in m.args.args[1:]]
                    cargs = self.coerce_args(pct, node.args)
                    return "%s_%s(%s%s)" % (
                        cls, func.attr,
                        self._class_ptr_expr(func.value, cls),
                        (", " + ", ".join(cargs)) if cargs else "")
            if isinstance(func.value, ast.Name) and \
                    func.value.id in self.classes:
                return "%s_%s(%s)" % (func.value.id, func.attr,
                                      ", ".join(argstrs))
            # method call on an untyped obj: resolve a unique non-vtable owner
            if self.is_obj_word(func.value) or \
                    self.value_ctype(func.value) == OBJ:
                owner = self.resolve_method_owner(func.attr)
                if owner:
                    m = owner.methods.get(func.attr)
                    pct = [arg_ctype(m, a) for a in m.args.args[1:]] \
                        if m else []
                    cargs = self.coerce_args(pct, node.args)
                    return "%s_%s((%s*)AS_OBJ(%s)%s)" % (
                        owner.csym, func.attr, owner.csym,
                        self.expr(func.value),
                        (", " + ", ".join(cargs)) if cargs else "")
                xowner = self.resolve_xmethod_owner(func.attr)
                if xowner:
                    m = xowner.methods.get(func.attr)
                    ret = self._c_ret(m) if m else OBJ
                    self.used_xmethods[(xowner.name, func.attr)] = ret
                    args = [self.expr(a) for a in node.args]
                    return "%s_%s((%s*)AS_OBJ(%s)%s)" % (
                        xowner.csym, func.attr, xowner.csym,
                        self.expr(func.value),
                        (", " + ", ".join(args)) if args else "")
                xmod = self.resolve_xvirtual(func.attr)
                if xmod:
                    return self.xvcall(xmod, func.value, func.attr, node.args)
                # method belonging to an imported hierarchy whose root spans
                # modules: dispatch through the root module's canonical vtable
                if func.attr in self.hierarchy_method:
                    return self.xvcall(self.hierarchy_method[func.attr],
                                       func.value, func.attr, node.args)
            if func.attr == "insert" and len(node.args) == 2 and \
                    func.attr not in self.method_owners:
                lo = self.coerce_to("int", node.args[0], self.expr(node.args[0]))
                return "list_insert(%s, %s, %s)" % (
                    self.wrap_obj(func.value), lo, self.wrap_obj(node.args[1]))
            if isinstance(func.value, ast.Name) and \
                    func.value.id in self.from_imports:
                sym = func.value.id
                if func.attr == sym:
                    kind, info = self.xref(sym, self.from_imports[sym])
                    if kind == "class":
                        init = info.methods.get("__init__")
                        pct = [arg_ctype(init, a) for a in init.args.args[1:]] \
                            if init else []
                        defs = self.defaults_for(init, True) if init else None
                        merged = self._merge_keyword_args(init, node.args,
                                                          node.keywords, True) \
                            if init and node.keywords else node.args
                        cargs = self.coerce_args(pct, merged, defs)
                        cargs = self._pad_ctor_kwargs(init, cargs)
                        return "%s_new(%s)" % (info.csym, ", ".join(cargs))
            if self.stdlib_root:
                return self._mp_method_call(func.value, func.attr, node)
            recv = self.expr(func.value)
            return "%s(%s)" % (func.attr, ", ".join([recv] + argstrs))

        # calling any *complex* obj-valued expression (e.g. the result of a
        # method that returns a function/constructor) dispatches through the
        # closure ABI; a bare Name here is an unhandled builtin -> plain call
        if isinstance(func, ast.Name) and func.id == "cls" and self.cur_class:
            ci = self.cur_class
            init = ci.methods.get("__init__")
            if init:
                defs = self.defaults_for(init, True)
                merged = self._merge_keyword_args(init, node.args, node.keywords,
                                                  True) \
                    if node.keywords else node.args
                cargs = self.coerce_args(self.init_param_ctypes(ci), merged, defs)
                cargs = self._pad_ctor_kwargs(init, cargs)
                return "OBJ_OBJ(%s_new(%s))" % (ci.csym, ", ".join(cargs))
        if not isinstance(func, ast.Name) and \
                (self.value_ctype(func) == OBJ or self.is_obj_word(func)):
            varcall = self._lower_varcall(self.expr(func), node)
            if varcall:
                return varcall
            wargs = [self.wrap_obj(a) for a in node.args]
            if wargs:
                return "call_obj(%s, %d, %s)" % (self.expr(func), len(wargs),
                                                 ", ".join(wargs))
            return "call_closure(%s, list_new())" % self.expr(func)
        if isinstance(func, ast.Name) and func.id in self.scope and \
                self.scope[func.id] == OBJ and func.id not in self.func_params \
                and func.id not in self.classes:
            varcall = self._lower_varcall(cname(func.id), node)
            if varcall:
                return varcall
        return "%s(%s)" % (self.expr(func), ", ".join(argstrs))

    def _merge_keyword_args(self, fndef, args, keywords, skip_self=False):
        """Merge explicit keywords into a positional arg list by param name."""
        params = fndef.args.args[1:] if skip_self else fndef.args.args
        names = [a.arg for a in params]
        by_name = {k.arg: k.value for k in keywords if k.arg}
        merged = list(args)
        while len(merged) < len(names):
            pname = names[len(merged)]
            if pname in by_name:
                merged.append(by_name[pname])
            else:
                break
        return merged

    def _pad_ctor_kwargs(self, init, cargs):
        """Ensure **kwargs and trailing defaults are present for a constructor."""
        if init is None:
            return cargs
        cargs = list(cargs)
        pos_defs = self.defaults_for(init, True)
        ko = init.args.kwonlyargs
        kd = init.args.kw_defaults
        ko_defs = []
        for i in range(len(ko)):
            di = i - (len(ko) - len(kd))
            ko_defs.append(kd[di] if di >= 0 else None)
        all_defs = pos_defs + ko_defs
        nparams = len(init.args.args) - 1 + len(ko)
        while len(cargs) < nparams:
            i = len(cargs)
            if i < len(all_defs) and all_defs[i] is not None:
                cargs.append(self.wrap_obj(all_defs[i]))
            elif init.args.kwarg and i + 1 < len(init.args.args) and \
                    init.args.kwarg.arg == init.args.args[i + 1].arg:
                cargs.append("dict_new()")
            else:
                cargs.append("OBJ_NONE")
        return cargs

    def _lower_varcall(self, func_expr, node):
        """Lower func(*args, **kwargs) style calls on a dynamic callable."""
        star = None
        prefix = []
        for a in node.args:
            if isinstance(a, ast.Starred):
                v = a.value
                if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Add):
                    star = "obj_add(%s, %s)" % (self.expr(v.left),
                                                self.expr(v.right))
                else:
                    star = self.expr(v)
            else:
                prefix.append(self.wrap_obj(a))
        if star is None and not node.keywords:
            return None
        if star is None:
            if prefix:
                star = "list_of(%d, %s)" % (len(prefix), ", ".join(prefix))
            else:
                star = "list_new()"
        elif prefix:
            star = "obj_add(list_of(%d, %s), %s)" % (
                len(prefix), ", ".join(prefix), star)
        kw = self._lower_call_kwargs(node)
        return "mp_call_obj(%s, %s, %s)" % (func_expr, star, kw)

    def _lower_call_kwargs(self, node):
        if not node.keywords:
            return "dict_new()"
        if len(node.keywords) == 1 and node.keywords[0].arg is None:
            return self.expr(node.keywords[0].value)
        parts = []
        for k in node.keywords:
            if k.arg:
                parts.append("OBJ_STR(%s)" % c_string(k.arg))
                parts.append(self.wrap_obj(k.value))
        if not parts:
            return "dict_new()"
        return "dict_of(%d, %s)" % (len(parts) // 2, ", ".join(parts))

    # ---- OO call lowering helpers ---------------------------------------

    def obj_ptr(self, node):
        s = self.expr(node)
        if self.is_obj_word(node) or self.value_ctype(node) == OBJ:
            return "AS_OBJ(%s)" % s
        return "(Obj*)(%s)" % s

    def vtable_recv(self, recv_node):
        """Single (Obj*) cast of a receiver for TYPE()/vtable dispatch."""
        s = self.expr(recv_node)
        if self.is_obj_word(recv_node) or self.value_ctype(recv_node) == OBJ:
            return "AS_OBJ(%s)" % s
        return "(Obj*)(%s)" % s

    def _mp_method_call_args(self, recv, attr, arg_nodes, fndef=None):
        args = list(arg_nodes)
        if fndef is not None:
            params = fndef.args.args[1:]
            defs = self.defaults_for(fndef, True)
            for i in range(len(args), len(params)):
                if i < len(defs) and defs[i] is not None:
                    args.append(defs[i])
        wrapped = [self.wrap_obj(a) for a in args]
        r = self.wrap_obj(recv) if not isinstance(recv, str) else recv
        sa = c_string(attr)
        n = len(wrapped)
        if n == 0:
            return "mp_call_method(%s, %s, 0)" % (r, sa)
        if n == 1:
            return "mp_call_method(%s, %s, 1, %s)" % (r, sa, wrapped[0])
        if n == 2:
            return "mp_call_method(%s, %s, 2, %s, %s)" % (
                r, sa, wrapped[0], wrapped[1])
        return "mp_call_method(%s, %s, %d, %s)" % (
            r, sa, n, ", ".join(wrapped))

    def _method_has_varargs(self, fndef):
        if fndef is None:
            return True
        return bool(fndef.args.vararg or fndef.args.kwarg or
                    fndef.args.kwonlyargs)

    def _format_direct_method_call(self, owner, m, recv_node, arg_nodes):
        """Direct call to a concrete (non-vtable) class method."""
        recv = self._class_ptr_expr(recv_node, owner.csym)
        pct = [arg_ctype(m, a) for a in m.args.args[1:]]
        n_named = len(pct)
        if m.args.vararg and m.name in VTABLE_METHODS:
            extra = arg_nodes[n_named:]
            wrapped = [self.coerce_to(OBJ, a, self.expr(a)) for a in extra]
            parts = [recv]
            if wrapped:
                parts.append("list_of(%d, %s)" % (len(wrapped),
                                                 ", ".join(wrapped)))
            else:
                parts.append("list_new()")
            return "%s_%s(%s)" % (owner.csym, m.name, ", ".join(parts))
        if m.args.vararg and m.name not in VTABLE_METHODS:
            extra = arg_nodes[n_named:]
            wrapped = [self.coerce_to(OBJ, a, self.expr(a)) for a in extra]
            parts = [recv]
            if n_named:
                parts.extend(self.coerce_args(pct, arg_nodes[:n_named]))
            parts.append(str(len(wrapped)))
            parts.extend(wrapped)
            return "%s_%s(%s)" % (owner.csym, m.name, ", ".join(parts))
        cargs = self.coerce_args(pct, arg_nodes)
        return "%s_%s(%s%s)" % (
            owner.csym, m.name, recv,
            (", " + ", ".join(cargs)) if cargs else "")

    def vcall(self, recv_node, meth, arg_nodes):
        _, pct, fndef = self.method_proto(meth)
        if self.stdlib_root:
            return self._mp_method_call_args(recv_node, meth, arg_nodes, fndef)
        if self._method_has_varargs(fndef) or len(arg_nodes) > len(pct):
            return self._mp_method_call_args(recv_node, meth, arg_nodes, fndef)
        xo = self.vtable_recv(recv_node)
        defs = self.defaults_for(fndef, True) if fndef else None
        cargs = self.coerce_args(pct, arg_nodes, defs)
        return "TYPE(%s)->%s(%s)" % (xo, vslot_name(meth), ", ".join([xo] + cargs))

    def init_param_ctypes(self, ci):
        init = ci.methods.get("__init__")
        if not init:
            return []
        return [arg_ctype(init, a) for a in init.args.args[1:]]

    def coerce_args(self, param_ctypes, arg_nodes, default_nodes=None):
        out = []
        n = len(param_ctypes) if param_ctypes else len(arg_nodes)
        for i in range(n):
            target = param_ctypes[i] if i < len(param_ctypes) else None
            if i < len(arg_nodes):
                a = arg_nodes[i]
            elif default_nodes and i < len(default_nodes) and \
                    default_nodes[i] is not None:
                a = default_nodes[i]
            else:
                break                       # no more provided args / defaults
            out.append(self.coerce_to(target, a, self.expr(a)))
        # any extra provided args beyond known params (e.g. *args) pass through
        for i in range(n, len(arg_nodes)):
            out.append(self.expr(arg_nodes[i]))
        return out

    def defaults_for(self, fndef, skip_self):
        """List aligned to params: default value node or None."""
        params = fndef.args.args[1:] if skip_self else fndef.args.args
        defs = fndef.args.defaults
        nd = len(defs)
        n = len(params)
        return [None] * (n - nd) + list(defs)

    def _local_method_accepts_argc(self, attr, argc):
        """True if some local class declares a method `attr` callable with
        `argc` positional args (beyond self). Used to disambiguate a builtin
        container method (list/dict `.pop()`, etc.) from a user method of the
        same name: if no user method can take the given argument count, the
        call must be the builtin."""
        for ci in self.method_owners.get(attr, []):
            fn = ci.methods.get(attr)
            if not fn:
                continue
            params = fn.args.args[1:]              # drop self
            lo = len(params) - len(fn.args.defaults)
            hi = (1 << 30) if fn.args.vararg else len(params)
            if lo <= argc <= hi:
                return True
        return False

    def method_on_instance(self, func, node):
        """If `func.value` is a concrete class instance (local or imported) and
        the class declares `func.attr` as a method, emit the direct call."""
        bt = self.value_ctype(func.value)
        if not (bt and bt.endswith("*") and bt != OBJ):
            return None
        cls = bt[:-1]
        if cls in self.classes:
            ci = self.classes[cls]
            owner = ci.find_method_owner(func.attr)
            if owner is None:
                return None
            if func.attr in owner.static_methods:    # @staticmethod: no receiver
                m = owner.methods.get(func.attr)
                pct = [arg_ctype(m, a) for a in m.args.args] if m else []
                defs = self.defaults_for(m, False) if m else None
                cargs = self.coerce_args(pct, node.args, defs)
                return "%s_%s(%s)" % (owner.csym, func.attr, ", ".join(cargs))
            if func.attr in VTABLE_METHODS:
                return self.vcall(func.value, func.attr, node.args)
            m = owner.methods.get(func.attr)
            if m is not None:
                return self._format_direct_method_call(
                    owner, m, func.value, node.args)
        if cls in self.xclasses:
            ci, modname = self.xclasses[cls]
            owner = ci.find_method_owner(func.attr)
            if owner is None:
                return None
            if func.attr in owner.static_methods:    # @staticmethod: no receiver
                m = owner.methods.get(func.attr)
                ret = self._c_ret(m) if m else OBJ
                self.used_xmethods[(owner.name, func.attr)] = ret
                args = [self.expr(a) for a in node.args]
                return "%s_%s(%s)" % (owner.csym, func.attr, ", ".join(args))
            m = owner.methods.get(func.attr)
            if m is not None:
                ret = self._c_ret(m)
                self.used_xmethods[(owner.name, func.attr)] = ret
                return self._format_direct_method_call(
                    owner, m, func.value, node.args)
        return None

    BUILTIN_TYPE_TAGS = {"str": "T_STR", "bytes": "T_STR", "bytearray": "T_OBJ",
                         "int": "T_INT",
                         "bool": "T_BOOL", "list": "T_LIST", "tuple": "T_LIST",
                         "set": "T_LIST", "frozenset": "T_LIST",
                         "dict": "T_DICT"}

    def lower_isinstance(self, val_node, cls_node):
        if isinstance(cls_node, ast.Name) and \
                cls_node.id in getattr(self, "tuple_type_globals", {}):
            parts = [self.lower_isinstance(val_node, ast.Name(id=t))
                     for t in self.tuple_type_globals[cls_node.id]]
            return "(" + " || ".join(parts) + ")"
        if isinstance(cls_node, (ast.Tuple, ast.List)):
            parts = [self.lower_isinstance(val_node, e) for e in cls_node.elts]
            return "(" + " || ".join(parts) + ")"
        # builtin types -> Tier-2 tag test
        if isinstance(cls_node, ast.Name) and \
                cls_node.id in self.BUILTIN_TYPE_TAGS and \
                cls_node.id not in self.classes:
            tag = self.BUILTIN_TYPE_TAGS[cls_node.id]
            return "((%s).tag == %s)" % (self.wrap_obj(val_node), tag)
        type_sym = self._type_symbol(cls_node)
        if self.is_obj_word(val_node) or self.value_ctype(val_node) == OBJ:
            return "OBJ_ISINST(%s, %s)" % (self.expr(val_node), type_sym)
        return "isinstance_of((Obj*)(%s), %s)" % (self.expr(val_node),
                                                  type_sym)

    def _type_symbol(self, cls_node):
        """`&Cls_type` for a class reference (local, alias.Cls, or imported)."""
        clsname = None
        if isinstance(cls_node, ast.Name):
            clsname = cls_node.id
            if clsname not in self.classes and clsname in self.from_imports:
                self.xref(clsname, self.from_imports[clsname])
        elif isinstance(cls_node, ast.Attribute) and \
                isinstance(cls_node.value, ast.Name) and \
                cls_node.value.id in self.import_alias:
            clsname = cls_node.attr
            self.xref(clsname, self.import_alias[cls_node.value.id])
        if clsname:
            if clsname in self.classes or clsname in self.xclasses:
                return "&%s_type" % self.ccls(clsname)
            if clsname in self.from_imports:
                kind, _ = self.xref(clsname, self.from_imports[clsname])
                if kind == "class":
                    return "&%s_type" % self.ccls(clsname)
        return "NULL"

    def lower_any_all(self, fn, arg):
        """any(...)/all(...) as a GCC statement-expression loop."""
        self.loop_n += 1
        it, idx = "_it%d" % self.loop_n, "_k%d" % self.loop_n
        init = "false" if fn == "any" else "true"
        hit = "true" if fn == "any" else "false"
        if isinstance(arg, ast.GeneratorExp) and len(arg.generators) == 1:
            gen = arg.generators[0]
            saved = dict(self.scope)
            binds = self.bind_target(gen.target,
                                     "index_obj(%s, %s)" % (it, idx),
                                     force_decl=True)
            conds = " && ".join("(%s)" % self.bool_expr(c) for c in gen.ifs)
            pred = self.bool_expr(arg.elt)
            if fn == "all":
                pred = "!(%s)" % pred
            guard = ("if (%s) " % conds) if conds else ""
            body = "%s %sif (%s) { _r = %s; break; }" % (
                " ".join(binds), guard, pred, hit)
            src = self.expr(gen.iter)
            self.scope = saved
        else:
            pred = "truthy(_e)"
            if fn == "all":
                pred = "!truthy(_e)"
            body = "obj _e = index_obj(%s, %s); if (%s) { _r = %s; break; }" \
                % (it, idx, pred, hit)
            src = self.expr(arg)
        return ("({ bool _r = %s; obj %s = %s; long _n%d = pylen(%s); "
                "for (long %s = 0; %s < _n%d; %s++) { %s } _r; })") % (
            init, it, src, self.loop_n, it, idx, idx, self.loop_n, idx, body)

    def unwrap_obj(self, target, rendered):
        """Coerce a rendered obj expression to the `target` C type."""
        if not target or target == OBJ:
            return rendered
        if target == "int":
            return "AS_INT(%s)" % rendered
        if target == "bool":
            return "truthy(%s)" % rendered
        if target == "char*":
            return "AS_STR(%s)" % rendered
        if target.endswith("*"):
            return "(%s)AS_OBJ(%s)" % (target, rendered)
        return rendered

    def target_ctype(self, tgt):
        """Declared C type of an assignment target, or None if unknown."""
        if isinstance(tgt, ast.Name):
            if tgt.id in self.scope:
                return self.scope[tgt.id]
            if tgt.id in self.singleton_names:
                return self.singleton_names[tgt.id] + "*"
        if isinstance(tgt, ast.Attribute):
            if isinstance(tgt.value, ast.Name) and tgt.value.id == "self" \
                    and self.cur_class:
                return self.cur_class.field_ctype(tgt.attr)
            bt = self.value_ctype(tgt.value)
            if bt and bt.endswith("*") and bt != OBJ:
                cls = bt[:-1]
                if cls in self.xclasses:
                    self.xstructs_needed.add(cls)
                ci = self.classes.get(cls) or \
                    (self.xclasses[cls][0] if cls in self.xclasses else None)
                if ci:
                    return ci.field_ctype(tgt.attr)
            if self.is_obj_word(tgt.value) or bt == OBJ:
                owner = self.resolve_attr_owner(tgt.attr)
                if owner:
                    return owner.field_ctype(tgt.attr)
        return None

    def coerce_to(self, target, value_node, rendered):
        """Coerce `rendered` (an expr for value_node) to the `target` C type."""
        if not target:
            return rendered
        vt = self.value_ctype(value_node)
        if target == vt:
            return rendered
        if target == OBJ:
            return self.wrap_obj(value_node)
        is_objval = vt == OBJ or self.is_obj_word(value_node) or \
            (isinstance(rendered, str) and rendered.startswith(
                ("mp_call_", "mp_getattr", "mp_call_obj", "call_obj",
                 "call_closure", "mp_call_method")))
        if target.endswith("*"):           # target is a (class) pointer
            if target == "char*":
                if vt == "char*":
                    return rendered
                if is_objval:
                    return "AS_STR(%s)" % rendered
                return rendered
            if is_objval:
                return "(%s)AS_OBJ(%s)" % (target, rendered)
            if vt and vt.endswith("*") and vt != OBJ:
                return "(%s)(%s)" % (target, rendered)   # base/derived cast
            return rendered
        if is_objval:
            if target == "int":
                return "AS_INT(%s)" % rendered
            if target == "bool":
                return "truthy(%s)" % rendered
            if target == "char*":
                return "AS_STR(%s)" % rendered
        if target == "bool" and vt in ("int", "bool"):
            return "(%s != 0)" % rendered if vt == "int" else rendered
        if target == OBJ and vt in ("int", "bool", "char*", "double"):
            return self.wrap_obj(value_node)
        return rendered

    def wrap_obj(self, node):
        if isinstance(node, ast.IfExp):
            bt = self.value_ctype(node.body)
            ot = self.value_ctype(node.orelse)
            be = self.expr(node.body)
            oe = self.expr(node.orelse)
            if bt != ot or be.startswith("mp_") or oe.startswith("mp_"):
                return self.expr(node)
        if self.is_obj_word(node) or self.value_ctype(node) == OBJ:
            return self.expr(node)
        t = self.value_ctype(node)
        s = self.expr(node)
        if s.startswith(("mp_call_", "mp_getattr", "mp_hasattr")):
            return s
        if isinstance(node, ast.Call) and self.value_ctype(node) == OBJ:
            return s
        if isinstance(node, ast.Call):
            ct = self.value_ctype(node)
            if ct == OBJ or ct is None:
                return s
        if t == "int":
            return "OBJ_INT(%s)" % s
        if t == "char*":
            return "OBJ_STR(%s)" % s
        if t == "bool":
            return "OBJ_BOOL(%s)" % s
        if t == "double":
            return "OBJ_FLOAT(%s)" % s
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return "OBJ_STR(%s)" % s
            if isinstance(node.value, bytes):
                return "OBJ_STR(%s)" % s
            if isinstance(node.value, bool):
                return "OBJ_BOOL(%s)" % s
            if isinstance(node.value, int):
                return "OBJ_INT(%s)" % s
        if t and t.endswith("*") and t != OBJ:
            return "OBJ_OBJ(%s)" % s
        return s

    def is_obj_word(self, node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)\
                and node.value.id == "self" and self.cur_class:
            return self.cur_class.field_ctype(node.attr) == OBJ
        if isinstance(node, ast.Name):
            if node.id == "self" and self.cur_class:
                return False
            if node.id in self.scope:
                return self.scope[node.id] == OBJ
            if node.id in self.singleton_names:
                return False
            if node.id in self.mod_const_types:
                return self.mod_const_types[node.id] == OBJ
            if node.id in self.mod_global_types:
                return self.mod_global_types[node.id] == OBJ
            if node.id in self.from_imports:
                # A name imported from another module: a singleton is a typed
                # Cls* pointer (not a tagged obj), a func/class is a typed
                # symbol, and a global carries its own declared ctype. Without
                # this, an imported singleton like `error_collector` falls to
                # the name-guess below and is wrongly treated as an obj word,
                # so a method call wraps it in AS_OBJ() and the C won't compile.
                kind, info = self.resolve_import(node.id,
                                                 self.from_imports[node.id])
                if kind in ("singleton", "func", "class"):
                    return False
                if kind == "global":
                    return info == OBJ
            return infer_from_name(node.id) is None
        if isinstance(node, ast.Constant) and node.value is None:
            return True
        return False

    def static_owner(self, ci, name):
        """Class in ci's chain that declares class-static `name`, else None."""
        c = ci
        while c:
            if name in getattr(c, "class_statics", {}):
                return c
            c = c.base
        return None

    def static_type(self, node):
        if isinstance(node, ast.Name):
            if node.id == "self" and self.cur_class:
                return self.cur_class.name + "*"
            if node.id in self.scope:
                return self.scope[node.id]
            if node.id in self.func_nodes:   # function used as a value -> closure
                return OBJ
            if node.id in self.classes:      # class used as a value -> ctor closure
                return OBJ
            if node.id in self.singleton_names:
                return self.singleton_names[node.id] + "*"
            if node.id in self.mod_const_types:
                return self.mod_const_types[node.id]
            if node.id in self.from_imports:
                kind, info = self.xref(node.id, self.from_imports[node.id])
                if kind == "singleton":
                    return info + "*"
            for gname, gct, _gk, _gv in self.mod_globals:
                if gname == node.id:
                    return gct
            return infer_from_name(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr == "__name__":      # type(x).__name__ -> TYPE(x)->name
                return "char*"
            if node.attr == "buffer" and self.stdlib_root:
                return OBJ
            if isinstance(node.value, ast.Name):
                bn = node.value.id
                if bn in self.import_alias:
                    modname = self.import_alias[bn]
                    consts = self.load_xmod(modname).get("consts", {})
                    if node.attr in consts:
                        v = consts[node.attr]
                        if isinstance(v, bool):
                            return "bool"
                        if isinstance(v, int):
                            return "int"
                        if isinstance(v, str):
                            return "char*"
                # class-static (obj global)
                if bn == "self" and self.cur_class and \
                        self.static_owner(self.cur_class, node.attr):
                    return OBJ
                sci = self.classes.get(bn) or (self.xclasses[bn][0]
                                               if bn in self.xclasses else None)
                if sci is not None and self.static_owner(sci, node.attr):
                    return OBJ
                # alias.ClassName used as a value -> a constructor closure (obj)
                if bn in self.import_alias:
                    kind, info = self.xref(node.attr, self.import_alias[bn])
                    if kind == "class":
                        return OBJ
                    if kind == "global":
                        return OBJ
                    if kind == "singleton":
                        return info + "*"
            if isinstance(node.value, ast.Name) and \
                    node.value.id == "self" and self.cur_class:
                fc = self.cur_class.field_ctype(node.attr)
                if fc:
                    return fc
                if node.attr in self.cur_class.property_methods:
                    pfn = self.cur_class.methods.get(node.attr)
                    if pfn:
                        return self._logical_ret(pfn)
            if isinstance(node.value, ast.Name) and \
                    node.value.id in self.modules:
                return None
            # isinstance-narrowed base: report the proven concrete field type,
            # so wrap_obj/coercion stays consistent with what ex_Attribute emits.
            if isinstance(node.value, ast.Name) and \
                    node.value.id in self.narrowed:
                cls = self.narrowed[node.value.id][:-1]
                if self._class_has_field(cls, node.attr):
                    ci = self.classes.get(cls) or self.xclasses[cls][0]
                    return ci.field_ctype(node.attr)
            if self.is_obj_word(node.value) or \
                    self.value_ctype(node.value) == OBJ:
                owner = self.resolve_attr_owner(node.attr)
                if owner:
                    return owner.field_ctype(node.attr)
            # concrete class-pointer base (e.g. self.output.ctype where
            # self.output is ILValue*): report the field's declared type so a
            # further `.size` lowers to `->size`.
            bt = self.value_ctype(node.value)
            if bt and bt.endswith("*") and bt != OBJ:
                cls = bt[:-1]
                ci = self.classes.get(cls) or (self.xclasses[cls][0]
                                               if cls in self.xclasses else None)
                if ci is not None and self._class_has_field(cls, node.attr):
                    return ci.field_ctype(node.attr)
                if ci is not None:
                    sub = self._field_owner_subclass(cls, node.attr)
                    if sub:
                        sci = self.classes.get(sub) or self.xclasses[sub][0]
                        return sci.field_ctype(node.attr)
            return OBJ  # other attribute reads degrade to a Tier-2 obj
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, int):
                return "int"
            if isinstance(v, str):
                return "char*"
        return None

    def bool_expr(self, node):
        """Render `node` as a C truth test, boxing obj values via truthy()."""
        s = self.expr(node)
        if isinstance(node, (ast.Compare, ast.UnaryOp)):
            return s
        if isinstance(node, ast.BoolOp):
            return self.truth_test(node, s)
        if isinstance(node, ast.Call) and s.startswith(
                ("mp_call_", "mp_getattr", "mp_call_obj", "call_obj",
                 "call_closure", "mp_call_method")):
            return "truthy(%s)" % s
        if self.is_obj_word(node) or \
                (self.static_type(node) or self.value_ctype(node)) == OBJ:
            return "truthy(%s)" % s
        return s

    # ---- operators -------------------------------------------------------

    def ex_BinOp(self, node):
        if isinstance(node.op, ast.Add) and (self.looks_str(node.left) or
                                             self.looks_str(node.right)):
            return "pyconcat(%s, %s)" % (self.as_str(node.left),
                                         self.as_str(node.right))
        lt = self.value_ctype(node.left)
        rt = self.value_ctype(node.right)
        numeric = {"int", "bool", "double"}
        # both sides are concrete numbers -> plain C arithmetic
        if lt in numeric and rt in numeric:
            if isinstance(node.op, ast.Pow):     # C has no ** operator
                return "ipow(%s, %s)" % (self.expr(node.left),
                                         self.expr(node.right))
            return "(%s %s %s)" % (self.expr(node.left),
                                   self.binop_sym(node.op),
                                   self.expr(node.right))
        # otherwise operate on Tier-2 values
        fns = {ast.Add: "obj_add", ast.Sub: "obj_sub", ast.Mult: "obj_mul",
               ast.FloorDiv: "obj_fdiv", ast.Div: "obj_fdiv",
               ast.Mod: "obj_mod", ast.Pow: "obj_pow"}
        f = fns.get(type(node.op))
        if f:
            return "%s(%s, %s)" % (f, self.wrap_obj(node.left),
                                   self.wrap_obj(node.right))
        binop = {ast.BitAnd: "'&'", ast.BitOr: "'|'", ast.BitXor: "'^'",
                 ast.LShift: "'l'", ast.RShift: "'r'"}.get(type(node.op))
        if binop:
            return "obj_bin(%s, %s, %s)" % (binop, self.wrap_obj(node.left),
                                            self.wrap_obj(node.right))
        return "(%s %s %s)" % (self.expr(node.left), self.binop_sym(node.op),
                               self.expr(node.right))

    def looks_str(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True
        return self.static_type(node) == "char*"

    def str_operand(self, node):
        s = self.expr(node)
        return ("AS_STR(%s)" % s
                if self.is_obj_word(node) or self.value_ctype(node) == OBJ
                else s)

    def as_str(self, node):
        """Render `node` as a char* expression."""
        t = self.value_ctype(node)
        if t == "char*":
            return self.expr(node)
        if t == OBJ or self.is_obj_word(node):
            return "AS_STR(%s)" % self.expr(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self.expr(node)
        return "pystr(%s)" % self.wrap_obj(node)

    def as_long(self, node):
        """Render `node` as a long/int expression."""
        t = self.value_ctype(node)
        if t in ("int", "bool"):
            return self.expr(node)
        if t == OBJ or self.is_obj_word(node):
            return "AS_INT(%s)" % self.expr(node)
        return "pyint(%s)" % self.wrap_obj(node)

    def lower_str_method(self, func, node):
        """Lower a string method call; returns C or None if not a str method."""
        m = func.attr
        recv = lambda: self.as_str(func.value)
        a = node.args
        if m == "startswith":
            return "str_startswith(%s, %s)" % (recv(), self.as_str(a[0]))
        if m == "endswith":
            return "str_endswith(%s, %s)" % (recv(), self.as_str(a[0]))
        if m in ("strip", "lstrip", "rstrip"):
            mode = {"strip": 0, "lstrip": 1, "rstrip": 2}[m]
            return "str_strip(%s, %d)" % (recv(), mode)
        if m == "split":
            sep = self.as_str(a[0]) if a else "NULL"
            return "str_split(%s, %s)" % (recv(), sep)
        if m == "partition":
            return "str_partition(%s, %s)" % (recv(), self.as_str(a[0]))
        if m == "splitlines":
            return "str_splitlines(%s)" % recv()
        if m == "replace":
            if len(a) < 2:
                return None
            return "str_replace(%s, %s, %s)" % (recv(), self.as_str(a[0]),
                                                self.as_str(a[1]))
        if m == "find":
            return "str_find(%s, %s, false)" % (recv(), self.as_str(a[0]))
        if m == "rfind":
            return "str_find(%s, %s, true)" % (recv(), self.as_str(a[0]))
        if m in ("isdigit", "isalpha", "isspace", "isalnum"):
            return "str_%s(%s)" % (m, recv())
        if m == "lower":
            return "str_lower(%s)" % recv()
        if m == "upper":
            return "str_upper(%s)" % recv()
        if m == "join":
            return "pyjoin(%s, %s)" % (recv(), self.wrap_obj(a[0]))
        if m == "encode":
            return recv()
        return None

    STR_METHODS = {"startswith", "endswith", "strip", "lstrip", "rstrip",
                   "split", "partition", "splitlines", "replace", "find",
                   "rfind", "isdigit", "isalpha", "isspace", "isalnum",
                   "lower", "upper", "join", "encode"}

    def truth_test(self, node, rendered):
        """A C truth test for `rendered` (the expr of `node`)."""
        if self.is_obj_word(node) or self.value_ctype(node) == OBJ:
            return "truthy(%s)" % rendered
        t = self.value_ctype(node)
        if t == "char*":
            return "(%s && *(%s))" % (rendered, rendered)
        if t and t.endswith("*"):
            return "(%s != NULL)" % rendered
        return "(%s)" % rendered

    def ex_BoolOp(self, node):
        vals = node.values
        is_and = isinstance(node.op, ast.And)

        def render(fn):
            """Apply each operand's fn left-to-right; for `and`, later operands
            see the isinstance-narrowings implied by the operands before them
            (e.g. `isinstance(x, T) and x.field`). Narrowings are restored."""
            saved, res = {}, []
            for v in vals:
                res.append(fn(v))
                if is_and:
                    for nm, ct in self._narrowings(v):
                        saved.setdefault(nm, self.narrowed.get(nm))
                        self.narrowed[nm] = ct
            for nm, old in saved.items():
                if old is None:
                    self.narrowed.pop(nm, None)
                else:
                    self.narrowed[nm] = old
            return res

        types = render(self.value_ctype)
        same = len(set(types)) == 1 and types[0] in ("int", "bool", "char*")
        if same:
            rend = render(self.expr)
            tests = render(lambda v: self.truth_test(
                v, self.expr(v)))
        else:                                # unify to Tier-2 obj
            rend = render(self.wrap_obj)
            tests = ["truthy(%s)" % r for r in rend]
        expr = rend[-1]
        for i in range(len(vals) - 2, -1, -1):
            if is_and:                       # a and b -> (test(a) ? b : a)
                expr = "(%s ? %s : %s)" % (tests[i], expr, rend[i])
            else:                            # a or b  -> (test(a) ? a : b)
                expr = "(%s ? %s : %s)" % (tests[i], rend[i], expr)
        return expr

    def ex_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            return "(!%s)" % self.bool_expr(node.operand)
        sym = {ast.USub: "-", ast.UAdd: "+", ast.Invert: "~"}[type(node.op)]
        operand = node.operand
        if isinstance(node.op, ast.USub):
            lt = self.value_ctype(operand)
            if self.is_obj_word(operand) or lt == OBJ:
                return "obj_neg(%s)" % self.expr(operand)
            if lt and lt.endswith("*") and lt not in ("char*", OBJ):
                return "obj_neg(OBJ_OBJ(%s))" % self.expr(operand)
        if isinstance(node.op, ast.UAdd):
            lt = self.value_ctype(operand)
            if self.is_obj_word(operand) or lt == OBJ:
                return self.expr(operand)
            if lt and lt.endswith("*") and lt not in ("char*", OBJ):
                return "OBJ_OBJ(%s)" % self.expr(operand)
        if self.is_obj_word(operand) or self.value_ctype(operand) == OBJ:
            if isinstance(node.op, ast.Invert):
                return "obj_invert(%s)" % self.expr(operand)
            return self.expr(operand)        # unary plus: identity
        return "(%s%s)" % (sym, self.expr(operand))

    def ex_Compare(self, node):
        parts = []
        cur = node.left
        for op, comp in zip(node.ops, node.comparators):
            parts.append(self.cmp(cur, op, comp))
            cur = comp
        return "(" + " && ".join(parts) + ")"

    def _wrap_cmp_operand(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "OBJ_STR(%s)" % c_string(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return "OBJ_INT(%s)" % self.expr(node)
        return self.wrap_obj(node)

    def cmp(self, left, op, right):
        ls, rs = self.expr(left), self.expr(right)
        if isinstance(op, (ast.In, ast.NotIn)):
            if isinstance(right, ast.Name) and right.id in self.str_sets:
                elems = self.str_sets[right.id]
                arr = "(const str[]){%s}" % ", ".join(c_string(e)
                                                      for e in elems)
                inner = "in_str(%s, %s, %d)" % (
                    self.str_operand(left), arr, len(elems))
            else:
                inner = "pycontains(%s, %s)" % (self.wrap_obj(right), self.wrap_obj(left))
            return ("(!%s)" % inner) if isinstance(op, ast.NotIn) else inner
        if isinstance(op, (ast.Is, ast.IsNot)):
            neg = isinstance(op, ast.IsNot)
            if isinstance(right, ast.Constant) and right.value is None:
                lt = self.value_ctype(left)
                if self.is_obj_word(left) or lt == OBJ:
                    s = "IS_NONE(%s)" % ls
                elif lt and lt.endswith("*"):       # char* / class pointer
                    s = "(%s == NULL)" % ls
                else:                                # a non-nullable scalar
                    s = "0"
                return ("(!%s)" % s) if neg else s
            # identity on objects -> obj_eq; otherwise raw pointer/scalar compare
            if self.is_obj_val(left) or self.is_obj_val(right):
                eq = "obj_eq(%s, %s)" % (self._wrap_cmp_operand(left),
                                         self._wrap_cmp_operand(right))
                return ("(!%s)" % eq) if neg else eq
            sym = "!=" if neg else "=="
            return "(%s %s %s)" % (ls, sym, rs)
        sym = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
               ast.Gt: ">", ast.GtE: ">="}[type(op)]
        lo, ro = self.is_obj_val(left), self.is_obj_val(right)
        lp, rp = self.is_ptr_val(left), self.is_ptr_val(right)
        if sym in ("==", "!="):
            # obj compared against a real pointer (e.g. self.base == RBP)
            if (lo and rp) or (ro and lp):
                o, p = (ls, rs) if lo else (rs, ls)
                core = "(IS_OBJ(%s) && AS_OBJ(%s) == (Obj*)(%s))" % (o, o, p)
                return core if sym == "==" else "(!%s)" % core
            if lo or ro:
                eq = "obj_eq(%s, %s)" % (self._wrap_cmp_operand(left),
                                        self._wrap_cmp_operand(right))
                return eq if sym == "==" else "(!%s)" % eq
            if self.looks_str(left) or self.looks_str(right):
                return "(strcmp(%s, %s) %s 0)" % (self.str_operand(left),
                                                  self.str_operand(right), sym)
            return "(%s %s %s)" % (ls, sym, rs)
        # ordering
        if lo or ro:
            return "(obj_cmp(%s, %s) %s 0)" % (self.wrap_obj(left),
                                               self.wrap_obj(right), sym)
        if self.looks_str(left) or self.looks_str(right):
            return "(strcmp(%s, %s) %s 0)" % (self.str_operand(left),
                                              self.str_operand(right), sym)
        return "(%s %s %s)" % (ls, sym, rs)

    def is_obj_val(self, node):
        if self.is_obj_word(node) or self.value_ctype(node) == OBJ:
            return True
        if self.stdlib_root and isinstance(node, ast.Attribute):
            cur = node
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in self.modules | \
                    set(self.import_alias):
                return True
        if isinstance(node, ast.Name) and node.id in self.mod_global_names \
                and node.id not in self.scope:
            if self.mod_global_types.get(node.id) == OBJ:
                return True
        if isinstance(node, ast.Call) and self.stdlib_root:
            if isinstance(node.func, ast.Name) and \
                    node.func.id in ("getattr", "mp_getattr", "mp_call_import",
                                     "mp_call_method", "mp_call_obj"):
                return True
            rt = self.value_ctype(node)
            if rt == OBJ or rt is None:
                return True
        if isinstance(node, ast.Name) and node.id in self.scope:
            return self.scope[node.id] == OBJ
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self" and self.cur_class:
            ft = self.cur_class.field_ctype(node.attr)
            if ft == OBJ:
                return True
        st = self.static_type(node)
        return st == OBJ

    def const_dict_owner(self, attr_node):
        """For an attribute `X.D` (X = self, a class name, or alias.Class),
        return the class declaring D as a specialized const dict (walking
        bases), else None. Registers an extern for imported owners."""
        if not isinstance(attr_node, ast.Attribute):
            return None
        d, xv = attr_node.attr, attr_node.value
        ci = None
        if isinstance(xv, ast.Name):
            if xv.id == "self":
                ci = self.cur_class
            else:
                ci = self.classes.get(xv.id) or (self.xclasses[xv.id][0]
                                                 if xv.id in self.xclasses
                                                 else None)
        elif isinstance(xv, ast.Attribute) and isinstance(xv.value, ast.Name) \
                and xv.value.id in self.import_alias:
            kind, info = self.xref(xv.attr, self.import_alias[xv.value.id])
            if kind == "class":
                ci = info
        while ci:
            if d in ci.const_dicts:
                if ci.name not in self.classes:
                    self.xconstdict_externs.add((ci.name, d))
                return ci
            ci = ci.base
        return None

    def is_ptr_val(self, node):
        t = self.value_ctype(node)
        return bool(t) and t.endswith("*") and t != OBJ

    def ex_Subscript(self, node):
        if isinstance(node.value, ast.Subscript):
            inner = node.value
            owner = self.const_dict_owner(inner.value) \
                if isinstance(inner.value, ast.Attribute) else None
            if owner is not None:
                d = inner.value.attr
                key = self.expr(inner.slice)
                i = self.expr(node.slice)
                return "%s_%s(%s, %s)" % (owner.name, d, key, i)
        sl = node.slice
        if isinstance(sl, ast.Slice):
            lo = self.as_long(sl.lower) if sl.lower else "0"
            hi = self.as_long(sl.upper) if sl.upper else "PY_SLICE_END"
            return "py_slice(%s, %s, %s)" % (self.wrap_obj(node.value), lo, hi)
        # indexing a Tier-2 obj (list/dict/str) dispatches at runtime
        vct = self.value_ctype(node.value)
        if self.stdlib_root and isinstance(node.value, ast.Attribute):
            bt = self.value_ctype(node.value)
            if bt == OBJ or self.is_obj_word(node.value) or bt is None:
                return "subscript(%s, %s)" % (self.wrap_obj(node.value),
                                              self.wrap_obj(sl))
        if self.is_obj_word(node.value) or vct == OBJ or \
                isinstance(node.value, ast.Call):
            return "subscript(%s, %s)" % (self.expr(node.value),
                                          self.wrap_obj(sl))
        if self.value_ctype(node.value) == "char*":   # s[i] -> 1-char string
            return "char_at(%s, %s)" % (self.expr(node.value), self.as_long(sl))
        if not isinstance(sl, ast.Slice):
            idx = self.expr(sl)
            if not idx.lstrip("-").isdigit():
                return "subscript(%s, %s)" % (self.wrap_obj(node.value),
                                              self.wrap_obj(sl))
        return "%s[%s]" % (self.expr(node.value), self.expr(sl))

    def ex_IfExp(self, node):
        bt = self.value_ctype(node.body)
        ot = self.value_ctype(node.orelse)
        be = self.expr(node.body)
        oe = self.expr(node.orelse)
        if self.stdlib_root and (bt != ot or be.startswith("mp_") or
                                 oe.startswith("mp_")):
            return "(%s ? %s : %s)" % (self.bool_expr(node.test),
                                       self.wrap_obj(node.body),
                                       self.wrap_obj(node.orelse))
        if bt != ot:                        # unify to a common Tier-2 obj
            return "(%s ? %s : %s)" % (self.bool_expr(node.test),
                                       self.wrap_obj(node.body),
                                       self.wrap_obj(node.orelse))
        return "(%s ? %s : %s)" % (self.bool_expr(node.test),
                                   self.expr(node.body),
                                   self.expr(node.orelse))

    def ex_List(self, node):
        if not node.elts:
            return "list_new()"
        items = ", ".join(self.wrap_obj(e) for e in node.elts)
        return "list_of(%d, %s)" % (len(node.elts), items)

    def ex_Tuple(self, node):
        if not node.elts:
            return "list_new() /* () */"
        items = ", ".join(self.wrap_obj(e) for e in node.elts)
        return "list_of(%d, %s)" % (len(node.elts), items)

    def ex_Set(self, node):
        if not node.elts:
            return "list_new() /* set */"
        items = ", ".join(self.wrap_obj(e) for e in node.elts)
        return "list_of(%d, %s) /* set */" % (len(node.elts), items)

    def ex_Dict(self, node):
        pairs = [(k, v) for k, v in zip(node.keys, node.values)
                 if k is not None]
        if not pairs:
            return "dict_new()"
        flat = []
        for k, v in pairs:
            flat.append(self.wrap_obj(k))
            flat.append(self.wrap_obj(v))
        return "dict_of(%d, %s)" % (len(pairs), ", ".join(flat))

    def ex_ListComp(self, node):
        return self.lower_comp(node, "list")

    def ex_SetComp(self, node):
        return self.lower_comp(node, "list")

    def ex_DictComp(self, node):
        return self.lower_comp(node, "dict")

    def ex_GeneratorExp(self, node):
        return self.lower_comp(node, "list")

    def ex_Lambda(self, node):
        if len(node.args.args) == 1 and isinstance(node.body, ast.Name) and \
                node.body.id == node.args.args[0].arg and \
                not node.args.vararg and not node.args.kwarg:
            return "make_closure(&identity__tramp, OBJ_NONE)"
        return "make_closure(&identity__tramp, OBJ_NONE)"

    def ex_Starred(self, node):
        v = node.value
        if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Add):
            return "obj_add(%s, %s)" % (self.expr(v.left), self.expr(v.right))
        return self.expr(v)

    def ex_JoinedStr(self, node):
        fmt, exprs = [], []
        for part in node.values:
            if isinstance(part, ast.Constant):
                fmt.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                fmt.append("{}")
                exprs.append(self.wrap_obj(part.value))
        lit = c_string("".join(fmt))
        return "pyfmt(%d, %s%s)" % (len(exprs), lit,
                                    (", " + ", ".join(exprs)) if exprs else "")

    def ex_FormattedValue(self, node):
        return self.expr(node.value)

    def binop_sym(self, op):
        return {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
                ast.Mod: "%", ast.FloorDiv: "/", ast.BitOr: "|",
                ast.BitAnd: "&", ast.BitXor: "^", ast.LShift: "<<",
                ast.RShift: ">>", ast.Pow: "POW"}.get(type(op), "/*op*/")

    def guess_from_value(self, node):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, int):
                return "int"
            if isinstance(v, str):
                return "char*"
            if isinstance(v, float):
                return "double"
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            return "bool"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in self.classes:
                return node.func.id + "*"
            if node.func.id == "str":
                return "char*"
            if node.func.id == "getattr" and not (
                    len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                return OBJ          # dynamic attribute -> Tier-2 obj
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "index":   # list.index -> list_index (obj int)
                return OBJ
        if isinstance(node, ast.JoinedStr):
            return "char*"
        if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
            return OBJ
        return None

    # ---- source helpers --------------------------------------------------

    def src(self, node):
        try:
            return ast.unparse(node)
        except Exception:
            return type(node).__name__

    def src1(self, node):
        s = self.src(node).replace("\n", " ").replace("*/", "* /")
        return s if len(s) <= 120 else s[:117] + "..."


def is_super_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
        and node.func.id == "super"


# ==========================================================================
# String literal helper
# ==========================================================================

def c_string(s):
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("\\x%02x" % (ord(ch) & 0xff))
    out.append('"')
    return "".join(out)


# ==========================================================================
# Driver
# ==========================================================================

def write_runtime(out_dir, mp_bridge=False):
    with open(os.path.join(out_dir, "shivyc_rt.h"), "w") as f:
        f.write(RUNTIME_H)
    with open(os.path.join(out_dir, "shivyc_rt.c"), "w") as f:
        f.write(RUNTIME_C)
    if mp_bridge:
        with open(os.path.join(out_dir, "mp_stdlib_bridge.h"), "w") as f:
            f.write(MP_BRIDGE_H)
        with open(os.path.join(out_dir, "mp_stdlib_bridge.c"), "w") as f:
            f.write(MP_BRIDGE_C)


def relative_stdlib_slug(stdlib_dir, py_path):
    rel = Path(py_path).resolve().relative_to(Path(stdlib_dir).resolve())
    return rel.as_posix().replace("/", "_").replace("-", "_").removesuffix(".py")


def _stdlib_context(path, stdlib_dir=None):
    ap = os.path.abspath(path)
    parts = ap.split(os.sep)
    if stdlib_dir is None and "python-stdlib" in parts:
        i = parts.index("python-stdlib")
        stdlib_dir = os.sep.join(parts[: i + 1])
    if stdlib_dir and os.path.commonpath([ap, os.path.abspath(stdlib_dir)]) == \
            os.path.abspath(stdlib_dir):
        modname = relative_stdlib_slug(stdlib_dir, path)
        return modname, None, stdlib_dir
    if "shivyc" in parts:
        i = parts.index("shivyc")
        base_dir = os.sep.join(parts[:i]) or os.sep
        rel = parts[i:]
        if len(rel) == 2:
            modname = rel[1][:-3]
        else:
            modname = ".".join(rel)[:-3]
        return modname, base_dir, None
    return os.path.splitext(os.path.basename(path))[0], os.path.dirname(ap), None


def transpile_file(path, out_dir, stdlib_dir=None):
    src = open(path, encoding="utf-8").read()
    modname, base_dir, stdlib_root = _stdlib_context(path, stdlib_dir)
    py_mod = py_modname_from_path(path, stdlib_root) if stdlib_root else None
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        print("  SYNTAX ERROR in %s: %s" % (path, e))
        return None, str(e)
    try:
        out = Transpiler(modname, base_dir, stdlib_root=stdlib_root,
                         py_modname=py_mod).run(tree)
    except Unsupported as e:
        print("  FAIL %s: %s" % (path, e))
        return None, str(e)
    out_path = os.path.join(out_dir, modname + ".c")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    return out_path, None


def translate_stdlib(stdlib_dir, out_dir, report_path):
    stdlib_dir = Path(stdlib_dir)
    out_dir = Path(out_dir)
    report_path = Path(report_path)
    ok, failed = [], []
    files = sorted(stdlib_dir.rglob("*.py"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.c"):
        old.unlink()
    for old in out_dir.glob("*.h"):
        old.unlink()
    write_runtime(str(out_dir), mp_bridge=True)
    for py_path in files:
        rel = py_path.relative_to(stdlib_dir).as_posix()
        res, err = transpile_file(str(py_path), str(out_dir), str(stdlib_dir))
        if res is None:
            failed.append((rel, err or "unknown error"))
        else:
            ok.append(rel)
    lines = [
        "micropython-lib python-stdlib -> C (via tools/py2c.py)",
        "stdlib: %s" % stdlib_dir,
        "output: %s" % out_dir,
        "",
        "OK:   %d" % len(ok),
        "FAIL: %d" % len(failed),
        "",
    ]
    if ok:
        lines.append("=== translated ===")
        lines.extend("  %s" % name for name in ok)
        lines.append("")
    if failed:
        lines.append("=== failures ===")
        for name, err in failed:
            lines.append("  %s" % name)
            lines.append("    %s" % err)
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return ok, failed


def transpile_file_legacy(path, out_dir):
    res, _err = transpile_file(path, out_dir)
    return res


def print_conventions():
    print(__doc__)


def main(argv):
    out_dir = "/tmp"
    stdlib_dir = None
    report_path = None
    files = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            out_dir = argv[i + 1]
            i += 2
            continue
        if a == "--stdlib-dir":
            stdlib_dir = argv[i + 1]
            i += 2
            continue
        if a == "--report":
            report_path = argv[i + 1]
            i += 2
            continue
        if a in ("--conventions", "-c"):
            print_conventions()
            return
        files.append(a)
        i += 1

    if stdlib_dir is not None:
        if report_path is None:
            report_path = os.path.join(out_dir, "stdlib_translate_report.txt")
        ok, failed = translate_stdlib(stdlib_dir, out_dir, report_path)
        print("translated %d file(s), %d failed" % (len(ok), len(failed)))
        print("report: %s" % report_path)
        sys.exit(1 if failed else 0)

    if not files:
        here = os.path.dirname(os.path.abspath(__file__))
        shivyc = os.path.normpath(os.path.join(here, "..", "shivyc"))
        if os.path.isdir(shivyc):
            files = sorted(os.path.join(shivyc, f)
                           for f in os.listdir(shivyc) if f.endswith(".py"))
            print("No files given; defaulting to %d files in %s" %
                  (len(files), shivyc))
        else:
            print("error: no input files and no ../shivyc directory", file=sys.stderr)
            sys.exit(2)

    os.makedirs(out_dir, exist_ok=True)
    _, _, stdlib_root = _stdlib_context(files[0] if len(files) == 1 else "", None)
    mp_bridge = bool(stdlib_dir) or any(
        "python-stdlib" in os.path.abspath(p) for p in files)
    write_runtime(out_dir, mp_bridge=mp_bridge)
    print("  runtime -> %s/shivyc_rt.{h,c}" % out_dir)
    ok = 0
    for path in files:
        res, _err = transpile_file(path, out_dir)
        if res:
            ok += 1
            print("  %-28s -> %s" % (os.path.basename(path), res))
    print("Transpiled %d/%d files into %s" % (ok, len(files), out_dir))


if __name__ == "__main__":
    main(sys.argv[1:])

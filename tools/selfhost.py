#!/usr/bin/env python3
"""Build, test, and benchmark ShivyCX self-hosting.

This drives the self-host pipeline end to end: it transpiles ShivyCX's own
Python source to C with ``tools/py2c.py``, compiles the modules with gcc, and
links runnable test/benchmark executables.

Two link backends produce a runnable exe:

  * ``host``     -- plain gcc + glibc. Used for pure-C modules (e.g. tokens)
                    whose only otherwise-undefined symbol is ``main``.
  * ``objcore``  -- additionally links micropython's objcore core objects, for
                    code that uses the dynamic bridge (``mp_call_import`` ...).
                    This is the path for genuinely-dynamic modules.

``--musl`` compiles the transpiled C against the *packaged* musl headers
(``shivyc/musl``) instead of glibc, validating that ShivyCX's own output is
glibc-independent. (Producing a fully static musl-libc exe -- building the
~1500 musl sources into a libc.a with crt/syscall startup -- is the next step;
see ``musl_link_libc`` below.)

All C support code (test harnesses and benchmarks) is kept inline as
triple-quoted strings in this file and written to ``/tmp`` before the compile
steps, so this script is the single source for the self-host build glue.

Usage:
    python3 tools/selfhost.py list
    python3 tools/selfhost.py test [NAME ...]      # default: all host tests
    python3 tools/selfhost.py bench [NAME ...]
    python3 tools/selfhost.py coverage [--musl]    # transpile+compile all mods
Options:
    --musl        compile transpiled C against packaged musl headers
    --objcore     allow objcore-backed targets (needs the objcore build)
    --keep        keep the scratch build dir and print its path
    -q/--quiet    less chatter
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY2C = os.path.join(REPO, "tools", "py2c.py")

# objcore (micropython) locations -- the proven link recipe from
# examples/link_objcore.sh.
OBJCORE = "/home/claude/micropython/ports/objcore"
MP_TOP = "/home/claude/micropython"

# ==========================================================================
# Inlined C support code (written to /tmp before compiling)
# ==========================================================================

# End-to-end correctness harness for the transpiled Token class. Declares a
# struct matching the generated layout, builds Tokens, and checks fields.
TOKEN_DEMO_C = r'''
#include "shivyc_rt.h"
#include <stdio.h>
#include <string.h>
typedef struct Range Range;                 /* opaque (errors.Range) */
typedef struct Token {                       /* matches generated tokens.c */
    Obj _hdr; Range* r; obj kind; char* content; char* rep; bool wide;
    obj logical_line;
} Token;
Token* Token_new(obj kind, char* content, char* rep, obj r);

int main(void) {
    Token* a = Token_new(OBJ_STR("identifier"), "myvar", "", OBJ_NONE);
    printf("token.content = %s (expect myvar)\n", a->content);
    Token* b = Token_new(OBJ_STR("kw_int"), NULL, "", OBJ_NONE);
    printf("default content = %s (expect kw_int)\n", b->content);
    printf("wide=%d logical_line_is_none=%d\n",
           (int)a->wide, a->logical_line.tag == T_NONE);
    int ok = !strcmp(a->content, "myvar") && !strcmp(b->content, "kw_int")
             && !a->wide && a->logical_line.tag == T_NONE;
    printf("%s\n", ok ? "ALL CHECKS PASS" : "FAIL");
    return ok ? 0 : 1;
}
'''

# Throughput benchmark: how fast does the transpiled Token constructor run
# (arena alloc + __init__) versus wall time.
TOKEN_BENCH_C = r'''
#define _POSIX_C_SOURCE 199309L   /* clock_gettime / struct timespec */
#include "shivyc_rt.h"
#include <stdio.h>
#include <time.h>
typedef struct Range Range;
typedef struct Token {
    Obj _hdr; Range* r; obj kind; char* content; char* rep; bool wide;
    obj logical_line;
} Token;
Token* Token_new(obj kind, char* content, char* rep, obj r);

int main(void) {
    const long N = 2000000;
    volatile char* sink = 0;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (long i = 0; i < N; i++) {
        Token* t = Token_new(OBJ_STR("identifier"), "myvar", "", OBJ_NONE);
        sink = t->content;
        if ((i & 0xffff) == 0) arena_reset();   /* keep the arena bounded */
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    (void)sink;
    double ms = (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6;
    printf("Token_new x %ld: %.1f ms  (%.1f M/s)\n", N, ms, N / ms / 1e3);
    return 0;
}
'''

# objcore bridge harness: init micropython, call a builtin through the
# ShivyCX->mp bridge, and supply the port stubs the core needs.
BRIDGE_DEMO_C = r'''
#include <stdio.h>
#include "py/builtin.h"
#include "py/gc.h"
#include "py/lexer.h"
#include "py/mperrno.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "shivyc_rt.h"
#include "mp_stdlib_bridge.h"

static char *stack_top;
static char heap[MICROPY_HEAP_SIZE];

int main(void) {
    int stack_dummy; stack_top = (char*)&stack_dummy;
    gc_init(heap, heap + sizeof(heap));
    mp_init();
    nlr_buf_t nlr;
    int ok = 0;
    if (nlr_push(&nlr) == 0) {
        obj r = mp_call_import("builtins", "abs", 1, OBJ_INT(-5));
        printf("abs(-5) via objcore bridge = %ld (expect 5)\n", AS_INT(r));
        ok = (AS_INT(r) == 5);
        nlr_pop();
    } else {
        printf("micropython raised during bridge call\n");
    }
    mp_deinit();
    printf("%s\n", ok ? "ALL CHECKS PASS" : "FAIL");
    return ok ? 0 : 1;
}
/* port stubs (from objcore main.c) */
void gc_collect(void){ void*d; gc_collect_start();
    gc_collect_root(&d,((mp_uint_t)stack_top-(mp_uint_t)&d)/sizeof(mp_uint_t));
    gc_collect_end(); }
mp_lexer_t *mp_lexer_new_from_file(qstr f){(void)f; mp_raise_OSError(MP_ENOENT);}
mp_import_stat_t mp_import_stat(const char *p){(void)p; return MP_IMPORT_STAT_NO_EXIST;}
void nlr_jump_fail(void *v){(void)v; for(;;){}}
void mp_hal_stdout_tx_strn_cooked(const char *s, size_t n){ fwrite(s,1,n,stdout); }
/* float disabled in this objcore config; bridge refs are dead code here */
mp_obj_t mp_obj_new_float(double v){(void)v; return mp_const_none;}
double mp_obj_get_float(mp_obj_t o){(void)o; return 0;}
const mp_obj_type_t mp_type_float;
'''

# ==========================================================================
# Target registry
# ==========================================================================
# Each target self-hosts one ShivyCX module end to end: transpile the listed
# .py module(s), compile, link a harness, run, and check the output.

TARGETS = {
    "tokens": {
        "modules": ["shivyc/tokens.py"],
        "harness": ("token_demo.c", TOKEN_DEMO_C),
        "backend": "host",
        "expect": "ALL CHECKS PASS",
        "desc": "Token class: arena alloc + __init__ + str(kind) default",
    },
    "bridge": {
        "modules": [],            # bridge runtime only; no ShivyCX module
        "harness": ("bridge_demo.c", BRIDGE_DEMO_C),
        "backend": "objcore",
        "expect": "ALL CHECKS PASS",
        "desc": "dynamic call (abs) routed through the objcore mp bridge",
    },
}

BENCHES = {
    "tokens": {
        "modules": ["shivyc/tokens.py"],
        "harness": ("token_bench.c", TOKEN_BENCH_C),
        "backend": "host",
        "desc": "Token_new throughput (arena alloc + __init__)",
    },
}

# All ShivyCX source globs used for the coverage report.
COVERAGE_GLOBS = ["shivyc/*.py", "shivyc/tree/*.py",
                  "shivyc/parser/*.py", "shivyc/il_cmds/*.py"]


# ==========================================================================
# Helpers
# ==========================================================================
def log(quiet, *a):
    if not quiet:
        print(*a)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def transpile(modules, out_dir, mp_bridge=False):
    """Transpile module(s) to C in out_dir. Always writes the runtime; with
    mp_bridge also writes the objcore bridge."""
    if modules:
        paths = [os.path.join(REPO, m) for m in modules]
        r = run([sys.executable, PY2C, *paths, "--out", out_dir], cwd=REPO)
        if r.returncode != 0:
            raise RuntimeError("transpile failed:\n" + r.stderr)
    # py2c writes shivyc_rt.{c,h}; for the bridge backend we also need
    # mp_stdlib_bridge.{c,h}, which write_runtime emits when mp_bridge=True.
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import py2c
    py2c.write_runtime(out_dir, mp_bridge=mp_bridge)


def musl_cflags():
    """-nostdinc plus the packaged musl include dirs and defines."""
    sys.path.insert(0, REPO)
    from shivyc import musl
    tree = musl.materialize()
    flags = ["-nostdinc"]
    flags += tree.public_cflags()
    return flags


def compile_c(src, obj, inc_dirs=(), extra=(), use_musl=False):
    cmd = ["gcc", "-c", src, "-o", obj, "-std=c99", "-O2"]
    for d in inc_dirs:
        cmd += ["-I", d]
    if use_musl:
        cmd += musl_cflags()
    cmd += list(extra)
    r = run(cmd)
    return r


def objcore_core_objects():
    import glob
    objs = glob.glob(os.path.join(OBJCORE, "build/py", "*.o"))
    objs += glob.glob(os.path.join(OBJCORE, "build/shared", "**", "*.o"),
                      recursive=True)
    # exclude the port's own entry objects -- the harness supplies them
    return [o for o in objs
            if os.path.basename(o) not in ("main.o", "hal.o", "script.o")]


def link(objs, exe, backend):
    cmd = ["gcc", "-no-pie", *objs, "-o", exe]
    if backend == "objcore":
        cmd[2:2] = objcore_core_objects()
    r = run(cmd)
    return r


def musl_link_libc():
    """Placeholder for the fully-static musl-libc backend.

    The packaged musl tree (~1500 .c sources) can be compiled into a libc.a
    with crt startup and linked -nostdlib to produce a glibc-free static exe.
    That build (correct source set, syscall/crt asm, generated headers) is the
    next self-host milestone; today --musl validates header compatibility.
    """
    raise NotImplementedError("musl static libc backend: see docstring")


# ==========================================================================
# Build / test / bench
# ==========================================================================
def build_target(name, spec, build_root, use_musl=False, quiet=False):
    """Transpile -> compile -> link one target. Returns (exe_path or None,
    message)."""
    backend = spec["backend"]
    work = os.path.join(build_root, name)
    os.makedirs(work, exist_ok=True)
    transpile(spec["modules"], work, mp_bridge=(backend == "objcore"))

    # write the inline harness
    hname, hsrc = spec["harness"]
    hpath = os.path.join(work, hname)
    with open(hpath, "w") as f:
        f.write(hsrc)

    inc = [work]
    if backend == "objcore":
        inc += [OBJCORE, MP_TOP, os.path.join(OBJCORE, "build")]

    objs = []
    # compile the harness
    o = os.path.join(work, "harness.o")
    r = compile_c(hpath, o, inc_dirs=inc)
    if r.returncode != 0:
        return None, "harness compile failed:\n" + r.stderr
    objs.append(o)

    # compile each transpiled module (against musl headers if requested)
    for m in spec["modules"]:
        base = os.path.splitext(os.path.basename(m))[0]
        # py2c names files by dotted module path; find the .c it produced
        cfile = _find_module_c(work, base)
        oo = os.path.join(work, base + ".o")
        r = compile_c(cfile, oo, inc_dirs=[work], use_musl=use_musl)
        if r.returncode != 0:
            return None, "module %s compile failed:\n%s" % (base, r.stderr)
        objs.append(oo)

    # runtime
    rt_o = os.path.join(work, "shivyc_rt.o")
    r = compile_c(os.path.join(work, "shivyc_rt.c"), rt_o, inc_dirs=[work])
    if r.returncode != 0:
        return None, "runtime compile failed:\n" + r.stderr
    objs.append(rt_o)

    # bridge runtime (objcore backend)
    if backend == "objcore":
        br_o = os.path.join(work, "mp_stdlib_bridge.o")
        r = compile_c(os.path.join(work, "mp_stdlib_bridge.c"), br_o,
                      inc_dirs=inc)
        if r.returncode != 0:
            return None, "bridge compile failed:\n" + r.stderr
        objs.append(br_o)

    exe = os.path.join(work, name)
    r = link(objs, exe, backend)
    if r.returncode != 0:
        return None, "link failed:\n" + r.stderr
    return exe, "ok"


def _find_module_c(work, base):
    """py2c emits one .c per module, named by dotted path (e.g.
    shivyc.tree.memory_exprs.c) or short name (tokens.c). Find it by suffix."""
    cands = [f for f in os.listdir(work)
             if f.endswith(".c") and f not in
             ("shivyc_rt.c", "mp_stdlib_bridge.c")
             and (f == base + ".c" or f.endswith("." + base + ".c"))]
    if not cands:
        raise RuntimeError("no transpiled .c for module %r in %s" % (base, work))
    return os.path.join(work, cands[0])


def cmd_test(names, build_root, args):
    targets = names or [n for n, s in TARGETS.items()
                        if s["backend"] == "host" or args.objcore]
    rc = 0
    for name in targets:
        spec = TARGETS.get(name)
        if not spec:
            print("  ?  %s: unknown target" % name); rc = 1; continue
        if spec["backend"] == "objcore" and not args.objcore:
            print("  -  %s: skipped (objcore backend; pass --objcore)" % name)
            continue
        if spec["backend"] == "objcore" and not os.path.isdir(
                os.path.join(OBJCORE, "build", "py")):
            print("  -  %s: skipped (objcore not built)" % name); continue
        try:
            exe, msg = build_target(name, spec, build_root,
                                    use_musl=args.musl, quiet=args.quiet)
        except Exception as e:
            print("  X  %s: %s" % (name, e)); rc = 1; continue
        if exe is None:
            print("  X  %s: %s" % (name, msg.splitlines()[0])); rc = 1
            log(args.quiet, "     " + "\n     ".join(msg.splitlines()[1:6]))
            continue
        out = run([exe])
        passed = spec["expect"] in out.stdout
        print("  %s  %s -- %s" % ("OK" if passed else "X ", name, spec["desc"]))
        for ln in out.stdout.strip().splitlines():
            log(args.quiet, "       " + ln)
        if not passed:
            rc = 1
    return rc


def cmd_bench(names, build_root, args):
    targets = names or list(BENCHES)
    for name in targets:
        spec = BENCHES.get(name)
        if not spec:
            print("  ?  %s: unknown bench" % name); continue
        try:
            exe, msg = build_target(name, spec, build_root,
                                    use_musl=args.musl, quiet=args.quiet)
        except Exception as e:
            print("  X  %s: %s" % (name, e)); continue
        if exe is None:
            print("  X  %s: %s" % (name, msg.splitlines()[0])); continue
        out = run([exe])
        print("  %s: %s" % (name, out.stdout.strip()))


def cmd_coverage(build_root, args):
    """Transpile every ShivyCX module, compile each, report how many compile
    (against glibc, or musl headers with --musl)."""
    import glob
    out = os.path.join(build_root, "coverage")
    os.makedirs(out, exist_ok=True)
    mods = []
    for g in COVERAGE_GLOBS:
        mods += glob.glob(os.path.join(REPO, g))
    r = run([sys.executable, PY2C, *mods, "--out", out], cwd=REPO)
    if r.returncode != 0:
        print("transpile failed:\n" + r.stderr); return 1
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import py2c as _p
    _p.write_runtime(out)
    rt = os.path.join(out, "shivyc_rt.o")
    compile_c(os.path.join(out, "shivyc_rt.c"), rt, inc_dirs=[out])
    cfiles = [f for f in os.listdir(out)
              if f.endswith(".c") and f != "shivyc_rt.c"]
    ok = 0
    fails = []
    for c in sorted(cfiles):
        oo = os.path.join(out, c[:-2] + ".o")
        res = compile_c(os.path.join(out, c), oo, inc_dirs=[out],
                        use_musl=args.musl)
        if res.returncode == 0:
            ok += 1
        else:
            n = res.stderr.count("error:")
            fails.append((n, c))
    mode = "musl headers" if args.musl else "glibc"
    print("self-host compile coverage (%s): %d/%d modules"
          % (mode, ok, len(cfiles)))
    if fails and not args.quiet:
        print("remaining (errors, module):")
        for n, c in sorted(fails)[:15]:
            print("  %2d  %s" % (n, c[:-2]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="ShivyCX self-host build/test")
    ap.add_argument("cmd", choices=["list", "test", "bench", "coverage"])
    ap.add_argument("names", nargs="*", help="target name(s)")
    ap.add_argument("--musl", action="store_true",
                    help="compile transpiled C against packaged musl headers")
    ap.add_argument("--objcore", action="store_true",
                    help="include objcore-backed targets")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch build dir")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if args.cmd == "list":
        print("self-host test targets:")
        for n, s in TARGETS.items():
            print("  %-8s [%s]  %s" % (n, s["backend"], s["desc"]))
        print("benchmarks:")
        for n, s in BENCHES.items():
            print("  %-8s [%s]  %s" % (n, s["backend"], s["desc"]))
        return 0

    build_root = tempfile.mkdtemp(prefix="shivyc-selfhost-")
    try:
        if args.cmd == "test":
            rc = cmd_test(args.names, build_root, args)
        elif args.cmd == "bench":
            rc = cmd_bench(args.names, build_root, args) or 0
        elif args.cmd == "coverage":
            rc = cmd_coverage(build_root, args)
    finally:
        if args.keep:
            print("build dir:", build_root)
        else:
            shutil.rmtree(build_root, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())

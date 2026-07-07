#!/usr/bin/env python3
"""jitc -- JIT-compile a page's <script type="rpython"> blocks to native .so.

For each rpython block the browser calls this at page load: py2c translates the
block to C and `gcc -O2 -shared` compiles it to a cached shared object the
page's python loads via ctypes -- a faster (native registers/SIMD, -O2) and
CPython-compatible alternative to a wasm VM.

rpython translation is slow, so each .so is cached under

    /tmp/minibrowser_cache/<page-id>/jit.<name>.so

keyed by a sha256 of the block source; an unchanged block on reload is a cache
hit with no recompile, so page reloads are fast. Per-page directories keep
caches from colliding when browsing multiple sites.

    python3 jitc.py page.json <page-id>     # compiles blocks, prints cache dir
"""
import glob
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_py2c():
    """Locate tools/py2c.py whether jitc runs from the repo or a staged build
    dir -- walk up from HERE looking for a tools/py2c.py."""
    d = HERE
    for _ in range(6):
        cand = os.path.join(d, "tools", "py2c.py")
        if os.path.isfile(cand):
            return cand
        d = os.path.dirname(d)
    return os.path.join(HERE, "..", "..", "..", "tools", "py2c.py")


PY2C = _find_py2c()
CACHE_ROOT = "/tmp/minibrowser_cache"


def page_id(name):
    """A filesystem-safe per-page cache key from a page name / URL."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_") or "page"


def cache_dir_for(name):
    return os.path.join(CACHE_ROOT, page_id(name))


def compile_block(name, code, cache_dir):
    """Translate + compile one rpython block to cache_dir/jit.<name>.so.
    Returns (so_path, status) where status is cached / built / a failure tag."""
    so = os.path.join(cache_dir, "jit.%s.so" % name)
    stamp = os.path.join(cache_dir, "jit.%s.hash" % name)
    h = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if os.path.isfile(so) and os.path.isfile(stamp) and \
            open(stamp).read().strip() == h:
        return so, "cached"

    bdir = os.path.join(cache_dir, "_build_%s" % name)
    os.makedirs(bdir, exist_ok=True)
    src = os.path.join(bdir, "%s.py" % name)
    with open(src, "w") as fh:
        fh.write(code + "\n")

    r = subprocess.run([sys.executable, PY2C, src, "--out", bdir],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        return None, "py2c-failed"
    csrc = glob.glob(os.path.join(bdir, "*.c"))
    if not csrc:
        return None, "no-c-emitted"
    cc = subprocess.run(["cc", "-O2", "-w", "-shared", "-fPIC"] + csrc +
                        ["-o", so, "-lm"], capture_output=True, text=True)
    if cc.returncode != 0:
        sys.stderr.write(cc.stderr)
        return None, "cc-failed"
    with open(stamp, "w") as fh:
        fh.write(h)
    return so, "built"


def compile_page(bundle, name):
    """JIT every rpython block in `bundle` into this page's cache dir."""
    cache_dir = cache_dir_for(name)
    os.makedirs(cache_dir, exist_ok=True)
    results = {}
    for blk, code in bundle.get("rpython", {}).items():
        so, status = compile_block(blk, code, cache_dir)
        results[blk] = (so, status)
    return cache_dir, results


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: jitc.py page.json <page-id>\n")
        return 2
    with open(argv[1]) as fh:
        bundle = json.load(fh)
    cache_dir, results = compile_page(bundle, argv[2])
    for blk, (so, status) in results.items():
        print("jit %s: %s (%s)" % (blk, status, so))
        if so is None:
            return 1
    print(cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

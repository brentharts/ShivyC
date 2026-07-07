#!/usr/bin/env python3
"""End-to-end check of the "native code in the page" model, under CPython.

The same shape the browser runs, but on ordinary CPython with real ctypes (the
backwards-compatible path). It proves the whole pipeline with the page's *exact*
source syntax -- `dll = ctypes.CDLL(...)` / `dll.calc_sum(1, 2)`:

  1. www2json extracts the <script type="rpython" id="foo"> block + the
     <script type="python"> block from pyjit.html.
  2. jitc JIT-compiles the rpython block (py2c -> gcc -O2 -shared) to a cached
     .so under /tmp/minibrowser_cache/<page-id>/jit.foo.so.
  3. we run the page's python with minidom's console/document/window and a ctypes
     whose CDLL redirects the source path (/tmp/jit.foo.so) to the per-page cache
     (exactly what the browser does), then fire the button's foo().
  4. assert the native calc_sum(1, 2) returned 3 into the page console.

Run:  python3 jit_test.py
"""
import ctypes as real_ctypes
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


class _RedirectCtypes(types.ModuleType):
    """A stand-in `ctypes` whose CDLL maps a page's /tmp/jit.<name>.so path to
    that page's cache dir -- the redirect the browser performs so caches from
    different sites don't collide. Everything else delegates to real ctypes."""
    def __init__(self, cache_dir):
        super().__init__("ctypes")
        self._cache_dir = cache_dir
        for k in dir(real_ctypes):
            if not k.startswith("__") and k != "CDLL":
                setattr(self, k, getattr(real_ctypes, k))

    def CDLL(self, path, *a, **kw):
        real = os.path.join(self._cache_dir, os.path.basename(path))
        return real_ctypes.CDLL(real, *a, **kw)


def main():
    sys.path.insert(0, HERE)
    import www2json
    import jitc

    with open(os.path.join(HERE, "pyjit.html")) as fh:
        bundle = www2json.build_bundle("pyjit.html", fh.read())
    assert bundle["rpython"], "no <script type='rpython'> captured"
    assert "ctypes" in bundle["python"], "python script missing"
    print("captured rpython blocks: %s" % list(bundle["rpython"]))

    print("JIT-compiling (py2c -> gcc -O2 -shared; first build is slow)...")
    cache_dir, results = jitc.compile_page(bundle, "pyjit")
    for blk, (so, status) in results.items():
        print("  %s: %s -> %s" % (blk, status, so))
        assert so, "JIT compile failed for %s" % blk

    # Run the page python with minidom globals + the redirecting ctypes.
    ns = {}
    exec(open(os.path.join(HERE, "minidom.py")).read(), ns)
    sys.modules["ctypes"] = _RedirectCtypes(cache_dir)
    exec(bundle["python"], ns)              # import ctypes; dll = CDLL(...); def foo
    ns["foo"]()                             # simulate the click

    out = ns["console"]._text()
    print("---- page console ----")
    print(out)
    print("----------------------")
    assert "hello minipy console" in out, "console.log(string) missing"
    assert "\n3" in out or out.strip().endswith("3"), \
        "native calc_sum(1,2) did not return 3"
    print("jit_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

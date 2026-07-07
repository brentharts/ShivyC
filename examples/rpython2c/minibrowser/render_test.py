#!/usr/bin/env python3
"""Off-target render check for the minibrowser (runs under CPython).

The rpython dialect is a subset of Python, so the *same* renderer source runs
unmodified under CPython -- letting us exercise the real build_page() ->
render_node() -> place()/paint() path (including the bitmap font) without a
Wayland compositor, and assert the page actually draws and that a button click
fires its signal. This mirrors how the rwayland/rpyqt drawing helpers are meant
to be unit-tested off-target.

    python3 render_test.py            # asserts only
    python3 render_test.py out.png    # also writes a PNG screenshot (needs PIL)

Under ShivyCX the same files are instead transpiled to a native Wayland client
(`make minibrowser`).
"""
import os
import sys
import types


def _install_ctypes_stub():
    # Importing rpyqt binds the generated-runtime symbol `rwl_run`, which does
    # not exist off-target. Stub `rpy_ctypes` so import succeeds; we never run
    # the Wayland loop here.
    stub = types.ModuleType("rpy_ctypes")

    class _Fn:
        restype = None
        argtypes = []

        def __call__(self, *a):
            return 0

    class _Lib:
        def __getattr__(self, k):
            f = _Fn()
            object.__setattr__(self, k, f)
            return f

    stub.CDLL = lambda *a, **k: _Lib()
    for n in ("c_int", "c_double", "c_char_p", "c_void_p", "c_float", "c_bool"):
        setattr(stub, n, None)
    sys.modules["rpy_ctypes"] = stub


def _find_button(rpyqt, layout):
    for it in layout.items:
        if isinstance(it, rpyqt.QPushButton):
            return it
        if isinstance(it, rpyqt.QBoxLayout):
            got = _find_button(rpyqt, it)
            if got is not None:
                return got
    return None


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    rpy_lib = os.path.abspath(
        os.path.join(here, "..", "..", "..", "tools", "rpy_lib"))
    _install_ctypes_stub()
    sys.path.insert(0, here)
    sys.path.insert(0, rpy_lib)

    import rpyqt
    import json2qt
    from page_data import build_page

    box = rpyqt.QVBoxLayout()
    root = build_page()
    json2qt.render_node(root, box)
    status = rpyqt.QLabel("READY")
    json2qt._status = status
    box.addWidget(status)

    W, H = rpyqt.WIN_W, 1000
    box.place(rpyqt.PAD, rpyqt.PAD, W - 2 * rpyqt.PAD, H)
    fb = [rpyqt.COL_BG] * (W * H)
    box.paint(fb, W, H)

    bg = rpyqt.COL_BG & 0xFFFFFF
    painted = sum(1 for p in fb if (p & 0xFFFFFF) != bg)
    print("non-background pixels: %d" % painted)
    assert painted > 500, "render produced too few pixels"

    btn = _find_button(rpyqt, box)
    assert btn is not None, "expected at least one button in the sample page"
    btn.on_press(btn.x + 2, btn.y + 2)
    assert status.text == "CLICKED", "button signal did not reach the handler"
    print("button click -> status = %r  OK" % status.text)

    if len(argv) > 1:
        from PIL import Image
        status.text = "READY"
        box.paint(fb, W, H)
        img = Image.new("RGB", (W, H))
        px = img.load()
        for y in range(H):
            base = y * W
            for x in range(W):
                v = fb[base + x]
                px[x, y] = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
        img.crop((0, 0, W, 780)).save(argv[1])
        print("wrote %s" % argv[1])

    print("render_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

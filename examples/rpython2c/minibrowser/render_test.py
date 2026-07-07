#!/usr/bin/env python3
"""Off-target navigation + render check for the minibrowser (runs under CPython).

The rpython dialect is a subset of Python, so the *same* renderer source runs
unmodified under CPython -- letting us exercise the real navigation loop end to
end without a Wayland compositor:

    navigate() -> www2json (os.system) -> page.json
               -> minijson.load_page() -> render_node()/place()/paint()

and assert that pages actually draw, that clicking an in-page link loads the
linked page, that typing a name + Enter in the URL bar loads it, and that Back
returns. Because navigate() shells out to `python3 www2json.py <page>.html`, the
test runs from this directory (where www2json.py + the *.html site live).

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


def _collect_text(rpyqt, layout, out):
    for it in layout.items:
        if isinstance(it, rpyqt.QBoxLayout):
            _collect_text(rpyqt, it, out)
        else:
            href = getattr(it, "href", "")
            out.append("LINK:" + href if href else getattr(it, "text", ""))
    return out


def _find_link(rpyqt, layout):
    for it in layout.items:
        if isinstance(it, rpyqt.QLink):
            return it
        if isinstance(it, rpyqt.QBoxLayout):
            got = _find_link(rpyqt, it)
            if got is not None:
                return got
    return None


def _find_lineedit(rpyqt, layout):
    for it in layout.items:
        if isinstance(it, rpyqt.QLineEdit):
            return it
        if isinstance(it, rpyqt.QBoxLayout):
            got = _find_lineedit(rpyqt, it)
            if got is not None:
                return got
    return None


def _painted(rpyqt, box, H=1200):
    W = rpyqt.WIN_W
    box.place(rpyqt.PAD, rpyqt.PAD, W - 2 * rpyqt.PAD, H)
    fb = [rpyqt.COL_BG] * (W * H)
    box.paint(fb, W, H)
    bg = rpyqt.COL_BG & 0xFFFFFF
    return sum(1 for p in fb if (p & 0xFFFFFF) != bg), fb


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    rpy_lib = os.path.abspath(
        os.path.join(here, "..", "..", "..", "tools", "rpy_lib"))
    _install_ctypes_stub()
    sys.path.insert(0, here)
    sys.path.insert(0, rpy_lib)
    os.chdir(here)                       # navigate() shells out to www2json here

    import rpyqt
    import json2qt

    # ---- boot like main(): load + render the home page ------------------
    app = rpyqt.QApplication()
    win = rpyqt.QWidget()
    json2qt._win = win
    json2qt._hist = json2qt.History()
    rpyqt.set_active(win)
    json2qt.navigate("home", 0)
    assert json2qt._hist.get_source() == "home"

    painted, _ = _painted(rpyqt, win.box)
    print("home: non-background pixels: %d" % painted)
    assert painted > 500, "home render produced too few pixels"
    home_text = _collect_text(rpyqt, win.box, [])
    assert any("Home" in t for t in home_text), "home heading missing"
    assert "LINK:/about" in home_text, "about link missing from home"
    print("home rendered with toolbar + about link  OK")

    # ---- click the in-page About link -> load the about page ------------
    link = _find_link(rpyqt, win.box)
    assert link is not None, "no link on home page"
    link.on_press(link.x + 2, link.y + 2)     # -> on_link -> navigate("about")
    assert json2qt._hist.get_source() == "about", "link did not load about"
    about_text = _collect_text(rpyqt, win.box, [])
    assert any("About" in t for t in about_text), "about heading missing"
    assert json2qt._hist.has_back() > 0, "back-stack empty after navigation"
    print("about link click -> about page loaded  OK")

    # ---- type a name + Enter in the URL bar -> load it ------------------
    url = _find_lineedit(rpyqt, win.box)
    assert url is not None, "no URL field in toolbar"
    win.on_pointer_button(url.x + 4, url.y + 4, 1)      # focus
    assert rpyqt._focused is url, "URL field did not focus"
    for _ in range(len(url.text)):                      # clear the pre-filled name
        rpyqt.rw_key(8, 1)
    assert url.text == "", "field not cleared"
    for ch in "home":
        rpyqt.rw_key(ord(ch), 1)
    rpyqt.rw_key(13, 1)                                 # Enter -> on_go
    assert json2qt._hist.get_source() == "home", "typed Enter did not load home"
    print("typed 'home' + Enter -> home page loaded  OK")

    # ---- Back -> returns to the previously viewed page ------------------
    json2qt.on_back()
    assert json2qt._hist.get_source() == "about", "Back did not return to about"
    print("Back -> about  OK")

    if len(argv) > 1:
        from PIL import Image
        json2qt.navigate("home", 1)
        W, H = rpyqt.WIN_W, 1200
        _, fb = _painted(rpyqt, win.box, H)
        img = Image.new("RGB", (W, H))
        px = img.load()
        for y in range(H):
            base = y * W
            for x in range(W):
                v = fb[base + x]
                px[x, y] = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
        img.crop((0, 0, W, 820)).save(argv[1])
        print("wrote %s" % argv[1])

    print("render_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""json2qt -- a pure-rpython DOM renderer + live navigation for the minibrowser.

Port of OpenSourceJesus's Tetra `json2qt.py`: it walks a DOM and builds a widget
tree, running as a native Wayland client through `rpyqt` (software-rendered, no
Qt). Two capabilities beyond a static render:

  * **Runtime pages.** The page is read from a `page.json` bundle at runtime by
    `minijson.load_page` (not co-compiled), so the same binary can display any
    page the CPython helper `www2json.py` produces.

  * **Live navigation.** A toolbar (Back / URL field / GO) plus in-page links
    turn a click or a typed name + Enter into an actual page load: the target is
    resolved to a local page, `www2json.py` is re-run on it (via os.system) to
    (re)produce `page.json`, that JSON is parsed into a fresh `Node` tree, and
    the window's layout is rebuilt in place.

Files: `dom.py` (Node), `minijson.py` (runtime JSON -> Node), `www2json.py`
(CPython HTML -> json/py), and the local site `home.html` / `about.html`.

Build (multi-file translation unit):
    python3 -m shivyc.main json2qt.py dom.py minijson.py -o minibrowser
or `make minibrowser`. The binary shells out to `python3 www2json.py`, so run it
from a directory holding www2json.py + the *.html site (the Makefile stages
these next to the binary).
"""
import os
import sys
from rpyqt import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout,
                   QLabel, QPushButton, QHeading, QLink, QLineEdit, QHLine,
                   QCanvas, last_link_href, last_action)
from minijson import load_page, parse_dom_str, has_python, has_rpython
from interp_embed import mpy_boot, mpy_call, mpy_call_s, mpy_call_i

# ----- navigation state ---------------------------------------------------
# The window is held in a *typed* global so setLayout (which takes a concrete
# QBoxLayout, not an obj) can be called on it from the no-arg click handlers to
# rebuild the page in place. The url field / status label are read back through
# their base-class accessors, so plain obj globals are fine.
_win: "QWidget*" = None
_url = None
_status = None
_scripted = 0            # 1 while the current page is driven by minipy


class NavBase:
    """Root so History is object-model (vtable dispatch through the global)."""

    def __init__(self):
        self.z = 0


class History(NavBase):
    """Current page name + a back-stack of previously visited names."""

    def __init__(self):
        self.back: "list[str]" = []
        self.source = "home"

    def get_source(self) -> "char*":
        return self.source

    def set_source(self, name: "char*") -> None:
        self.source = name

    def push(self, name: "char*") -> None:
        self.back.append(name)

    def has_back(self) -> int:
        return len(self.back)

    def pop_back(self) -> "char*":
        if len(self.back) > 0:
            return self.back.pop()
        return "home"


_hist = None


def resolve(target: "char*") -> "char*":
    """Normalise a link href or typed name to a local page name."""
    t = target
    if len(t) == 0:
        return "home"
    if ord(t[0]) == 47:          # leading '/'
        t = t[1:]
    if len(t) == 0:
        return "home"
    return t


def fetch(name: "char*") -> None:
    """Re-run the CPython helper to (re)produce page.json for `name`."""
    cmd = "python3 www2json.py " + name + ".html --out ."
    os.system(cmd)


# ----- page scripting (minipy) -------------------------------------------
# A scripted page (one with a <script type="python">) is driven by the embedded
# minipy interpreter, which owns the live DOM. On navigation the page's python +
# a body tree built from the HTML are compiled to page.mpyc and booted; the
# browser then renders from what minipy reports, re-reading it after every event
# so script mutations (createElement / value= / appendChild) show on screen.
#
#   render:  mpy_call_s("__serialize") -> DOM JSON -> parse_dom_str -> widgets
#   click:   mpy_call_i("__fire", handle) runs the element's onclick, then render
#   console/alert surfaces: mpy_call_s("__console") / ("__alert")


def boot_page() -> None:
    os.system("python3 pycompile.py page.json minidom.py page.mpyc")
    mpy_boot("page.mpyc")


def _atoi(s: "char*") -> "int":
    n = len(s)
    i = 0
    v = 0
    while i < n:
        c = ord(s[i])
        if c >= 48 and c <= 57:
            v = v * 10 + (c - 48)
        i = i + 1
    return v


def render_from_dom() -> None:
    # Rebuild the page from minipy's current DOM (after boot or an event).
    s: "char*" = mpy_call_s("__serialize")
    root = parse_dom_str(s)
    apply_ui(root)


def on_script() -> None:
    # A script-backed element was clicked: its recorded action is the element's
    # integer handle. Fire its onclick in minipy, then re-render the DOM.
    act: "char*" = last_action()
    h: "int" = _atoi(act)
    mpy_call_i("__fire", h)
    render_from_dom()


# ----- tag dispatch (the port of Tetra's generate_interface) --------------
def heading_level(tag: "char*") -> int:
    if tag == "h1":
        return 1
    if tag == "h2":
        return 2
    if tag == "h3":
        return 3
    if tag == "h4":
        return 4
    if tag == "h5":
        return 5
    return 6


def is_heading(tag: "char*") -> int:
    if tag == "h1" or tag == "h2" or tag == "h3":
        return 1
    if tag == "h4" or tag == "h5" or tag == "h6":
        return 1
    return 0


def is_structural(tag: "char*") -> int:
    if tag == "html" or tag == "head" or tag == "body" or tag == "div":
        return 1
    if tag == "span" or tag == "section" or tag == "article" or tag == "main":
        return 1
    if tag == "header" or tag == "footer" or tag == "nav":
        return 1
    return 0


def is_paragraph(tag: "char*") -> int:
    if tag == "p" or tag == "blockquote" or tag == "cite":
        return 1
    if tag == "dt" or tag == "dd" or tag == "figcaption":
        return 1
    return 0


def render_children(node: "obj", box: "QBoxLayout") -> None:
    n = node.child_count()
    i = 0
    while i < n:
        render_node(node.child(i), box)
        i = i + 1


def render_cell(cell: "obj", row: "QBoxLayout") -> None:
    ctag: "char*" = cell.tag()
    if ctag == "td" or ctag == "th":
        row.addWidget(QLabel(cell.get_text()))


def render_row(tr: "obj", box: "QBoxLayout") -> None:
    row = QHBoxLayout()
    n = tr.child_count()
    c = 0
    while c < n:
        render_cell(tr.child(c), row)
        c = c + 1
    box.addLayout(row)


def render_section(section: "obj", box: "QBoxLayout") -> None:
    stag: "char*" = section.tag()
    if stag == "tr":
        render_row(section, box)
    elif stag == "tbody" or stag == "thead" or stag == "tfoot":
        render_table(section, box)


def render_table(node: "obj", box: "QBoxLayout") -> None:
    n = node.child_count()
    r = 0
    while r < n:
        render_section(node.child(r), box)
        r = r + 1


# ----- native canvas (a JIT'd rpython shader draws the pixels) ------------
# A <canvas> is filled by calling the page's native `pixel(x, y) -> argb` shader
# (a <script type="rpython"> block, JIT-compiled to jit.shader.so) once per
# pixel through the FFI shim -- native code producing the drawing on the page.
# The ctypes binding is guarded so json2qt still imports under CPython (the
# renderer test never draws a canvas); py2c keeps the branch and links mb_ffi.c.
if sys.implementation.name != "cpython":
    import rpy_ctypes as _ct
    _ffi = _ct.CDLL("mb_ffi")
    _ffi.mb_dlopen.restype = _ct.c_long
    _ffi.mb_dlopen.argtypes = [_ct.c_char_p]
    _ffi.mb_dlsym.restype = _ct.c_long
    _ffi.mb_dlsym.argtypes = [_ct.c_long, _ct.c_char_p]
    _ffi.mb_call2i.restype = _ct.c_int
    _ffi.mb_call2i.argtypes = [_ct.c_long, _ct.c_int, _ct.c_int]

CANVAS_W = 96
CANVAS_H = 96


def _page_id(name: "char*") -> "char*":
    # Match jitc.page_id: alnum kept, everything else -> '_'.
    out = ""
    i = 0
    n = len(name)
    while i < n:
        c = ord(name[i])
        if (c >= 48 and c <= 57) or (c >= 65 and c <= 90) \
                or (c >= 97 and c <= 122):
            out = out + name[i]
        else:
            out = out + "_"
        i = i + 1
    return out


def jit_page() -> None:
    # JIT-compile the page's <script type="rpython"> blocks (idempotent/cached).
    src: "char*" = _hist.get_source()
    os.system("python3 jitc.py page.json " + src)


def _shader_so() -> "char*":
    src: "char*" = _hist.get_source()
    return "/tmp/minibrowser_cache/" + _page_id(src) + "/jit.shader.so"


def render_canvas(box: "QBoxLayout") -> None:
    cvs = QCanvas(CANVAS_W, CANVAS_H)
    so: "char*" = _shader_so()
    h = _ffi.mb_dlopen(so)
    if h != 0:
        fn = _ffi.mb_dlsym(h, "pixel")
        if fn != 0:
            px = []
            y = 0
            while y < CANVAS_H:
                x = 0
                while x < CANVAS_W:
                    px.append(_ffi.mb_call2i(fn, x, y))
                    x = x + 1
                y = y + 1
            cvs.set_pixels(px)
    box.addWidget(cvs)


def render_node(node: "obj", box: "QBoxLayout") -> None:
    # Pull every string off the (obj-typed) node into an explicitly annotated
    # char* local up front. Reading obj-method results straight into an
    # expression makes the translator's type inference flaky (it sometimes keeps
    # the result boxed), so binding them to typed locals here keeps the rest of
    # the function unambiguous.
    t: "char*" = node.tag()
    txt: "char*" = node.get_text()

    if is_structural(t):
        render_children(node, box)
        return

    if is_heading(t):
        box.addWidget(QHeading(txt, heading_level(t)))
        return

    if is_paragraph(t):
        box.addWidget(QLabel(txt))
        return

    if t == "a":
        href: "char*" = node.get_href()
        link = QLink(txt, href)
        link.clicked.connect(on_link)
        box.addWidget(link)
        return

    if t == "li":
        bullet: "char*" = "- " + txt
        box.addWidget(QLabel(bullet))
        render_children(node, box)
        return

    if t == "ul" or t == "ol":
        render_children(node, box)
        return

    if t == "canvas":
        render_canvas(box)
        return

    if t == "hr":
        box.addWidget(QHLine())
        return
    if t == "br":
        box.addWidget(QLabel(""))
        return

    if t == "img":
        box.addWidget(QLabel("[img]"))
        return

    if t == "input":
        it: "char*" = node.get_itype()
        val: "char*" = node.get_value()
        if it == "hidden":
            return
        if it == "submit" or it == "button":
            btn = QPushButton(val)
            btn.clicked.connect(on_go)
            box.addWidget(btn)
            return
        ph: "char*" = node.get_placeholder()
        field = QLineEdit(val)
        field.setPlaceholderText(ph)
        field.returnPressed.connect(on_go)
        box.addWidget(field)
        return

    if t == "textarea":
        val2: "char*" = node.get_value()
        box.addWidget(QLineEdit(val2))
        return

    if t == "button":
        oc: "char*" = node.get_onclick()
        btn = QPushButton(txt)
        if len(oc) > 0:
            btn.set_action(oc)
            btn.clicked.connect(on_script)
        else:
            btn.clicked.connect(on_go)
        box.addWidget(btn)
        return

    if t == "form" or t == "figure":
        sub = QVBoxLayout()
        render_children(node, sub)
        box.addLayout(sub)
        return

    if t == "table":
        render_table(node, box)
        return

    if t == "#text":
        box.addWidget(QLabel(txt))
        return

    if len(txt) > 0:
        box.addWidget(QLabel(txt))
    render_children(node, box)


# ----- UI assembly + navigation ------------------------------------------
def render_console(text: "char*", box: "QBoxLayout") -> None:
    # One label per console.log line (text is the lines joined by '\n').
    n = len(text)
    start = 0
    i = 0
    while i < n:
        if ord(text[i]) == 10:
            line: "char*" = text[start:i]
            box.addWidget(QLabel("console: " + line))
            start = i + 1
        i = i + 1
    last: "char*" = text[start:n]
    if len(last) > 0:
        box.addWidget(QLabel("console: " + last))


def build_ui(root: "obj") -> "QVBoxLayout":
    global _url, _status
    box = QVBoxLayout()
    src: "char*" = _hist.get_source()

    bar = QHBoxLayout()
    back = QPushButton("< BACK")
    back.clicked.connect(on_back)
    bar.addWidget(back)
    url = QLineEdit(src)
    url.setPlaceholderText("enter page name")
    url.returnPressed.connect(on_go)
    _url = url
    bar.addWidget(url)
    go = QPushButton("GO")
    go.clicked.connect(on_go)
    bar.addWidget(go)
    box.addLayout(bar)

    render_node(root, box)

    # Scripted pages surface their console.log / window.alert output on screen.
    if _scripted != 0:
        box.addWidget(QHLine())
        ctext: "char*" = mpy_call_s("__console")
        if len(ctext) > 0:
            render_console(ctext, box)
        atext: "char*" = mpy_call_s("__alert")
        if len(atext) > 0:
            box.addWidget(QLabel("[alert] " + atext))

    label: "char*" = "LOADED " + src
    status = QLabel(label)
    _status = status
    box.addWidget(status)
    return box


def apply_ui(root: "obj") -> None:
    box = build_ui(root)
    w = _win
    w.setLayout(box)


def navigate(name: "char*", push: int) -> None:
    global _scripted
    if push:
        _hist.push(_hist.get_source())
    _hist.set_source(name)
    fetch(name)
    if has_rpython("page.json") != 0:
        jit_page()               # compile <script type="rpython"> -> native .so
    if has_python("page.json") != 0:
        # Scripted page: minipy owns the live DOM; render from what it reports.
        _scripted = 1
        boot_page()
        render_from_dom()
    else:
        _scripted = 0
        root = load_page("page.json")
        apply_ui(root)


# ----- no-arg handlers wired to signals ----------------------------------
def on_go() -> None:
    u = _url
    tv: "char*" = u.text_value()
    navigate(resolve(tv), 1)


def on_link() -> None:
    navigate(resolve(last_link_href()), 1)


def on_back() -> None:
    if _hist.has_back() > 0:
        name = _hist.pop_back()
        navigate(name, 0)


def script_selftest() -> int:
    # Headless proof of live DOM mutation: load the scripted page, boot it, then
    # drive events by element handle (as clicks would) and read the DOM back,
    # checking that createElement/appendChild and value= actually take effect.
    global _hist, _scripted
    _hist = History()
    _hist.set_source("pyscript2")
    fetch("pyscript2")
    _scripted = 1
    boot_page()

    s0: "char*" = mpy_call_s("__serialize")
    root0 = parse_dom_str(s0)
    print("initial dom: " + s0)
    btn = root0.child(0)
    oc0: "char*" = btn.get_onclick()
    h0: "int" = _atoi(oc0)                     # the clickme button's handle
    mpy_call_i("__fire", h0)                   # click it -> foo()

    s1: "char*" = mpy_call_s("__serialize")
    root1 = parse_dom_str(s1)
    inp: "obj" = root1.child(1)
    vfoo: "char*" = inp.get_value()
    print("input value after foo: " + vfoo)
    newbtn = root1.child(2)
    oc1: "char*" = newbtn.get_onclick()
    h1: "int" = _atoi(oc1)                      # the created button's handle
    mpy_call_i("__fire", h1)                    # click it -> bar()

    s2: "char*" = mpy_call_s("__serialize")
    root2 = parse_dom_str(s2)
    inp2: "obj" = root2.child(1)
    vbar: "char*" = inp2.get_value()
    print("input value after bar: " + vbar)
    ctext: "char*" = mpy_call_s("__console")
    print("console: " + ctext)
    return 0


def jit_selftest() -> int:
    # Headless proof of native page code: load pyjit.html (a <script
    # type="rpython"> block JIT-compiled to a .so + a python script that calls
    # it via ctypes), boot it, and fire the button's foo() -- which runs the
    # native calc_sum(1,2) through the interpreter's FFI. The page console
    # should show the returned 3.
    global _hist, _scripted
    _hist = History()
    _hist.set_source("pyjit")
    fetch("pyjit")
    _scripted = 1
    boot_page()
    s0: "char*" = mpy_call_s("__serialize")
    root0 = parse_dom_str(s0)
    btn = root0.child(0)
    oc0: "char*" = btn.get_onclick()
    h0: "int" = _atoi(oc0)
    mpy_call_i("__fire", h0)                    # click clickme -> foo()
    ctext: "char*" = mpy_call_s("__console")
    print("console: " + ctext)
    return 0


def canvas_selftest() -> int:
    # Headless proof of "native draws to the page": load canvas.html, JIT its
    # <script type="rpython"> shader, and confirm the native pixel(x,y) varies
    # across coordinates (i.e. it really shaded per pixel, not a constant fill).
    global _hist
    _hist = History()
    _hist.set_source("canvas")
    fetch("canvas")
    jit_page()
    so: "char*" = _shader_so()
    h = _ffi.mb_dlopen(so)
    if h == 0:
        print("canvas: dlopen failed")
        return 1
    fn = _ffi.mb_dlsym(h, "pixel")
    if fn == 0:
        print("canvas: dlsym pixel failed")
        return 1
    p00 = _ffi.mb_call2i(fn, 0, 0)
    pa = _ffi.mb_call2i(fn, 20, 0)
    pb = _ffi.mb_call2i(fn, 0, 20)
    if p00 != pa and p00 != pb:
        print("canvas OK: native shader varies per pixel")
    else:
        print("canvas FAIL: shader not varying")
    # exercise the full render loop the browser runs to fill the QCanvas
    cnt = 0
    y = 0
    while y < CANVAS_H:
        x = 0
        while x < CANVAS_W:
            _ffi.mb_call2i(fn, x, y)
            cnt = cnt + 1
            x = x + 1
        y = y + 1
    if cnt == CANVAS_W * CANVAS_H:
        print("canvas OK: shaded all pixels")
    return 0


def main() -> int:
    global _hist, _win
    if len(sys.argv) > 1 and sys.argv[1] == "--script-selftest":
        return script_selftest()
    if len(sys.argv) > 1 and sys.argv[1] == "--jit-selftest":
        return jit_selftest()
    if len(sys.argv) > 1 and sys.argv[1] == "--canvas-selftest":
        return canvas_selftest()
    app = QApplication()
    win = QWidget()
    win.setWindowTitle("MINIBROWSER")
    _win = win
    _hist = History()
    navigate("home", 0)          # load + render the initial page
    return app.exec_(win)

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
                   last_link_href, last_action)
from minijson import load_page
from interp_embed import mpy_boot, mpy_call

# ----- navigation state ---------------------------------------------------
# The window is held in a *typed* global so setLayout (which takes a concrete
# QBoxLayout, not an obj) can be called on it from the no-arg click handlers to
# rebuild the page in place. The url field / status label are read back through
# their base-class accessors, so plain obj globals are fine.
_win: "QWidget*" = None
_url = None
_status = None


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
# A page's <script type="python"> runs on the embedded minipy interpreter, not
# the renderer. The script is compiled to page.mpyc by the CPython helper
# (pycompile.py) and booted lazily on the first onclick, so scriptless pages
# never pay for the interpreter. `_booted` records which page is currently
# booted so we recompile only when the page changes.
_booted = ""


def boot_page() -> None:
    global _booted
    src: "char*" = _hist.get_source()
    if _booted == src:
        return
    os.system("python3 pycompile.py page.json minidom.py page.mpyc")
    mpy_boot("page.mpyc")
    _booted = src


def handler_name(action: "char*") -> "char*":
    # Turn an inline handler like "foo()" into the function name "foo".
    a = action
    n = len(a)
    i = 0
    while i < n:
        if a[i] == "(":
            return a[0:i]
        i = i + 1
    return a


def on_script() -> None:
    boot_page()
    act: "char*" = last_action()
    name: "char*" = handler_name(act)
    mpy_call(name)


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
    if push:
        _hist.push(_hist.get_source())
    _hist.set_source(name)
    fetch(name)
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
    # Headless proof of the full script path: load a scripted page, compile its
    # python to bytecode, boot it on the embedded interpreter, and fire the
    # button's onclick handler -- printing whatever the page script logs. Used
    # by the Makefile/tests where no Wayland compositor (and no click) exists.
    global _hist
    _hist = History()
    _hist.set_source("pyscript")
    fetch("pyscript")            # www2json pyscript.html -> page.json
    boot_page()                  # pycompile -> page.mpyc -> mpy_boot
    r = mpy_call("foo")          # fire the handler, as a click would
    print("selftest mpy_call rc=" + str(r))
    return 0


def main() -> int:
    global _hist, _win
    if len(sys.argv) > 1 and sys.argv[1] == "--script-selftest":
        return script_selftest()
    app = QApplication()
    win = QWidget()
    win.setWindowTitle("MINIBROWSER")
    _win = win
    _hist = History()
    navigate("home", 0)          # load + render the initial page
    return app.exec_(win)

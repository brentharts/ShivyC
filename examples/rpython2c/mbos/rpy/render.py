"""render -- walk a dom.Node tree and paint it to the VGA text console.

The rpython replacement for mbos's hand-written render.c, and the in-kernel
counterpart of json2qt.py: same traversal (block vs inline, recurse over
children), lowered to characters on the 80x25 grid instead of Qt widgets. The
console itself lives in C (console.c); this module reaches it through the
mbos_glue FFI shim -- exactly the ctypes-guarded pattern json2qt.py uses to
call its Wayland/canvas backend, so the same source imports under CPython too.
"""
import sys
if sys.implementation.name == 'shivyc':
    import rpy_ctypes as ctypes
else:
    import ctypes

from dom import Node

_g = ctypes.CDLL("mbos_glue")
_g.mb_putc.restype = None
_g.mb_putc.argtypes = [ctypes.c_int]
_g.mb_newline.restype = None
_g.mb_newline.argtypes = []
_g.mb_set_attr.restype = None
_g.mb_set_attr.argtypes = [ctypes.c_int]
_g.mb_col.restype = ctypes.c_int
_g.mb_col.argtypes = []
_g.mb_clear.restype = None
_g.mb_clear.argtypes = [ctypes.c_int]

# VGA text attributes (fg | bg<<4), same palette as console.c / render.c.
GREY = 7
DGREY = 8
LCYAN = 11
WHITE = 15
YELLOW = 14
COLS = 80


def _putc(c: int) -> None:
    _g.mb_putc(c)


def _puts(s: "char*") -> None:
    i = 0
    n = len(s)
    while i < n:
        _g.mb_putc(ord(s[i]))
        i = i + 1


def _emit_word(w: "char*") -> None:
    ln = len(w)
    if ln <= 0:
        return
    col = _g.mb_col()
    need = ln
    if col > 0:
        need = need + 1
    if col + need > COLS:
        _g.mb_newline()
        col = 0
    if col > 0:
        _g.mb_putc(32)
    _puts(w)


def _emit_text(s: "char*") -> None:
    # split on ASCII whitespace, wrap each word
    i = 0
    n = len(s)
    while i < n:
        while i < n:
            c = ord(s[i])
            if c == 32 or c == 9 or c == 10 or c == 13:
                i = i + 1
            else:
                break
        start = i
        while i < n:
            c = ord(s[i])
            if c == 32 or c == 9 or c == 10 or c == 13:
                break
            i = i + 1
        if i > start:
            _emit_word(s[start:i])


def _ensure_line_start() -> None:
    if _g.mb_col() > 0:
        _g.mb_newline()


def _underline(ch: int) -> None:
    _g.mb_newline()
    i = 0
    while i < COLS:
        _g.mb_putc(ch)
        i = i + 1
    _g.mb_newline()


def _is_heading(t: "char*") -> int:
    if t == "h1" or t == "h2" or t == "h3":
        return 1
    return 0


def _is_block(t: "char*") -> int:
    if _is_heading(t) == 1:
        return 1
    if t == "p" or t == "div" or t == "ul" or t == "ol":
        return 1
    if t == "li" or t == "body" or t == "document" or t == "title":
        return 1
    return 0


def render_node(node: "obj", attr: int) -> None:
    t: "char*" = node.tag()

    if t == "text":
        _g.mb_set_attr(attr)
        _emit_text(node.get_text())
        return
    if t == "br":
        _g.mb_newline()
        return
    if t == "head" or t == "script" or t == "style" or t == "meta":
        return

    if t == "a":
        _g.mb_set_attr(LCYAN)
        render_children(node, LCYAN)
        href: "char*" = node.get_href()
        if len(href) > 0:
            _g.mb_set_attr(DGREY)
            hl = len(href)
            if _g.mb_col() + 3 + hl > COLS:
                _g.mb_newline()
            elif _g.mb_col() > 0:
                _g.mb_putc(32)
            _g.mb_putc(91)          # '['
            _puts(href)
            _g.mb_putc(93)          # ']'
        _g.mb_set_attr(attr)
        return

    if _is_block(t) == 1:
        _ensure_line_start()
        my = attr
        if t == "h1":
            my = YELLOW
        elif _is_heading(t) == 1:
            my = WHITE
        elif t == "title":
            my = 10             # light green
        if t == "li":
            _g.mb_set_attr(attr)
            _emit_word("*")
        render_children(node, my)
        if t == "h1":
            _g.mb_set_attr(my)
            _underline(61)          # '='
        elif _is_heading(t) == 1 or t == "p":
            _g.mb_newline()
            _g.mb_newline()
        else:
            _g.mb_newline()
        return

    render_children(node, attr)


def render_children(node: "obj", attr: int) -> None:
    n = node.child_count()
    i = 0
    while i < n:
        render_node(node.child(i), attr)
        i = i + 1


def render_page(doc: "obj") -> None:
    _g.mb_clear(GREY)
    render_node(doc, GREY)

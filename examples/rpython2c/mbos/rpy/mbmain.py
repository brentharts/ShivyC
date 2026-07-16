"""mbmain -- the rpython entry the kernel calls to render one page.

Ties the rpython pieces together: take the page HTML (handed in from C via the
mbos_glue shim), parse it with htmlparse, and paint it with render. This is the
rpython equivalent of json2qt.py's main() -- but instead of owning the process,
it is a leaf the C kernel drives, so py2c emits an ordinary `mbos_render_main`
that kmain() calls after fetch/boot. Everything below `parse_html` /
`render_page` is the same dom.py Node model the Wayland minibrowser uses.
"""
import sys
if sys.implementation.name == 'shivyc':
    import rpy_ctypes as ctypes
else:
    import ctypes

from htmlparse import parse_html
from render import render_page

_g = ctypes.CDLL("mbos_glue")
_g.mb_page.restype = ctypes.c_char_p
_g.mb_page.argtypes = []


def mbos_render_main() -> int:
    html: "char*" = _g.mb_page()
    doc = parse_html(html)
    render_page(doc)
    return 0

#!/usr/bin/env python3
"""www2json -- the CPython half of the minibrowser pipeline.

Runs under *ordinary* CPython (not rpython): it may use the full standard
library. It turns an HTML file (or, with --url, a fetched page) into:

  1. `page.json` -- the canonical DOM bundle, same shape as OpenSourceJesus's
     Tetra `www2json.py`: {"source", "title", "dom", "scripts"} where each DOM
     node is {"type", "attributes", "text", "children"}.

  2. `page_data.py` -- the *rpython* "py" form of that same page: a
     `build_page()` that constructs the `dom.Node` tree with concrete locals and
     direct field writes, ready to be co-compiled with `json2qt.py` + `dom.py`
     by py2c. This is the "json/py" the renderer consumes.

Page scripts (JS) are captured verbatim into the bundle for now. Translating
them to python (via OpenSourceJesus's Js2Py fork) and running them through
embedded minipy is a later step -- see the README.

Usage:
    python3 www2json.py INPUT.html [--out DIR]
    python3 www2json.py --url https://example.com [--out DIR]
"""
import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
# Tags we drop entirely (with their subtrees) -- non-visual or handled apart.
DROP = {"script", "style", "noscript", "template", "head", "meta", "link",
        "title"}
# Attributes the rpython renderer actually consumes -> dom.Node fields.
KEEP_ATTRS = {"href", "name", "value", "type", "onclick", "placeholder", "src",
              "id", "class", "action", "method"}
# Element kinds whose immediate text we fold into the node's `text` field.
TEXTY = {"p", "span", "a", "button", "li", "h1", "h2", "h3", "h4", "h5", "h6",
         "td", "th", "cite", "dt", "dd", "figcaption", "blockquote", "label"}


class DomBuilder(HTMLParser):
    """Build a {type, attributes, text, children} tree from HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"type": "#root", "attributes": {}, "text": "",
                     "children": []}
        self.stack = [self.root]
        self.title = ""
        self.scripts = []          # (type, id, code) for every <script>
        self._in_title = False
        self._in_script = False
        self._script_type = ""
        self._script_id = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            return
        if tag == "script":
            self._in_script = True
            self._script_type = ""
            self._script_id = ""
            for (k, v) in attrs:
                if k == "type":
                    self._script_type = (v or "").lower()
                elif k == "id":
                    self._script_id = v or ""
            return
        attributes = {k: (v if v is not None else "")
                      for (k, v) in attrs if k in KEEP_ATTRS}
        node = {"type": tag, "attributes": attributes, "text": "",
                "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        # <img/> style: a void element, never pushed.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if tag == "script":
            self._in_script = False
            return
        if tag in VOID:
            return
        # pop back to the matching open tag if present
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["type"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._in_script:
            self.scripts.append((self._script_type, self._script_id, data))
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        parent = self.stack[-1]
        if parent["type"] in TEXTY:
            parent["text"] = (parent["text"] + " " + text).strip()
        else:
            parent["children"].append(
                {"type": "#text", "attributes": {}, "text": text,
                 "children": []})


def prune(node):
    """Drop non-visual subtrees; keep the rest."""
    kids = []
    for ch in node.get("children", []):
        if ch.get("type") in DROP:
            continue
        prune(ch)
        kids.append(ch)
    node["children"] = kids
    return node


def find_body(root):
    for ch in root.get("children", []):
        if ch.get("type") == "html":
            for gch in ch.get("children", []):
                if gch.get("type") == "body":
                    return gch
        if ch.get("type") == "body":
            return ch
    # no explicit <body>: wrap everything visible under a synthetic body
    return {"type": "body", "attributes": {}, "text": "",
            "children": root.get("children", [])}


def _resolve_img_src(base_is_url, base, src):
    """Resolve an <img src> against the page's location so relative paths and
    site-relative URLs both work."""
    if (src.startswith("http://") or src.startswith("https://")
            or src.startswith("data:")):
        return src
    if base_is_url:
        from urllib.parse import urljoin
        return urljoin(base, src)
    return os.path.join(base, src)


def _convert_images(node, base_is_url, base, max_w=512, max_h=512):
    """Walk the DOM tree and rewrite each <img src> to a blit-ready cache file
    (produced by PIL, ahead of time). The pure-rpython browser then only carries
    the cache path -- no libpng/libjpeg, no pixel data in its bytecode. A source
    that can't be fetched/decoded is left untouched, so the browser falls back to
    an "[img]" placeholder rather than failing the whole page."""
    if node.get("type") == "img":
        attrs = node.setdefault("attributes", {})
        src = attrs.get("src", "")
        if src and not src.startswith("data:"):
            try:
                import mb_imgcache
                resolved = _resolve_img_src(base_is_url, base, src)
                attrs["src"] = mb_imgcache.convert_cached(resolved, max_w, max_h)
            except Exception as e:  # noqa: BLE001 -- best-effort per image
                sys.stderr.write("img: could not prepare %s (%s)\n" % (src, e))
    for ch in node.get("children", []):
        _convert_images(ch, base_is_url, base, max_w, max_h)


def build_bundle(source, html_text):
    parser = DomBuilder()
    parser.feed(html_text)
    prune(parser.root)
    body = find_body(parser.root)
    # Rewrite <img src> to blit-ready cache files (relative to the page).
    _base_is_url = (source.startswith("http://")
                    or source.startswith("https://"))
    _base = source if _base_is_url else os.path.dirname(
        os.path.abspath(source))
    _convert_images(body, _base_is_url, _base)
    title = parser.title.strip() or source
    py_types = ("python", "text/python", "application/python")
    ts_types = ("typescript", "text/typescript", "application/typescript")
    py_code = "\n".join(
        code.strip() for (stype, sid, code) in parser.scripts
        if stype in py_types and code.strip())
    other_code = "\n".join(
        code.strip() for (stype, sid, code) in parser.scripts
        if stype not in ("rpython", "text/rpython") + py_types + ts_types
        and code.strip())
    # <script type="rpython" id="NAME"> blocks: native code for the page, keyed
    # by id. The browser JIT-compiles each (py2c -> gcc -O2 -shared) to a cached
    # .so the page's python loads via ctypes -- a faster, CPython-compatible
    # alternative to a wasm VM (see jitc.py).
    rpython = {}
    for (stype, sid, code) in parser.scripts:
        if stype in ("rpython", "text/rpython") and code.strip():
            rpython[sid or ("rpy%d" % len(rpython))] = code.strip()
        elif stype in ts_types and code.strip():
            # TypeScript -> typed rpython -> native .so, via the same JIT path.
            # Everybody compiles TypeScript to JavaScript; we compile it to
            # native machine code. Best-effort: an untranslatable block is
            # skipped rather than breaking the page.
            try:
                import ts2py as _ts2py
                rpython[sid or ("ts%d" % len(rpython))] = \
                    _ts2py.translate(code).strip()
            except Exception as e:                   # noqa: BLE001
                sys.stderr.write("ts2py: skipping <script type=typescript> "
                                 "(%s)\n" % e)
    # Translate plain <script> JavaScript to minipy python (js2py, via the
    # pyjsparser AST) so it runs on the *same* engine + DOM as a python script.
    # Best-effort: if pyjsparser is missing or the JS uses an unsupported
    # construct, the JS is left unrun (still captured in "scripts") rather than
    # breaking the page.
    js_python = ""
    if other_code.strip():
        try:
            import js2py as _js2py
            js_python = _js2py.translate(other_code)
        except Exception as e:                       # noqa: BLE001
            sys.stderr.write("js2py: skipping <script> (%s)\n" % e)
            js_python = ""
    combined_py = py_code
    if js_python.strip():
        combined_py = (py_code + "\n\n" + js_python).strip() \
            if py_code.strip() else js_python.strip()
    return {
        "source": source,
        "title": title,
        "dom": body,
        "python": combined_py,      # <script type="python"> + translated JS
        "rpython": rpython,         # <script type="rpython" id=..> -> native .so
        "scripts": other_code,      # original JavaScript, captured verbatim
    }


# ------------------------------------------------------------------ codegen
def _lit(s):
    """A safe rpython/C string literal: ASCII-only, JSON-escaped."""
    s = "".join(c if 32 <= ord(c) < 127 else " " for c in (s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return json.dumps(s)


ATTR_FIELD = {"href": "href", "name": "name", "value": "value",
              "type": "itype", "onclick": "onclick",
              "placeholder": "placeholder", "src": "src", "id": "eid"}


def emit_page_data(bundle):
    """Generate the rpython `page_data.py` that rebuilds `bundle['dom']`."""
    lines = [
        '"""Generated by www2json.py from %s -- do not edit by hand.' % (
            bundle["source"],),
        "",
        "The rpython \"py\" form of the page: build_page() constructs the",
        "dom.Node tree the renderer walks. Co-compile with json2qt.py + dom.py.",
        '"""',
        "from dom import Node",
        "",
        "",
        'def build_page() -> "obj":',
    ]
    counter = [0]
    body = []

    def emit(node):
        idx = counter[0]
        counter[0] += 1
        var = "n%d" % idx
        tag = node.get("type", "div")
        body.append('    %s = Node(%s)' % (var, _lit(tag)))
        text = node.get("text", "")
        if text:
            body.append('    %s.text = %s' % (var, _lit(text)))
        attrs = node.get("attributes", {})
        for a, field in ATTR_FIELD.items():
            if a in attrs and attrs[a]:
                body.append('    %s.%s = %s' % (var, field, _lit(attrs[a])))
        for ch in node.get("children", []):
            cvar = emit(ch)
            body.append('    %s.add(%s)' % (var, cvar))
        return var

    root_var = emit(bundle["dom"])
    body.append('    return %s' % root_var)
    lines.extend(body)
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="HTML file")
    ap.add_argument("--url", help="fetch this URL instead of reading a file")
    ap.add_argument("--out", default=".", help="output directory")
    args = ap.parse_args()

    if args.url:
        import urllib.request
        source = args.url
        req = urllib.request.Request(source, headers={"User-Agent": "minibrowser"})
        html_text = urllib.request.urlopen(req, timeout=30).read().decode(
            "utf-8", "replace")
    elif args.input:
        source = args.input
        with open(args.input, encoding="utf-8") as fh:
            html_text = fh.read()
    else:
        ap.error("give an HTML file or --url")

    os.makedirs(args.out, exist_ok=True)
    bundle = build_bundle(source, html_text)

    json_path = os.path.join(args.out, "page.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2)

    py_path = os.path.join(args.out, "page_data.py")
    with open(py_path, "w", encoding="utf-8") as fh:
        fh.write(emit_page_data(bundle))

    print("wrote %s" % json_path)
    print("wrote %s" % py_path)


if __name__ == "__main__":
    main()

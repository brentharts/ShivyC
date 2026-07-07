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
        self.scripts = []
        self._in_title = False
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            return
        if tag == "script":
            self._in_script = True
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
            self.scripts.append(data)
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


def build_bundle(source, html_text):
    parser = DomBuilder()
    parser.feed(html_text)
    prune(parser.root)
    body = find_body(parser.root)
    title = parser.title.strip() or source
    return {
        "source": source,
        "title": title,
        "dom": body,
        "scripts": "\n".join(s.strip() for s in parser.scripts if s.strip()),
    }


# ------------------------------------------------------------------ codegen
def _lit(s):
    """A safe rpython/C string literal: ASCII-only, JSON-escaped."""
    s = "".join(c if 32 <= ord(c) < 127 else " " for c in (s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return json.dumps(s)


ATTR_FIELD = {"href": "href", "name": "name", "value": "value",
              "type": "itype", "onclick": "onclick",
              "placeholder": "placeholder", "src": "src"}


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

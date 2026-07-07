#!/usr/bin/env python3
"""pycompile -- turn a page's python into minipy bytecode the browser can run.

Runs under CPython (like www2json): the browser shells out to it at page load.
It reads the `page.json` bundle, assembles one minipy program

    minidom prelude  +  document._register(...) for each id'd element
                     +  the page's <script type="python"> body

and compiles it to a `.mpyc` file. The native browser then `mpy_boot`s that
bytecode (defining the page's functions + the document/console/window globals)
and calls a handler by name whenever a button's onclick fires.

    python3 pycompile.py page.json minidom.py out.mpyc

Exits 0 on success (even with no page python -- the prelude alone still boots so
the DOM globals exist); non-zero only on a real compile error.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
# Find the minipy package: staged next to us (build dir) or in the repo tools/.
sys.path.insert(0, HERE)
_tools = os.path.join(ROOT, "tools")
if os.path.isdir(os.path.join(_tools, "minipy")):
    sys.path.insert(0, _tools)


def _lit(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("?")
    out.append('"')
    return "".join(out)


def _walk(node, out):
    attrs = node.get("attributes", {})
    eid = attrs.get("id", "")
    if eid:
        out.append((eid, node.get("type", ""), node.get("text", "")))
    for ch in node.get("children", []):
        _walk(ch, out)
    return out


def assemble(bundle, minidom_path):
    prelude = open(minidom_path).read()
    lines = ["", "# ---- DOM populated from the parsed page ----"]
    for (eid, tag, text) in _walk(bundle.get("dom", {}), []):
        lines.append("document._register(Element(%s, %s, %s))"
                     % (_lit(eid), _lit(tag), _lit(text)))
    parts = [prelude, "\n".join(lines)]
    py = bundle.get("python", "")
    if py.strip():
        parts += ["", "# ---- page <script type=\"python\"> ----", py]
    return "\n".join(parts)


def main(argv):
    if len(argv) < 4:
        sys.stderr.write("usage: pycompile.py page.json minidom.py out.mpyc\n")
        return 2
    page_json, minidom_path, out_mpyc = argv[1], argv[2], argv[3]
    from minipy import compiler as C, mpyc

    with open(page_json) as fh:
        bundle = json.load(fh)
    program = assemble(bundle, minidom_path)
    prog = C.compile_source(program)
    with open(out_mpyc, "wb") as fh:
        fh.write(mpyc.encode(prog))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

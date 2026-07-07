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




def _handler_name(onclick):
    """Map an inline handler like "foo()" to the function name "foo", or "" if
    it is not a simple name()-call we can bind to a script function."""
    s = onclick.strip()
    if not s.endswith(")"):
        return ""
    head = s[:s.find("(")].strip()
    if head and (head[0].isalpha() or head[0] == "_") and \
            all(c.isalnum() or c == "_" for c in head):
        return head
    return ""


def _emit_node(node, parent_var, counter, lines):
    tag = node.get("type", "")
    if tag.startswith("#"):            # #text and friends: fold as text, no node
        return
    counter[0] += 1
    var = "__e%d" % counter[0]
    lines.append("%s = document.createElement(%s)" % (var, _lit(tag)))
    text = node.get("text", "")
    if text:
        lines.append("%s.textContent = %s" % (var, _lit(text)))
    attrs = node.get("attributes", {})
    if attrs.get("id", ""):
        lines.append("%s.setAttribute(\"id\", %s)" % (var, _lit(attrs["id"])))
    if attrs.get("value", ""):
        lines.append("%s.value = %s" % (var, _lit(attrs["value"])))
    name = _handler_name(attrs.get("onclick", ""))
    if name:
        lines.append("%s.onclick = %s" % (var, name))
    lines.append("%s.appendChild(%s)" % (parent_var, var))
    for ch in node.get("children", []):
        _emit_node(ch, var, counter, lines)


def assemble(bundle, minidom_path):
    """One minipy program: minidom prelude + page script + a live body tree.

    The body is built with createElement/appendChild so it is the same mutable
    structure the script edits (not a separate read-only registration), and it
    comes *after* the script so onclick="foo()" can bind to the function foo.
    """
    prelude = open(minidom_path).read()
    parts = [prelude]
    py = bundle.get("python", "")
    if py.strip():
        parts += ["", "# ---- page <script type=\"python\"> ----", py]
    lines = ["", "# ---- live body built from the parsed page ----"]
    counter = [0]
    for ch in bundle.get("dom", {}).get("children", []):
        _emit_node(ch, "document.body", counter, lines)
    parts.append("\n".join(lines))
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

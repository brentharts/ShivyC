#!/usr/bin/env python3
"""End-to-end check: a `<script type="python">` block runs on minipy with a DOM.

This is the first step of the scripting engine (before Js2Py). It proves the
whole offline+runtime shape without yet embedding the interpreter in the native
browser:

  1. www2json parses an HTML page, capturing the python script, element `id`s,
     and inline `onclick` handlers.
  2. we assemble one minipy program = minidom prelude (document/console/window)
     + generated `document._register(Element(...))` calls for every id'd element
     + the page's python script + a dispatch line that fires the button's
     `onclick` (simulating a click).
  3. that program is compiled and run on *native minipy* (via tools/rpy.py),
     and we assert the console/alert output matches what the script should do.

Run:  python3 pyscript_test.py            # asserts (builds native minipy once)
      python3 pyscript_test.py --show     # also prints the assembled program
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RPY = os.path.join(ROOT, "tools", "rpy.py")


def _lit(s):
    """A minipy/python string literal for s (ASCII-escaped, double-quoted)."""
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
    """Collect (id, tag, text) for every element carrying an id attribute."""
    attrs = node.get("attributes", {})
    eid = attrs.get("id", "")
    if eid:
        out.append((eid, node.get("type", ""), node.get("text", "")))
    for ch in node.get("children", []):
        _walk(ch, out)
    return out


def _first_onclick(node):
    """Return the first inline onclick handler body found in the tree, or ''."""
    oc = node.get("attributes", {}).get("onclick", "")
    if oc:
        return oc
    for ch in node.get("children", []):
        got = _first_onclick(ch)
        if got:
            return got
    return ""


def assemble(bundle):
    """Build the single minipy program: prelude + DOM + script + dispatch."""
    prelude = open(os.path.join(HERE, "minidom.py")).read()

    lines = ["", "# ---- DOM populated from the parsed page ----"]
    for (eid, tag, text) in _walk(bundle["dom"], []):
        lines.append("document._register(Element(%s, %s, %s))"
                     % (_lit(eid), _lit(tag), _lit(text)))

    parts = [prelude, "\n".join(lines),
             "", "# ---- page <script type=\"python\"> ----", bundle["python"]]

    onclick = _first_onclick(bundle["dom"])
    if onclick:
        parts += ["", "# ---- simulated click: fire the button's onclick ----",
                  onclick]
    return "\n".join(parts)


def run_on_minipy(program):
    """Run a minipy program source through native minipy; return its stdout."""
    src = os.path.join("/tmp", "pyscript_run.py")
    open(src, "w").write(program)
    p = subprocess.run([sys.executable, RPY, src],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode("utf-8", "replace")


def main(argv):
    # 1. parse the page with www2json (import it in-process)
    sys.path.insert(0, HERE)
    import www2json
    with open(os.path.join(HERE, "pyscript.html")) as fh:
        bundle = www2json.build_bundle("pyscript.html", fh.read())

    assert bundle["python"], "no <script type='python'> captured"
    print("captured python script (%d chars) + %d dom nodes"
          % (len(bundle["python"]), _count(bundle["dom"])))

    # 2. assemble the runnable minipy program
    program = assemble(bundle)
    if "--show" in argv:
        print("=" * 60)
        print(program)
        print("=" * 60)

    # 3. run on native minipy
    print("building native minipy + running (first build is slow)...")
    out = run_on_minipy(program)
    print("---- minipy output ----")
    sys.stdout.write(out)
    print("-----------------------")

    # 4. assert the script did what it should
    assert "hello minipy console" in out, "console.log(string) missing"
    assert "HTMLDocument" in out, "console.log(document) missing"
    assert '<button id="A">clickme</button>' in out, \
        "getElementById did not resolve the button"
    assert "[alert] hello minipy" in out, "window.alert missing"
    print("pyscript_test: PASS")
    return 0


def _count(node):
    n = 1
    for ch in node.get("children", []):
        n += _count(ch)
    return n


if __name__ == "__main__":
    sys.exit(main(sys.argv))

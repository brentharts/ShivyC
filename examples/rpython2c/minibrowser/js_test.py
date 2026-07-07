#!/usr/bin/env python3
"""Check that JavaScript rides the same engine: js2py translates a page's
<script> to minipy python, and it runs on native minipy against the minidom.

  1. unit-translate a few JS snippets and sanity-check the emitted python;
  2. run jsdemo.html end to end: www2json translates its <script> into the
     bundle's python, pycompile assembles it, and native minipy fires the
     button's greet() -- which must log to the console and set the OUT input.

Needs pyjsparser (pip install pyjsparser), the parser Js2Py is built on.

Run:  python3 js_test.py            # asserts (builds native minipy once)
      python3 js_test.py --show     # also prints the assembled program
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RPY = os.path.join(ROOT, "tools", "rpy.py")

# Fire the button's onclick (greet, from JS) and read the OUT input back.
DRIVER = '''
_i = 0
while _i < len(document.body.children):
    _e = document.body.children[_i]
    _cb = _e.onclick
    if _cb != None:
        document._fire(_e._handle)
    _i = _i + 1
_b = document.getElementById("OUT")
print("OUT:" + _b.value)
'''


def run_on_minipy(program):
    src = os.path.join("/tmp", "js_run.py")
    open(src, "w").write(program)
    p = subprocess.run([sys.executable, RPY, src],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode("utf-8", "replace")


def unit_translate():
    import js2py
    py = js2py.translate("function f(a){ if (a === 1) { return 2; } "
                         "return a + 1; }")
    assert "def f(a):" in py, py
    assert "== 1" in py and "a + 1" in py, py
    # && / || / ! and member assignment
    py2 = js2py.translate("var x = document.getElementById('q'); "
                          "x.value = (1 < 2 && 3 > 2) ? 'y' : 'n';")
    assert "document.getElementById(\"q\")" in py2, py2
    assert " and " in py2 and " if " in py2, py2
    print("js2py unit translation OK")


def main(argv):
    sys.path.insert(0, HERE)
    try:
        import js2py  # noqa: F401
        import js2py as _j
        if _j._js_parse is None:
            print("js_test: SKIP (pyjsparser not installed)")
            return 0
    except ImportError:
        print("js_test: SKIP (pyjsparser not installed)")
        return 0

    unit_translate()

    import www2json
    import pycompile
    with open(os.path.join(HERE, "jsdemo.html")) as fh:
        bundle = www2json.build_bundle("jsdemo.html", fh.read())
    assert "def greet" in bundle["python"], \
        "JS was not translated into the python field"
    assert bundle["scripts"].strip(), "original JS should still be captured"

    program = pycompile.assemble(bundle, os.path.join(HERE, "minidom.py")) \
        + DRIVER
    if "--show" in argv:
        print("=" * 60)
        print(program)
        print("=" * 60)

    print("building native minipy + running (first build is slow)...")
    out = run_on_minipy(program)
    print("---- minipy output ----")
    sys.stdout.write(out)
    print("-----------------------")
    assert "hello from javascript" in out, "JS console.log did not run"
    assert "OUT:set by JS" in out, "JS did not mutate the DOM"
    print("js_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

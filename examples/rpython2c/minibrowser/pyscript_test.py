#!/usr/bin/env python3
"""Source-level check of live DOM mutation: a `<script type="python">` runs on
minipy and actually mutates the DOM.

It uses the real build helper (`pycompile.assemble`) to turn `pyscript2.html`
into one minipy program -- minidom prelude + the page script + a live body tree
built with createElement/appendChild -- then runs it on *native minipy* (via
tools/rpy.py) with a small driver that fires the buttons' onclick handlers by
handle (as the browser does on a click) and prints the DOM back. We assert that
createElement/appendChild added the elements and that value= took effect
(hello -> world). This is the same path the native browser drives at runtime;
here it is checked without a compositor.

Run:  python3 pyscript_test.py            # asserts (builds native minipy once)
      python3 pyscript_test.py --show     # also prints the assembled program
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RPY = os.path.join(ROOT, "tools", "rpy.py")

# A driver appended to the assembled page program: fire the initial button's
# onclick (foo), then the button foo created (bar), reading the DOM in between.
DRIVER = '''
print("INIT:" + __serialize())
c0 = document.body.children[0]
document._fire(c0._handle)
b = document.getElementById("INPUT")
print("AFTER_FOO_INPUT:" + b.value)
nb = document.body.children[2]
document._fire(nb._handle)
b2 = document.getElementById("INPUT")
print("AFTER_BAR_INPUT:" + b2.value)
print("CONSOLE:" + __console())
'''


def run_on_minipy(program):
    src = os.path.join("/tmp", "pyscript2_run.py")
    open(src, "w").write(program)
    p = subprocess.run([sys.executable, RPY, src],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode("utf-8", "replace")


def main(argv):
    sys.path.insert(0, HERE)
    import www2json
    import pycompile
    with open(os.path.join(HERE, "pyscript2.html")) as fh:
        bundle = www2json.build_bundle("pyscript2.html", fh.read())
    assert bundle["python"], "no <script type='python'> captured"

    program = pycompile.assemble(bundle, os.path.join(HERE, "minidom.py")) + DRIVER
    if "--show" in argv:
        print("=" * 60)
        print(program)
        print("=" * 60)

    print("building native minipy + running (first build is slow)...")
    out = run_on_minipy(program)
    print("---- minipy output ----")
    sys.stdout.write(out)
    print("-----------------------")

    assert "hello minipy console" in out, "console.log(string) missing"
    # foo(): created input starts at 'hello'
    assert "AFTER_FOO_INPUT:hello" in out, "createElement/appendChild/value failed"
    # bar() (fired via the created button's onclick callable): 'hello' -> 'world'
    assert "AFTER_BAR_INPUT:world" in out, "live value mutation failed"
    print("pyscript_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

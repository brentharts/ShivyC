#!/usr/bin/env python3
"""gen_embed -- emit `interp_embed.py`: the minipy interpreter, minus its main().

The browser co-compiles the minipy interpreter (tools/minipy/interp.py) into its
own binary to run page scripts. py2c turns any function literally named `main`
into the C entry point, and the browser already has one (json2qt.main), so the
interpreter's `main` (and its `__main__` guard) are stripped here. Everything
else -- including the `mpy_boot` / `mpy_call` embedding facades -- is kept.

This is a pure mechanical transform (a build artifact), so `interp_embed.py` is
generated rather than committed; the Makefile and render_test both call this
before building/importing.

    python3 gen_embed.py OUT_DIR      # writes OUT_DIR/interp_embed.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
INTERP = os.path.join(ROOT, "tools", "minipy", "interp.py")


def generate(out_dir):
    tree = ast.parse(open(INTERP).read())
    body = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "main":
            continue
        if isinstance(n, ast.If):
            t = n.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "__name__"):
                continue
        body.append(n)
    tree.body = body
    out = os.path.join(out_dir, "interp_embed.py")
    with open(out, "w") as fh:
        fh.write(ast.unparse(tree))
    return out


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else HERE
    path = generate(out_dir)
    print("wrote %s" % path)

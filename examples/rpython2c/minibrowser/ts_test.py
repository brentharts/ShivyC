#!/usr/bin/env python3
"""Check the TypeScript -> rpython translator and its www2json integration.

Fast (no native build): unit-translate a few TypeScript functions and assert the
emitted typed rpython, then confirm www2json routes a <script type="typescript">
block into the page's rpython (native-JIT) map rather than the JavaScript path.
The end-to-end native compile is exercised by the browser's --ts-selftest.

    python3 ts_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def unit():
    import ts2py
    py = ts2py.translate(
        "function add(a: number, b: number): number { return a + b; }")
    assert "def add(a: int, b: int) -> int:" in py, py
    assert "return (a + b)" in py, py
    # typed local + C-style for + if/elif/else
    py2 = ts2py.translate(
        "function f(n: number): number {"
        "  let s: number = 0;"
        "  for (let i: number = 0; i < n; i++) { s = s + i; }"
        "  if (s > 10) { return 1; } else if (s === 0) { return 2; }"
        "  else { return 0; } }")
    assert "def f(n: int) -> int:" in py2, py2
    assert "while (i < n):" in py2 and "i = i + 1" in py2, py2
    assert "elif (s == 0):" in py2, py2
    # boolean / string / void types and && || ! mapping
    py3 = ts2py.translate(
        "function g(ok: boolean, name: string): void {"
        "  let r: boolean = ok && !false; }")
    assert "def g(ok: bool, name: str):" in py3, py3
    assert " and " in py3 and "not " in py3, py3
    # float type + arrow functions (expression body and block body)
    py4 = ts2py.translate("const scale = (x: float, k: float): float => x * k;")
    assert "def scale(x: float, k: float) -> float:" in py4, py4
    assert "return (x * k)" in py4, py4
    py5 = ts2py.translate(
        "const inc = (n: number): number => { return n + 1; };")
    assert "def inc(n: int) -> int:" in py5 and "return (n + 1)" in py5, py5
    # classes: constructor -> __init__, this -> self, methods, new
    py6 = ts2py.translate(
        "class P { x: number; constructor(x: number) { this.x = x; } "
        "get(): number { return this.x; } } "
        "function mk(v: number): number { let p: P = new P(v); "
        "return p.get(); }")
    assert "class P:" in py6 and "def __init__(self, x: int):" in py6, py6
    assert "self.x = x" in py6 and "def get(self) -> int:" in py6, py6
    assert "P(v)" in py6 and "p.get()" in py6, py6
    # string return type and dom_get usage
    py7 = ts2py.translate('function label(): string { return "hi"; }')
    assert "def label() -> str:" in py7, py7
    print("ts2py unit translation OK")


def integration():
    import www2json
    with open(os.path.join(HERE, "ts.html")) as fh:
        b = www2json.build_bundle("ts.html", fh.read())
    assert "tsmod" in b["rpython"], "TS block not routed into the rpython map"
    assert "def fib(n: int) -> int:" in b["rpython"]["tsmod"], \
        b["rpython"]["tsmod"]
    assert "function fib" not in b["scripts"], "TS leaked into the JS bucket"
    print("www2json TS integration OK")


def main(argv):
    sys.path.insert(0, HERE)
    unit()
    integration()
    print("ts_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

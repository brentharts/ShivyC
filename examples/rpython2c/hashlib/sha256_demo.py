"""SHA-256 over the rpython `rpy_hashlib` library.

The whole digest is pure 32-bit integer arithmetic, so it lowers to tight C and
the transpiled program prints exactly what `hashlib.sha256(...).hexdigest()`
prints under CPython -- a byte-for-byte differential check.
"""
import rpy_hashlib


def show(label: "str", s: "str") -> None:
    print(label + ": " + rpy_hashlib.sha256_str(s))


def main() -> "int":
    show("empty", "")
    show("abc", "abc")
    show("fox", "The quick brown fox jumps over the lazy dog")
    show("len55", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    show("len56", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    show("len64", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    show("shivyc", "ShivyCX rpython to C")
    return 0

"""Multi-file rpython program exercising cross-module field writes:

    python3 -m shivyc.main app.py lib.py -o app

`a.next = b` writes an object into lib.Cell's None-initialised `obj` field from
another module (boxed as OBJ_OBJ); `a.v = ...` writes its plain int field. The
value is then read back through the obj field as a typed `Cell*` pointer and
used for a direct field read and method call. Deterministic exit: 55."""
import lib


def main() -> int:
    a = lib.Cell(10)
    b = lib.Cell(20)

    a.next = b                          # cross-module obj-field write
    a.v = a.v + 5                       # cross-module int-field write -> 15

    nxt: "lib.Cell" = a.next            # read obj field back as a typed Cell*
    return a.v + nxt.v + nxt.total()    # 15 + 20 + 20 = 55

"""Typed lists in rpython -> unboxed C arrays (no runtime, no boxing).

A `list[int]` / `list[float]` annotation lowers to a growable struct
`{ T* data; long len; long cap; }` backed by malloc/realloc -- exactly the
vector you'd write by hand in C, generated from Python. Operations map to plain
C: indexing is `data[i]`, `len(xs)` is `xs->len`, `xs.append(v)` grows the
buffer, and `for x in xs` walks the array. No tagged `obj`, no GC.

    python3 -m shivyc.main --no-cache typed_list.py -o /tmp/tl && /tmp/tl
"""


def sum_squares(n: "int") -> int:
    xs: "list[int]" = []          # empty typed list
    i = 0
    while i < n:
        xs.append(i * i)          # _tlist_int_push, realloc-grows
        i = i + 1
    total = 0
    for x in xs:                  # walks xs->data[0..len)
        total = total + x         # native int add, no obj_add
    return total


def main() -> int:
    vals: "list[int]" = [3, 1, 4, 1, 5, 9, 2, 6]   # literal -> array of 8
    vals[0] = 10                  # subscript write: vals->data[0] = 10
    best = vals[0]
    for v in vals:
        if v > best:
            best = v              # best = 10

    # 0+1+4+9+16+25+36+49 = 140
    return best + len(vals) + (sum_squares(8) % 100)   # 10 + 8 + 40 = 58

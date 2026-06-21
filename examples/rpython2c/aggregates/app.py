"""aggregates - varargs-free construction of lists, dicts, and calls.

The runtime builds a list/tuple/set/dict literal, packs a dynamic call's
arguments, and forms the (index, value) pairs of enumerate/zip by collecting
`obj` values together. Passing a 16-byte `obj` by value through a C `...`
parameter mis-lowers on some backends -- only the first argument survives -- so
every one of these now stores its values into a stack array and hands the helper
a pointer (`list_from`, `call_obj_a`, `dict_of_a`, `list_pair`) instead.

This program exercises each path with two-or-more elements, where the varargs
form used to return garbage. CPython, gcc, and ShivyCX-self-compiled all agree.
"""


def add3(a, b, c) -> int:
    return a + b + c


def main() -> int:
    total = 0

    xs = [3, 5, 7]                       # multi-element list literal
    for v in xs:
        total += v                       # 15

    grid = [[1, 2], [3, 4]]              # nested list literals
    for row in grid:
        for x in row:
            total += x                   # +10 -> 25

    d = {"a": 1, "b": 2, "c": 3}         # dict literal (3 entries)
    for k in d:
        total += d[k]                    # +6 -> 31

    fns = [add3]                         # dynamic call with 3 args
    for f in fns:
        total += f(2, 4, 6)              # +12 -> 43

    for i, v in enumerate([10, 20]):     # enumerate pairs
        total += i + v                   # (0+10)+(1+20) = 31 -> 74

    for a, b in zip([1, 2], [3, 4]):     # zip pairs
        total += a + b                   # (1+3)+(2+4) = 10 -> 84

    return total                         # 84


if __name__ == "__main__":
    import sys
    sys.exit(main())

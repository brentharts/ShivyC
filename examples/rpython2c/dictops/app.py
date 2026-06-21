"""dictops - a broad tour of dict operations.

Dicts are a first-class runtime type (T_DICT): an insertion-ordered array of
key/value entries with linear lookup by `obj_eq`. Keys and values are boxed
`obj`s, so they can be mixed scalar types (ints, strings, bools). Supported:
literals and `{k: v for ...}` comprehensions, `d[k]` read/write, `d[k] += ...`,
`get`/`setdefault`/`pop`/`update`/`clear`/`copy`, `del d[k]`, `in`, `len`,
`keys`/`values`/`items` (and direct iteration over keys), `|` merge, and
order-independent `==`.
"""


def main() -> int:
    d = {"a": 1, "b": 2, "c": 3}
    total = 0
    total += d["a"] + d["b"] + d["c"]        # 6
    total += d.get("z", 100)                 # missing key default -> +100 (106)

    d["d"] = 4                               # insert
    d.setdefault("a", 99)                    # present -> unchanged
    d.setdefault("e", 5)                     # absent  -> inserts 5
    total += len(d)                          # 5 keys -> +5 (111)
    total += sum(d.values())                 # 1+2+3+4+5 = 15 -> (126)

    acc = 0
    for k, v in d.items():                   # items iteration
        acc += v
    total += acc                             # +15 (141)

    d.update({"a": 10})                      # overwrite a
    total += d["a"]                          # +10 (151)

    m = d | {"f": 6}                         # merge (non-mutating)
    total += len(m)                          # 6 keys -> +6 (157)

    e = d.copy()                             # shallow copy
    e.pop("b")                               # remove b
    del e["c"]                               # remove c
    total += len(e)                          # 5 - 2 = 3 -> +3 (160)

    grid = {0: {1: 7}}                       # nested dicts
    total += grid[0][1]                      # +7 (167)

    if {"x": 1, "y": 2} == {"y": 2, "x": 1}:  # order-independent equality
        total += 3                           # (170)

    sq = {i: i * i for i in range(5)}        # dict comprehension
    total += sq[4]                           # +16 (186)
    return total                             # 186


if __name__ == "__main__":
    import sys
    sys.exit(main())

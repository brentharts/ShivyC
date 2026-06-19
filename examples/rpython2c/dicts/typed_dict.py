"""Typed dicts in rpython -> unboxed parallel arrays (no runtime, no boxing).

A `dict[str, int]` / `dict[int, int]` annotation lowers to a struct
`{ K* keys; V* vals; long len; long cap; }` with linear-probe lookup -- strcmp
for string keys, `==` otherwise. `d[k]`, `d[k] = v`, `k in d`, `len(d)` and
`for k in d` (iterates keys) all map to plain C. A missing key reads as 0, so
counters need no initialization. No tagged `obj`, no hashing runtime, no GC.

    python3 -m shivyc.main --no-cache typed_dict.py -o /tmp/td && /tmp/td
"""


def main() -> int:
    # Count occurrences with an int-keyed dict. A missing key reads as 0,
    # so `freq[w] = freq[w] + 1` works without initializing.
    lengths: "list[int]" = [3, 5, 3, 2, 5, 5]
    freq: "dict[int,int]" = {}
    for w in lengths:
        freq[w] = freq[w] + 1            # {3:2, 5:3, 2:1}

    # Find the most common length by iterating the dict's keys.
    best_len = 0
    best_count = 0
    for k in freq:
        if freq[k] > best_count:
            best_count = freq[k]         # 3
            best_len = k                 # 5

    # A string-keyed dict: literal, write, membership test.
    score: "dict[str,int]" = {"red": 1, "green": 2}
    score["blue"] = 3
    bonus = 0
    if "green" in score:
        bonus = score["green"]           # 2

    # 5*10 + 3 + 2 + 3 = 58
    return best_len * 10 + best_count + bonus + len(score)

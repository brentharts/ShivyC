"""promote - opt-in auto-promotion of cleanly-inferred containers.

With PY2C_PROMOTE_CONTAINERS=1, an unannotated empty list/dict whose element (or
key/value) types infer to a single scalar -- and whose every use is supported by
the unboxed typed form -- is rewritten to the typed `list[int]` / `dict[str,int]`
representation automatically (unboxed parallel arrays, no per-element boxing).
Without the flag the same code compiles to boxed containers. Either way the
result is identical; promotion is purely a representation choice, and it is
conservative: any escape (return, pass as arg, alias, store-in-container),
unsupported method, slice, or negative index leaves the container boxed.

    # boxed (default):
    python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p; echo $?   # 70
    # unboxed (promoted):
    PY2C_PROMOTE_CONTAINERS=1 python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p
"""


def main() -> int:
    squares = []                   # -> list[int]
    for i in range(6):
        squares.append(i * i)
    total = 0
    for s in squares:
        total += s                 # 0+1+4+9+16+25 = 55
    total += squares[1]            # +1  (56)
    total += len(squares)          # +6  (62)

    freq = {}                      # -> dict[str, int]  (guarded counter)
    for ch in "mississippi":
        if ch in freq:
            freq[ch] = freq[ch] + 1
        else:
            freq[ch] = 1
    total += freq["s"]            # 4   (66)
    total += freq["i"]            # 4   (70)
    return total                  # 70


if __name__ == "__main__":
    import sys
    sys.exit(main())

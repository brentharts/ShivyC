"""sets - set as a first-class type.

Sets used to share the list representation, so the set operators collided with
the bitwise ones (`a | b` ran `obj_bin('|', ...)` and produced garbage) and there
was no way to tell a set from a list at runtime. Sets now have their own runtime
tag (T_SET): a set is a `List` whose tag marks it as a set, which reuses all the
structural machinery (len, iteration, membership, clear/remove) while letting the
operators dispatch correctly.

`|` `&` `-` `^` are union / intersection / difference / symmetric-difference on
two sets (and stay bitwise on integers); set literals and `set(iterable)`
de-duplicate; equality is order-independent; `{...}` renders with braces (and the
empty set as `set()`). Membership, `add`, `discard`, `remove`, `clear`,
iteration, and set comprehensions all work.
"""


def main() -> int:
    a = {1, 2, 3, 4}
    b = {3, 4, 5, 6}
    total = 0

    total += len(a | b)                      # union {1..6}        -> 6
    total += len(a & b)                      # intersection {3,4}  -> 2
    total += len(a - b)                      # difference {1,2}    -> 2
    total += len(a ^ b)                      # symdiff {1,2,5,6}   -> 4   (14)

    total += 1 if 3 in a else 0              # 15
    total += 1 if 9 not in a else 0          # 16

    s = {10, 10, 20}                         # dedup -> {10, 20}
    s.add(30)                                # {10, 20, 30}
    s.discard(20)                            # {10, 30}
    s.discard(99)                            # absent -> no-op
    total += len(s)                          # +2                  -> 18

    if {1, 2, 3} == {3, 2, 1}:               # order-independent equality
        total += 5                           # 23

    acc = 0
    for x in {100, 200, 300}:                # iteration
        acc += x
    total += acc // 100                      # +6                  -> 29

    total += len(set([7, 7, 8, 9, 9]))       # set() from list -> {7,8,9} -> +3 (32)
    total += len({i % 3 for i in range(9)})  # comprehension {0,1,2} -> +3 (35)
    return total                             # 35


if __name__ == "__main__":
    import sys
    sys.exit(main())

"""pgo - profile-guided auto-typing (-fprofile-generate).

Static inference can only see literal/arithmetic shapes, so when a container is
filled from a *function call* it gives up and the value stays boxed. The PGO
pass instead runs the script, observes the real runtime types, and rewrites the
source with concrete annotations -- so these containers compile to the unboxed
typed form even though nothing about them is annotated or statically obvious.

    # boxed (default) -- vals/cache stay obj because square()'s type is opaque:
    python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p; echo $?          # 70
    # profile-guided -- vals -> list[int], cache -> dict[int,int], unboxed:
    python3 tools/py2c.py app.py -fprofile-generate --out /tmp/d   # see the notes
    RPY_PROFILE_GENERATE=1 python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p

Either way the result is identical; profiling only changes representation.
"""


def square(n):
    return n * n


def main() -> int:
    vals = []                      # filled from square(i): static obj, PGO list[int]
    for i in range(6):
        vals.append(square(i))
    total = 0
    for v in vals:
        total += v                 # 0+1+4+9+16+25 = 55

    cache = {}                     # values from square(): static obj, PGO dict[int,int]
    for k in range(5):
        cache[k] = square(k)
    total += cache[3]             # +9   (64)
    total += len(cache)           # +5   (69)
    total += vals[1]              # +1   (70)
    return total                  # 70


if __name__ == "__main__":
    import sys
    sys.exit(main())

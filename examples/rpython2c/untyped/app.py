"""untyped - untyped containers with rpython inference advisories.

A Python user can write natural untyped dicts / lists / sets. The transpiler
infers each container's element (or key/value) type from how it is used and
prints an advisory to stderr -- suggesting a `name: "list[int]"`-style annotation
for the unboxed fast path, or flagging an rpython-rule violation such as mixed
element types. The advisories never change the generated code: untyped containers
compile and run as boxed containers either way (run with PY2C_NO_CONTAINER_WARN=1
to silence them).
"""


def word_lengths(words):
    out = {}                       # -> dict[obj, int]
    for w in words:
        out[w] = len(w)
    return out


def main() -> int:
    counts = {}                    # -> dict[obj, int]  (value from .get + int)
    for c in "abracadabra":
        counts[c] = counts.get(c, 0) + 1
    total = counts["a"]            # 'a' occurs 5x

    nums = []                      # -> list[int]  (annotate for fast path)
    for i in range(5):
        nums.append(i * i)
    total += sum(nums)            # 0+1+4+9+16 = 30 -> 35

    seen = set()                   # -> set[str]
    for w in ["x", "y", "x", "z"]:
        seen.add(w)
    total += len(seen)            # {x,y,z} -> +3 (38)

    lens = word_lengths(["aa", "bbb", "c"])
    total += lens["bbb"]          # +3 (41)
    return total                  # 41


if __name__ == "__main__":
    import sys
    sys.exit(main())

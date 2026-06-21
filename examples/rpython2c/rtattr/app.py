"""rtattr - bridge-free runtime getattr/setattr on object-model classes.

`getattr(obj, key, default)` and `setattr(obj, key, val)` with a *runtime* string
key, on a polymorphic object-model receiver (one held as a tagged `obj`, not a
statically-typed struct pointer). These lower to the runtime helpers `rt_getattr`
/ `rt_setattr`, which walk the receiver type's per-type field table
(name -> offset + 1-char storage code) plus its base chain: a declared field is
read/written directly at its offset, an absent one yields the default. No dict,
no hashing, and crucially no micropython core -- the generated program links
against shivyc_rt.c alone.

This is the dynamic-key counterpart to the `dynattr` example. There the receiver
has a known struct type and the lookup switch is inlined at the call site; here
the receiver is a boxed `obj` whose concrete type is only known at runtime, so
the access goes through that object's TypeInfo field table. Inherited fields work
because the table walk follows the base chain (a Leaf instance reaches the fields
declared on Node).
"""


class Node:
    def __init__(self, ival: int):
        self.ival = ival
        self.weight = 2.5          # float field, exercises the 'd' storage code
        self.sname = "node"        # char* field ('s')
        self.flag = False          # bool field  ('b')

    def kind(self) -> int:
        return 1


class Leaf(Node):
    def kind(self) -> int:
        return 2


def bump(it, key: "char*", by: int):
    """Read-modify-write a field chosen by a *runtime* key (rt_getattr +
    rt_setattr); `it` is a boxed obj, so both go through the field table."""
    cur = int(getattr(it, key, 0))
    setattr(it, key, cur + by)


def main() -> int:
    items = [Leaf(10)]                             # a Leaf, held polymorphically
    total = 0
    for it in items:                               # `it` is an obj
        setattr(it, "ival", 42)                    # const key  -> rt_setattr
        bump(it, "ival", 4)                        # runtime key -> 42 -> 46
        setattr(it, "flag", True)                  # bool write

        total += int(getattr(it, "ival", -1))      # 46
        total += int(getattr(it, "flag", 0))       # True -> 1
        total += int(getattr(it, "weight", -1))    # inherited float, int(2.5)=2
        total += int(getattr(it, "missing", -1))   # no such field -> default -1
    return total                                   # 46 + 1 + 2 - 1 = 48


if __name__ == "__main__":
    import sys
    sys.exit(main())

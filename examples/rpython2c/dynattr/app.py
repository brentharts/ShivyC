# Compiled dynamic getattr/setattr on a POD struct.
#
# rpython supports getattr(obj, key) / setattr(obj, key, val) with a *runtime*
# string key on a statically-typed struct -- with no dict, no hash table, and
# no micropython bridge.  The transpiler lowers each access to an inline switch
# on the key's first character (the rpython type-encoding convention: a field's
# initial letter selects its C type), then a strcmp picks the exact field for
# direct, typed member access.  It compiles to a jump table.
#
# Convention (first letter of the field name encodes its type):
#   i.. -> int      d.. -> double/float   s.. -> str (char*)
#   b.. -> bool     <Upper>.. -> object
#
# This is the mechanism a minimal ctypes/FFI layer is built on (a c_int's
# .value, a struct field poked by name), without paying for dynamic dispatch.

class Particle:
    def __init__(self, ix: int, iy: int, dmass: float, sname: "char*"):
        self.ix = ix          # 'i' -> int
        self.iy = iy          # 'i' -> int
        self.dmass = dmass    # 'd' -> double
        self.sname = sname    # 's' -> char*


def bump(p: "Particle", field: "char*", by: int):
    """Read a named int field, add `by`, write it back -- all by runtime key."""
    cur = int(getattr(p, field))
    setattr(p, field, cur + by)


def main() -> int:
    p = Particle(3, 4, 2.5, "proton")

    # read by runtime key
    fx = "ix"
    fy = "iy"
    x = int(getattr(p, fx))           # 3
    y = int(getattr(p, fy))           # 4

    # write by runtime key (round-trips through the same switch)
    bump(p, fx, 10)                   # ix -> 13
    bump(p, fy, 100)                  # iy -> 104
    setattr(p, "dmass", 9.5)          # dmass -> 9.5

    total = int(getattr(p, fx)) + int(getattr(p, fy)) + int(getattr(p, "dmass"))
    # 13 + 104 + 9 = 126
    return total


import sys
sys.exit(main())

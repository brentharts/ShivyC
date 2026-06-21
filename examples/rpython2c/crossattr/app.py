"""crossattr - cross-class field discovery for dynamic attributes.

A class often sets attributes on instances of *another* class that the other
class never declares on `self` -- a configurator stamping options onto a target.
In Python that just works (the attribute springs into existence). For an
ahead-of-time struct layout the attribute needs a slot, or the write has nowhere
to go.

The transpiler discovers these: when `obj.attr = ...` or `setattr(obj, "attr",
...)` is written through a receiver whose class is known -- here from the
parameter annotation `target: "Widget"` -- and `attr` is not already a declared
field, `attr` is promoted to a field on that class (and so is inherited by its
subclasses). The write then lands in a real slot instead of being dropped, and a
later `getattr` finds it.

Without this pass, `setattr(target, "margin", 8)` on a typed receiver whose class
has no `margin` field would not compile at all (there is no slot and no bridge to
fall back on); with it, the field exists and the value round-trips.
"""


class Widget:
    def __init__(self, width: int):
        self.width = width          # the only *declared* field

    def kind(self) -> int:
        return 1


class Button(Widget):               # inherits the discovered fields too
    def kind(self) -> int:
        return 2


def configure(target: "Widget"):
    # None of these are declared on Widget; each is discovered and given a slot.
    setattr(target, "margin", 8)            # via setattr(), runtime-style
    target.padding = 4                      # via attribute assignment
    target.visible = 1


def read(w, key: "char*") -> int:
    return int(getattr(w, key, -1))         # rt_getattr against the field table


def main() -> int:
    b = Button(100)
    configure(b)                            # cross-class writes onto a Button

    total = read(b, "width")                # 100 (declared)
    total += read(b, "margin")              # 8   (discovered)
    total += read(b, "padding")             # 4   (discovered)
    total += read(b, "visible")             # 1   (discovered)
    total += read(b, "missing")             # -1  (no such field -> default)
    return total                            # 100 + 8 + 4 + 1 - 1 = 112


if __name__ == "__main__":
    import sys
    sys.exit(main())

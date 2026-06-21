"""crossattr - cross-class field discovery, with concrete-type inference.

A configurator class often stamps attributes onto instances of *another* class
that the other class never declares on `self`. In Python the attribute simply
springs into existence; for an ahead-of-time C struct it needs a real slot, or
the write has nowhere to go.

The transpiler discovers these. When a field is written through a receiver whose
class is statically known -- `obj.attr = ...` or `setattr(obj, "attr", ...)`,
where the receiver is a `var = Cls()` local or a parameter annotated with a class
-- and `attr` is not already declared, `attr` is promoted to a field on that
class (and inherited by its subclasses).

The slot's *type* is inferred too. A scalar or otherwise dynamic value gives the
generic `obj` word; but when every write assigns a direct constructor result of
one class -- `target.style = Style(3)` -- the field takes that concrete `Style*`
type, so it can be used as a typed pointer (here `b.style.inset()` is a direct
call, not a dynamic dispatch).
"""


class Style:                              # a typed object, stamped cross-class
    def __init__(self, pad: int):
        self.pad = pad

    def inset(self) -> int:
        return self.pad * 2


class Widget:
    def __init__(self, width: int):
        self.width = width                # the only field Widget declares

    def kind(self) -> int:
        return 1


class Button(Widget):                     # inherits every discovered field too
    def kind(self) -> int:
        return 2


def configure(target: "Widget"):          # annotation pins the receiver's class
    setattr(target, "margin", 8)          # scalar  -> obj field
    target.visible = 1                    # scalar  -> obj field
    target.style = Style(3)               # ctor    -> concrete Style* field


def main() -> int:
    b = Button(100)
    configure(b)                          # cross-class writes onto a Button

    total = b.width                       # 100  (declared)
    total += int(getattr(b, "margin", -1))    # 8   (discovered, obj)
    total += int(getattr(b, "visible", 0))    # 1   (discovered, obj)
    total += b.style.inset()              # 6   (discovered, typed Style* -> 3*2)
    total += int(getattr(b, "missing", -1))   # -1  (no such field -> default)
    return total                          # 100 + 8 + 1 + 6 - 1 = 114


if __name__ == "__main__":
    import sys
    sys.exit(main())

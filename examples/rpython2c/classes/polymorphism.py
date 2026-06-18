"""Polymorphism in rpython -- the full object model, compiled by ShivyCX.

Unlike the POD examples (bare structs, no runtime), this uses ShivyCX's tagged
object model: classes with inheritance and *virtual dispatch*. `Shape` is a
base with two subclasses; a `Shape*`-typed variable holds either subclass at
runtime, and `base.area()` dispatches through the per-class vtable.

This is notable because ShivyCX now compiles its own object-model runtime
(`shivyc_rt.c`) -- the tagged `obj` union (16 bytes) is passed and returned in
two registers (SysV), boxed on assignment (`base = OBJ_OBJ(sq)`), and dispatched
via `TYPE(o)->area(o)`. The exit code is the summed areas.

    python3 -m shivyc.main --no-cache polymorphism.py -o /tmp/poly && /tmp/poly
"""


class Shape:
    def __init__(self, tag: "int"):
        self.tag = tag

    def area(self) -> int:
        return 1


class Square(Shape):
    def __init__(self, side: "int"):
        self.tag = 1
        self.side = side

    def area(self) -> int:
        return self.side * self.side


class Circle(Shape):
    def __init__(self, r: "int"):
        self.tag = 2
        self.r = r

    def area(self) -> int:
        return 3 * self.r * self.r


def main() -> int:
    total = 0
    i = 0
    while i < 4:
        base: "Shape*" = Square(i)      # base-typed (obj) variable
        if i % 2 == 0:
            base = Circle(i)            # runtime-varying subtype
        total = total + base.area()     # virtual dispatch
        i = i + 1
    return total % 256                  # 0 + 1 + 12 + 9 = 22

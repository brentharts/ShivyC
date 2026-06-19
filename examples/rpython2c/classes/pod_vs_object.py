"""POD classes vs the object model in rpython.

A plain data class with no inheritance and no runtime polymorphism lowers to a
bare C struct -- `malloc` + direct method calls, no object header, no vtable
("POD", plain old data). A class that takes part in inheritance / virtual
dispatch lowers to the tagged object model: arena allocation + a per-class
vtable, so a base-typed variable can hold any subclass and calls dispatch
dynamically.

    python3 -m shivyc.main --no-cache pod_vs_object.py -o /tmp/pvo && /tmp/pvo
"""


# ---- POD: bare struct, direct calls, no vtable ------------------------------
class Vec2:
    def __init__(self, x: "int", y: "int"):
        self.x = x
        self.y = y

    def dot(self, o: "Vec2*") -> int:        # takes a Vec2* by pointer
        return self.x * o.x + self.y * o.y


# ---- Object model: base + subclasses, virtual dispatch ----------------------
class Shape:
    def area(self) -> int:
        return 0


class Square(Shape):
    def __init__(self, s: "int"):
        self.s = s

    def area(self) -> int:
        return self.s * self.s


class Circle(Shape):
    def __init__(self, r: "int"):
        self.r = r

    def area(self) -> int:
        return 3 * self.r * self.r           # area ~= pi r^2, pi ~= 3


def main() -> int:
    # POD: Vec2 is a bare struct; a.dot(b) is a direct call.
    a = Vec2(3, 4)
    b = Vec2(1, 2)
    d = a.dot(b)                             # 3*1 + 4*2 = 11

    # Object model: a Shape*-typed variable holds either subclass and
    # area() dispatches through the vtable at runtime.
    sq = Square(5)
    ci = Circle(2)
    base: "Shape*" = sq
    t1 = base.area()                         # virtual -> 25
    base = ci
    t2 = base.area()                         # virtual -> 12

    return d + t1 + t2                       # 11 + 25 + 12 = 48

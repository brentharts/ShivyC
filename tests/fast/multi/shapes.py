"""Second helper: a class that *stores* a Point from the other module
(cross-module + cross-class field discovery)."""
from geometry import Point


class Segment:
    def __init__(self, a: "Point", b: "Point"):
        self.a = a
        self.b = b

    def span(self) -> int:
        return self.a.manhattan() + self.b.manhattan()

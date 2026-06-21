"""Entry module: imports from two sibling modules and combines them."""
from geometry import Point, scale
from shapes import Segment


def main() -> int:
    p = Point(3, -4)            # manhattan = 7
    q = Point(1, 2)            # manhattan = 3
    seg = Segment(p, q)        # span = 10
    total = seg.span()         # 10
    total += scale(total, 3)   # + 30 -> 40
    return total               # 40


if __name__ == "__main__":
    import sys
    sys.exit(main())

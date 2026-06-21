"""Helper module: a class and a free function used from another module."""


class Point:
    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y

    def manhattan(self) -> int:
        return abs(self.x) + abs(self.y)


def scale(v: int, factor: int) -> int:
    return v * factor

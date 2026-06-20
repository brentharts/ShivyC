"""A helper module compiled as part of a multi-file translation unit. `next`
starts as None and is only ever None inside this module -- a consumer module
fills it in. Such a None-only field is nullable, so py2c types it `obj` (not a
name-heuristic scalar/string), which is what lets another module store a Cell
into it and read it back."""


class Cell:
    def __init__(self, v: int):
        self.v = v
        self.next = None

    def total(self) -> int:
        return self.v

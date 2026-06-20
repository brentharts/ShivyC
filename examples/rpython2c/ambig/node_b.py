"""A 'b-flavour' Node, same bare name as node_a.Node (ambiguous across the
program). `cached` starts as None and is later assigned a list, exercising the
"None-initialised field must be obj, not a scalar" inference rule."""


class Node:
    def __init__(self, name: str, weight: int):
        self.name = name
        self.weight = weight
        self.cached = None              # later holds a list -> must infer obj

    def bump(self, extra: int) -> int:
        self.weight = self.weight + extra
        self.cached = [self.weight]     # None-init field now holds an object
        return self.weight

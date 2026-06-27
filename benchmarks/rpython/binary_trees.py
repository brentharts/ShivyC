"""Binary-trees allocation stress -- builds and walks many small objects.

Each tree of a given depth allocates 2^(depth+1)-1 Node objects; the harness
builds several and sums their node counts. This pits ShivyCX's arena allocator
against the CPython/PyPy3 object allocators and GC. Depth is read from argv.
"""
import sys


class Node:
    def __init__(self, left, right):
        self.left = left
        self.right = right


def make(depth: int) -> "Node":
    if depth == 0:
        return Node(None, None)
    return Node(make(depth - 1), make(depth - 1))


def check(node: "Node") -> int:
    if node.left is None:
        return 1
    return 1 + check(node.left) + check(node.right)


def main() -> int:
    depth = int(sys.argv[1])
    total = 0
    i = 0
    while i < 24:
        total = total + check(make(depth))
        i = i + 1
    return total % 256


if __name__ == "__main__":
    sys.exit(main())

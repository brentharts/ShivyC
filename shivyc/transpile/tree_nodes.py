"""Transpile-ready AST node base types."""

from __future__ import annotations

from shivyc.transpile.errors_core import Range


class Node:
    """Base AST node."""

    def __init__(self) -> None:
        self.r: Range | None = None


class Root(Node):
    """Root of the translation unit."""

    def __init__(self, nodes: list[Node]) -> None:
        self.r: Range | None = None
        self.nodes: list[Node] = nodes

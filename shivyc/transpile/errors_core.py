"""Transpile-ready subset of shivyc.errors foundational types.

These classes are the first targets for the ShivyCX-to-C transpiler (Phase 1).
They mirror the Python originals but carry strict type annotations and avoid
constructs the transpiler does not yet handle (f-strings, magic methods, etc.).
"""

from __future__ import annotations


class Position:
    """A position in source code."""

    def __init__(self, file: str, line: int, col: int, full_line: str) -> None:
        self.file: str = file
        self.line: int = line
        self.col: int = col
        self.full_line: str = full_line


class Range:
    """A continuous range between two positions."""

    def __init__(self, start: Position, end: Position | None = None) -> None:
        self.start: Position = start
        self.end: Position = end if end else start


class Tagged:
    """Tagged character for lexing."""

    def __init__(self, c: str, p: Position) -> None:
        self.c: str = c
        self.p: Position = p
        self.r: Range = Range(p, p)


class CompilerError:
    """Compile-time error with source location."""

    def __init__(self, descrip: str, range: Range | None = None) -> None:
        self.descrip: str = descrip
        self.range: Range | None = range
        self.warning: bool = False


class ErrorCollector:
    """Accumulates compile-time issues."""

    def __init__(self) -> None:
        self.issue_count: int = 0

    def add(self, issue: CompilerError) -> None:
        self.issue_count = self.issue_count + 1

    def ok(self) -> bool:
        if self.issue_count == 0:
            return True
        return False

    def clear(self) -> None:
        self.issue_count = 0


error_collector: ErrorCollector | None = None


def init_errors_core() -> None:
    """Initialize module globals (call once at startup)."""
    global error_collector
    error_collector = ErrorCollector()

"""Transpile-ready token types."""

from __future__ import annotations

from shivyc.transpile.errors_core import Range


class TokenKind:
    """A lexer token kind (keyword, symbol, or special)."""

    def __init__(self, text_repr: str = "") -> None:
        self.text_repr: str = text_repr


class Token:
    """Single lexed token."""

    def __init__(
        self,
        kind: TokenKind,
        content: str = "",
        rep: str = "",
        r: Range | None = None,
    ) -> None:
        self.kind: TokenKind = kind
        self.content: str = kind.text_repr
        self.rep: str = rep
        self.r: Range | None = r
        self.wide: bool = False
        self.int_content: list[int] = []
        self.use_int_content: bool = False
        if len(content) > 0:
            self.content = content

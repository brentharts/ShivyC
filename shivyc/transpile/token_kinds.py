"""Transpile-ready token kind registry."""

from __future__ import annotations

from shivyc.transpile.tokens import TokenKind

symbol_kinds: list[TokenKind] | None = None
keyword_kinds: list[TokenKind] | None = None

dquote: TokenKind | None = None
squote: TokenKind | None = None
pound: TokenKind | None = None
identifier: TokenKind | None = None
string: TokenKind | None = None
char_string: TokenKind | None = None
include_file: TokenKind | None = None
number: TokenKind | None = None
unrecognized: TokenKind | None = None


def _new_kind_list() -> list[TokenKind]:
    result: list[TokenKind] = []
    return result


def _register(kinds: list[TokenKind], text_repr: str) -> TokenKind:
    kind: TokenKind = TokenKind(text_repr)
    kinds.append(kind)
    return kind


def _sort_kinds_desc(kinds: list[TokenKind]) -> None:
    i: int = 0
    while i < len(kinds):
        j: int = i + 1
        while j < len(kinds):
            if len(kinds[j].text_repr) > len(kinds[i].text_repr):
                tmp: TokenKind = kinds[i]
                kinds[i] = kinds[j]
                kinds[j] = tmp
            j = j + 1
        i = i + 1


def init_token_kinds() -> None:
    """Register all token kinds (call once at startup)."""
    global symbol_kinds, keyword_kinds
    global dquote, squote, pound, identifier, string, char_string
    global include_file, number, unrecognized

    symbol_kinds = _new_kind_list()
    keyword_kinds = _new_kind_list()

    _register(keyword_kinds, "_Bool")
    _register(keyword_kinds, "char")
    _register(keyword_kinds, "short")
    _register(keyword_kinds, "int")
    _register(keyword_kinds, "long")
    _register(keyword_kinds, "float")
    _register(keyword_kinds, "double")
    _register(keyword_kinds, "signed")
    _register(keyword_kinds, "unsigned")
    _register(keyword_kinds, "void")
    _register(keyword_kinds, "return")
    _register(keyword_kinds, "if")
    _register(keyword_kinds, "else")
    _register(keyword_kinds, "while")
    _register(keyword_kinds, "do")
    _register(keyword_kinds, "switch")
    _register(keyword_kinds, "case")
    _register(keyword_kinds, "default")
    _register(keyword_kinds, "goto")
    _register(keyword_kinds, "for")
    _register(keyword_kinds, "break")
    _register(keyword_kinds, "continue")
    _register(keyword_kinds, "auto")
    _register(keyword_kinds, "register")
    _register(keyword_kinds, "static")
    _register(keyword_kinds, "extern")
    _register(keyword_kinds, "struct")
    _register(keyword_kinds, "union")
    _register(keyword_kinds, "enum")
    _register(keyword_kinds, "const")
    _register(keyword_kinds, "typedef")
    _register(keyword_kinds, "sizeof")
    _register(keyword_kinds, "_Alignof")

    _register(symbol_kinds, "++")
    _register(symbol_kinds, "--")
    _register(symbol_kinds, "+=")
    _register(symbol_kinds, "-=")
    _register(symbol_kinds, "*=")
    _register(symbol_kinds, "/=")
    _register(symbol_kinds, "%=")
    _register(symbol_kinds, "|=")
    _register(symbol_kinds, "&=")
    _register(symbol_kinds, "^=")
    _register(symbol_kinds, "<<=")
    _register(symbol_kinds, ">>=")
    _register(symbol_kinds, "==")
    _register(symbol_kinds, "!=")
    _register(symbol_kinds, "&&")
    _register(symbol_kinds, "||")
    _register(symbol_kinds, "<<")
    _register(symbol_kinds, ">>")
    _register(symbol_kinds, "<=")
    _register(symbol_kinds, ">=")
    _register(symbol_kinds, "->")
    _register(symbol_kinds, "...")
    _register(symbol_kinds, "+")
    _register(symbol_kinds, "-")
    _register(symbol_kinds, "*")
    _register(symbol_kinds, "/")
    _register(symbol_kinds, "%")
    _register(symbol_kinds, "=")
    _register(symbol_kinds, "!")
    _register(symbol_kinds, "<")
    _register(symbol_kinds, ">")
    _register(symbol_kinds, "&")
    _register(symbol_kinds, "|")
    _register(symbol_kinds, "^")
    pound = _register(symbol_kinds, "#")
    _register(symbol_kinds, "~")
    dquote = _register(symbol_kinds, '"')
    squote = _register(symbol_kinds, "'")
    _register(symbol_kinds, "(")
    _register(symbol_kinds, ")")
    _register(symbol_kinds, "{")
    _register(symbol_kinds, "}")
    _register(symbol_kinds, "[")
    _register(symbol_kinds, "]")
    _register(symbol_kinds, ",")
    _register(symbol_kinds, ";")
    _register(symbol_kinds, "?")
    _register(symbol_kinds, ":")
    _register(symbol_kinds, ".")

    identifier = TokenKind("")
    number = TokenKind("")
    unrecognized = TokenKind("")
    string = TokenKind("")
    char_string = TokenKind("")
    include_file = TokenKind("")

    _sort_kinds_desc(symbol_kinds)
    _sort_kinds_desc(keyword_kinds)

"""Transpile-ready parser entry point (Phase 3).

Handles empty translation units and file-scope semicolon skipping.
Declaration and function parsing will be added in follow-up modules.
"""

from __future__ import annotations

import shivyc.transpile.parser_utils as parser_utils
import shivyc.transpile.token_kinds as token_kinds
from shivyc.transpile.parser_utils import (
    PARSER_ERROR_AT,
    ParserError,
    clear_pending_parser_error,
    has_remaining_tokens,
    log_error_begin,
    log_error_caught,
    raise_error,
    reset_parse_state,
    take_pending_parser_error,
    token_is,
)
from shivyc.transpile.tokens import Token
from shivyc.transpile.tree_nodes import Node, Root


def parse_root(index: int) -> tuple[Root, int]:
    """Parse top-level declarations until no more input remains."""
    items: list[Node] = []
    while True:
        if token_is(index, token_kinds.semicolon):
            index = index + 1
            continue
        break

    if not has_remaining_tokens(index):
        return Root(items), index
    raise_error("unexpected token", index, PARSER_ERROR_AT)
    return Root(items), index


def parse(tokens_to_parse: list[Token]) -> Root | None:
    """Parse tokens into an AST root, or None when parsing fails."""
    reset_parse_state()
    parser_utils.tokens = tokens_to_parse

    symbols_bak: list[dict[str, bool]] = log_error_begin()
    clear_pending_parser_error()
    parsed = parse_root(0)
    err: ParserError | None = take_pending_parser_error()
    if err is None:
        return parsed[0]

    log_error_caught(symbols_bak, err)
    return None

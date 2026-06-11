"""Transpile-ready parser utilities (Phase 3 groundwork).

Mirrors shivyc.parser.utils using only constructs the ShivyCX transpiler
can emit to correct C (no f-strings, comprehensions, context managers, etc.).
"""

from __future__ import annotations

from shivyc.transpile.errors_core import Position, Range, position_add_col
from shivyc.transpile.tokens import Token, TokenKind

PARSER_ERROR_AT: int = 1
PARSER_ERROR_GOT: int = 2
PARSER_ERROR_AFTER: int = 3


class SimpleSymbolTable:
    """Record declared identifiers and whether each names a typedef."""

    def __init__(self) -> None:
        self.symbols: list[dict[str, bool]] = []
        self.new_scope()

    def new_scope(self) -> None:
        self.symbols.append({})

    def end_scope(self) -> None:
        self.symbols.pop()

    def add_symbol(self, name: str, is_typedef: bool) -> None:
        self.symbols[-1][name] = is_typedef

    def is_typedef(self, name: str) -> bool:
        scope_idx: int = len(self.symbols) - 1
        while scope_idx >= 0:
            table: dict[str, bool] = self.symbols[scope_idx]
            if name in table:
                return table[name]
            scope_idx = scope_idx - 1
        return False

    def snapshot(self) -> list[dict[str, bool]]:
        copied: list[dict[str, bool]] = []
        idx: int = 0
        while idx < len(self.symbols):
            copied.append(dict(self.symbols[idx]))
            idx = idx + 1
        return copied

    def restore(self, snap: list[dict[str, bool]]) -> None:
        rebuilt: list[dict[str, bool]] = []
        idx: int = 0
        while idx < len(snap):
            rebuilt.append(dict(snap[idx]))
            idx = idx + 1
        self.symbols = rebuilt


symbols: SimpleSymbolTable | None = None
tokens: list[Token] | None = None
best_error: ParserError | None = None
shivycx_pending_parser_error: ParserError | None = None


def init_parser_utils() -> None:
    """Initialize module globals (call once at startup)."""
    global symbols
    symbols = SimpleSymbolTable()


class ParserError:
    """Parser error carrying amount_parsed for backtracking."""

    def __init__(self, descrip: str, range: Range | None, amount_parsed: int) -> None:
        self.descrip: str = descrip
        self.range: Range | None = range
        self.amount_parsed: int = amount_parsed
        self.warning: bool = False


cur_func_name: str | None = None


def _token_spelling(tok: Token) -> str:
    if len(tok.rep) > 0:
        return tok.rep
    return tok.content


def set_pending_parser_error(err: ParserError) -> None:
    global shivycx_pending_parser_error
    shivycx_pending_parser_error = err


def clear_pending_parser_error() -> None:
    global shivycx_pending_parser_error
    shivycx_pending_parser_error = None


def take_pending_parser_error() -> ParserError | None:
    global shivycx_pending_parser_error
    err: ParserError | None = shivycx_pending_parser_error
    shivycx_pending_parser_error = None
    return err


def reset_parse_state() -> None:
    """Clear parser globals before a new parse."""
    global best_error, cur_func_name
    best_error = None
    cur_func_name = None
    clear_pending_parser_error()


def has_remaining_tokens(index: int) -> bool:
    if tokens is None:
        return False
    return index < len(tokens)


def build_parser_error(message: str, index: int, message_type: int) -> ParserError:
    """Build a ParserError matching shivyc.parser.utils.ParserError formatting."""
    descrip: str = ""
    spell: str = ""
    new_range: Range | None = None
    if tokens is None or len(tokens) == 0:
        descrip = message + " at beginning of source"
        return ParserError(descrip, None, index)

    idx: int = index
    msg_type: int = message_type
    tok_len: int = len(tokens)

    if idx >= tok_len:
        idx = tok_len
        msg_type = PARSER_ERROR_AFTER
    elif idx <= 0:
        idx = 0
        if msg_type == PARSER_ERROR_AFTER:
            msg_type = PARSER_ERROR_GOT

    if msg_type == PARSER_ERROR_AT:
        spell = _token_spelling(tokens[idx])
        descrip = message + " at '" + spell + "'"
        return ParserError(descrip, tokens[idx].r, index)
    if msg_type == PARSER_ERROR_GOT:
        spell = _token_spelling(tokens[idx])
        descrip = message + ", got '" + spell + "'"
        return ParserError(descrip, tokens[idx].r, index)

    prev_tok: Token = tokens[idx - 1]
    spell = _token_spelling(prev_tok)
    descrip = message + " after '" + spell + "'"
    if prev_tok.r is not None:
        after_pos: Position = position_add_col(prev_tok.r.end, 1)
        new_range = Range(after_pos)
    return ParserError(descrip, new_range, index)


def raise_error(err: str, index: int, error_type: int) -> None:
    set_pending_parser_error(build_parser_error(err, index, error_type))


def log_error_begin() -> list[dict[str, bool]]:
    if symbols is None:
        init_parser_utils()
    return symbols.snapshot()


def log_error_caught(symbols_bak: list[dict[str, bool]], err: ParserError | None) -> None:
    global best_error
    if err is not None:
        if best_error is None or err.amount_parsed >= best_error.amount_parsed:
            best_error = err
        if symbols is not None:
            symbols.restore(symbols_bak)


def token_is(index: int, kind: TokenKind) -> bool:
    if tokens is None:
        return False
    if len(tokens) <= index:
        return False
    return tokens[index].kind == kind


def token_in(index: int, kinds: list[TokenKind]) -> bool:
    if tokens is None or len(tokens) <= index:
        return False
    k: int = 0
    while k < len(kinds):
        if tokens[index].kind == kinds[k]:
            return True
        k = k + 1
    return False


def match_token(
    index: int,
    kind: TokenKind,
    message_type: int,
    message: str | None = None,
) -> int:
    msg: str
    if message is None:
        msg = "expected '" + kind.text_repr + "'"
    else:
        msg = message
    if token_is(index, kind):
        return index + 1
    set_pending_parser_error(build_parser_error(msg, index, message_type))
    return index


def token_range(start: int, end: int) -> Range | None:
    if tokens is None or len(tokens) == 0:
        return None
    tok_len: int = len(tokens)
    start_index: int = start
    if start_index < 0:
        start_index = 0
    if start_index >= tok_len:
        start_index = tok_len - 1
    bound: int = end - 1
    if start_index > bound:
        start_index = bound
    end_index: int = end - 1
    if end_index < 0:
        end_index = 0
    if end_index >= tok_len:
        end_index = tok_len - 1
    start_range: Range | None = tokens[start_index].r
    end_range: Range | None = tokens[end_index].r
    if start_range is None or end_range is None:
        return None
    return Range(start_range.start, end_range.end)

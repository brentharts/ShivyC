"""Transpile-ready subset of shivyc.lexer (Phase 2)."""

from __future__ import annotations

import shivyc.transpile.errors_core as errors_core
import shivyc.transpile.token_kinds as token_kinds
from shivyc.transpile.errors_core import (
    CompilerError,
    Position,
    Range,
    Tagged,
    set_pending_compiler_error,
)
from shivyc.transpile.regex_helpers import (
    float_const_fullmatch,
    identifier_fullmatch,
    int_const_fullmatch,
    str_contains_char,
    str_splitlines,
)
from shivyc.transpile.tokens import Token, TokenKind


def chunk_to_str(chunk: list[Tagged]) -> str:
    """Convert tagged characters to a string."""
    result: str = ""
    idx: int = 0
    while idx < len(chunk):
        tagged: Tagged = chunk[idx]
        result = result + tagged.c
        idx = idx + 1
    return result


def split_to_tagged_lines(text: str, filename: str) -> list[list[Tagged]]:
    """Split input text into tagged lines."""
    raw_lines: list[str] = str_splitlines(text)
    tagged_lines: list[list[Tagged]] = []
    line_num: int = 0
    while line_num < len(raw_lines):
        line: str = raw_lines[line_num]
        tagged_line: list[Tagged] = []
        col: int = 0
        while col < len(line):
            pos: Position = Position(filename, line_num + 1, col + 1, line)
            tagged_line.append(Tagged(line[col], pos))
            col = col + 1
        tagged_lines.append(tagged_line)
        line_num = line_num + 1
    return tagged_lines


def join_extended_lines(lines: list[list[Tagged]]) -> None:
    """Join lines ending in an escaped backslash."""
    i: int = 0
    while i < len(lines):
        line: list[Tagged] = lines[i]
        if len(line) > 0 and line[-1].c == "\\":
            if i + 1 < len(lines):
                del line[-1]
                next_line: list[Tagged] = lines[i + 1]
                j: int = 0
                while j < len(next_line):
                    line.append(next_line[j])
                    j = j + 1
                del lines[i + 1]
                i = i - 1
            else:
                del line[-1]
        i = i + 1


def _code_in_set(char_set: str, code: int) -> bool:
    idx: int = 0
    while idx < len(char_set):
        if ord(char_set[idx]) == code:
            return True
        idx = idx + 1
    return False


def _continues_number(line: list[Tagged], chunk_start: int, chunk_end: int) -> bool:
    """Whether the symbol at chunk_end continues a floating constant."""
    chunk: str = chunk_to_str(line[chunk_start:chunk_end])
    ch: str = line[chunk_end].c
    if ch == ".":
        if len(chunk) > 0 and chunk[0].isdigit():
            return True
        if len(chunk) == 0 and chunk_end + 1 < len(line):
            return line[chunk_end + 1].c.isdigit()
        return False
    if ch == "+" or ch == "-":
        if len(chunk) == 0:
            return False
        if not chunk[0].isdigit():
            return False
        last_idx: int = len(chunk) - 1
        if not _code_in_set("eEpP", ord(chunk[last_idx])):
            return False
        if _code_in_set("pP", ord(chunk[last_idx])):
            return True
        return not (len(chunk) >= 2 and chunk[0] == "0" and (chunk[1] == "x" or chunk[1] == "X"))
    return False


def match_number_string(token_repr: list[Tagged]) -> str | None:
    """Return number spelling if token_repr is a numeric constant."""
    token_str: str = chunk_to_str(token_repr)
    if float_const_fullmatch(token_str):
        return token_str
    if int_const_fullmatch(token_str):
        return token_str
    return None


def match_identifier_name(token_repr: list[Tagged]) -> str | None:
    """Return identifier name if token_repr is an identifier."""
    token_str: str = chunk_to_str(token_repr)
    if identifier_fullmatch(token_str):
        return token_str
    return None


def is_float_constant(spelling: str) -> bool:
    """Return whether spelling is a floating constant."""
    return float_const_fullmatch(spelling)


def _token_kind_list_get(kinds: list[TokenKind], index: int) -> TokenKind:
    return kinds[index]


def match_symbol_kind_at(content: list[Tagged], start: int) -> TokenKind | None:
    """Return the longest matching symbol token kind at start."""
    idx: int = 0
    while idx < len(token_kinds.symbol_kinds):
        symbol_kind: TokenKind = _token_kind_list_get(token_kinds.symbol_kinds, idx)
        text: str = symbol_kind.text_repr
        i: int = 0
        matched: bool = True
        while i < len(text):
            if start + i >= len(content):
                matched = False
                break
            if content[start + i].c[0] != text[i]:
                matched = False
                break
            i = i + 1
        if matched and len(text) > 0:
            return symbol_kind
        idx = idx + 1
    return None


def match_include_command(tokens: list[Token]) -> bool:
    """Check if end of tokens is a #include directive."""
    if len(tokens) != 2:
        return False
    if tokens[len(tokens) - 2].kind != token_kinds.pound:
        return False
    if tokens[len(tokens) - 1].kind != token_kinds.identifier:
        return False
    if tokens[len(tokens) - 1].content != "include":
        return False
    return True


def match_keyword_kind(token_repr: list[Tagged]) -> TokenKind | None:
    """Return keyword kind matching token_repr exactly."""
    token_str: str = chunk_to_str(token_repr)
    idx: int = 0
    while idx < len(token_kinds.keyword_kinds):
        keyword_kind: TokenKind = _token_kind_list_get(token_kinds.keyword_kinds, idx)
        if keyword_kind.text_repr == token_str:
            return keyword_kind
        idx = idx + 1
    return None


def add_chunk(chunk: list[Tagged], tokens: list[Token]) -> None:
    """Convert chunk into a token if possible and append to tokens."""
    if len(chunk) == 0:
        return
    tok_range: Range = Range(chunk[0].p, chunk[len(chunk) - 1].p)
    keyword_kind: TokenKind | None = match_keyword_kind(chunk)
    if keyword_kind:
        tokens.append(Token(keyword_kind, "", "", tok_range))
        return
    number_string: str | None = match_number_string(chunk)
    if number_string:
        tokens.append(Token(token_kinds.number, number_string, "", tok_range))
        return
    identifier_name: str | None = match_identifier_name(chunk)
    if identifier_name:
        tokens.append(Token(token_kinds.identifier, identifier_name, "", tok_range))
        return
    tokens.append(Token(token_kinds.unrecognized, chunk_to_str(chunk), "", tok_range))


def _simple_escape(ch: str) -> int:
    """Return ASCII value for a simple backslash escape, or -1 if unknown."""
    if ch == "'":
        return 39
    if ch == '"':
        return 34
    if ch == "?":
        return 63
    if ch == "\\":
        return 92
    if ch == "a":
        return 7
    if ch == "b":
        return 8
    if ch == "f":
        return 12
    if ch == "n":
        return 10
    if ch == "r":
        return 13
    if ch == "t":
        return 9
    if ch == "v":
        return 11
    return -1


def read_string(
    line: list[Tagged],
    start: int,
    delim: str,
    null: bool,
) -> tuple[list[int], int]:
    """Lex a string/char literal; return char codes and closing-quote index."""
    i: int = start
    chars: list[int] = []
    octdigits: str = "01234567"
    hexdigits: str = "0123456789abcdefABCDEF"

    while True:
        if i >= len(line):
            descrip: str = "missing terminating quote"
            set_pending_compiler_error(descrip, line[start - 1].r)
            return chars, 0
        if line[i].c == delim:
            if null:
                chars.append(0)
            return chars, i
        if i + 1 < len(line) and line[i].c == "\\":
            esc: int = _simple_escape(line[i + 1].c)
            if esc >= 0:
                chars.append(esc)
                i = i + 2
                continue
            if str_contains_char(octdigits, line[i + 1].c):
                octal: str = line[i + 1].c
                i = i + 2
                while i < len(line) and len(octal) < 3 and str_contains_char(octdigits, line[i].c):
                    octal = octal + line[i].c
                    i = i + 1
                chars.append(int(octal, 8))
                continue
            if i + 2 < len(line) and line[i + 1].c == "x" and str_contains_char(hexdigits, line[i + 2].c):
                hexa: str = line[i + 2].c
                i = i + 3
                while i < len(line) and str_contains_char(hexdigits, line[i].c):
                    hexa = hexa + line[i].c
                    i = i + 1
                chars.append(int(hexa, 16))
                continue
        chars.append(ord(line[i].c))
        i = i + 1


def read_include_filename(line: list[Tagged], start: int) -> tuple[str, int]:
    """Read a #include filename; return spelling and closing delimiter index."""
    end: str = ""
    if start < len(line) and line[start].c == '"':
        end = '"'
    elif start < len(line) and line[start].c == "<":
        end = ">"
    else:
        descrip: str = 'expected "FILENAME" or <FILENAME> after include directive'
        err_range: Range
        if start < len(line):
            err_range = line[start].r
        else:
            err_range = line[len(line) - 1].r
        set_pending_compiler_error(descrip, err_range)
        return "", 0

    i: int = start + 1
    found: bool = False
    while i < len(line):
        if line[i].c == end:
            found = True
            break
        i = i + 1
    if not found:
        missing_descrip: str = "missing terminating character for include filename"
        set_pending_compiler_error(missing_descrip, line[start].r)
        return "", 0

    return chunk_to_str(line[start:i + 1]), i


def tokenize_line(line: list[Tagged], in_comment: bool) -> tuple[list[Token], bool]:
    """Tokenize a single logical line."""
    tokens: list[Token] = []
    chunk_start: int = 0
    chunk_end: int = 0
    include_line: bool = False
    seen_filename: bool = False
    next_c: str = ""
    quote_str: str = '"'
    kind: TokenKind = token_kinds.string
    add_null: bool = True
    end: int = 0

    while chunk_end < len(line):
        symbol_kind: TokenKind | None = match_symbol_kind_at(line, chunk_end)
        next_symbol_kind: TokenKind | None = match_symbol_kind_at(line, chunk_end + 1)

        cur_c: str = line[chunk_end].c
        if chunk_end + 1 < len(line):
            next_c = line[chunk_end + 1].c
        else:
            next_c = ""

        if match_include_command(tokens):
            include_line = True

        if in_comment:
            if cur_c == "*" and next_c == "/":
                in_comment = False
                chunk_start = chunk_end + 2
                chunk_end = chunk_start
            else:
                chunk_start = chunk_end + 1
                chunk_end = chunk_start
        elif cur_c == "/" and next_c == "*":
            add_chunk(line[chunk_start:chunk_end], tokens)
            in_comment = True
        elif cur_c == "/" and next_c == "/":
            break
        elif line[chunk_end].c.isspace():
            add_chunk(line[chunk_start:chunk_end], tokens)
            chunk_start = chunk_end + 1
            chunk_end = chunk_start
        elif include_line:
            if seen_filename:
                descrip = "extra tokens at end of include directive"
                set_pending_compiler_error(descrip, line[chunk_end].r)
                return tokens, in_comment
            filename: str
            end: int
            filename, end = read_include_filename(line, chunk_end)
            if errors_core.shivycx_pending_error is not None:
                return tokens, in_comment
            tokens.append(
                Token(
                    token_kinds.include_file,
                    filename,
                    "",
                    Range(line[chunk_end].p, line[end].p),
                )
            )
            chunk_start = end + 1
            chunk_end = chunk_start
            seen_filename = True
        elif symbol_kind == token_kinds.dquote or symbol_kind == token_kinds.squote:
            if symbol_kind == token_kinds.dquote:
                quote_str = '"'
                kind = token_kinds.string
                add_null = True
            else:
                quote_str = "'"
                kind = token_kinds.char_string
                add_null = False
            prefix: str = chunk_to_str(line[chunk_start:chunk_end])
            wide: bool = prefix == "L"
            chars: list[int]
            chars, end = read_string(line, chunk_end + 1, quote_str, add_null)
            if errors_core.shivycx_pending_error is not None:
                return tokens, in_comment
            rep: str = chunk_to_str(line[chunk_end:end + 1])
            tok_range: Range = Range(line[chunk_end].p, line[end].p)
            if kind == token_kinds.char_string and len(chars) == 0:
                err: str = "empty character constant"
                errors_core.error_collector.add(CompilerError(err, tok_range))
            tok: Token = Token(kind, "", rep, tok_range)
            tok.int_content = chars
            tok.use_int_content = True
            tok.wide = wide
            tokens.append(tok)
            chunk_start = end + 1
            chunk_end = chunk_start
        elif symbol_kind and _continues_number(line, chunk_start, chunk_end):
            chunk_end = chunk_end + 1
        elif symbol_kind:
            symbol_start_index: int = chunk_end
            symbol_end_index: int = chunk_end + len(symbol_kind.text_repr) - 1
            sym_range: Range = Range(
                line[symbol_start_index].p,
                line[symbol_end_index].p,
            )
            symbol_token: Token = Token(symbol_kind, "", "", sym_range)
            add_chunk(line[chunk_start:chunk_end], tokens)
            tokens.append(symbol_token)
            chunk_start = chunk_end + len(symbol_kind.text_repr)
            chunk_end = chunk_start
        else:
            chunk_end = chunk_end + 1

    add_chunk(line[chunk_start:chunk_end], tokens)

    if (include_line or match_include_command(tokens)) and not seen_filename:
        read_include_filename(line, chunk_end)
        if errors_core.shivycx_pending_error is not None:
            return tokens, in_comment

    return tokens, in_comment


def tokenize(code: str, filename: str) -> list[Token]:
    """Convert source text into a flat list of tokens."""
    tokens: list[Token] = []
    lines: list[list[Tagged]] = split_to_tagged_lines(code, filename)
    join_extended_lines(lines)
    in_comment: bool = False
    logical_line: int = 0
    while logical_line < len(lines):
        line: list[Tagged] = lines[logical_line]
        errors_core.clear_pending_error()
        line_tokens: list[Token]
        line_tokens, in_comment = tokenize_line(line, in_comment)
        err: CompilerError | None = errors_core.take_pending_error()
        if err is not None:
            errors_core.error_collector.add(err)
        else:
            t_idx: int = 0
            while t_idx < len(line_tokens):
                tok: Token = line_tokens[t_idx]
                tok.logical_line = logical_line
                t_idx = t_idx + 1
            tokens.extend(line_tokens)
        logical_line = logical_line + 1
    return tokens


def tokenize_text_line(text: str, filename: str, in_comment: bool) -> tuple[list[Token], bool]:
    """Tokenize a single source line given as raw text."""
    tagged_lines: list[list[Tagged]] = split_to_tagged_lines(text, filename)
    if len(tagged_lines) > 0:
        return tokenize_line(tagged_lines[0], in_comment)
    tokens: list[Token] = []
    return tokens, in_comment


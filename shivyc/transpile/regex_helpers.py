"""Transpile-friendly pattern matching (replaces shivyc.lexer regex usage)."""

from __future__ import annotations

# str_contains_char is provided by shivycx_runtime.h in generated C.


def _has_char(s: str, ch: str) -> bool:
    idx: int = 0
    while idx < len(s):
        if len(ch) > 0 and s[idx] == ch[0]:
            return True
        idx = idx + 1
    return False


def _has_substr(s: str, needle: str) -> bool:
    if len(needle) == 0:
        return True
    max_start: int = len(s) - len(needle)
    start: int = 0
    while start <= max_start:
        match: bool = True
        j: int = 0
        while j < len(needle):
            if s[start + j] != needle[j]:
                match = False
                break
            j = j + 1
        if match:
            return True
        start = start + 1
    return False


def substr(s: str, start: int, end: int) -> str:
    """Return s[start:end] as a new string."""
    result: str = ""
    idx: int = start
    while idx < end and idx < len(s):
        result = result + s[idx]
        idx = idx + 1
    return result


def str_rstrip(s: str, chars: str) -> str:
    """Remove trailing characters in chars from s."""
    end: int = len(s)
    while end > 0 and str_contains_char(chars, s[end - 1]):
        end = end - 1
    return substr(s, 0, end)


def str_contains_char(s: str, ch: str) -> bool:
    """transpiler: skip"""
    idx: int = 0
    while idx < len(s):
        if s[idx] == ch:
            return True
        idx = idx + 1
    return False


def float_const_fullmatch(token_str: str) -> bool:
    """Return whether token_str is a C floating constant spelling."""
    if len(token_str) == 0:
        return False
    if str_contains_char("fFlL", token_str[-1]):
        token_str = substr(token_str, 0, len(token_str) - 1)
    if _has_substr(token_str, "0x") or _has_substr(token_str, "0X"):
        if _has_char(token_str, "p") or _has_char(token_str, "P"):
            return True
    if _has_char(token_str, "e") or _has_char(token_str, "E"):
        return True
    if _has_char(token_str, "."):
        idx: int = 0
        while idx < len(token_str):
            if token_str[idx].isdigit():
                return True
            idx = idx + 1
    return False


def int_const_fullmatch(token_str: str) -> bool:
    """Return whether token_str is a C integer constant spelling."""
    if len(token_str) == 0:
        return False
    core: str = str_rstrip(token_str, "uUlL")
    prefix: str = substr(core, 0, 2)
    idx: int = 0
    if prefix == "0x" or prefix == "0X":
        idx = 2
        while idx < len(core):
            if not str_contains_char("0123456789abcdefABCDEF", core[idx]):
                return False
            idx = idx + 1
        return True
    if prefix == "0b" or prefix == "0B":
        idx = 2
        while idx < len(core):
            if not str_contains_char("01", core[idx]):
                return False
            idx = idx + 1
        return True
    if len(core) > 1 and core[0] == "0":
        idx = 1
        while idx < len(core):
            if not str_contains_char("01234567", core[idx]):
                return False
            idx = idx + 1
        return True
    idx = 0
    while idx < len(core):
        if not core[idx].isdigit():
            return False
        idx = idx + 1
    return len(core) > 0


def identifier_fullmatch(token_str: str) -> bool:
    """Return whether token_str is a C identifier."""
    if len(token_str) == 0:
        return False
    if not (token_str[0] == "_" or token_str[0].isalpha()):
        return False
    idx: int = 1
    while idx < len(token_str):
        if not (token_str[idx] == "_" or token_str[idx].isalnum()):
            return False
        idx = idx + 1
    return True


def str_splitlines(text: str) -> list[str]:
    """Split text on line boundaries without retaining newlines."""
    lines: list[str] = []
    start: int = 0
    idx: int = 0
    while idx < len(text):
        if text[idx] == "\n":
            lines.append(substr(text, start, idx))
            start = idx + 1
        idx = idx + 1
    if start <= len(text):
        lines.append(substr(text, start, len(text)))
    return lines

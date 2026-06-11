"""C-backed lexer via ctypes (optional acceleration path).

Requires ``./tools/transpile lib`` to build ``generated/libshivycx_lexer.so``.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, c_bool, c_char_p, c_int, c_size_t, c_void_p
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = ROOT / "generated" / "libshivycx_lexer.so"

_lib: Optional[ctypes.CDLL] = None
_kind_by_addr: Dict[int, str] = {}
_text_kind_cache: Dict[str, object] = {}


class _IntList(Structure):
    _fields_ = [("data", POINTER(c_int)), ("size", c_size_t), ("capacity", c_size_t)]


class _TokenKind(Structure):
    _fields_ = [("text_repr", c_char_p)]


class _Position(Structure):
    _fields_ = [
        ("file", c_char_p),
        ("line", c_int),
        ("col", c_int),
        ("full_line", c_char_p),
    ]


class _Range(Structure):
    _fields_ = [("start", POINTER(_Position)), ("end", POINTER(_Position))]


class _Token(Structure):
    _fields_ = [
        ("kind", POINTER(_TokenKind)),
        ("content", c_char_p),
        ("rep", c_char_p),
        ("r", POINTER(_Range)),
        ("wide", c_bool),
        ("int_content", POINTER(_IntList)),
        ("use_int_content", c_bool),
        ("logical_line", c_int),
    ]


class _TokenList(Structure):
    _fields_ = [
        ("data", POINTER(POINTER(_Token))),
        ("size", c_size_t),
        ("capacity", c_size_t),
    ]


class _CompilerError(Structure):
    _fields_ = [("descrip", c_char_p), ("range", POINTER(_Range)), ("warning", c_bool)]


class _CompilerErrorList(Structure):
    _fields_ = [
        ("data", POINTER(POINTER(_CompilerError))),
        ("size", c_size_t),
        ("capacity", c_size_t),
    ]


class _ErrorCollector(Structure):
    _fields_ = [("issues", POINTER(_CompilerErrorList))]


def available() -> bool:
    return LIB_PATH.is_file()


def _decode(data: Optional[bytes]) -> str:
    if not data:
        return ""
    return data.decode("utf-8")


def _load() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib

    lib = ctypes.CDLL(str(LIB_PATH))
    lib.init_errors_core.argtypes = []
    lib.init_errors_core.restype = None
    lib.init_token_kinds.argtypes = []
    lib.init_token_kinds.restype = None
    lib.tokenize.argtypes = [c_char_p, c_char_p]
    lib.tokenize.restype = POINTER(_TokenList)
    lib.ErrorCollector_clear.argtypes = [POINTER(_ErrorCollector)]
    lib.ErrorCollector_clear.restype = None
    lib.ErrorCollector_ok.argtypes = [POINTER(_ErrorCollector)]
    lib.ErrorCollector_ok.restype = c_bool
    lib.ErrorCollector_issue_count.argtypes = [POINTER(_ErrorCollector)]
    lib.ErrorCollector_issue_count.restype = c_size_t
    lib.ErrorCollector_issue_at.argtypes = [POINTER(_ErrorCollector), c_size_t]
    lib.ErrorCollector_issue_at.restype = POINTER(_CompilerError)

    lib.init_errors_core()
    lib.init_token_kinds()

    for name in (
        "identifier",
        "number",
        "string",
        "char_string",
        "include_file",
        "unrecognized",
    ):
        ptr = c_void_p.in_dll(lib, name)
        _kind_by_addr[ptr.value] = name

    _lib = lib
    return lib


def _map_kind(kind_ptr: POINTER(_TokenKind)):
    import shivyc.token_kinds as tk

    addr = ctypes.cast(kind_ptr, c_void_p).value
    special = _kind_by_addr.get(addr)
    if special:
        return getattr(tk, special)

    text = _decode(kind_ptr.contents.text_repr)
    if text in _text_kind_cache:
        return _text_kind_cache[text]
    for kind in tk.keyword_kinds + tk.symbol_kinds:
        if kind.text_repr == text:
            _text_kind_cache[text] = kind
            return kind
    return tk.unrecognized


def _position_to_py(pos: POINTER(_Position)):
    from shivyc.errors import Position

    p = pos.contents
    return Position(_decode(p.file), p.line, p.col, _decode(p.full_line))


def _range_to_py(rng: Optional[POINTER(_Range)]):
    from shivyc.errors import Range

    if not rng:
        return None
    r = rng.contents
    return Range(_position_to_py(r.start), _position_to_py(r.end))


def _token_to_py(tok_ptr: POINTER(_Token)):
    from shivyc.tokens import Token

    tok = tok_ptr.contents
    kind = _map_kind(tok.kind)
    rep = _decode(tok.rep)
    content = _decode(tok.content)
    r = _range_to_py(tok.r)

    if tok.use_int_content and tok.int_content:
        ilist = tok.int_content.contents
        chars: List[int] = []
        idx = 0
        while idx < ilist.size:
            chars.append(ilist.data[idx])
            idx = idx + 1
        py_tok = Token(kind, chars, rep, r=r)
    else:
        py_tok = Token(kind, content, rep, r=r)

    py_tok.wide = bool(tok.wide)
    py_tok.logical_line = tok.logical_line
    return py_tok


def tokenize(code: str, filename: str):
    """Tokenize source using the transpiled C lexer."""
    if not available():
        raise RuntimeError(f"C lexer library not found: {LIB_PATH}")

    lib = _load()
    from shivyc.errors import CompilerError, error_collector

    error_collector.clear()
    ec = POINTER(_ErrorCollector).in_dll(lib, "error_collector")
    lib.ErrorCollector_clear(ec)

    raw = code.encode("utf-8")
    name = filename.encode("utf-8")
    token_list = lib.tokenize(raw, name)
    if not token_list:
        return []

    lst = token_list.contents
    tokens = []
    idx = 0
    while idx < lst.size:
        tokens.append(_token_to_py(lst.data[idx]))
        idx = idx + 1

    if not lib.ErrorCollector_ok(ec):
        count = lib.ErrorCollector_issue_count(ec)
        i = 0
        while i < count:
            err = lib.ErrorCollector_issue_at(ec, i)
            if err:
                descrip = _decode(err.contents.descrip)
                error_collector.add(CompilerError(descrip, _range_to_py(err.contents.range)))
            i = i + 1

    return tokens

#!/usr/bin/env python3
"""Compare transpiled-C parser_core against the Python reference."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.transpile_build import build_harness, transpile_dir

HARNESS_SRC = ROOT / "tools" / "parser_core_harness.c"
HARNESS_NAME = "parser_core_harness"


def python_output() -> str:
    from shivyc.transpile import parser_core as pc
    from shivyc.transpile import parser_utils as pu
    from shivyc.transpile import token_kinds as tk
    from shivyc.transpile.tokens import Token, TokenKind
    from shivyc.transpile.errors_core import Range, Position

    pu.init_parser_utils()
    tk.init_token_kinds()
    lines: list[str] = []

    empty = pc.parse([])
    lines.append(f"empty:{str(empty is not None).lower()}")
    if empty is not None:
        lines.append(f"empty_nodes:{len(empty.nodes)}")

    semi = Token(
        tk.semicolon,
        "",
        ";",
        Range(Position("t.c", 1, 1, ";"), Position("t.c", 1, 1, ";")),
    )
    semi_only = pc.parse([semi, semi])
    lines.append(f"semi_only:{str(semi_only is not None).lower()}")
    if semi_only is not None:
        lines.append(f"semi_nodes:{len(semi_only.nodes)}")

    ident_kind = TokenKind("int")
    ident = Token(
        ident_kind,
        "int",
        "int",
        Range(Position("t.c", 1, 1, "int x;"), Position("t.c", 1, 1, "int x;")),
    )
    bad = pc.parse([ident])
    lines.append(f"bad:{str(bad is None).lower()}")
    lines.append(f"best:{pu.best_error.descrip if pu.best_error else ''}")

    int_tok = Token(
        tk.int_kw,
        "int",
        "int",
        Range(Position("t.c", 1, 1, "int x;"), Position("t.c", 1, 1, "int x;")),
    )
    x_tok = Token(
        tk.identifier,
        "x",
        "x",
        Range(Position("t.c", 1, 5, "int x;"), Position("t.c", 1, 5, "int x;")),
    )
    int_x = pc.parse([int_tok, x_tok, semi])
    lines.append(f"int_x:{str(int_x is not None).lower()}")
    if int_x is not None:
        lines.append(f"int_x_nodes:{len(int_x.nodes)}")
        lines.append(f"int_x_decls:{len(int_x.nodes[0].node.decls)}")

    int_tok2 = Token(
        tk.int_kw,
        "int",
        "int",
        Range(Position("t.c", 1, 1, "int f(int);"), Position("t.c", 1, 1, "int f(int);")),
    )
    f_tok = Token(
        tk.identifier,
        "f",
        "f",
        Range(Position("t.c", 1, 5, "int f(int);"), Position("t.c", 1, 5, "int f(int);")),
    )
    open_paren = Token(
        tk.open_paren,
        "(",
        "(",
        Range(Position("t.c", 1, 6, "int f(int);"), Position("t.c", 1, 6, "int f(int);")),
    )
    int_param = Token(
        tk.int_kw,
        "int",
        "int",
        Range(Position("t.c", 1, 7, "int f(int);"), Position("t.c", 1, 7, "int f(int);")),
    )
    close_paren = Token(
        tk.close_paren,
        ")",
        ")",
        Range(Position("t.c", 1, 10, "int f(int);"), Position("t.c", 1, 10, "int f(int);")),
    )
    proto = pc.parse([int_tok2, f_tok, open_paren, int_param, close_paren, semi])
    lines.append(f"proto:{str(proto is not None).lower()}")

    def func_def_tokens(source: str, extra: list[Token] | None = None) -> list[Token]:
        toks: list[Token] = [
            Token(
                tk.int_kw,
                "int",
                "int",
                Range(Position("t.c", 1, 1, source), Position("t.c", 1, 1, source)),
            ),
            Token(
                tk.identifier,
                "f",
                "f",
                Range(Position("t.c", 1, 5, source), Position("t.c", 1, 5, source)),
            ),
            Token(
                tk.open_paren,
                "(",
                "(",
                Range(Position("t.c", 1, 6, source), Position("t.c", 1, 6, source)),
            ),
            Token(
                tk.close_paren,
                ")",
                ")",
                Range(Position("t.c", 1, 7, source), Position("t.c", 1, 7, source)),
            ),
            Token(
                tk.open_brack,
                "{",
                "{",
                Range(Position("t.c", 1, 9, source), Position("t.c", 1, 9, source)),
            ),
        ]
        if extra:
            toks.extend(extra)
        toks.append(
            Token(
                tk.close_brack,
                "}",
                "}",
                Range(Position("t.c", 1, len(source), source), Position("t.c", 1, len(source), source)),
            )
        )
        return toks

    empty_body = pc.parse(func_def_tokens("int f() { }"))
    lines.append(f"func_empty:{str(empty_body is not None).lower()}")
    if empty_body is not None:
        decl = empty_body.nodes[0]
        lines.append(f"func_empty_body:{str(decl.body is not None).lower()}")
        if decl.body is not None:
            lines.append(f"func_empty_items:{len(decl.body.items)}")

    return "\n".join(lines) + "\n"


def c_output() -> str:
    proc = subprocess.run(
        [str(transpile_dir() / HARNESS_NAME)],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip transpile/compile (use existing harness binary)",
    )
    args = parser.parse_args()

    if not args.no_build:
        build_harness(HARNESS_SRC, HARNESS_NAME, parser=True)

    py = python_output()
    c = c_output()
    if py != c:
        print("FAIL: Python vs C parser_core mismatch")
        print("--- Python ---")
        print(py, end="")
        print("--- C ---")
        print(c, end="")
        return 1
    print("OK: parser_core matches Python reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())

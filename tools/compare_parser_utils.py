#!/usr/bin/env python3
"""Compare transpiled-C parser_utils against the Python reference."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.transpile_build import build_harness, transpile_dir

HARNESS_SRC = ROOT / "tools" / "parser_utils_harness.c"
HARNESS_NAME = "parser_utils_harness"


def python_output() -> str:
    from shivyc.transpile import parser_utils as pu
    from shivyc.transpile.errors_core import Position, Range
    from shivyc.transpile.tokens import Token, TokenKind

    pu.init_parser_utils()
    table = pu.symbols
    table.add_symbol("foo", True)
    table.add_symbol("bar", False)

    lines = [
        f"foo:{str(table.is_typedef('foo')).lower()}",
        f"bar:{str(table.is_typedef('bar')).lower()}",
        f"missing:{str(table.is_typedef('missing')).lower()}",
    ]

    table.new_scope()
    table.add_symbol("bar", True)
    lines.append(f"bar_inner:{str(table.is_typedef('bar')).lower()}")
    lines.append(f"foo_outer:{str(table.is_typedef('foo')).lower()}")

    snap = table.snapshot()
    table.end_scope()
    lines.append(f"bar_after_pop:{str(table.is_typedef('bar')).lower()}")

    table.restore(snap)
    lines.append(f"bar_after_restore:{str(table.is_typedef('bar')).lower()}")
    lines.append(f"foo_after_restore:{str(table.is_typedef('foo')).lower()}")

    kind = TokenKind(";")
    tok = Token(kind, "", ";", Range(Position("t.c", 1, 1, "int x;")))
    pu.tokens = [tok]
    pu.best_error = None
    pu.clear_pending_parser_error()
    err = pu.build_parser_error("expected identifier", 0, pu.PARSER_ERROR_AT)
    lines.append(f"err_at:{err.descrip}")
    lines.append(f"err_parsed:{err.amount_parsed}")

    pu.tokens = []
    err2 = pu.build_parser_error("unexpected token", 0, pu.PARSER_ERROR_GOT)
    lines.append(f"err_empty:{err2.descrip}")

    bak = pu.log_error_begin()
    pu.log_error_caught(bak, err)
    lines.append(f"best_parsed:{pu.best_error.amount_parsed if pu.best_error else -1}")

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
        build_harness(HARNESS_SRC, HARNESS_NAME, parser=True, parser_utils_only=True)

    py = python_output()
    c = c_output()
    if py != c:
        print("FAIL: Python vs C parser_utils mismatch")
        print("--- Python ---")
        print(py, end="")
        print("--- C ---")
        print(c, end="")
        return 1
    print("OK: parser_utils matches Python reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())

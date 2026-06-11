#!/usr/bin/env python3
"""Compare transpiled-C parser_utils against the Python reference."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GENERATED = ROOT / "generated"
INC_DIR = ROOT / "shivyc" / "transpile"
HARNESS_SRC = ROOT / "tools" / "parser_utils_harness.c"
HARNESS_BIN = ROOT / "tools" / "parser_utils_harness"


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


def build_harness() -> None:
    for mod in ("errors_core", "tokens", "parser_utils"):
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "shivycx_transpiler.py"),
                str(ROOT / "shivyc/transpile" / f"{mod}.py"),
                "-o",
                str(GENERATED / f"{mod}.c"),
            ],
            check=True,
            cwd=ROOT,
        )
    for header in (
        "errors_core.h",
        "tokens.h",
        "token_kinds.h",
        "parser_utils.h",
    ):
        subprocess.run(
            ["cp", str(INC_DIR / header), str(GENERATED / header)],
            check=True,
            cwd=ROOT,
        )
    flags = [
        "gcc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{ROOT}",
        f"-I{GENERATED}",
        f"-I{INC_DIR}",
    ]
    for mod in ("errors_core", "tokens", "parser_utils"):
        subprocess.run(
            flags + ["-c", str(GENERATED / f"{mod}.c"), "-o", str(GENERATED / f"{mod}.o")],
            check=True,
            cwd=ROOT,
        )
    subprocess.run(
        flags
        + [
            "-c",
            str(ROOT / "tools/errors_core_link.c"),
            "-o",
            str(GENERATED / "errors_core_link.o"),
        ],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        flags
        + [
            str(HARNESS_SRC),
            str(GENERATED / "errors_core.o"),
            str(GENERATED / "errors_core_link.o"),
            str(GENERATED / "tokens.o"),
            str(GENERATED / "parser_utils.o"),
            "-o",
            str(HARNESS_BIN),
        ],
        check=True,
        cwd=ROOT,
    )


def c_output() -> str:
    proc = subprocess.run(
        [str(HARNESS_BIN)],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return proc.stdout


def main() -> int:
    if "--no-build" not in sys.argv:
        build_harness()
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

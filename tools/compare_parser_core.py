#!/usr/bin/env python3
"""Compare transpiled-C parser_core against the Python reference."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GENERATED = ROOT / "generated"
INC_DIR = ROOT / "shivyc" / "transpile"
HARNESS_SRC = ROOT / "tools" / "parser_core_harness.c"
HARNESS_BIN = ROOT / "tools" / "parser_core_harness"

MODULES = (
    "errors_core",
    "tokens",
    "token_kinds",
    "parser_utils",
    "tree_nodes",
    "parser_core",
)

HEADERS = (
    "errors_core.h",
    "tokens.h",
    "token_kinds.h",
    "parser_utils.h",
    "tree_nodes.h",
    "parser_core.h",
)


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

    return "\n".join(lines) + "\n"


def build_harness() -> None:
    for mod in MODULES:
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
    for header in HEADERS:
        subprocess.run(["cp", str(INC_DIR / header), str(GENERATED / header)], check=True, cwd=ROOT)

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
    for mod in MODULES:
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
            str(GENERATED / "token_kinds.o"),
            str(GENERATED / "parser_utils.o"),
            str(GENERATED / "tree_nodes.o"),
            str(GENERATED / "parser_core.o"),
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

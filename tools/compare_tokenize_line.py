#!/usr/bin/env python3
"""Compare transpiled-C tokenize_line output against the Python reference."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.transpile_build import build_harness, transpile_dir

HARNESS_SRC = ROOT / "tools" / "tokenize_line_harness.c"
HARNESS_NAME = "tokenize_line_harness"

DEFAULT_SAMPLES = [
    "int x = 42;",
    "a ? b : c",
    "int x[sizeof(long)==8?14:9];",
    "0x1F",
    "#include <stdio.h>",
    '"hello"',
    "'A'",
    "foo + bar",
    "/* still open",
]

SPECIAL_KINDS: dict[int, str] = {}


def _init_python_lexer() -> None:
    from shivyc.transpile import token_kinds
    from shivyc.transpile.errors_core import init_errors_core

    init_errors_core()
    token_kinds.init_token_kinds()

    for name in (
        "identifier",
        "number",
        "string",
        "char_string",
        "include_file",
        "unrecognized",
    ):
        kind = getattr(token_kinds, name)
        SPECIAL_KINDS[id(kind)] = name


def _kind_label(kind) -> str:
    label = SPECIAL_KINDS.get(id(kind))
    if label:
        return label
    if kind.text_repr:
        return kind.text_repr
    return "?"


def _token_text(tok) -> str:
    return tok.rep if tok.rep else tok.content


def python_output(line: str, in_comment: bool = False) -> str:
    from shivyc.transpile.errors_core import error_collector
    from shivyc.transpile.lexer_core import tokenize_text_line

    error_collector.clear()
    try:
        tokens, out_in_comment = tokenize_text_line(line, "harness.c", in_comment)
    except Exception as exc:
        return f"line:{line}\nerror:{exc}\n"

    lines = [f"line:{line}"]
    for tok in tokens:
        lines.append(f"token:{_kind_label(tok.kind)}:{_token_text(tok)}")
    lines.append(f"in_comment:{'true' if out_in_comment else 'false'}")
    if not error_collector.ok():
        lines.append(f"warnings:{error_collector.issue_count}")
    return "\n".join(lines) + "\n"


def c_output(line: str) -> str:
    proc = subprocess.run(
        [str(transpile_dir() / HARNESS_NAME), line],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    out = proc.stdout
    if out.endswith("---\n"):
        out = out[:-4]
    return out


def compare_line(line: str) -> bool:
    py = python_output(line)
    c = c_output(line)
    ok = py == c
    if not ok:
        print(f"FAIL: {line!r}")
        print("--- Python ---")
        print(py, end="")
        print("--- C ---")
        print(c, end="")
    else:
        print(f"OK:   {line!r}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "lines",
        nargs="*",
        help="Lines to tokenize (default: built-in samples)",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip transpile/compile (use existing harness binary)",
    )
    args = parser.parse_args()

    _init_python_lexer()
    if not args.no_build:
        build_harness(HARNESS_SRC, HARNESS_NAME)

    samples = args.lines or DEFAULT_SAMPLES
    failed = sum(not compare_line(line) for line in samples)
    if failed:
        print(f"\n{failed}/{len(samples)} mismatches")
        return 1
    print(f"\nAll {len(samples)} lines matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

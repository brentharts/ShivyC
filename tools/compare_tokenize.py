#!/usr/bin/env python3
"""Compare transpiled-C tokenize output against the Python reference."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GENERATED = ROOT / "generated"
HARNESS_SRC = ROOT / "tools" / "tokenize_harness.c"
HARNESS_BIN = ROOT / "tools" / "tokenize_harness"
TRANSPILER = ROOT / "tools" / "transpile"

DEFAULT_SAMPLES = [
    "int x = 42;\n",
    "a ? b : c\n",
    "int x[sizeof(long)==8?14:9];\n",
    "#include <stdio.h>\n",
    '"hello"\n\'A\'\n',
    "foo \\\nbar\n",
    "int a;\n/* comment */\nint b;\n",
    "#include\n",
]

SPECIAL_KINDS: dict[int, str] = {}


def _init_python_lexer() -> None:
    from shivyc.transpile import errors_core, token_kinds

    errors_core.init_errors_core()
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


def python_output(code: str) -> str:
    from shivyc.transpile import errors_core
    from shivyc.transpile.lexer_core import tokenize

    errors_core.error_collector.clear()
    tokens = tokenize(code, "harness.c")

    lines = [f"tokens:{len(tokens)}"]
    for tok in tokens:
        lines.append(
            f"token:{_kind_label(tok.kind)}:{_token_text(tok)}:L{tok.logical_line}"
        )
    if not errors_core.error_collector.ok():
        lines.append(f"issues:{errors_core.error_collector.issue_count}")
    return "\n".join(lines) + "\n"


def build_harness() -> None:
    subprocess.run([str(TRANSPILER), "lexer_core"], check=True, cwd=ROOT)
    objs = [
        GENERATED / f"{name}.o"
        for name in ("errors_core", "tokens", "token_kinds", "regex_helpers", "lexer_core")
    ]
    cmd = [
        "gcc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        f"-I{ROOT}",
        f"-I{GENERATED}",
        str(HARNESS_SRC),
        str(ROOT / "tools" / "strlist_link.c"),
        *[str(o) for o in objs],
        "-o",
        str(HARNESS_BIN),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)


def c_output(code: str) -> str:
    proc = subprocess.run(
        [str(HARNESS_BIN), code],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return proc.stdout


def compare_sample(code: str) -> bool:
    py = python_output(code)
    c = c_output(code)
    ok = py == c
    label = code.replace("\n", "\\n")[:60]
    if not ok:
        print(f"FAIL: {label!r}")
        print("--- Python ---")
        print(py, end="")
        print("--- C ---")
        print(c, end="")
    else:
        print(f"OK:   {label!r}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="*", help="Source snippets to tokenize")
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip transpile/compile (use existing harness binary)",
    )
    args = parser.parse_args()

    _init_python_lexer()
    if not args.no_build:
        build_harness()

    samples = args.samples or DEFAULT_SAMPLES
    failed = sum(not compare_sample(sample) for sample in samples)
    if failed:
        print(f"\n{failed}/{len(samples)} mismatches")
        return 1
    print(f"\nAll {len(samples)} samples matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

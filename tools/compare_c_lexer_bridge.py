#!/usr/bin/env python3
"""Compare shivyc.c_lexer (ctypes) against shivyc.lexer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.compare_lexer_original import DEFAULT_SAMPLES, _init


def _fmt(which: str):
    fmt = _init()

    def run(code: str) -> str:
        return fmt(code, which)

    return run


def compare_sample(py_fmt, c_fmt, code: str) -> bool:
    py = py_fmt(code)
    c = c_fmt(code)
    ok = py == c
    label = code.replace("\n", "\\n")[:60]
    if not ok:
        print(f"FAIL: {label!r}")
        print("--- shivyc.lexer ---")
        print(py, end="")
        print("--- shivyc.c_lexer ---")
        print(c, end="")
    else:
        print(f"OK:   {label!r}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="*", help="Source snippets to tokenize")
    args = parser.parse_args()

    import shivyc.c_lexer as c_lexer

    if not c_lexer.available():
        print(f"error: {c_lexer.LIB_PATH} not found (run ./tools/transpile lib)")
        return 1

    py_fmt = _fmt("orig")

    def c_fmt(code: str) -> str:
        import shivyc.lexer as orig_lexer
        import shivyc.token_kinds as orig_tk
        from shivyc.errors import error_collector as orig_ec

        from shivyc.c_lexer import tokenize as c_tokenize

        orig_special = {
            id(getattr(orig_tk, name)): name
            for name in (
                "identifier",
                "number",
                "string",
                "char_string",
                "include_file",
                "unrecognized",
            )
        }

        def label(kind, special):
            name = special.get(id(kind))
            if name:
                return name
            return kind.text_repr or "?"

        orig_ec.clear()
        toks = c_tokenize(code, "harness.c")
        issues = len(orig_ec.issues)

        lines = [f"tokens:{len(toks)}"]
        for tok in toks:
            text = tok.rep if tok.rep else tok.content
            lines.append(
                f"token:{label(tok.kind, orig_special)}:{text}:L{tok.logical_line}"
            )
        if issues:
            lines.append(f"issues:{issues}")
        return "\n".join(lines) + "\n"

    samples = args.samples or DEFAULT_SAMPLES
    failed = sum(not compare_sample(py_fmt, c_fmt, sample) for sample in samples)
    if failed:
        print(f"\n{failed}/{len(samples)} mismatches")
        return 1
    print(f"\nAll {len(samples)} samples matched via ctypes bridge")
    return 0


if __name__ == "__main__":
    sys.exit(main())

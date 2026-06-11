#!/usr/bin/env python3
"""Compare .text disassembly from Python vs --c-lexer compiles."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_c_lexer_compile import SAMPLES, _compile, _disasm


def compare_source(name: str, source: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        py_obj = base / "py.o"
        c_obj = base / "c.o"
        _compile(source, py_obj, c_lexer=False)
        _compile(source, c_obj, c_lexer=True)
        py_asm = _disasm(py_obj)
        c_asm = _disasm(c_obj)
        ok = py_asm == c_asm
        if not ok:
            print(f"FAIL: {name}")
            print("--- Python lexer ---")
            print(py_asm)
            print("--- C lexer ---")
            print(c_asm)
        else:
            print(f"OK:   {name}")
        return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", help="C source files to compile")
    args = parser.parse_args()

    import shivyc.lexer_dispatch as lexer_dispatch

    if not lexer_dispatch.ensure_available():
        print("error: could not build or load libshivycx_lexer.so", file=sys.stderr)
        return 1

    if args.sources:
        failed = 0
        for path in args.sources:
            source = Path(path).read_text(encoding="utf-8")
            if not compare_source(path, source):
                failed += 1
        if failed:
            print(f"\n{failed}/{len(args.sources)} mismatches")
            return 1
        print(f"\nAll {len(args.sources)} sources matched")
        return 0

    failed = sum(not compare_source(name, src) for name, src in SAMPLES.items())
    if failed:
        print(f"\n{failed}/{len(SAMPLES)} mismatches")
        return 1
    print(f"\nAll {len(SAMPLES)} built-in samples matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

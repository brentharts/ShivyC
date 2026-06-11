"""Build helpers for transpiled-C harnesses. All artifacts live under /tmp."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPILER = ROOT / "tools" / "transpile"
STRLIST_SRC = ROOT / "tools" / "strlist_link.c"

LEXER_OBJS = (
    "errors_core",
    "tokens",
    "token_kinds",
    "regex_helpers",
    "lexer_core",
)


def transpile_dir() -> Path:
    out = Path(os.environ.get("SHIVYC_TRANSPILE_DIR", "/tmp/shivyc-transpile"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def ensure_lexer_transpiled() -> Path:
    subprocess.run([str(TRANSPILER), "lexer_core"], check=True, cwd=ROOT)
    return transpile_dir()


def build_harness(harness_src: Path, binary_name: str) -> Path:
    out = ensure_lexer_transpiled()
    flags = ["-std=c11", "-Wall", "-Wextra", f"-I{ROOT}", f"-I{out}"]

    strlist_o = out / "strlist_link.o"
    harness_o = out / f"{binary_name}.o"
    binary = out / binary_name
    lexer_objs = [out / f"{name}.o" for name in LEXER_OBJS]

    subprocess.run(
        ["gcc", *flags, "-c", str(STRLIST_SRC), "-o", str(strlist_o)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        ["gcc", *flags, "-c", str(harness_src), "-o", str(harness_o)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        ["gcc", *flags, str(harness_o), str(strlist_o), *map(str, lexer_objs), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    return binary

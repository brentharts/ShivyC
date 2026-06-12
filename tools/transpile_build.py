"""Build helpers for transpiled-C harnesses. All artifacts live under /tmp."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPILER = ROOT / "tools" / "transpile"
STRLIST_SRC = ROOT / "tools" / "strlist_link.c"
ERRORS_CORE_LINK_SRC = ROOT / "tools" / "errors_core_link.c"
INC_DIR = ROOT / "shivyc" / "transpile"

LEXER_OBJS = (
    "errors_core",
    "tokens",
    "token_kinds",
    "regex_helpers",
    "lexer_core",
)

DECL_NODES_LINK_SRC = ROOT / "tools" / "decl_nodes_link.c"

PARSER_UTILS_OBJS = (
    "errors_core",
    "tokens",
    "token_kinds",
    "parser_utils",
)

PARSER_OBJS = (
    *PARSER_UTILS_OBJS,
    "decl_nodes",
    "tree_nodes",
    "expr_nodes",
    "parser_declaration",
    "parser_expression",
    "parser_statement",
    "parser_core",
)

PARSER_HEADERS = (
    "errors_core.h",
    "tokens.h",
    "token_kinds.h",
    "parser_utils.h",
    "decl_nodes.h",
    "tree_nodes.h",
    "expr_nodes.h",
    "parser_declaration.h",
    "parser_expression.h",
    "parser_statement.h",
    "parser_core.h",
)

LEXER_HEADERS = (
    "errors_core.h",
    "regex_helpers.h",
    "tokens.h",
    "token_kinds.h",
    "lexer_core.h",
)

STANDALONE_EXE = "shivycx_tokenize"


def transpile_dir() -> Path:
    out = Path(os.environ.get("SHIVYC_TRANSPILE_DIR", "/tmp/shivyc-transpile"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def transpile_module(out: Path, name: str) -> None:
    src = ROOT / "shivyc" / "transpile" / f"{name}.py"
    subprocess.run(
        ["python3", str(ROOT / "shivycx_transpiler.py"), str(src), "-o", str(out / f"{name}.c")],
        check=True,
        cwd=ROOT,
    )


def copy_headers(out: Path, headers: tuple[str, ...]) -> None:
    for header in headers:
        subprocess.run(["cp", str(INC_DIR / header), str(out / header)], check=True, cwd=ROOT)


def compile_unit(out: Path, flags: list[str], name: str) -> Path:
    obj = out / f"{name}.o"
    subprocess.run(
        ["gcc", *flags, "-c", str(out / f"{name}.c"), "-o", str(obj)],
        check=True,
        cwd=ROOT,
    )
    return obj


def compile_decl_nodes_link(out: Path, flags: list[str]) -> Path:
    link_o = out / "decl_nodes_link.o"
    subprocess.run(
        ["gcc", *flags, "-c", str(DECL_NODES_LINK_SRC), "-o", str(link_o)],
        check=True,
        cwd=ROOT,
    )
    return link_o


def compile_errors_core_link(out: Path, flags: list[str]) -> Path:
    link_o = out / "errors_core_link.o"
    subprocess.run(
        ["gcc", *flags, "-c", str(ERRORS_CORE_LINK_SRC), "-o", str(link_o)],
        check=True,
        cwd=ROOT,
    )
    return link_o


def ensure_lexer_transpiled() -> Path:
    subprocess.run([str(TRANSPILER), "lexer_core"], check=True, cwd=ROOT)
    return transpile_dir()


def ensure_parser_transpiled() -> Path:
    subprocess.run([str(TRANSPILER), "parser"], check=True, cwd=ROOT)
    return transpile_dir()


def build_harness(
    harness_src: Path,
    binary_name: str,
    *,
    parser: bool = False,
    parser_utils_only: bool = False,
) -> Path:
    out = ensure_parser_transpiled() if parser else ensure_lexer_transpiled()
    flags = ["-std=c11", "-Wall", "-Wextra", f"-I{ROOT}", f"-I{out}"]

    support_objs = [compile_errors_core_link(out, flags)]
    if parser:
        support_objs.append(compile_decl_nodes_link(out, flags))
    if not parser:
        strlist_o = out / "strlist_link.o"
        subprocess.run(
            ["gcc", *flags, "-c", str(STRLIST_SRC), "-o", str(strlist_o)],
            check=True,
            cwd=ROOT,
        )
        support_objs.append(strlist_o)

    harness_o = out / f"{binary_name}.o"
    subprocess.run(
        ["gcc", *flags, "-c", str(harness_src), "-o", str(harness_o)],
        check=True,
        cwd=ROOT,
    )

    if parser_utils_only:
        module_names = PARSER_UTILS_OBJS
    elif parser:
        module_names = PARSER_OBJS
    else:
        module_names = LEXER_OBJS
    module_objs = [out / f"{name}.o" for name in module_names]
    binary = out / binary_name
    subprocess.run(
        ["gcc", *flags, str(harness_o), *map(str, support_objs), *map(str, module_objs), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    return binary


def build_standalone_exe() -> Path:
    """Link the transpiled lexer into a standalone tokenizer executable."""
    harness = ROOT / "tools" / "tokenize_harness.c"
    return build_harness(harness, STANDALONE_EXE)

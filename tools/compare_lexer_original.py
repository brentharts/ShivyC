#!/usr/bin/env python3
"""Compare shivyc.transpile.lexer_core against shivyc.lexer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_FRONTEND_SAMPLES = [
    (Path(__file__).resolve().parents[1] / "tests" / "frontend_tests" / name).read_text()
    for name in (
        "lexer.c",
        "identifier.c",
        "string.c",
        "spacing.c",
        "error_missing_semicolon.c",
    )
    if (Path(__file__).resolve().parents[1] / "tests" / "frontend_tests" / name).is_file()
]

DEFAULT_SAMPLES = [
    "int x = 42;\n",
    "a ? b : c\n",
    "int x[sizeof(long)==8?14:9];\n",
    "#include <stdio.h>\n",
    '"hello"\n\'A\'\n',
    "foo \\\nbar\n",
    "int a;\n/* comment */\nint b;\n",
    "#include\n",
    "0x1F\n0b101\n010\n10UL\n",
    "3.14e10\n0x1.0p+0\n",
    "L'\\x41'\n'\\0'\n",
    "''\n",
    '"unclosed\n',
    "#include <stdio\n",
    "int main() { return 0; }\n",
    "#define FOO 1\n",
    "1e+10\n.5\n5.\n",
    "include\n",
    "ptr->field\n",
    "++\n...\n",
    "??=\n",
    "/* still open\n",
    "int x; // comment\n",
    "'ab'\n",
    "'\\123'\n",
    "L\"hi\"\n",
    "0x\n",
    "0b\n",
    "0xG\n",
] + _FRONTEND_SAMPLES


def _init() -> tuple:
    import shivyc.lexer as orig_lexer
    import shivyc.token_kinds as orig_tk
    from shivyc.errors import error_collector as orig_ec

    from shivyc.transpile import errors_core, token_kinds as tx_tk
    from shivyc.transpile.lexer_core import tokenize as tx_tokenize

    errors_core.init_errors_core()
    tx_tk.init_token_kinds()

    def special_map(tk_mod):
        return {
            id(getattr(tk_mod, name)): name
            for name in (
                "identifier",
                "number",
                "string",
                "char_string",
                "include_file",
                "unrecognized",
            )
        }

    orig_special = special_map(orig_tk)
    tx_special = special_map(tx_tk)

    def label(kind, special):
        name = special.get(id(kind))
        if name:
            return name
        return kind.text_repr or "?"

    def fmt(code: str, which: str) -> str:
        if which == "orig":
            orig_ec.clear()
            toks = orig_lexer.tokenize(code, "harness.c")
            issues = len(orig_ec.issues)
            special = orig_special
        else:
            errors_core.error_collector.clear()
            errors_core.shivycx_pending_error = None
            toks = tx_tokenize(code, "harness.c")
            issues = len(errors_core.error_collector.issues)
            special = tx_special

        lines = [f"tokens:{len(toks)}"]
        for tok in toks:
            text = tok.rep if tok.rep else tok.content
            lines.append(
                f"token:{label(tok.kind, special)}:{text}:L{tok.logical_line}"
            )
        if issues:
            lines.append(f"issues:{issues}")
        return "\n".join(lines) + "\n"

    return fmt


def compare_sample(fmt, code: str) -> bool:
    orig = fmt(code, "orig")
    tx = fmt(code, "tx")
    ok = orig == tx
    label = code.replace("\n", "\\n")[:60]
    if not ok:
        print(f"FAIL: {label!r}")
        print("--- shivyc.lexer ---")
        print(orig, end="")
        print("--- transpile.lexer_core ---")
        print(tx, end="")
    else:
        print(f"OK:   {label!r}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="*", help="Source snippets to tokenize")
    args = parser.parse_args()

    fmt = _init()
    samples = args.samples or DEFAULT_SAMPLES
    failed = sum(not compare_sample(fmt, sample) for sample in samples)
    if failed:
        print(f"\n{failed}/{len(samples)} mismatches")
        return 1
    print(f"\nAll {len(samples)} samples matched shivyc.lexer")
    return 0


if __name__ == "__main__":
    sys.exit(main())

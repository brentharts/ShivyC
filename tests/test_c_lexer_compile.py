"""Compile-path checks for the --c-lexer backend."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SAMPLES = {
    "simple_main": "int main(void) { return 0; }\n",
    "ternary": "int f(int x) { return x ? 1 : 0; }\n",
    "macro": "#define TWICE(x) ((x)+(x))\nint main(void) { return TWICE(3); }\n",
    "string": 'const char *s = "hello"; int main(void) { return s[0]; }\n',
    "float": "double x = 3.14; int main(void) { return (int)x; }\n",
}


def _disasm(obj_path: Path) -> str:
    proc = subprocess.run(
        ["objdump", "-d", str(obj_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = []
    in_text = False
    for line in proc.stdout.splitlines():
        if line.startswith("Disassembly of section .text"):
            in_text = True
            lines.append(line)
            continue
        if in_text:
            if line.startswith("Disassembly of section "):
                break
            lines.append(line)
    return "\n".join(lines)


def _compile(source: str, obj_path: Path, *, c_lexer: bool) -> None:
    src_path = obj_path.with_suffix(".c")
    src_path.write_text(source, encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "shivyc.main",
        "--no-cache",
        str(src_path),
        "-c",
        "-o",
        str(obj_path),
    ]
    if c_lexer:
        cmd.insert(3, "--c-lexer")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(
            f"compile failed ({'c' if c_lexer else 'py'} lexer):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


@unittest.skipUnless(
    __import__("shutil").which("objdump"),
    "objdump required to compare object code",
)
class TestCLexerCompile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import shivyc.lexer_dispatch as lexer_dispatch

        if not lexer_dispatch.ensure_available():
            raise unittest.SkipTest("C lexer library is not available")

    def test_text_sections_match_python_lexer(self) -> None:
        for name, source in SAMPLES.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    py_obj = base / f"{name}_py.o"
                    c_obj = base / f"{name}_c.o"
                    _compile(source, py_obj, c_lexer=False)
                    _compile(source, c_obj, c_lexer=True)
                    self.assertEqual(_disasm(py_obj), _disasm(c_obj))

    def test_c_lexer_flag_activates_backend(self) -> None:
        import shivyc.lexer_dispatch as lexer_dispatch

        lexer_dispatch.configure(False)
        self.assertFalse(lexer_dispatch.using_c_lexer())
        self.assertTrue(lexer_dispatch.ensure_available())
        self.assertTrue(lexer_dispatch.using_c_lexer())
        lexer_dispatch.configure(False)


if __name__ == "__main__":
    unittest.main()

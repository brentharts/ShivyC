"""Select between the Python lexer and the transpiled C lexer."""

from __future__ import annotations

import subprocess
from pathlib import Path

_active: bool = False
_ROOT = Path(__file__).resolve().parents[1]


def configure(use_c_lexer: bool) -> bool:
    """Enable the C lexer when requested and available."""
    global _active
    if not use_c_lexer:
        _active = False
        return True
    from shivyc.c_lexer import available

    if available():
        _active = True
        return True
    _active = False
    return False


def ensure_available() -> bool:
    """Build the C lexer shared library if needed and configure it."""
    from shivyc.c_lexer import available

    if available():
        return configure(True)
    script = _ROOT / "tools" / "transpile"
    if not script.is_file():
        return False
    try:
        subprocess.run(
            [str(script), "lib"],
            cwd=_ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return configure(True)


def using_c_lexer() -> bool:
    return _active


def tokenize(code: str, filename: str):
    """Tokenize source with the configured lexer backend."""
    if _active:
        from shivyc.c_lexer import tokenize as c_tokenize

        return c_tokenize(code, filename)
    import shivyc.lexer as lexer

    return lexer.tokenize(code, filename)

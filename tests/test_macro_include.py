"""Tests for macro-expanded `#include` operands (C11 6.10.2p4).

When the tokens after `#include` are neither `"FILENAME"` nor `<FILENAME>`,
the C standard says they are macro-expanded and the result must then match one
of those two forms. This is how `py/mpconfig.h` selects a port config with
`#include MP_CONFIGFILE`, and it is the one construct in micropython's
`ports/objcore` C sources that ShivyCX rejected while gcc accepted it.

The control test (`test_plain_quoted_include`) uses a literal `#include` so a
failure there means the harness is wrong, not the feature; the remaining tests
each fail iff the macro-computed include is unsupported.
"""

import os
import subprocess
import tempfile
import unittest

import shivyc.main
from shivyc.errors import error_collector


class _Args:
    show_reg_alloc_perf = False
    variables_on_stack = False
    simd_pack_globals = False
    stackless_calls = False
    metamorphic = False
    opt_level = 0

    def __init__(self, files, output_name):
        self.files = files
        self.output_name = output_name


def _run(source, headers=None):
    """Write `headers` then compile and run `source`; return its exit code.

    headers - dict of {filename: text} placed alongside the source file, so a
    quoted include resolves relative to it without needing an -I flag.
    """
    workdir = tempfile.mkdtemp()
    for name, text in (headers or {}).items():
        with open(os.path.join(workdir, name), "w") as f:
            f.write(text)

    c_path = os.path.join(workdir, "prog.c")
    out_path = os.path.join(workdir, "prog")
    with open(c_path, "w") as f:
        f.write(source)

    args = _Args([c_path], [out_path])
    shivyc.main.get_arguments = lambda: args
    error_collector.show = lambda: True
    error_collector.clear()
    rc = shivyc.main.main()
    assert rc == 0, "compilation failed"
    return subprocess.run([out_path]).returncode


class TestMacroInclude(unittest.TestCase):
    def test_plain_quoted_include(self):
        # Control: a literal quoted include already works; isolates the feature.
        self.assertEqual(_run(
            '#include "payload.h"\n'
            "int main(void){ return VAL; }\n",
            {"payload.h": "#define VAL 7\n"}), 7)

    def test_object_macro_quoted_include(self):
        # The MP_CONFIGFILE idiom: a macro expands to a "FILENAME" spelling.
        self.assertEqual(_run(
            '#define HDR "payload.h"\n'
            "#include HDR\n"
            "int main(void){ return VAL; }\n",
            {"payload.h": "#define VAL 9\n"}), 9)

    def test_chained_macro_include(self):
        # The expansion may go through more than one macro before yielding the
        # filename spelling.
        self.assertEqual(_run(
            '#define INNER "payload.h"\n'
            "#define HDR INNER\n"
            "#include HDR\n"
            "int main(void){ return VAL; }\n",
            {"payload.h": "#define VAL 11\n"}), 11)

    def test_macro_include_runs_header_code(self):
        # The included file is fully processed, not just located: a function it
        # declares/defines is callable from the includer.
        self.assertEqual(_run(
            '#define HDR "lib.h"\n'
            "#include HDR\n"
            "int main(void){ return add(40, 2); }\n",
            {"lib.h": "int add(int a, int b){ return a + b; }\n"}), 42)


if __name__ == "__main__":
    unittest.main()

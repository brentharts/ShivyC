"""Tests for compile-time folding of `&&` and `||`.

`&&` and `||` must fold to a constant when their operands are constants, so they
can appear in constant contexts: array dimensions, bit-field widths, and the
glibc `_Static_assert` fallback macro, whose bit-field width is
`(COND_A && COND_B) ? 2 : -1`. micropython's `runtime.c`/`objstr.c` use that
fallback, so without this the core does not compile.

Folding must honor short-circuit evaluation: `0 && x` is `0` and `1 || x` is `1`
without requiring (or evaluating) `x`. The runtime tests confirm that when the
result is *not* a constant, side effects are still short-circuited correctly.
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


def _compile(source):
    """Compile source; return (rc, out_path)."""
    workdir = tempfile.mkdtemp()
    c_path = os.path.join(workdir, "prog.c")
    out_path = os.path.join(workdir, "prog")
    with open(c_path, "w") as f:
        f.write(source)
    args = _Args([c_path], [out_path])
    shivyc.main.get_arguments = lambda: args
    error_collector.show = lambda: True
    error_collector.clear()
    rc = shivyc.main.main()
    return rc, out_path


def _run(source):
    rc, out_path = _compile(source)
    assert rc == 0, "compilation failed"
    return subprocess.run([out_path]).returncode


class TestLogicalConstFoldInArraySize(unittest.TestCase):
    def test_and_as_array_size(self):
        self.assertEqual(_run(
            "int main(void){ int a[(1 && 1) ? 3 : 7];"
            " return sizeof(a)/sizeof(int); }"), 3)

    def test_or_as_array_size(self):
        self.assertEqual(_run(
            "int main(void){ int a[(0 || 1) ? 3 : 7];"
            " return sizeof(a)/sizeof(int); }"), 3)

    def test_and_false_branch(self):
        self.assertEqual(_run(
            "int main(void){ int a[(1 && 0) ? 3 : 7];"
            " return sizeof(a)/sizeof(int); }"), 7)


class TestLogicalConstFoldInBitfield(unittest.TestCase):
    def test_and_bitfield_width(self):
        # Width folds to 2; the struct holds the 2-bit field.
        self.assertEqual(_run(
            "struct S { unsigned x : ((1 && 1) ? 2 : -1); };\n"
            "int main(void){ struct S s; s.x = 3; return s.x; }"), 3)

    def test_static_assert_idiom_holds(self):
        # The glibc _Static_assert shape: matching offsets -> width 2 -> OK.
        rc, _ = _compile(
            "struct A { char a; int b; int c; };\n"
            "struct B { char a; int b; int c; };\n"
            "struct S { unsigned x : ("
            "(__builtin_offsetof(struct A,b)==__builtin_offsetof(struct B,b)"
            " && __builtin_offsetof(struct A,c)==__builtin_offsetof(struct B,c))"
            " ? 2 : -1); };\n"
            "int main(void){ return 0; }\n")
        self.assertEqual(rc, 0)

    def test_static_assert_idiom_fails_compile(self):
        # When the condition is false the width is -1, which must be rejected
        # (this is exactly how the assert reports a layout violation).
        rc, _ = _compile(
            "struct S { unsigned x : ((1 && 0) ? 2 : -1); };\n"
            "int main(void){ return 0; }\n")
        self.assertNotEqual(rc, 0)


class TestShortCircuitFolding(unittest.TestCase):
    def test_and_short_circuits_nonconstant_right(self):
        # `0 && (non-constant)` folds to 0 even though the right side is not a
        # constant -- so it is usable as an array size.
        self.assertEqual(_run(
            "int main(void){ int n = 5; int a[(0 && n) ? 3 : 7];"
            " return sizeof(a)/sizeof(int); }"), 7)

    def test_or_short_circuits_nonconstant_right(self):
        self.assertEqual(_run(
            "int main(void){ int n = 5; int a[(1 || n) ? 3 : 7];"
            " return sizeof(a)/sizeof(int); }"), 3)


class TestRuntimeSemanticsUnchanged(unittest.TestCase):
    def test_runtime_and_or_and_short_circuit(self):
        # Non-constant operands keep ordinary runtime behavior, including
        # short-circuit suppression of side effects.
        self.assertEqual(_run(
            "int calls;\n"
            "int side(int v){ calls++; return v; }\n"
            "int main(void){\n"
            "  int r = 0;\n"
            "  if (side(1) && side(2)) r += 1;     /* both called, true */\n"
            "  if (side(0) && side(9)) r += 100;   /* 2nd skipped, false */\n"
            "  if (side(5) || side(9)) r += 8;     /* 2nd skipped, true */\n"
            "  if (side(0) || side(0)) r += 100;   /* both called, false */\n"
            "  /* calls: 2 + 1 + 1 + 2 = 6 ; r = 1 + 8 = 9 ; total 15 */\n"
            "  return r + calls;\n"
            "}"), 15)


if __name__ == "__main__":
    unittest.main()

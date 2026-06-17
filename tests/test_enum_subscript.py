"""Tests for enum constants used directly inside an array subscript.

An enum constant is an rvalue. Array subscripting probes each operand's lvalue
to decide which side is the array, and a bare enum-constant identifier
(`a[ARG_sep]`) was wrongly reported "undeclared" because it has no lvalue. This
is the `u.args[ARG_sep]` pattern from micropython's `modbuiltins.c`, where the
index comes from a block-scope `enum { ARG_sep, ... }`.
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


class TestEnumSubscript(unittest.TestCase):
    def test_enum_const_as_subscript(self):
        self.assertEqual(_run(
            "int main(void){\n"
            "  enum { A, B, C };\n"
            "  int a[3]; a[A] = 7;\n"
            "  return a[A];\n"
            "}"), 7)

    def test_nonzero_enum_const_as_subscript(self):
        self.assertEqual(_run(
            "int main(void){\n"
            "  enum { A, B, C };\n"
            "  int a[3]; a[B] = 9; a[C] = 4;\n"
            "  return a[B] + a[C];\n"
            "}"), 13)

    def test_commutative_subscript(self):
        # `A[a]` is equivalent to `a[A]` in C.
        self.assertEqual(_run(
            "int main(void){\n"
            "  enum { A, B, C };\n"
            "  int a[3]; A[a] = 5;\n"
            "  return A[a];\n"
            "}"), 5)

    def test_global_enum_const_as_subscript(self):
        self.assertEqual(_run(
            "enum { A, B, C };\n"
            "int main(void){ int a[3]; a[A] = 8; return a[A]; }"), 8)

    def test_modbuiltins_pattern(self):
        # The shape from modbuiltins.c: a struct-member array indexed by a
        # block-scope enum constant.
        self.assertEqual(_run(
            "struct args { int u_int; };\n"
            "int main(void){\n"
            "  enum { ARG_sep, ARG_end, ARG_file };\n"
            "  struct args args[3];\n"
            "  args[ARG_sep].u_int = 11;\n"
            "  args[ARG_end].u_int = 22;\n"
            "  return args[ARG_sep].u_int + args[ARG_end].u_int;\n"
            "}"), 33)

    def test_address_of_enum_const_rejected(self):
        # An enum constant has no lvalue, so taking its address is an error.
        rc, _ = _compile(
            "int main(void){ enum { A }; int *p = &A; return *p; }")
        self.assertNotEqual(rc, 0)

    def test_undeclared_subscript_still_errors(self):
        rc, _ = _compile(
            "int main(void){ int a[3]; a[NOPE] = 1; return 0; }")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

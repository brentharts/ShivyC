"""Tests for compile-time folding of `&a == &b` and `&a != &b`.

Distinct named objects have distinct addresses, and an object's address equals
itself, so comparing the addresses of two whole named objects is a compile-time
constant. micropython's `obj.c`/`objdict.c`/`objint.c` rely on this through the
address-comparison build-assert `sizeof(char[1 - 2 * !(&A != &B)])`, which only
compiles if `&A != &B` folds to a constant.

Only addresses of whole named objects fold; genuine runtime pointer compares
(e.g. of two pointer parameters) are left alone.
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


class TestAddrCompareFold(unittest.TestCase):
    def test_distinct_globals_not_equal_as_array_size(self):
        # `&a != &b` folds to 1, so it is usable as an array dimension.
        self.assertEqual(_run(
            "int a; int b;\n"
            "int main(void){ int arr[(&a != &b) ? 3 : 7];"
            " return sizeof(arr)/sizeof(int); }"), 3)

    def test_distinct_globals_equal_is_false(self):
        self.assertEqual(_run(
            "int a; int b;\n"
            "int main(void){ int arr[(&a == &b) ? 3 : 7];"
            " return sizeof(arr)/sizeof(int); }"), 7)

    def test_same_object_is_equal(self):
        self.assertEqual(_run(
            "int a;\n"
            "int main(void){ int arr[(&a == &a) ? 5 : 9];"
            " return sizeof(arr)/sizeof(int); }"), 5)

    def test_distinct_locals_not_equal(self):
        self.assertEqual(_run(
            "int main(void){ int x; int y; int arr[(&x != &y) ? 4 : 8];"
            " return sizeof(arr)/sizeof(int); }"), 4)

    def test_build_assert_idiom_holds(self):
        # The micropython idiom: distinct objects -> dimension 1 -> compiles.
        self.assertEqual(_run(
            "int a; int b;\n"
            "int main(void){\n"
            "  char c[1 - 2 * !((&a) != &b)];\n"
            "  return sizeof(c);\n"
            "}"), 1)

    def test_build_assert_idiom_violation_fails_compile(self):
        # Comparing an object's address with itself for inequality is false, so
        # the dimension is `1 - 2*1 = -1`: the assert fires (compile error).
        rc, _ = _compile(
            "int a;\n"
            "int main(void){ char c[1 - 2 * !((&a) != &a)]; return sizeof(c); }")
        self.assertNotEqual(rc, 0)

    def test_runtime_pointer_compare_not_folded(self):
        # Pointers that are not addresses of named objects keep runtime
        # comparison semantics.
        self.assertEqual(_run(
            "int a; int b;\n"
            "int cmp(int *p, int *q){ return p != q; }\n"
            "int main(void){ return cmp(&a, &b) + cmp(&a, &a); }"), 1)


if __name__ == "__main__":
    unittest.main()

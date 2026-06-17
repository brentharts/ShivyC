"""Tests for GNU zero-length arrays (`T member[0];`).

micropython's bytecode state uses a trailing `mp_obj_t state[0];` as a
variable-length member (the GNU spelling of a flexible array member), so the
core `runtime.c`/`vm.c`/`objstr.c` translation units do not compile without it.
A zero-length array is a *complete* type that occupies no storage.

These assertions are self-consistent rather than gcc-comparing, because ShivyCX
lays struct members out packed (see tests/test_offsetof.py). The invariant a
zero-length trailing member must satisfy in any layout is that it contributes no
storage: the struct's size is unchanged and the member sits at the end.
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


def _run(source):
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
    assert rc == 0, "compilation failed"
    return subprocess.run([out_path]).returncode


class TestZeroLengthArray(unittest.TestCase):
    def test_standalone_size_is_zero(self):
        self.assertEqual(_run(
            "int main(void){ int a[0]; return sizeof(a); }"), 0)

    def test_trailing_member_adds_no_storage(self):
        # A struct with a trailing zero-length array has the same size as the
        # struct without it.
        self.assertEqual(_run(
            "struct A { int n; };\n"
            "struct B { int n; int items[0]; };\n"
            "int main(void){ return sizeof(struct A) == sizeof(struct B); }"),
            1)

    def test_member_sits_at_end(self):
        # The zero-length member's offset equals the struct size: it occupies
        # the one-past-the-end position and contributes nothing.
        self.assertEqual(_run(
            "struct B { char c; int n; long items[0]; };\n"
            "int main(void){\n"
            "  struct B b;\n"
            "  long off = (char*)&b.items[0] - (char*)&b;\n"
            "  return off == (long)sizeof(struct B);\n"
            "}"), 1)

    def test_usable_as_flexible_array(self):
        # The classic use: a header struct plus a trailing run of elements laid
        # out in caller-provided storage. Writing/reading through the member
        # round-trips.
        self.assertEqual(_run(
            "struct vec { int len; int data[0]; };\n"
            "int main(void){\n"
            "  char buf[64];\n"
            "  struct vec *v = (struct vec*)buf;\n"
            "  v->len = 3;\n"
            "  v->data[0] = 10; v->data[1] = 20; v->data[2] = 12;\n"
            "  return v->data[0] + v->data[1] + v->data[2];\n"
            "}"), 42)

    def test_negative_size_still_rejected(self):
        # Only zero is newly allowed; a negative dimension is still an error.
        with self.assertRaises(AssertionError):
            _run("int main(void){ int a[-1]; return sizeof(a); }")


if __name__ == "__main__":
    unittest.main()

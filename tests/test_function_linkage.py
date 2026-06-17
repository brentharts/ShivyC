"""Tests for function linkage inheritance (C11 6.2.2).

A function declared with no storage-class specifier is treated "as if extern"
(6.2.2p5) and so takes the linkage of a prior visible declaration (6.2.2p4).
That makes the common idiom of a `static` forward declaration followed by a
plain definition keep internal linkage instead of being rejected as a linkage
conflict -- micropython's `objlist.c` forward-declares
`static mp_obj_t mp_obj_new_list_iterator(...)` and later defines it without
`static`.

Genuine internal/external conflicts must still be rejected.
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


class TestFunctionLinkage(unittest.TestCase):
    def test_static_decl_then_plain_definition(self):
        # The micropython idiom: forward-declared static, defined without
        # `static`. The definition inherits internal linkage; compiles & runs.
        self.assertEqual(_run(
            "static int helper(int x);\n"
            "int use(void){ return helper(21); }\n"
            "int helper(int x){ return x * 2; }\n"
            "int main(void){ return use(); }\n"), 42)

    def test_plain_decl_then_plain_definition(self):
        self.assertEqual(_run(
            "int f(int x);\n"
            "int f(int x){ return x + 1; }\n"
            "int main(void){ return f(41); }\n"), 42)

    def test_static_definition_then_plain_redeclaration(self):
        # A plain redeclaration after a static definition keeps internal
        # linkage (no conflict).
        self.assertEqual(_run(
            "static int f(void){ return 42; }\n"
            "int f(void);\n"
            "int main(void){ return f(); }\n"), 42)

    def test_extern_decl_then_static_definition_conflicts(self):
        # extern (external) then static (internal) is a genuine conflict.
        rc, _ = _compile(
            "extern int f(void);\n"
            "static int f(void){ return 1; }\n"
            "int main(void){ return f(); }\n")
        self.assertNotEqual(rc, 0)

    def test_plain_definition_then_static_decl_conflicts(self):
        # A plain definition has external linkage; a later static declaration
        # conflicts.
        rc, _ = _compile(
            "int f(void){ return 1; }\n"
            "static int f(void);\n"
            "int main(void){ return f(); }\n")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

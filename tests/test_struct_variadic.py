"""SysV struct-by-value in the variadic ABI, and member access on a struct
rvalue.

These exercise the codegen paths that let ShivyCX compile its own object-model
runtime: a variadic function whose named parameter is a 9..16-byte struct (so
``va_start`` must skip the struct's two eightbyte slots, not one), and the
``.`` operator applied to a struct *rvalue* (a call or ``?:`` result), which the
generated constructor trampolines rely on. Each result is checked against gcc.
"""
import os
import shutil
import subprocess
import tempfile
import unittest


def _run(cc_argv, src):
    d = tempfile.mkdtemp()
    c = os.path.join(d, "t.c")
    out = os.path.join(d, "t")
    with open(c, "w") as f:
        f.write(src)
    if subprocess.run(cc_argv(c, out),
                      capture_output=True).returncode != 0:
        return None
    return subprocess.run([out]).returncode


def _shivyc_run(src):
    return _run(lambda c, o: ["shivyc", "--no-cache", c, "-o", o], src)


def _gcc_run(src):
    if not shutil.which("gcc"):
        return None
    return _run(lambda c, o: ["gcc", c, "-o", o], src)


class TestStructVariadicAndRvalue(unittest.TestCase):
    def _matches_gcc(self, src):
        ref = _gcc_run(src)
        if ref is None:
            self.skipTest("gcc unavailable")
        got = _shivyc_run(src)
        self.assertIsNotNone(got, "shivyc failed to compile/run")
        self.assertEqual(got & 0xff, ref & 0xff)

    def test_variadic_struct_named_param(self):
        # `p` occupies two eightbyte slots; the varargs begin after p(2)+n(1).
        self._matches_gcc(
            "typedef struct { long a, b; } Pair;\n"
            "long sumv(Pair p, int n, ...){\n"
            "  long s = p.a + p.b;\n"
            "  __builtin_va_list ap; __builtin_va_start(ap, n);\n"
            "  for (int i=0;i<n;i++) s += __builtin_va_arg(ap, long);\n"
            "  __builtin_va_end(ap); return s; }\n"
            "int main(){ Pair p; p.a=10; p.b=20;\n"
            "  return (int)sumv(p, 3, 1L, 2L, 3L); }\n")   # 30 + 6 = 36

    def test_member_on_call_rvalue(self):
        self._matches_gcc(
            "typedef struct { long a, b; } Pair;\n"
            "Pair mk(long x, long y){ Pair r; r.a=x; r.b=y; return r; }\n"
            "int main(){ return (int)(mk(5, 9).a + mk(5, 9).b); }\n")  # 14

    def test_member_on_ternary_rvalue(self):
        self._matches_gcc(
            "typedef struct { long a, b; } Pair;\n"
            "Pair mk(long x, long y){ Pair r; r.a=x; r.b=y; return r; }\n"
            "int main(){ Pair a=mk(1,2), b=mk(3,4); int c=1;\n"
            "  return (int)((c ? a : b).a + (c ? a : b).b); }\n")  # 1 + 2 = 3

    def test_union_rvalue_member(self):
        # The runtime's `obj` is a tagged union returned by value and then
        # member-accessed; this mirrors that shape.
        self._matches_gcc(
            "typedef struct { unsigned char tag;"
            " union { long i; double d; } u; } obj;\n"
            "obj mki(long v){ obj r; r.tag=1; r.u.i=v; return r; }\n"
            "int main(){ return (int)mki(42).u.i; }\n")   # 42


if __name__ == "__main__":
    unittest.main()

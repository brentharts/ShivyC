"""SIMD via contracts: a numpy-style int reduction.

`ptr: "int*"` is a real C array (native indexing, not a boxed list). The two
leading `len(ptr)` asserts are *contracts*: py2c lowers them to ShivyCX contract
clauses placed between the parameter list and the body. ShivyCX proves them at
each call site (it reads the literal allocation size and call length), and
because it then knows the length is a multiple of the 4-wide SSE2 int lane and
at least 64, it replaces the scalar loop with a vectorized reduction -- no
scalar remainder, no runtime guard.

Build it with ./build_simd.sh (compiles with ShivyCX and shows the SSE2 asm).
"""


def array_sum(ptr: "int*", n) -> int:
    assert len(ptr) % 4 == 0
    assert len(ptr) >= 64
    total = 0
    i = 0
    while i < n:
        total = total + ptr[i]
        i = i + 1
    return total

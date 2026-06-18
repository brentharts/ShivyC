"""Element-wise float32 kernels that ShivyCX itself vectorizes to mulps/addps.

These take real 32-bit `f32*` arrays plus a length `n`. Each leading
`assert len(.) % 4 == 0` is a contract: ShivyCX proves it at the call site
(literal malloc size + literal length) and then -- via the element-wise SSE
synthesizer in shivyc/simd_contracts.py -- replaces the scalar loop with a
packed-single SSE kernel (movups load, mulps/addps/subps, movups store), with
no scalar remainder. No gcc auto-vectorizer involved; this is ShivyCX's own
codegen. Build with ./build_vec_simd.sh.
"""


def vadd(a: "f32*", b: "f32*", out: "f32*", n) -> None:
    assert len(a) % 4 == 0
    i = 0
    while i < n:
        out[i] = a[i] + b[i]
        i = i + 1


def vmul(a: "f32*", b: "f32*", out: "f32*", n) -> None:
    assert len(a) % 4 == 0
    i = 0
    while i < n:
        out[i] = a[i] * b[i]
        i = i + 1


def saxpy(alpha: "f32", x: "f32*", y: "f32*", out: "f32*", n) -> None:
    assert len(x) % 4 == 0
    i = 0
    while i < n:
        out[i] = alpha * x[i] + y[i]
        i = i + 1

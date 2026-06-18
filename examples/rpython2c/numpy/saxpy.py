"""Element-wise float32 kernels -> native C that vectorizes to mulps/addps.

`f32*` is a real 32-bit C float array (numpy float32), so the element-wise
expressions lower to clean packed-float-friendly C and gcc -O3 turns them into
SSE `mulps`/`addps`. The `-> None` kernels mutate `out` in place.

`vadd256` takes fixed-size `f32[256]` arrays and writes NO assert: py2c infers
the SIMD-divisibility contract straight from the known element count (256 is a
multiple of the 4-wide single-precision lane), so the user never spells it out.
"""


def saxpy(alpha: "f32", x: "f32*", y: "f32*", out: "f32*", n) -> None:
    assert len(x) % 4 == 0
    i = 0
    while i < n:
        out[i] = alpha * x[i] + y[i]
        i = i + 1


def vadd(x: "f32*", y: "f32*", out: "f32*", n) -> None:
    assert len(x) % 4 == 0
    i = 0
    while i < n:
        out[i] = x[i] + y[i]
        i = i + 1


def vadd256(x: "f32[256]", y: "f32[256]", out: "f32[256]") -> None:
    i = 0
    while i < 256:
        out[i] = x[i] + y[i]
        i = i + 1

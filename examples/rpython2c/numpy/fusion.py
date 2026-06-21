"""NumPy operator fusion (à la Codon's core-numpy-fusion), the rpython way.

A whole-array elementwise store

    out[:n] = (x - 1.0)**2 + (y - 1.0)**2 < 1.0

compiles to a SINGLE C loop with no intermediate array temporaries -- operator
fusion and memory-allocation elision in one step. A naive evaluator would
allocate a temporary array for each of `x-1`, `(x-1)**2`, `y-1`, `(y-1)**2`,
their sum, and the comparison (six full passes + six allocations); the fused
form makes one pass and allocates nothing. ShivyCX then vectorizes the loop.

No language changes are needed (unlike Codon's `@par` / `@gpu.kernel`): `out[:] =
expr` and `out[:n] = expr` are ordinary NumPy in-place stores. Supported leaves
are native scalar arrays (`f64*`, `f32*`, `i32*`, fixed-size `T[N]`) and scalars;
supported ops are `+ - * / **`, comparisons, unary `-`, and the libm ufuncs
(`sqrt`, `exp`, `sin`, ...). Set `PY2C_NPFUSE_VERBOSE=1` to print each fused
expression and its cost (mirroring Codon's `-npfuse-verbose`).

This program builds a grid, computes the unit-circle membership mask two ways --
fused and with an explicit loop -- and exits with the inside-count (97) only if
they agree, proving fusion is behavior-preserving.
"""


def fused_mask(x: "f64*", y: "f64*", out: "f64*", n) -> None:
    out[:n] = (x - 1.0)**2 + (y - 1.0)**2 < 1.0          # one fused pass


def manual_mask(x: "f64*", y: "f64*", out: "f64*", n) -> None:
    i = 0
    while i < n:
        dx = x[i] - 1.0
        dy = y[i] - 1.0
        out[i] = float(dx * dx + dy * dy < 1.0)
        i = i + 1


def main() -> int:
    N = 900
    x: "f64*" = malloc(N * 8)
    y: "f64*" = malloc(N * 8)
    mf: "f64*" = malloc(N * 8)
    mm: "f64*" = malloc(N * 8)
    i = 0
    while i < N:
        x[i] = (i % 30) / 15.0
        y[i] = (i // 30) / 15.0
        i = i + 1
    fused_mask(x, y, mf, N)
    manual_mask(x, y, mm, N)
    inside = 0
    mism = 0
    i = 0
    while i < N:
        inside = inside + int(mf[i])
        if int(mf[i]) != int(mm[i]):
            mism = mism + 1
        i = i + 1
    if mism != 0:
        return 200 + mism            # fused disagreed with manual -> signal
    return inside % 200              # 97


if __name__ == "__main__":
    import sys
    sys.exit(main())

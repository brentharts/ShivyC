"""numpy-style element-wise math (ufuncs) -> native libm calls, no runtime.

`exp`, `sqrt` (and the rest of libm) now type as `double`, so an expression like
`1.0 / (1.0 + exp(-x[i]))` stays native floating point with no boxing. `f64*`
arrays are real C double arrays. The exit code is a checksum of the results.
"""


def sigmoid(x: "f64*", out: "f64*", n) -> None:
    i = 0
    while i < n:
        out[i] = 1.0 / (1.0 + exp(-x[i]))
        i = i + 1


def l2norm(x: "f64*", n) -> float:
    acc = 0.0
    i = 0
    while i < n:
        acc = acc + x[i] * x[i]
        i = i + 1
    return sqrt(acc)


def main() -> int:
    x: "f64*" = malloc(64 * 8)
    out: "f64*" = malloc(64 * 8)
    i = 0
    while i < 64:
        x[i] = (i - 32) / 8.0
        i = i + 1
    sigmoid(x, out, 64)
    acc = 0.0
    i = 0
    while i < 64:
        acc = acc + out[i]
        i = i + 1
    # sigmoid is symmetric about 0.5, so the 64 samples sum to ~32; add the L2
    # norm of the inputs for good measure.
    return int(acc + l2norm(x, 64)) % 256

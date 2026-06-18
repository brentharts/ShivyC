"""Naive NxN matrix multiply on flat f64 arrays -> native C double loops.

No annotations needed beyond the array types: `i`, `j`, `k`, `n` are int by name
and `acc` is inferred double. Indices are computed (`a[i*n + k]`), exercising
native pointer indexing for arbitrary subscripts. The exit code is the trace of
the product matrix, mod 256.
"""


def matmul(a: "f64*", b: "f64*", c: "f64*", n) -> None:
    i = 0
    while i < n:
        j = 0
        while j < n:
            acc = 0.0
            k = 0
            while k < n:
                acc = acc + a[i * n + k] * b[k * n + j]
                k = k + 1
            c[i * n + j] = acc
            j = j + 1
        i = i + 1


def main() -> int:
    a: "f64*" = malloc(32 * 32 * 8)
    b: "f64*" = malloc(32 * 32 * 8)
    c: "f64*" = malloc(32 * 32 * 8)
    i = 0
    while i < 32 * 32:
        a[i] = (i % 7) * 1.0
        b[i] = (i % 5) * 1.0
        i = i + 1
    matmul(a, b, c, 32)
    acc = 0.0
    i = 0
    while i < 32:
        acc = acc + c[i * 32 + i]      # trace
        i = i + 1
    return int(acc) % 256

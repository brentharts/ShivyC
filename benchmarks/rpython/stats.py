"""Streaming statistics -- floating-point reduction + an in-place sort.

Generates N pseudo-random doubles from a deterministic LCG, then computes the
mean, population variance, standard deviation (Newton sqrt), and the median
(which forces an O(N log N)-ish sort of the sample). Returns a checksum of those
aggregates mod 256, so the four backends must agree on the floating-point result
bit-for-bit. This is the suite's floating-point-reduction benchmark and its only
heavy sort; sample count N is read from argv.

It mirrors the work of the bundled `rpy_stats` library, but is self-contained
(no imports) so the cross-runtime harness runs it directly on every backend.
"""
import sys


def gen(n: int) -> "list[float]":
    """Deterministic LCG -> doubles in [0, 1000)."""
    out: "list[float]" = []
    state: "i64" = 2463534242
    i = 0
    while i < n:
        state = (state * 1103515245 + 12345) & 2147483647
        out.append((state % 1000000) / 1000.0)
        i = i + 1
    return out


def mean(xs: "list[float]", n: int) -> float:
    acc = 0.0
    i = 0
    while i < n:
        acc = acc + xs[i]
        i = i + 1
    return acc / n


def pvariance(xs: "list[float]", m: float, n: int) -> float:
    acc = 0.0
    i = 0
    while i < n:
        d = xs[i] - m
        acc = acc + d * d
        i = i + 1
    return acc / n


def sqrt(x: float) -> float:
    if x <= 0.0:
        return 0.0
    g = x
    i = 0
    while i < 40:
        g = 0.5 * (g + x / g)
        i = i + 1
    return g


def sort_inplace(xs: "list[float]", n: int) -> None:
    # insertion sort -- simple, deterministic, exercises list read/write
    i = 1
    while i < n:
        key = xs[i]
        j = i - 1
        while j >= 0 and xs[j] > key:
            xs[j + 1] = xs[j]
            j = j - 1
        xs[j + 1] = key
        i = i + 1


def median(xs: "list[float]", n: int) -> float:
    sort_inplace(xs, n)
    mid = n // 2
    if n % 2 == 1:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) * 0.5


def main() -> int:
    n = int(sys.argv[1])
    xs = gen(n)
    m = mean(xs, n)
    v = pvariance(xs, m, n)
    sd = sqrt(v)
    md = median(xs, n)         # sorts xs in place
    checksum = m + v * 0.001 + sd + md
    return int(checksum) % 256


if __name__ == "__main__":
    sys.exit(main())

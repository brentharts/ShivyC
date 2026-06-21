"""Fusion behavior-preservation: each fused `out[:]=expr` is checked against an
explicit manual loop computing the same thing. Exits 0 iff every fused kernel
matches its manual twin (so gcc and ShivyCX must both return 0)."""


def f_saxpy(a: float, x: "f64*", y: "f64*", o: "f64*", n) -> None:
    o[:n] = a * x + y


def m_saxpy(a: float, x: "f64*", y: "f64*", o: "f64*", n) -> None:
    i = 0
    while i < n:
        o[i] = a * x[i] + y[i]
        i = i + 1


def f_poly(x: "f64*", o: "f64*", n) -> None:
    o[:n] = (x - 2.0) * (x + 2.0) + x * x


def m_poly(x: "f64*", o: "f64*", n) -> None:
    i = 0
    while i < n:
        o[i] = (x[i] - 2.0) * (x[i] + 2.0) + x[i] * x[i]
        i = i + 1


def f_mask(x: "f64*", o: "f64*", n) -> None:
    o[:n] = -x * x < (0.0 - 1.0)


def m_mask(x: "f64*", o: "f64*", n) -> None:
    i = 0
    while i < n:
        o[i] = float((-x[i] * x[i]) < (0.0 - 1.0))
        i = i + 1


def f_fill(o: "f64*", n) -> None:
    o[:n] = 7.0                       # scalar broadcast fill


def m_fill(o: "f64*", n) -> None:
    i = 0
    while i < n:
        o[i] = 7.0
        i = i + 1


def f_sigmoid(x: "f64*", o: "f64*", n) -> None:
    o[:n] = 1.0 / (1.0 + exp(-x))     # transcendental fusion (libm)


def m_sigmoid(x: "f64*", o: "f64*", n) -> None:
    i = 0
    while i < n:
        o[i] = 1.0 / (1.0 + exp(-x[i]))
        i = i + 1


def f_rms(x: "f64*", y: "f64*", o: "f64*", n) -> None:
    o[:n] = sqrt(x * x + y * y)       # libm sqrt fused with arithmetic


def m_rms(x: "f64*", y: "f64*", o: "f64*", n) -> None:
    i = 0
    while i < n:
        o[i] = sqrt(x[i] * x[i] + y[i] * y[i])
        i = i + 1


def diffcount(a: "f64*", b: "f64*", n) -> int:
    d = 0
    i = 0
    while i < n:
        if int(a[i] * 1000.0) != int(b[i] * 1000.0):
            d = d + 1
        i = i + 1
    return d


def main() -> int:
    N = 256
    x: "f64*" = malloc(N * 8)
    y: "f64*" = malloc(N * 8)
    fo: "f64*" = malloc(N * 8)
    mo: "f64*" = malloc(N * 8)
    i = 0
    while i < N:
        x[i] = (i - 128) / 32.0
        y[i] = i * 0.25
        i = i + 1
    bad = 0
    f_saxpy(3.0, x, y, fo, N); m_saxpy(3.0, x, y, mo, N)
    bad = bad + diffcount(fo, mo, N)
    f_poly(x, fo, N); m_poly(x, mo, N)
    bad = bad + diffcount(fo, mo, N)
    f_mask(x, fo, N); m_mask(x, mo, N)
    bad = bad + diffcount(fo, mo, N)
    f_fill(fo, N); m_fill(mo, N)
    bad = bad + diffcount(fo, mo, N)
    f_sigmoid(x, fo, N); m_sigmoid(x, mo, N)
    bad = bad + diffcount(fo, mo, N)
    f_rms(x, y, fo, N); m_rms(x, y, mo, N)
    bad = bad + diffcount(fo, mo, N)
    return bad                         # 0 iff all fused kernels match manual


if __name__ == "__main__":
    import sys
    sys.exit(main())

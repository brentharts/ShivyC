"""Numerical kernels in restricted Python -> native C doubles.

No annotations: the int drivers (`terms`, `iters`, `steps`, `n`) are int by
name, and the real-valued locals (`acc`, `sign`, `guess`, ...) are inferred as
`double` because they are assigned float literals / divisions. The bodies
become plain C floating-point loops with no boxing.
"""


def pi_leibniz(terms) -> float:
    acc = 0.0
    sign = 1.0
    k = 0
    while k < terms:
        acc = acc + sign / (2.0 * k + 1.0)
        sign = -sign
        k = k + 1
    return acc * 4.0


def e_series(terms) -> float:
    acc = 1.0
    term = 1.0
    k = 1
    while k < terms:
        term = term / k
        acc = acc + term
        k = k + 1
    return acc


def sqrt_newton(n, iters) -> float:
    guess = 1.0
    i = 0
    while i < iters:
        guess = (guess + n / guess) / 2.0
        i = i + 1
    return guess


def logistic_final(steps) -> float:
    r = 3.9
    x = 0.5
    i = 0
    while i < steps:
        x = r * x * (1.0 - x)
        i = i + 1
    return x

"""rpy_stats -- a restricted-Python (rpython) port of the stdlib `statistics`.

A small, dependency-free numeric module in the rpython dialect: every function
takes a `list[float]` (or `list[int]`) and returns a concrete scalar, so py2c.py
lowers each to a plain C loop over an unboxed `double*` with no boxing and no
object core. The results match CPython's `statistics` for the supported subset,
so a transpiled program can be cross-checked against the genuine module.

This is the "more of the standard library, ported one at a time from
micropython" direction applied to the numeric side: `statistics` is pure Python
and maps cleanly onto rpython once the inputs are typed as scalar lists.

Supported subset:
    fsum(xs)                 -- summation
    mean(xs)                 -- arithmetic mean
    median(xs)               -- middle value (sorted), averaged for even n
    pvariance / variance     -- population / sample variance
    pstdev / stdev           -- population / sample standard deviation
    minimum / maximum        -- extremes
    normalize(xs)            -- rescale to [0, 1] (returns a new list[float])
"""


def fsum(xs: "list[float]") -> "float":
    acc = 0.0
    i = 0
    while i < len(xs):
        acc = acc + xs[i]
        i = i + 1
    return acc


def mean(xs: "list[float]") -> "float":
    n = len(xs)
    if n == 0:
        return 0.0
    return fsum(xs) / n


def _sorted_copy(xs: "list[float]") -> "list[float]":
    # insertion sort into a fresh list (stable, no reliance on sorted() over
    # a typed scalar list); keeps the input untouched.
    out: "list[float]" = []
    i = 0
    while i < len(xs):
        out.append(xs[i])
        i = i + 1
    i = 1
    while i < len(out):
        key = out[i]
        j = i - 1
        while j >= 0 and out[j] > key:
            out[j + 1] = out[j]
            j = j - 1
        out[j + 1] = key
        i = i + 1
    return out


def median(xs: "list[float]") -> "float":
    n = len(xs)
    if n == 0:
        return 0.0
    s = _sorted_copy(xs)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) * 0.5


def pvariance(xs: "list[float]") -> "float":
    n = len(xs)
    if n == 0:
        return 0.0
    m = mean(xs)
    acc = 0.0
    i = 0
    while i < n:
        d = xs[i] - m
        acc = acc + d * d
        i = i + 1
    return acc / n


def variance(xs: "list[float]") -> "float":
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    acc = 0.0
    i = 0
    while i < n:
        d = xs[i] - m
        acc = acc + d * d
        i = i + 1
    return acc / (n - 1)


def _sqrt(x: "float") -> "float":
    # Newton's method; avoids depending on a libm import inside the library.
    if x <= 0.0:
        return 0.0
    g = x
    i = 0
    while i < 40:
        g = 0.5 * (g + x / g)
        i = i + 1
    return g


def pstdev(xs: "list[float]") -> "float":
    return _sqrt(pvariance(xs))


def stdev(xs: "list[float]") -> "float":
    return _sqrt(variance(xs))


def minimum(xs: "list[float]") -> "float":
    if len(xs) == 0:
        return 0.0
    m = xs[0]
    i = 1
    while i < len(xs):
        if xs[i] < m:
            m = xs[i]
        i = i + 1
    return m


def maximum(xs: "list[float]") -> "float":
    if len(xs) == 0:
        return 0.0
    m = xs[0]
    i = 1
    while i < len(xs):
        if xs[i] > m:
            m = xs[i]
        i = i + 1
    return m


def normalize(xs: "list[float]") -> "list[float]":
    out: "list[float]" = []
    lo = minimum(xs)
    hi = maximum(xs)
    span = hi - lo
    i = 0
    while i < len(xs):
        if span == 0.0:
            out.append(0.0)
        else:
            out.append((xs[i] - lo) / span)
        i = i + 1
    return out

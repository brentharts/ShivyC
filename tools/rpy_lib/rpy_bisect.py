"""rpy_bisect -- a restricted-Python (rpython) port of the stdlib `bisect`.

Binary-search and sorted-insertion over an unboxed `list[float]`, ported from
micropython-lib's `bisect.py`. py2c lowers each function to a tight C loop over a
`double*`; the search functions are pure (return an index) and the insort
functions mutate the list in place through the typed-list pointer.

Two departures from upstream keep it on the rpython fast path, both behaviour
-preserving:
  * explicit `lo`/`hi` bounds instead of `hi=None` defaults (rpython has no
    `None`-defaulted optional args); thin `*_all` wrappers cover the common
    "whole list" call.
  * insort grows the list with one `append` and shifts right by hand rather than
    calling `list.insert`, so it needs only append + indexed assignment, both of
    which lower directly on a typed list.

Matches CPython's `bisect` for the supported subset, so a transpiled program can
be cross-checked against the genuine module.

API:
    bisect_left(a, x, lo, hi)   -> leftmost insertion index in a[lo:hi]
    bisect_right(a, x, lo, hi)  -> rightmost insertion index in a[lo:hi]
    bisect_left_all(a, x)       -> bisect_left over the whole list
    bisect_right_all(a, x)      -> bisect_right over the whole list
    insort_left(a, x)           -> insert x keeping a sorted (left of equals)
    insort_right(a, x)          -> insert x keeping a sorted (right of equals)
"""


def bisect_right(a: "list[float]", x: "float", lo: "int", hi: "int") -> "int":
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def bisect_left(a: "list[float]", x: "float", lo: "int", hi: "int") -> "int":
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def bisect_right_all(a: "list[float]", x: "float") -> "int":
    return bisect_right(a, x, 0, len(a))


def bisect_left_all(a: "list[float]", x: "float") -> "int":
    return bisect_left(a, x, 0, len(a))


def _shift_in(a: "list[float]", i: "int", x: "float") -> None:
    # grow by one and shift a[i:] right, then drop x into the gap
    a.append(0.0)
    j = len(a) - 1
    while j > i:
        a[j] = a[j - 1]
        j = j - 1
    a[i] = x


def insort_right(a: "list[float]", x: "float") -> None:
    i = bisect_right(a, x, 0, len(a))
    _shift_in(a, i, x)


def insort_left(a: "list[float]", x: "float") -> None:
    i = bisect_left(a, x, 0, len(a))
    _shift_in(a, i, x)

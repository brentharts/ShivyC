"""Helper module compiled as part of a multi-file translation unit. Its internal
`counts` dict is untyped here, but a single profiling run of the whole program
(driven from app.py) observes its key/value types across the module boundary."""


def histogram(values):
    counts = {}                    # profiled cross-module -> dict[int, int]
    for v in values:
        if v in counts:
            counts[v] = counts[v] + 1
        else:
            counts[v] = 1
    peak = 0
    for v in values:
        if counts[v] > peak:
            peak = counts[v]
    return peak * 11

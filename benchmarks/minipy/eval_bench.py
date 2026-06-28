"""Microbenchmark: minipy-native eval vs CPython eval.

Runs a million eval(f"{a}+{b}") calls and accumulates. Build with py2c -> gcc
and compare wall-clock against `python3 eval_bench.py`. The native build routes
eval through minipy's C expression evaluator; CPython runs its full eval().
"""


def main() -> int:
    a = 1
    b = 3
    total = 0
    i = 0
    while i < 1000000:
        v: int = eval(f"{a}+{b}")
        total = total + v
        i = i + 1
    print(total)
    return 0


main()

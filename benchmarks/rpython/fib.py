"""Recursive Fibonacci -- pure function-call / recursion stress (integers).

Runnable under CPython and PyPy3, and transpilable to C by tools/py2c.py, so the
same program is measured on all four backends. Work size (the Fibonacci index)
is read from argv.
"""
import sys


def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def main() -> int:
    n = int(sys.argv[1])
    return fib(n) % 256


if __name__ == "__main__":
    sys.exit(main())

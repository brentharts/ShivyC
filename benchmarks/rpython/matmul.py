"""Naive n*n integer matrix multiply -- nested lists + multiply/add inner loop.

Builds two deterministic n*n matrices, multiplies them, and returns a checksum
mod 256. Integer-valued so the element lists stay unboxed. Order n from argv.
"""
import sys


def main() -> int:
    n = int(sys.argv[1])
    a = []
    b = []
    i = 0
    while i < n:
        ra = []
        rb = []
        j = 0
        while j < n:
            ra.append((i * 7 + j) % 10)
            rb.append((i + j * 3) % 10)
            j = j + 1
        a.append(ra)
        b.append(rb)
        i = i + 1
    total = 0
    i = 0
    while i < n:
        ai = a[i]
        j = 0
        while j < n:
            s = 0
            k = 0
            while k < n:
                s = s + ai[k] * b[k][j]
                k = k + 1
            total = total + s
            j = j + 1
        i = i + 1
    return total % 256


if __name__ == "__main__":
    sys.exit(main())

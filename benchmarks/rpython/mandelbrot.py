"""Mandelbrot escape-time -- floating-point + nested loops, no allocation.

Sums the escape iteration counts over a size*size grid (a deterministic integer
the four backends must agree on). Grid size is read from argv.
"""
import sys


def main() -> int:
    size = int(sys.argv[1])
    total = 0
    y = 0
    while y < size:
        ci = 2.0 * y / size - 1.0
        x = 0
        while x < size:
            cr = 2.0 * x / size - 1.5
            zr = 0.0
            zi = 0.0
            i = 0
            while i < 100:
                zr2 = zr * zr
                zi2 = zi * zi
                if zr2 + zi2 > 4.0:
                    break
                zi = 2.0 * zr * zi + ci
                zr = zr2 - zi2 + cr
                i = i + 1
            total = total + i
            x = x + 1
        y = y + 1
    return total % 256


if __name__ == "__main__":
    sys.exit(main())

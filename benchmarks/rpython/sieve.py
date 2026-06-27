"""Sieve of Eratosthenes -- list indexing + integer loops (memory-touching).

Counts primes below N (read from argv) and returns the count mod 256. The bool
list makes this the allocation/working-set benchmark of the set.
"""
import sys


def main() -> int:
    n = int(sys.argv[1])
    flags = [True] * n
    count = 0
    i = 2
    while i < n:
        if flags[i]:
            count = count + 1
            j = i + i
            while j < n:
                flags[j] = False
                j = j + i
        i = i + 1
    return count % 256


if __name__ == "__main__":
    sys.exit(main())

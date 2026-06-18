"""Restricted-Python numeric kernels (no annotations).

These rely only on rpython's name-based type defaults: `n`, `limit`, `count`,
`i`, `total` etc. are inferred as int, so the loops transpile to native C with
no boxing. Run examples/rpython2c/build.sh numpy/vectorize to see the C and a
gcc-vs-ShivyCX timing.
"""


def sum_squares(n):
    total = 0
    i = 1
    while i <= n:
        total = total + i * i
        i = i + 1
    return total


def count_primes(limit):
    count = 0
    n = 2
    while n < limit:
        d = 2
        is_prime = 1
        while d * d <= n:
            if n % d == 0:
                is_prime = 0
                d = n
            d = d + 1
        count = count + is_prime
        n = n + 1
    return count

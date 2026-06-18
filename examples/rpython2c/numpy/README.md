# numpy — restricted numeric kernels -> native C

Working examples of unannotated (or lightly-annotated) restricted Python that
transpiles to **direct C** — native `int`/`double` loops with no boxing. Run any
of them with the bundled harness:

    ./run.sh vectorize.py main.c          # integer kernels
    ./run.sh numerics.py numerics_main.c  # floating-point kernels

## vectorize.py — integer kernels

`sum_squares(n)` and `count_primes(limit)`. The drivers `n`/`limit`/`count`/`i`
are `int` purely by name, so the loops lower to native C integer arithmetic.

## numerics.py — floating-point kernels

`pi_leibniz`, `e_series`, `sqrt_newton`, `logistic_final`. The int drivers
(`terms`, `iters`, `steps`) are int by name; the real-valued locals (`acc`,
`sign`, `guess`, `x`, ...) are inferred as `double` because they are assigned
float literals or divisions — Python's `/` is always float division. So e.g.
`sqrt_newton` becomes:

```c
double sqrt_newton(int n, int iters) {
    double guess = 1.0; int i = 0;
    while (i < iters) { guess = (guess + ((double)n / (double)guess)) / 2.0; i = i + 1; }
    return guess;
}
```

Only the return types are annotated (`-> float`); everything else is inferred.
Sample output:

    pi_leibniz(1e7)   = 3.141593
    e_series(20)      = 2.718282
    sqrt_newton(2,50) = 1.414214

## Not yet: true ndarray ops

Vector/matrix kernels over `list[float]` still lower to the dynamic `obj` path
(typed-array lowering, `list[float]` -> `double*`, is the next step). Today's
fast path is scalar int/float numerics, which already covers a lot of numerical
code (series, iterative solvers, maps).

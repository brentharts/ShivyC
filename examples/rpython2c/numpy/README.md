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

## simd_sum.py — numpy arrays -> SSE2 via contracts

`array_sum(ptr: "int*", n)` takes a *real* C array (`int*`, native indexing —
not a boxed list), and its two leading `assert len(ptr) ...` lines are length
**contracts**. py2c lowers them to ShivyCX contract clauses between the
parameter list and the body:

```c
int array_sum(int* ptr, int n)
assert not len(ptr) % 4
assert len(ptr) >= 64
{ ... scalar reduction loop ... }
```

ShivyCX proves these at each call site (reading the literal allocation size and
call length), so it knows the length is a multiple of the 4-wide SSE2 int lane
and at least 64 — and replaces the scalar loop with a vectorized reduction with
no scalar remainder and no runtime guard. `./build_simd.sh` shows it:

```
simd-contracts: 'array_sum': contracts proven at all 1 call site(s)
   3 paddd   2 movdqa   1 movdqu   2 psrldq   1 pxor
exit code = 60   (reduction result correct)
```

Strip the two asserts and the identical loop compiles to ShivyCX's ordinary
scalar code (0 SSE instructions) — the contracts are exactly the AST-level size
information that unlocks the SIMD path. (Note: ShivyCX's variadic `printf` ABI
is unreliable, so the harness verifies the result via the exit code instead.)

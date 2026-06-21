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

## saxpy.py — element-wise float32 kernels -> mulps/addps

`saxpy` and `vadd` operate on `f32*` (real 32-bit C float arrays, i.e. numpy
float32). The element-wise expressions lower to native packed-float-friendly C:

```c
void saxpy(float alpha, float* x, float* y, float* out, int n) { ...
    out[i] = ((alpha * x[i]) + y[i]); }
void vadd(float* x, float* y, float* out, int n) { ...
    out[i] = (x[i] + y[i]); }
```

`./build_saxpy.sh` compiles that with `gcc -O3` and shows the SSE single-
precision instructions, then runs for correctness:

```
== SSE single-precision instructions emitted ==
   5 addps   2 mulps   9 movups   2 movaps
saxpy: out[100]=500.0   vadd: out[100]=300.0
```

### Contracts inferred from a fixed size — no assert needed

`vadd256(x: "f32[256]", y: "f32[256]", out: "f32[256]")` writes **no** assert.
Because the element count 256 is a compile-time multiple of the 4-wide single-
precision lane, py2c infers the divisibility + minimum-length contracts itself:

```c
void vadd256(float* x, float* y, float* out)
assert not len(x) % 4
assert len(x) >= 4
...   (same for y and out)
```

This is the "the user shouldn't have to write the assert" step: a known array
size at the AST level is enough for py2c to emit the SIMD contract.

### Honest scope: ShivyCX vs gcc for these kernels

The packed-float result above is from `gcc -O3`. ShivyCX's own contract
vectorizer (`shivyc/simd_contracts.py`) currently recognizes exactly one shape:
the `(int* ptr, len)` **reduction**, which it replaces with a hand-written SSE2
`paddd` loop (see `simd_sum.py`). It does not yet vectorize multi-array float
element-wise stores — two things are missing and are the clear next compiler-
side step:

1. `_prove_one_call` assumes a 2-argument `(ptr, len)` signature
   (`call.args[1 - arg_index]`), so a `(alpha, x, y, out, n)` kernel can't be
   proven; it needs to locate the length argument among >2 args and prove every
   pointer argument.
2. `synth_sse2_reduce` is a fixed int32-reduction template; an element-wise
   `synth_sse_elementwise` (load `movups`, op `mulps`/`addps`, store `movups`)
   would be a new synthesizer.

So today: py2c emits the vectorizable float C + the contracts, gcc -O3 turns it
into `mulps`/`addps`, and the ShivyCX path is wired for the int reduction with
the float element-wise synthesizer identified as the next addition.

## vec_simd.py — ShivyCX *itself* vectorizes float element-wise kernels

Earlier (`saxpy.py`) the packed-float result came from `gcc -O3`; ShivyCX's own
contract vectorizer only handled the int reduction. That gap is now closed.
`shivyc/simd_contracts.py` gained:

- a **multi-argument proof** (`_prove_one_call_multi`): a kernel like
  `saxpy(alpha, x, y, out, n)` has several pointer arguments and one length, so
  the old 2-arg `(ptr, len)` assumption is replaced by one that finds the length
  argument and proves *every* pointer traces to a large-enough allocation;
- an element-wise **classifier** (`_classify_elementwise`) recognizing
  `out[i] = a[i] {+,-,*} b[i]` and `out[i] = alpha*x[i] + y[i]`; and
- a packed-SSE **synthesizer** (`synth_sse_elementwise`) that emits a
  fallback-free loop: `movups` load, `mulps`/`addps`/`subps` (or `pd` for
  doubles), `movups` store, with `shufps`/`unpcklpd` to broadcast the saxpy
  scalar.

`./build_vec_simd.sh` compiles the f32 `vadd`/`vmul`/`saxpy` kernels **with
ShivyCX** (no gcc auto-vectorizer) and shows its output:

```
simd-contracts: 'vadd'/'vmul'/'saxpy': contracts proven at all 1 call site(s)
   2 addps   2 mulps   1 shufps   9 movups
exit code = 30   (vadd=30, vmul=200, saxpy=50)
```

`f32`/`f64`/`i32` are numpy-style dtype annotations: `f32*` is a real 32-bit
float array (single precision, 4-wide → `mulps`/`addps`); `float*` is 64-bit
(2-wide → `mulpd`/`addpd`). The int reduction path (`simd_sum.py`) is unchanged.

## main.py reads .py directly + wider SIMD (simd_kernels.py)

ShivyCX's driver now accepts `.py` sources. `process_py_file` runs them through
`tools/py2c.py`, supplies any runtime support C (and drops the unused runtime
include for pure kernels, re-adding just the libc prototypes used), then
compiles and links. So an rpython example needs no hand-written `.c` copy and no
build script:

    python3 -m shivyc.main examples/rpython2c/numpy/simd_kernels.py -o simd && ./simd
    echo $?     # 55

You can also mix sources in one call: `shivyc.main kernels.py harness.c -o run`
(note: the contract proof is per-translation-unit, so a kernel whose only call
site is in a separate `.c` stays correct but scalar; put the call site in the
same `.py` to vectorize).

The contract vectorizer (`shivyc/simd_contracts.py`) grew three shapes beyond
binary `a op b` and saxpy:

- **no-length fixed-size** — `vadd256(x: "f32[256]", ...)` has no `n` argument
  and no assert; py2c infers the contract from the size and ShivyCX proves the
  element count from the allocation, baking a literal trip count into the loop;
- **single-input map** — `out[i] = sqrt(x[i])` lowers to `sqrtps` (the indirect
  libm call is recognized via its `AddrOf`, then replaced wholesale);
- **fused multiply-add** — `out[i] = a[i]*b[i] + c[i]` → `mulps` + `addps`.

The synthesizer is now register-driven (System V assignment computed from the
signature) rather than hardcoded to one shape, so 2-, 3- and 4-pointer kernels
and an fp scalar all land in the right registers. `simd_kernels.py` exercises
all three and returns 55 (= 30 + 4 + 21) as the proof of correctness.

## simd_blas.py — scalar broadcast + dot product

Two more contract-proven kinds in `shivyc/simd_contracts.py`:

- **scalar broadcast** (`scale`): `out[i] = x[i] * s` (also `+`/`-`) where `s`
  is an fp scalar argument. The scalar is splatted across the SSE lanes
  (`shufps`/`unpcklpd`) once, then a single `mulps`/`addps`/`subps` per vector.
- **dot product** (`dot`): `acc += a[i]*b[i]` over a loop returning the
  accumulator. This is a *reduction* with no store: `mulps`/`mulpd` then
  `addps`/`addpd` into a lane accumulator, followed by a horizontal sum, with
  the scalar result returned in `xmm0`. The accumulator and array element type
  must match (use `f64*` for a double accumulator).

```
python3 -m shivyc.main examples/rpython2c/numpy/simd_blas.py -o blas && ./blas
echo $?      # 186   (scale -> 50, dot -> 136)
```

The classifier/synthesizer are register-driven, so the dot's 2-pointers+length
layout (`a=rdi, b=rsi, n=edx`, result in `xmm0`) and the scale's
scalar+2-pointers layout are assigned correctly without any per-kernel
hardcoding. Both honor the same auto-contract path: annotate fixed sizes and the
asserts disappear entirely.

### Kernel shapes ShivyCX now vectorizes from contracts

    reduce   sum += p[i]                 (int)        paddd + horizontal
    dot      acc += a[i]*b[i]            (float)      mulps/pd + horizontal
    binary   out[i] = a[i] {+,-,*} b[i]               addps/subps/mulps
    scale    out[i] = x[i] {+,-,*} s                  broadcast + op
    saxpy    out[i] = s*x[i] + y[i]                   broadcast + mul + add
    fma      out[i] = a[i]*b[i] + c[i]                mul + add
    map      out[i] = sqrt(x[i])                      sqrtps/pd

## Operator fusion (`fusion.py`)

Ported from the good part of Codon's `core-numpy-fusion` pass (minus the C++/LLVM
machinery and the `@par`/`@gpu.kernel` language changes, which rpython rejects). A
whole-array elementwise store

```python
out[:n] = (x - 1.0)**2 + (y - 1.0)**2 < 1.0
```

lowers to a **single** C loop with **no intermediate array temporaries** --
operator fusion and memory-allocation elision together. A naive evaluator would
allocate and traverse a temporary array for each of `x-1`, `(x-1)**2`, `y-1`,
`(y-1)**2`, the sum and the comparison (six passes, six allocations); the fused
form makes one pass and allocates nothing. ShivyCX then vectorizes the resulting
scalar loop.

No language change is involved -- `out[:] = expr` / `out[:n] = expr` are ordinary
NumPy in-place stores. The fused form is detected when the target is a native
scalar array (`f64*`, `f32*`, `i32*`, fixed-size `T[N]`) sliced as `out[:]`
(trip count from a `T[N]` annotation) or `out[:n]` (explicit count, works for raw
pointers). Supported leaves are native arrays and scalars; supported operators
are `+ - * / % **`, the comparisons, bitwise ops, unary `-`, and the libm ufuncs
(`sqrt`, `exp`, `log`, `sin`, `cos`, ...). Small integer powers (`**2`..`**4`) are
expanded to repeated multiplies (so they vectorize and need no `ipow`); other
powers use `pow`. A pure-scalar right-hand side broadcasts (`out[:n] = 7.0`).

The libm ufuncs resolve under **both** gcc and the ShivyCX self-backend. ShivyCX
has no system headers and `shivyc_rt.h` does not pull in `<math.h>`, so a bare
`exp`/`sqrt`/... was previously an *undeclared identifier* in any function that
also touched the runtime (the prototype was only re-supplied for pure kernels
that dropped the runtime header). `shivyc/main.py` now injects the needed libm
prototypes (`_libm_protos`, used by both the runtime and pure-kernel paths)
whenever a math function is referenced, so transcendental fused kernels -- and
ordinary hand-written `exp`/`sqrt` loops -- compile under the self-backend too.

Set `PY2C_NPFUSE_VERBOSE=1` to print each fused expression with the cost the
analysis assigned it (mirroring Codon's `-npfuse-verbose`); the per-op weights
match Codon's model (`+ - *` = 1, `/ % **` = 8, `sqrt` = 2, `exp/log` = 5,
`sin/cos` = 10, ...). Because py2c emits the fused loop directly rather than going
through a temp-array runtime, fusion is always a win here, so the cost is
reported for transparency rather than used to fall back to sequential evaluation.

```
python3 -m shivyc.main examples/rpython2c/numpy/fusion.py -o fusion && ./fusion
echo $?      # 97  (unit-circle membership count; fused result == manual loop)
```

`make testfuse` builds `fusion.py` and `tests/fast/fuse_kernels.py` (saxpy,
polynomial, mask, broadcast-fill, plus the `sigmoid`/`sqrt` transcendental
kernels, each checked against an explicit manual loop) through both gcc and the
ShivyCX self-backend and requires every fused result to match its manual twin.

# Contract-driven, fallback-free SIMD (`shivyc/simd_contracts.py`)

When GCC or Clang auto-vectorizes a reduction like `for (i) v += ptr[i]`, the
generated code includes a scalar **remainder** loop (and sometimes runtime
alignment checks) for the cases where the length is not a multiple of the SIMD
width. Those branches are the "fallback".

If the compiler can see the whole call graph and **prove** that every call
passes a SIMD-aligned array, the remainder can never execute, so it can be
omitted. This pass performs that proof and, for a recognized integer sum
reduction, emits a fallback-free SSE2 loop.

## The contract

```c
int calc_sum(int *ptr, unsigned int len)
assert len(ptr) >= 64
assert not len(ptr) % 4
{
  int v = 0;
  unsigned int i = 0;
  for (i = 0; i < len; i = i + 1) { v = v + ptr[i]; }
  return v;
}
```

`assert not len(ptr) % 4` is the key: it guarantees the element count is a
multiple of 4, which is the SSE2 int32 width.

## Proving from the call graph

For each contract-bearing function, the pass inspects every call site (across
the whole program). For each call it:

1. Resolves the pointer argument back to its allocation by following SET-copies
   to a `malloc(...)` call, and reads that call's **literal byte size**. The
   element count is `bytes / sizeof(element)`.
2. Resolves the length argument to a literal.
3. Checks the count/length against the contracts (`len>=`, `len<=`, `div-by`)
   and confirms the length does not exceed the allocation.

If *every* call site is proven, and the body is a recognized sum reduction
(`acc = acc + ptr[i]` -- a ReadAt feeding an Add whose result is written back
into the accumulator), the function is marked SIMD-safe.

A diagnostic is printed at build time, e.g.:

```
simd-contracts: 'calc_sum': contracts proven at all 1 call site(s);
                scalar fallback omitted
```

or, when a call cannot be proven:

```
simd-contracts: 'calc_sum': not proven (a call site could not be proven
                aligned); keeping scalar code
```

## The emitted code

For a proven reduction, the body is replaced with a hand-written SSE2 loop
(System V: `rdi` = ptr, `esi` = len):

```asm
pxor   xmm0, xmm0          ; 4-lane int32 accumulator
mov    ecx, esi
shr    ecx, 2              ; len / 4 iterations
xor    rax, rax
.loop:
movdqu xmm1, [rdi + rax]
paddd  xmm0, xmm1          ; accumulate 4 ints
add    rax, 16
dec    ecx
jnz    .loop
; horizontal add of the 4 lanes -> eax, then return
```

There is **no scalar tail** -- that is the whole point. Correctness rests on the
proof that `len % 4 == 0`.

## Safety

This is a deliberately narrow, verifiable slice:

* It only vectorizes integer sum reductions whose alignment is *proven*.
* If the proof fails for any reason (non-literal length, unaligned size, a
  pointer not from a literal `malloc`, a body that is not a sum), the function
  keeps ShivyC's ordinary scalar codegen, which is correct for any length.

So enabling contracts never changes a program's result; it only removes the
remainder handling when it is provably dead.

## Verification

* Proven case: `calc_sum` over a `malloc(64*sizeof(int))` buffer of 2s emits
  `paddd` and returns 128, matching gcc.
* Unprovable case (length 70, not a multiple of 4): stays scalar, returns 140,
  matching gcc.
* `tests/test_metamorphic_simd.py::TestSimdContracts` covers both directions;
  `tests/general_tests/extensions/calc_sum.c` is a runnable demo.

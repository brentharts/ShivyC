# Near-function scratch storage (`-O4`)

This realizes the stack-pressure half of `-O4`: a function can keep its
register **spills** in a static per-function buffer instead of on the stack, so
its frame shrinks -- and for a leaf function, disappears entirely.

## Motivation

When the register allocator runs out of registers it spills a temporary to a
stack slot (`[rbp - k]`), which forces a frame (`push rbp; mov rbp, rsp;
sub rsp, N`). For a non-reentrant function, that slot can instead be a fixed
address in a static buffer: the function is never active twice at once, so a
single buffer serves every call. The buffer is touched on every invocation, so
when the function is hot the buffer stays resident in L1 data cache.

```asm
; without -O4                       ; with -O4
compute:                            compute:
    push rbp                            ...  [compute__scratch+0] ...
    mov  rbp, rsp                       ret
    sub  rsp, 16                    .comm compute__scratch, 16
    ...  [rbp-4] ...
    ...
    leave / ret
```

## What is relocated -- and what is not

Only **register spills** move to the buffer. They are compiler-introduced
temporaries whose addresses are never observable, so relocating them cannot
change program behavior.

**Address-taken locals stay on the stack.** A variable whose address is taken
(`&x`) has an observable address, and some programs rely on the stack layout of
adjacent locals (e.g. cross-object pointer arithmetic). Those keep their normal
rbp-relative slots, so their layout and addresses are unchanged.

## Eligibility (safety)

A single static buffer cannot serve two concurrent activations, so a function
qualifies only if it cannot be re-entered. Under `-O4` the compiler selects a
function for near-scratch when, in the direct call graph:

* it is **not reachable from itself** (no direct/transitive recursion), and
* its **address is not taken** (so it cannot be re-entered via an indirect
  call).

Recursive or address-taken functions keep ordinary stack spills and remain
correct.

## Frame interaction

Because the relocated slots are no longer rbp-relative, the function's computed
stack size drops to zero, and the existing frame logic adapts automatically:

* A **leaf** non-reentrant function (no calls) becomes completely frameless --
  no `push rbp`, no `sub rsp` -- using zero stack.
* A **non-leaf** function keeps a minimal `push rbp; mov rbp, rsp` (with
  `sub rsp, 0`) so that `rsp` stays 16-byte aligned across its calls, as the
  System V ABI requires, while its spills still live in the buffer.

## A note on placement

The original sketch was to use writable `.text` padding "just before the
function". A `.comm` BSS symbol is a cleaner equivalent: it is writable,
statically allocated, and L1-resident when hot, but **not executable**, so it
avoids the RWX segment that literal writable-`.text` storage would require. The
performance intent (no stack traffic, cache-resident scratch) is met without
the safety cost. (Metamorphic returns still use a writable+executable section,
because their slot is genuinely self-modified code-adjacent state; see
`METAMORPHIC.md`.)

## Verification

* `compute` (a spill-heavy leaf) returns 253 with and without `-O4`; under
  `-O4` it is frameless with a `.comm compute__scratch` buffer.
* A non-leaf `worker` returns identically and keeps its alignment frame.
* A recursive `fact` is excluded and stays correct (120).
* Address-taken locals (a pointer-arithmetic program) are unaffected.
* The whole feature corpus produces identical results at `-O0` and `-O4`
  (36/36), and `tests/test_near_scratch.py` covers the cases above.
* Demo: `tests/general_tests/extensions/near_scratch.c`.

# Stackless / low-overhead function calls (`-fstackless-calls`)

This is the second of two optimization ideas ported from the
[jitbit](https://gitlab.com/HartsAntler) JIT experiments and the
OpenSourceJesus C-Compiler. The first, `-fsimd-pack-globals`, removed *memory*
traffic for small global flags. This one removes *call* overhead from the
deeply-nested call pattern those projects use as their motivating benchmark:

```c
void sum() { ... }
void foo() { sum(); }
void bar() { foo(); sum(); }
void boo() { bar(); sum(); }
void zoo() { boo(); sum(); }
```

jitbit's thesis is that such chains shouldn't pay full function-call cost at
each level. OSJ chases this with "indexed-jump function calls" and
"metamorphic return sites" -- co-locating small functions and writing return
addresses directly into the code so a return never touches the stack. That
literally requires self-modifying, writable `.text`, which is fragile and
unsafe. This port reaches the same goal -- *returning without spending stack* --
through three standard, behavior-preserving transformations instead.

## What it does

Everything below is opt-in via `-fstackless-calls` and is a pure code-quality
change: the program's observable behavior is identical with the flag on or off.

### 1. Direct calls

ShivyC normally lowers a call to a named function as two steps -- load the
address, then call through the register:

```asm
lea rax, [foo]
call rax
```

When the callee is a statically known function, we drop the address-load and
emit a direct, register-free call:

```asm
call foo
```

The fold is reference-count based, so it also fires when arguments are computed
between the address-load and the call (e.g. `fact(n - 1)`), not just for
no-argument calls.

### 2. Tail-call elimination

When a call sits in tail position -- immediately followed by a return of that
call's value, or both are void -- the frame is torn down and the call becomes a
jump:

```asm
;  foo() { sum(); }   becomes simply:
jmp sum
```

The callee's own `ret` then returns straight to *our* caller. No return address
for this frame is ever pushed: this is the "stackless" core, and it is exactly
the classic tail-call optimization used by functional-language compilers.

### 3. Frame-pointer omission

A function with no stack-resident locals that makes no non-tail call needs no
`rbp` frame at all, so the prologue/epilogue disappears:

```asm
; without the flag            ; with the flag
foo:                          foo:
  push rbp                      jmp sum
  mov rbp, rsp
  sub rsp, 0
  lea rax, [sum]
  call rax
  mov rsp, rbp
  pop rbp
  ret
```

`foo` goes from nine instructions (two of them memory round-trips for the
return address, two more for the saved `rbp`) to one.

## Worked example

For the chain above, `-fstackless-calls` produces:

```asm
foo:
    jmp sum                 ; frameless, no call/ret at all
bar:
    push rbp                ; keeps a frame: it makes a non-tail call
    mov rbp, rsp
    sub rsp, 0
    call foo                ; direct (no lea, no clobbered register)
    mov rsp, rbp            ; tear down, then...
    pop rbp
    jmp sum                 ; ...tail-jump; sum returns to bar's caller
```

## Why it stays correct

* **Tail jumps preserve return semantics.** A frameless function that tail-jumps
  pushes nothing, so the callee's `ret` consumes the return address the original
  `call` pushed and lands in the right place. A framed function restores `rsp`
  and `rbp` *before* the jump.
* **Stack alignment is safe by construction.** A function is only made frameless
  when it issues no `call` (only `jmp`), and a `jmp` doesn't perturb alignment --
  the callee sees exactly the `rsp` the caller did. Functions that do make a
  real `call` keep their frame, which preserves the System V 16-byte alignment
  the ABI requires.
* **Only caller-saved registers are touched.** ShivyC allocates exclusively
  caller-saved GPRs and never uses `rbx`/`r12`-`r15`, so a frameless function
  has nothing it is obliged to preserve.
* **Unsupported shapes fall back.** Calls through a function pointer aren't
  statically known, so they keep the ordinary indirect `call reg` path.

## Verification

* The full suite passes: **109 tests** (`python3 -m unittest discover`),
  including 8 new tests in `tests/test_stackless.py`.
* Differential testing against gcc: the nested chain, value-returning tail
  calls, non-tail recursion (`fact`), and indirect function-pointer calls all
  match. Across the feature-test corpus, 36 programs produce identical results
  with the flag on and off.
* `tests/general_tests/pi/pi.c` (an obfuscated pi generator) produces
  byte-identical output to gcc with the flag on.
* Combining `-fsimd-pack-globals -fstackless-calls` on the flag-packing demo
  still returns 51.

## Benchmark

200,000,000 iterations of the full `foo/bar/boo/zoo` chain (10 calls each, 2
billion calls total), best of three wall-clock runs:

| build                        | time   |
| ---------------------------- | ------ |
| `shivyc` (no flag)           | 2.00 s |
| `shivyc -fstackless-calls`   | 0.57 s |

About a **3.5x speedup** on call-bound code, complementary to the memory-traffic
reduction from `-fsimd-pack-globals`.

## Files

* `shivyc/stackless.py` -- the IL pass (direct-call folding + tail-call marking,
  plus per-function call-structure flags for framelessness).
* `shivyc/il_cmds/control.py` -- `Call` emits direct `call`/tail `jmp`; `Return`
  skips teardown when frameless.
* `shivyc/asm_gen.py` -- finalizes the frameless decision and omits the prologue.
* `shivyc/main.py` -- the `-fstackless-calls` flag and pass invocation.
* `tests/test_stackless.py` -- unit/codegen tests.
* `tests/general_tests/stackless/kernel_calls.c` -- runnable demo (returns 30).

# ShivyCX code-generation optimizations

ShivyCX is a textbook `-O0` code generator: it lowers each IL command in
isolation. That leaves a large, mechanical gap against `gcc -O2`. This document
records the optimizations added to narrow that gap, measured on the C-subset
lexer kernel in `examples/rpython2c/compiler/`.

All optimizations are correctness-preserving and can be disabled with
`--no-peephole` (for the peephole pass) to A/B their effect.

## 1. Index strength reduction for byte-sized elements

`get_size` (`shivyc/tree/utils.py`) used to emit `index * sizeof(elem)` for
every `p[i]`. For `char`/byte arrays `sizeof(elem) == 1`, so this produced a
useless `imul $1` on *every* character access — pervasive in lexers and all
string code. `get_size` now returns the index unscaled when the element size is
1, eliminating the multiply.

## 2. Compare-and-branch fusion (`shivyc/peephole.py`)

A comparison feeding directly into a conditional jump used to compile as:

```
mov  r, 1
cmp  a, b
jcc  L          ; materialize a 0/1 boolean
mov  r, 0
L:
cmp  r, 0       ; ...then test it
je   target
```

i.e. ~6 instructions per loop/`if` test. The peephole detects a `_GeneralCmp`
whose boolean result is consumed solely by the next `JumpZero`/`JumpNotZero`
and fuses them into a single compare-and-branch:

```
cmp  a, b
jge  target     ; one cmp, one jcc -- matches gcc
```

Details and guards:

- The comparison gains a `fuse = (label, negate)` marker. When set, its
  `make_asm` emits `cmp; jcc` and skips the boolean; `outputs()` becomes empty
  and `targets()` reports the branch label so the register allocator's liveness
  analysis still sees the control-flow edge. (Omitting `targets()` corrupts
  liveness and lets the allocator wrongly coalesce a loop-carried value with a
  per-iteration temporary — caught in testing.)
- Each comparison subclass carries both its true-condition jump and its negated
  jump (signed and unsigned), so `JumpZero` (branch when the comparison is
  false) is handled correctly.
- Fusion is skipped when the first operand is a literal (the comparison codegen
  swaps literal-first operands, which would invert an ordering test) and for
  floating-point comparisons.

## 3. Arithmetic identities (`shivyc/peephole.py`)

`x * 1`, `1 * x`, `x + 0`, `0 + x`, and `x - 0` become a plain copy; these arise
from address arithmetic and generic code. Implemented as IL rewrites to `Set`,
so the register coalescer usually removes them entirely.

## Result (lexer kernel, 20000 reps)

| backend  | speedup vs CPython |
|----------|--------------------|
| CPython  | 1.0x               |
| ShivyCX  | ~18.6x  ->  ~20.9x |
| gcc -O2  | ~52x               |

## Remaining gap (future work)

Inspecting the hottest loop (`word_hash`), the rest of the gap to `gcc -O2` is:

- **Loop-invariant code motion.** ShivyCX reloads the loop-invariant global
  `HMOD` from memory on every iteration; gcc loads it once before the loop.
- **Induction-variable strength reduction.** ShivyCX recomputes `base + index`
  (with a sign-extend) each iteration; gcc strength-reduces the index to a
  pointer increment.

Both compilers emit the same `idiv` for the modulo (the divisor is a runtime
global, so neither strength-reduces it), so division is not the differentiator.

### On SIMD / contracts for this kernel

The hash accumulator is a serial recurrence (`h = (h*K + byte) % M`), so the
hot loop is not vectorizable, and no data-parallel divisibility contract (of the
kind the SSE element-wise path uses) applies to a scanner. Realistic SIMD for
lexing means simdjson-style character-class/boundary finding, which is an
algorithmic rewrite rather than a codegen pass. The gap here is scalar codegen
quality, addressed by the optimizations above and the LICM/IV work listed.

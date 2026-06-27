# The ShivyCX AArch64 (ARM64) back end

ShivyCX compiles the same architecture-neutral IL its x86-64 back end consumes
into AArch64 assembly, selected with `--target arm64`. The back end lives
entirely in [`shivyc/asm_gen.py`](shivyc/asm_gen.py) behind a `Target` seam
([`shivyc/targets`](shivyc/targets/__init__.py)); the x86-64 path is untouched by
any of it. This document describes what it supports, how the code generator and
register allocator work, and how every stage was validated.

The whole effort was done at the Python (rpython) level, which is the point: a
new bare-metal ISA back end is a few hundred lines of legible Python, and each
increment is checked the same way — differentially, against a real toolchain.

## Trying it

```sh
# C -> AArch64 assembly
python3 -m shivyc.main prog.c -S -o prog.s --target arm64

# assemble + link + run under qemu (using the GNU cross toolchain)
aarch64-linux-gnu-gcc -static prog.s -o prog
qemu-aarch64 ./prog ; echo "exit: $?"
```

The differential tester does this end to end for a corpus of 130 programs and
checks each exit code against the same program compiled by `gcc`:

```sh
python3 tools/arm64_difftest.py
# arm64 difftest: 130 pass, 0 fail, 0 skip, 0 error
```

## What is supported

- **Integers** of every width: `char`/`short`/`int`/`long`, signed and unsigned,
  with correct narrowing/widening on assignment.
- **Arithmetic and bitwise**: `+ - * / %`, `& | ^ ~`, `<< >>` (logical/arithmetic
  by signedness), unary `-`, with immediate forms where the encoding allows.
- **Comparisons** (the six relational/equality operators) and **control flow**:
  `if`, `while`, `for`, `&&`/`||` short-circuiting — with compare/branch fusion
  (`cmp` + `b.<cc>`) when a comparison feeds only a branch.
- **Floating point**: `float` and `double` arithmetic (`fadd`/`fsub`/`fmul`/
  `fdiv`), comparisons (`fcmp`), every `int`↔`float`↔`double` conversion, float
  literals, and a parallel FP register file with its own caller/callee split.
- **Aggregates and memory**: pointers and address-of, single- and
  multi-dimensional arrays, `struct`/`union` (including by-value copy), and
  compound assignment.
- **Globals**: file-scope/static storage emitted as `.data`/`.bss`, addressed
  with `adrp`/`add`; frequently-used global addresses are cached in a register
  for the function.
- **Calls**: direct calls and recursion under AAPCS64, including the separate
  integer (`x0`-`x7`) and floating-point (`v0`-`v7`) argument sequences and the
  matching return registers.

Unsupported IL (e.g. by-value struct *arguments*/returns, more than eight
integer or FP arguments, indirect calls) makes the back end **raise** rather than
emit wrong code — so the differential tester reports it as a skip, never a
silent miscompile.

## Pipeline

```
C  ->  lexer  ->  parser  ->  tree  ->  il_gen  ->  IL  ->  make_asm  ->  text
                                              (target-neutral)   (arm64)
```

`ASMGen.make_asm` dispatches on `target.name`; for `arm64` it calls
`_make_asm_arm64`, which walks each function's IL through `_arm64_function`
(allocation + framing) and `_lower_arm64` (per-command instruction selection).
Integer values get `w`/`x` register homes, floating-point values a parallel
`s`/`d` file, and anything in memory (spills, address-taken locals, aggregates) a
frame slot at `[x29, #off]`. Scratch registers (`x9`-`x15`, `v16`/`v17`) are
reserved for operand staging and never used as value homes.

## Register allocation: liveness-based linear scan with a caller/callee split

The allocator is the most interesting part, and most of it is
**architecture-neutral** — the same `_il_*` methods are reused by the RISC-V back
end.

1. **Copy coalescing.** A `Set(out, tmp)` copying a single-use temporary can let
   the defining instruction write `out` directly, eliding the move — but only
   when it is provably safe (`_il_coalesce_safe`): `tmp`'s definition and the copy
   sit in one straight-line block and `out`'s prior value is not read in between.
   This guard is what makes a swap like `t = a + b; a = b; b = t` compile
   correctly instead of clobbering `b` early.

2. **Liveness** (`_il_liveness`). A backward live-variable fixpoint over a CFG
   built from the label/jump structure: `Return` has no successor, `Jump` targets
   its label, `JumpZero`/`JumpNotZero` branch to both fall-through and target, and
   `Call` falls through (it is not a branch).

3. **Intervals and call-crossing** (`_il_intervals`). Each value gets a
   conservative `[start, end]` live interval (safe across loop back-edges). A
   value is flagged as *crossing a call* when it is live both into and out of a
   `Call` — meaning it must survive the `bl`.

4. **Caller/callee split + linear scan** (`_il_linear_scan`). Two register pools
   per file:
   - **Callee-saved** homes (`x19`-`x28`, `d8`-`d15`) for values that cross a
     call; these are preserved by the callee, saved once in the prologue.
   - **Caller-saved** homes for call-clean values; these need *no* save/restore.
     The integer caller pool is the argument registers *above* the function's
     needs — `x[cs..7]` with `cs = max(max-call-arity, incoming-int-params, 1)` —
     so neither call-argument set-up (which writes `x0..x{argc-1}`) nor parameter
     unloading at entry (which reads `x0..x{params-1}`) can clobber a live home.
     That single bound removes the parallel-move hazard without a shuffle solver.
     The FP caller pool is `v18`-`v31`, inherently above all argument and scratch
     registers.

   Values are scanned in interval-start order; a register is reused once its
   previous occupant's interval has ended. Only the callee-saved registers
   actually used are saved, at packed offsets — and a function that needs no
   callee saves, no spills, and makes no calls is emitted **frameless** (no
   `stp x29,x30`, no `mov x29,sp`).

The result beats `gcc -O0` instruction counts on leaf and call-light functions
(which carry zero save/restore overhead), and cuts recursive `fib` to less than
half its size under the previous "every value gets a dedicated callee-saved home"
model, using only the callee registers its cross-call values require.

| function        | ShivyCX | gcc -O0 | callee-saves | frame      |
|-----------------|:-------:|:-------:|:------------:|:----------:|
| `int sq(int)`   |    4    |    6    |      0       | frameless  |
| sum loop (leaf) |   12    |   19    |      0       | frameless  |
| call-light      |   16    |   24    |      0       | —          |
| recursive `fib` |   29    |   20    |      3       | —          |

The remaining gap to `gcc` on branchy/recursive code is live-range *splitting*:
intervals are whole-range `[min, max]` with no holes, so a value live across one
call but idle for a long stretch still holds its register throughout. That is the
natural next allocator step.

## Floating point

Floating-point values run a pipeline parallel to the integer one: callee-saved
homes `d8`-`d15`, caller-saved `v18`-`v31`, scratch `v16`/`v17`, and arguments in
`v0`-`v7`. Arithmetic maps to `fadd`/`fsub`/`fmul`/`fdiv`; comparisons to `fcmp`
+ `cset` (ordered, NaN compares false; excluded from branch fusion to avoid the
unordered-condition subtleties). Conversions cover `fcvt` (float↔double),
`scvtf`/`ucvtf` (integer→float), and truncating `fcvtzs`/`fcvtzu` (float→integer).
Float literals are emitted once into `.data` and loaded via `adrp`/`add`/`ldr`,
which sidesteps the natural-alignment relocation a `:lo12:`-on-load would
require. The AAPCS64 calling convention is honored by counting integer and FP
arguments independently when lowering `LoadArg`, `Call`, and `Return`.

## How it was built and validated

The back end grew in independently shippable stages — a target seam; a minimal
"return a constant"; arithmetic and branches; calls; the register model;
pointers; arrays; structs; globals; codegen polish (immediates, fusion,
coalescing); bitwise/shift and global-address caching; floating point and
multi-dimensional arrays; and finally the linear-scan allocator rewrite. Each
stage is a delta on the previous one and is gated by three checks:

- **Differential correctness.** [`tools/arm64_difftest.py`](tools/arm64_difftest.py)
  compiles a growing corpus (now 130 programs) with both ShivyCX and the AArch64
  cross `gcc`, runs both under `qemu-aarch64`, and asserts the exit codes match.
  Using a real compiler as an oracle is what caught the bugs that mattered — an
  over-eager coalescer eating conversions, a latent narrowing-`Set`, and the
  swap-clobbering coalesce — each surfaced as a concrete exit-code mismatch.
- **No x86 regression.** The full x86-64 test suite (`make testfast`) must stay
  green; the arm64 path is wholly separate.
- **Self-host safety.** `shivyc/asm_gen.py` must still transpile through the
  Python→C front end (`py2c`), so the compiler can eventually compile itself for
  these targets too.

## Files

- [`shivyc/targets/__init__.py`](shivyc/targets/__init__.py) — the `Target` seam
  (`X86_64Target`, `Arm64Target`, `RiscV64Target`, `get_target`).
- [`shivyc/asm_gen.py`](shivyc/asm_gen.py) — `_make_asm_arm64`, `_arm64_function`,
  `_lower_arm64`, the `_arm64_*` lowering helpers, and the shared `_il_*`
  allocator core.
- [`tools/arm64_difftest.py`](tools/arm64_difftest.py) — the differential tester.

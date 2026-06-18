# ShivyCX feature benchmarks vs gcc -O0

These benchmarks isolate four of the paper's unique extended-C features and
measure what each one buys. ShivyCX emits unoptimized, `-O0`-class code, so the
fair external peer is **gcc -O0** — comparing against gcc -O2 would measure a
different *class* of compiler, not the feature. The primary comparison for each
feature is therefore **feature-ON vs feature-OFF on the same compiler** (codegen
quality held constant, only the feature varies), with gcc -O0 shown alongside as
the "ordinary unoptimizing C compiler" reference.

## Running

```sh
cd benchmarks
python3 run_benchmarks.py     # compile, correctness, timing, static metrics -> results/results.json
python3 plot_results.py        # render results/benchmarks.png
```

Every configuration is checked for **differential correctness**: gcc, ShivyCX
feature-off and ShivyCX feature-on must agree on the program exit code. All four
benchmarks report PASS.

## Results at a glance (this machine; best-of-5, machine-dependent)

| Feature | ShivyCX off | ShivyCX on | gcc -O0 | On vs off | Codegen witness |
|---|---|---|---|---|---|
| `_Nbit` globals | 0.262 s | 0.350 s | 0.606 s | 0.75× | hot-fn flag mem-loads **5 -> 0** (reads `xmm15`) |
| contracts SIMD | 1.501 s | **0.169 s** | 4.480 s | **8.88×** | scalar -> **SSE2 body, no remainder** |
| `-fstackless-calls` | 0.447 s | **0.264 s** | 0.549 s | **1.70×** | 8 calls/6 frames -> 7 calls/4 frames |
| `-fmetamorphic` | 0.013 s | 0.677 s | 0.014 s | **0.02×** | ordinary call/ret -> RWX `.mtext` slot, SMC/call |

Two clean wins (contracts, stackless), one codegen-only win whose runtime payoff
needs a latency/cache-bound workload (`_Nbit`), and one feature that is correct
but a large *regression* on a hot loop (`-fmetamorphic`) — reported honestly.

### Whole-program capabilities gcc structurally lacks (chart: `benchmarks2.png`)

| Capability | ShivyCX | gcc -O0 | Witness |
|---|---|---|---|
| unused-member elimination | `table[1000]` = **8000 B** | 32000 B | struct 32 B -> 8 B (**4× smaller**); gcc layout is ABI-fixed |
| left/right thread switch | **4 regs** saved/switch | n/a (17, save-all) | disjoint register banks; gcc has no per-thread bank concept |
| cross-TU use-after-free (`wrapper_uaf`) | **detected** | missed | free in one callee, deref in another |
| double-free / intra-fn UAF | detected | detected (with `-Wall`) | both find the easy cases |
| auto-free insertion | **inserts `free`** | n/a | escape analysis closes a leak; no gcc equivalent |

These are capability/static-metric results, not runtime races; correctness is
verified (member-elim exit codes agree; auto-free'd program runs correctly).

## 1 — `_Nbit` globals (xmm15 bit-packing)

`bench_nbit.c` has a hot `irq_handler` reading five small global flags. The
robust result is static: memory loads of the packed flags in the hot handler
drop from **5 to 0**, replaced by `xmm15` register extractions — gcc never does
this (`grep -c xmm15` on its output is 0; it issues five `movzx BYTE PTR
flag[rip]`). Honest runtime note: with the flag bytes resident in L1 a load is
nearly free, so packing's refresh+extract overhead makes it slightly *slower*
here. The feature targets latency/cache-bound interrupt paths and context-switch
SIMD-state cost (paper ref [1]), which a tight L1-hot loop does not model.

## 2 — contracts -> fallback-free SIMD

`assert not len(ptr) % 4` lets ShivyCX prove the reduction length is a multiple
of the SSE2 width across the whole call graph, so it emits an SSE2 loop with
**no scalar remainder and no runtime guard**. Result: ~**8.9× faster than
ShivyCX's own scalar code**, and it beats gcc -O0 by ~26×. This is the paper's
"guarantee gcc and clang cannot make without runtime checks" made concrete.

## 3a — rpython `.py` SIMD vs **gcc -O2** (dynamic argv defeats folding)

`simd_py/bench_evolve.py` raises the bar to gcc **-O2**. Two things stop -O2 from
cheating: the loop count is read from `sys.argv` (rpython now supports it, so
`main` becomes `int main(int argc, char** argv)` and `int(sys.argv[1])` lowers to
`atoi`), and the kernel is an in-place recurrence `x[i] += y[i]` that is not
loop-invariant, so -O2 cannot hoist it out. Both compilers must actually run and
auto-vectorize the loop. ShivyCX vectorizes via the `len(x) % 4` contract with no
alignment peeling and no scalar remainder:

```
ShivyCX .py (+contract SIMD)   0.488s  [addps, no peel/remainder]  (1.05x vs gcc -O2)
gcc -O2                        0.510s  [auto-vectorized + peel/remainder]
gcc -O0                       10.047s  [scalar loop]               (0.05x)
```

ShivyCX's contract SIMD **matches (edges out) gcc -O2** here and is ~20× faster
than gcc -O0 — because the proven contract lets it skip the peel/remainder
scaffolding -O2 must emit when it cannot know the length is a multiple of the
vector width. Differential correctness holds (all configs return the same value,
which depends on the runtime argument).

## 3 — rpython `.py` SIMD kernel vs gcc -O0

`simd_py/bench_saxpy.py` is an *rpython* source (`out[i] = alpha*x[i] + y[i]`
with a `len(x) % 4` contract). ShivyCX now reads `.py` directly: the harness
transpiles it once with `tools/py2c.py`, then compiles the same C three ways --
ShivyCX with the contract (a packed-single `mulps`+`addps` body), ShivyCX with
the contract stripped (scalar), and gcc -O0 (scalar). All three agree on the
exit code; the SIMD body runs ~**13× faster than ShivyCX's own scalar code and
~11× faster than gcc -O0** on this element-wise workload. This is the end-to-end
story: high-level restricted Python compiled, via proven contracts, to vector
code that beats an ordinary unoptimizing C compiler by an order of magnitude.

## 3b — `-fstackless-calls` (direct call + tail-call + FPO)

`bench_stackless.c` is the deeply-nested wrapper chain (`sum/foo/bar/boo/zoo`).
Tail wrappers collapse from a 9-instruction framed body with an indirect
`call rax` to a single frameless `jmp sum`. Result: ~**1.70× faster than the
framed baseline**, and faster than gcc -O0. Behavior is identical with the flag
on or off.

## 4 — `-fmetamorphic` (experimental, self-modifying return)

A `__metamorphic__` leaf returns by jumping through an 8-byte slot in a
writable+executable `.mtext` section that each caller patches before jumping in
— no call/ret. It is **correct** (the re-entrancy guard refuses recursion) but
the slot is self-modified on every call, and writing into an RWX page that holds
executing code triggers the CPU's self-modifying-code machinery. In this tight
5M-call loop that makes it ~**50× slower** than an ordinary call. The paper
flags it experimental and meant for rare, tightly-controlled hot paths; this
benchmark is the stress test that shows why a high-frequency loop is the wrong
place for it.

## 5 — `-f-eliminate-unused-members` (whole-program struct shrink)

`bench_member_elim.c` declares an 8-field `struct Rec` but only ever touches two
fields program-wide. ShivyCX proves the other six are never accessed in any
translation unit and removes them, shrinking the type 32 B -> 8 B; the global
`table[1000]` drops from **32000 B to 8000 B** in `.bss` (**4×**). gcc cannot do
this at any optimization level — struct layout is ABI-fixed, so an unused field
keeps its bytes. Exit codes agree across all three, so behaviour is preserved.

## 6 — register-partitioned left/right threads

`bench_threads.c` declares two disjoint worker threads (`foo` left, `bar` right)
via the `assert ... in threads.left/right(core=0)` header clause. ShivyCX
computes each side's register footprint from the call graph, constrains
allocation so the banks are disjoint (`left: rax,rcx,rdx,rsi` /
`right: r8,r9,r10,r11`), and emits a switcher that saves only the running bank:
**4 registers per context switch instead of 17** for a naive save-all. gcc has
no per-thread register-partition concept.

## 7 — whole-program memory safety (UAF / double-free / auto-free)

ShivyCX's `--check-memory` pass tracks allocation, aliasing and freeing across
the whole call graph. On the four `examples/memory/*.c` cases:

* `dangling_alias`, `double_free` — both ShivyCX and gcc `-Wall` catch them.
* `wrapper_uaf` — a `free` hidden in one callee and a deref hidden in another:
  **ShivyCX flags the cross-function use-after-free; gcc does not.**
* `autofree_leak` — ShivyCX's escape analysis proves a local allocation is dead
  and, with `--auto-free`, inserts the `free` automatically (the program still
  runs correctly). gcc has no equivalent.

This is a safety/capability benchmark, not a speed race: the point is *which
bugs are caught and which frees are recovered* on unannotated C.

## Files

```
benchmarks/
  run_benchmarks.py                       harness (compile, correctness, timing, static metrics)
  plot_results.py                         renders results/benchmarks.png + benchmarks2.png
  nbit_globals/bench_nbit.c               hot handler reading five _Nbit flags
  contracts/bench_contracts.c             contract-bearing reduction (+ _baseline.c)
  stackless/bench_stackless.c             nested wrapper call chain
  metamorphic/bench_metamorphic.c         metamorphic leaf in a loop (+ _baseline.c for gcc)
  member_elim/bench_member_elim.c         8-field struct, 2 fields used
  threads/bench_threads.c                 disjoint left/right worker threads
  (memory safety uses examples/memory/*.c in the repo root)
  results/results.json                    raw measurements
  results/benchmarks.png                  runtime chart (4 features)
  results/benchmarks2.png                 capability chart (member-elim, threads, mem-safety)
```

## Third peer: CCC

[CCC](https://github.com/anthropics/claudes-c-compiler) (Claude's C Compiler) is
included alongside gcc -O0 as a second from-scratch unoptimizing compiler. Build
it with `./build_ccc.sh` (needs a Rust toolchain); the harness auto-detects
`../ccc/target/release/ccc` or `$CCC`, and omits the CCC column if it is not
built. CCC compiles the same plain-C source gcc -O0 uses.

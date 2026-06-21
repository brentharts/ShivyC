# ShivyCX — an extended C compiler in pure Python

ShivyCX is a self-hosting-oriented C compiler written in Python3. It descends
from [ShivyC](https://github.com/ShivamSarodia/ShivyC) by Shivam Sarodia,
supports a subset of C11, targets x86-64 System V (Intel-syntax GNU assembler),
and adds a set of **whole-program language extensions and analyses** that are
practical precisely because every pass is a few hundred lines of legible Python.

On top of standard C, ShivyCX adds:

- **`_Nbit` globals** — pack small global flags (`name_Nbit`, 1≤N≤8) into an
  otherwise-idle SIMD register (`xmm15`) via `-fsimd-pack-globals`, collapsing
  *N* flag memory loads in a hot routine to one. A loop pass register-promotes
  packed globals across a loop — decompress into GP registers once before,
  operate on registers inside, recompress once after — turning a read-modify-write
  loop from a regression into a win (~1.4× over memory globals, ~1.8× over
  `gcc -O0`).
- **Contracts** — compile-time precondition clauses that bound argument lengths
  and can discharge a vectorizer's remainder loop.
- **Register-partitioned threads** — a whole-program *left/right* register split
  with specialized context switchers.
- **Argument packing** — an opt-in (`-f-pack-args`) non-standard calling
  convention that bit-packs small integer parameters into as few registers as
  possible (eight `char`s in one register instead of six registers plus two
  stack slots).
- **Callee-saved register allocation** — the allocator uses `rbx`/`r12`–`r15`
  for values live across a call, so they need not spill to memory (always on;
  ~1.5× on deeply nested call chains).
- **Memory safety** — whole-program use-after-free / double-free detection and
  automatic `free` insertion for unannotated C.
- **Bare-metal operation** — freestanding, bootable 64-bit images with an inlined
  mini-OS.
- **A Python→C transpiler** — toward compiling the front end with itself, which
  doubles as **rpython**: a fast, runtime-free Python subset (typed numpy-style
  arrays, auto-contract SIMD, libm, file/socket I/O) that `shivyc.main` compiles
  straight from `.py` to a native binary.


---

## Quickstart

ShivyCX needs only Python 3.6+; assembling and linking use GNU binutils.

```sh
# compile and run a C file (note: the entry point must be `int main()`)
python3 -m shivyc.main hello.c -o hello && ./hello

```

---

## Memory safety for unannotated C

C's manual `malloc`/`free` is the classic source of **use-after-free** and
**double-free** bugs. Because ShivyCX sees the whole call graph, a Python pass
([`shivyc/memory_safety.py`](shivyc/memory_safety.py)) tracks every allocation,
pointer copy (alias), and free, and:

- **flags use-after-free** — dereferencing (or passing to a callee that
  dereferences) a pointer whose allocation was freed, *through aliases and across
  functions*;
- **flags double-free**;
- **auto-frees** — when escape/region analysis proves an allocation is local with
  no live reference, the compiler inserts the `free` for you.

```sh
# report only (no code generated)
python3 -m shivyc.main tests/dangling_alias.c --check-memory
#   [use-after-free] in main: dereferences a pointer after its allocation was freed

# insert automatic frees during a normal compile
python3 -m shivyc.main tests/autofree_leak.c --auto-free -o leak
```

---

## Register-partitioned threads (left/right)

A function header can declare which side of a two-way register split a thread
runs on. ShivyCX computes each thread's transitive register footprint from the
call graph, splits the register file into disjoint `left` / `right` budgets,
re-runs allocation constrained to each budget, and emits a specialized context
switcher that saves only the bank in use.

```c
int main()
assert foo in threads.left( core=0 )
assert bar in threads.right( core=0 )
{ foo(); bar(); }
```

```sh
python3 -m shivyc.main threads_demo.c \
    --emit-thread-switcher switcher.s
#   left  GP footprint : rax, rcx, rdx, rsi
#   right GP footprint : r8, r9, r10, r11   -> footprints are disjoint
# writes switcher.s (cooperative) and switcher.preempt.s (timer-driven)
```

Details in [`shivyc/README.md`](shivyc/README.md)
---

## Calling convention: argument packing and callee-saved registers

Two optimizations attack the cost of moving values across a call.

**Argument packing (`-f-pack-args`, opt-in).** Under the System V ABI a function
of nine `char`s burns six registers and spills three to the stack. With
`-f-pack-args` ([`shivyc/pack_args.py`](shivyc/pack_args.py)), small integer
parameters are bit-packed by offset into as few registers as possible — the
caller builds each packed register with shifts/`or`s and the callee unpacks it —
so eight `char`s arrive in **one** register and nine in two, with no stack
traffic. Caller and callee recompute the identical layout from the signature, so
the convention is self-describing. It is applied only to statically known
(direct) calls, and any function whose address is taken (including via a global
function-pointer initializer) is **never** packed, so the standard ABI is always
honored at indirect call sites.

```sh
# pack small integer args into shared registers (composes with -fstackless-calls)
python3 -m shivyc.main -f-pack-args prog.c -o prog
```

**Callee-saved register allocation (always on).** The register allocator uses
`rbx` and `r12`–`r15` for values that are *live across a call*, which the call
clobbers in every caller-saved register and would otherwise force to memory.
Each callee-saved register a function uses is saved in its prologue and restored
on every exit path; frameless and `-O4` near-scratch leaves stay on caller-saved
registers so they remain frameless. On deeply nested call chains this is roughly
a **1.5×** speedup and beats `gcc -O0`.

---

## Bare-metal / freestanding operation

A mini-OS (*MiniKraft*) is inlined into [`minikraft.py`](minikraft.py) — every
source file embedded as a raw triple-quoted string, plus a registry of
hand-written 64-bit boot files — so the freestanding runtime travels with the
compiler. The bare-metal driver
([`shivycx_baremetal.py`](shivycx_baremetal.py)) compiles your app, resolves the
OS pieces it needs by transitive **symbol closure**, and links freestanding (no
libc, no CRT).

```sh
# freestanding app linked against the mini-OS console
python3 shivycx_baremetal.py tests/hello.c -o hello.elf

# bootable 64-bit Multiboot image (boot stub + long mode + GDT + IDT)
python3 shivycx_baremetal.py tests/kernel_irq.c -o irq.elf --image
```

The `--image` path emits a Multiboot stub that identity-maps the first GiB,
enables PAE/long mode, installs a flat GDT, and jumps to a 64-bit kernel with a
long-mode IDT (timer + keyboard). The preemptive thread switcher above installs
itself into the timer vector. Boot is validated statically here (header/checksum,
ELF64 entry, symbol resolution) since this environment has no emulator. See

---

## Implementation overview

#### Preprocessor
A token-stream macro engine with conditional compilation, function-like macros,
`#`/`##`, and hide sets ([`preproc.py`](shivyc/preproc.py)), preceded by an
extension pre-pass ([`extensions.py`](shivyc/extensions.py)) that recognizes the
non-standard constructs and blanks them while preserving line/column numbers.

#### Lexer
Implemented in [`lexer.py`](shivyc/lexer.py), with token classes in
[`tokens.py`](shivyc/tokens.py) and recognized keywords/symbols in
[`token_kinds.py`](shivyc/token_kinds.py).

#### Parser
Recursive descent in [`parser/*.py`](shivyc/parser/), producing a parse tree of
nodes from [`tree/*.py`](shivyc/tree/).

#### IL generation
The parse tree is traversed to a flat three-address IL; commands live in
[`il_cmds/*.py`](shivyc/il_cmds/) and the generators in each tree node.

#### ASM generation
IL is lowered to Intel-syntax x86-64; register allocation uses George and
Appel's iterated register coalescing over a pool that includes the callee-saved
registers (`rbx`, `r12`–`r15`), which are saved/restored per function so that
values live across a call can stay in registers. General code in
[`asm_gen.py`](shivyc/asm_gen.py); the argument-packing convention is a
whole-program pass in [`pack_args.py`](shivyc/pack_args.py), and loop
register-promotion of `_Nbit` packed globals is an IL pass in
[`simd_pack_promote.py`](shivyc/simd_pack_promote.py).

#### Whole-program call graph
The driver can build and print the program-wide call graph
([`callgraph.py`](shivyc/callgraph.py), `--print-call-graph`); it is the
substrate for the thread partitioner, the memory-safety analysis, and member
elimination.

---

## Compiling the front end with itself (Python→C transpiler)

[`tools/py2c.py`](tools/py2c.py) is a **specialized** Python→C translator that
emits C from ShivyCX's own Python source — a step toward a self-hosting front
end that is smaller and faster than the interpreted path. It is not a general
Python→C compiler; it understands only the subset of Python the front end is
written in, and uses that narrowness to produce small, idiomatic C.

It represents values in three tiers — concrete C scalars, concrete class
`struct`s with a shared `Obj` header for dispatch/`isinstance`, and a tagged
`obj` union as the dynamic fallback — and keeps each value in the most concrete
tier it can prove. Type inference, `isinstance` narrowing (including
`isinstance(x, T) and x.field` chains), cross-module method dispatch through
replicated vtables, first-class functions, and nested-function lifting are all
supported. A few small, ordinary type annotations in the front end (for example
typing the IL-command operand fields as `ILValue` via a `TYPE_CHECKING` import)
let the translator lower attribute chains like `self.output.ctype.size` to plain
struct accesses.

Correctness is enforced by **byte-identical behavior harnesses**: for each
feature, the transpiled C and the original Python run on the same inputs and
their outputs must match exactly. The transpiler never emits a silently-wrong
stub to inflate its compile count.

All targeted IL-command modules (`base`, `asm`, `math`, `compare`, `value`,
`control`) currently translate to cleanly-compiling C. See
[`TRANSPILER.md`](TRANSPILER.md) for the full design — object model, type
inference, the annotation convention, downcasting, cross-module machinery, and
the verification methodology.

```sh
# transpile the whole front end into a directory (runtime is emitted alongside)
python3 tools/py2c.py --out /tmp/out
# or a single module
python3 tools/py2c.py --out /tmp/out shivyc/il_cmds/value.py
```

---

## rpython — a fast, safe Python subset that compiles to native C

The same transpiler doubles as **rpython**: a restricted-Python dialect that
compiles, with *no runtime and no boxing*, straight to native C and on to a
ShivyCX binary. `shivyc.main` accepts `.py` sources directly — it transpiles
through `tools/py2c.py`, supplies the few libc prototypes the kernel needs, and
compiles and links:

```sh
python3 -m shivyc.main examples/rpython2c/numpy/simd_kernels.py -o simd && ./simd
```

What makes it fast and small:

- **Name- and flow-based type inference.** Unannotated integer drivers (`i`,
  `n`, `count`, `iters`, …) become `int`; locals assigned float literals or
  division become `double` (via a fixpoint). No annotations needed for ordinary
  numeric loops, which lower to plain C with zero boxing.
- **numpy-style typed arrays.** `"f32*"`, `"f64*"`, `"i32*"` (and fixed-size
  `"f32[256]"`) are real C arrays with native indexing — not boxed lists.
- **Typed containers.** `"list[int]"` / `"list[float]"` lower to a growable
  `{T* data; long len, cap;}` array (malloc/realloc), and `"dict[str,int]"` /
  `"dict[int,int]"` to parallel key/value arrays with linear-probe lookup — both
  unboxed and runtime-free, supporting literals, indexing, `append`, `len`,
  `in`, and iteration. A negative integer literal index (`xs[-1]`) wraps to
  `data[len-1]` statically; dynamic indices are taken as-is. Lists of objects
  keep the tagged model.
- **Native byte scanning + 64-bit ints.** `ord(s[i])` on a `char*` compiles to a
  direct byte read (no per-character allocation), and an `"i64"` annotation gives
  true 64-bit arithmetic. Together these let character- and table-driven compiler
  passes (lexers, hashers) be written in rpython and run at native speed — see
  [`examples/rpython2c/compiler/`](examples/rpython2c/compiler/).
- **Auto-contracts → SIMD.** A leading `assert len(x) % 4 == 0` (or an inferred
  divisibility fact from a fixed-size array) is lowered to a ShivyCX contract;
  the compiler proves it at the call site and emits a packed-SSE body with no
  scalar remainder. On an element-wise recurrence this **matches `gcc -O2` and
  beats `gcc -O0` ~20×** (see [`benchmarks/`](benchmarks/)).
- **libm ufuncs.** `exp`, `log`, `sin`, `sqrt`, `tanh`, … (bare, or `math.`/
  `np.`-qualified) type as `double` and lower to native libm calls.
- **Classes become structs.** A plain data class (no inheritance or dynamic
  dispatch) is lowered POD-style to a bare C `struct` with `malloc` and direct
  method calls — no object header, vtable, or runtime. Instances pass by
  pointer, so a function or method can take them directly
  (`def pull(p: "Body*", q: "Body*")` → `void pull(Body* p, Body* q)`). Richer
  classes (inheritance, `isinstance`, virtual dispatch) use the tagged-object
  model, which ShivyCX now compiles end to end — including its own object-model
  runtime — so polymorphism works in self-compiled code, not just under gcc.
- **Multi-file programs.** Pass several `.py` files at once
  (`shivyc.main app.py lib.py -o app`) and they compile as one translation unit:
  `import lib` resolves against the input directory, so functions call directly,
  classes construct and dispatch across the boundary, and fields read/write
  directly (boxing into `obj` fields as needed). A POD class stays POD when used
  from another module — its decision is propagated so layout and dispatch agree.
  Two modules may even define classes with the *same* bare name; the translator
  module-qualifies the colliding symbols and emits a distinct struct for each
  (plus a separate `TypeInfo` for object-model classes, so `isinstance` still
  distinguishes them). A field assigned only `None` in its
  module is typed `obj` (nullable), so another module can store an object in it.
- **System glue.** File I/O (`open`/`read`/`write`/`close`), `input()`,
  `os.system`, `os.fork`, BSD **sockets** (`socket`/`bind`/`connect`/`accept`/
  `send`/`recv`), and `sys.argv` all lower to plain C — enough to write real
  programs (a TCP echo server, a Mandelbrot renderer) with no runtime.
- **Build reports.** `--pdf` renders any build (C or rpython) as a PDF: overview,
  TikZ call graph, safety findings in red, the Python source beside the
  generated C with its auto-inferred contracts, and the program output.

Worked examples live in [`examples/rpython2c/`](examples/rpython2c/): `numpy/`
(SIMD kernels, BLAS, ufuncs, matmul), `nn/` (a feed-forward neural net showing
classes→structs), `nbody/` (a gravity sim that passes class instances by
pointer), `classes/` (inheritance + polymorphism via the object model, plus a
POD-vs-object-model comparison), `lists/` and `dicts/` (typed `list[T]` /
`dict[K,V]` lowered to unboxed C arrays — no boxing, no GC),
`compiler/` (a C-subset lexer kernel — a ShivyCX hotspot rewritten in rpython,
~18x faster through ShivyCX and ~50x through gcc, with a benchmark harness),
`memory/` (`del`, compiler-inserted `free` via whole-program escape analysis,
and the `--pdf` memory report), `multifile/`, `ambig/`, and `fieldwrite/`
(multi-file programs: cross-module calls, same-named classes module-qualified
into distinct structs, and cross-module writes into None-initialised `obj`
fields), `dynattr/` (compiled `getattr`/`setattr` on a struct by runtime key —
an inline first-character type switch, no dict and no bridge), `rtattr/`
(`getattr`/`setattr` by runtime key on a *polymorphic* object held as a tagged
`obj`, dispatched through a per-type field table by the `rt_getattr`/`rt_setattr`
runtime helpers — again no micropython bridge), `crossattr/` (cross-class field
discovery: attributes a configurator stamps onto another class via
`obj.attr =`/`setattr` are promoted to real slots so the writes persist),
`aggregates/` (varargs-free list/dict/call construction — a 16-byte `obj` mis-lowers through C `...` on the self-compiled backend, so literals and calls build via a stack array instead), `formatting/` (`%` string formatting and f-strings — `fmt % args` is real printf-style formatting, not arithmetic modulo, lowered to a varargs-free `str_mod`), `ctorval/` (constructors used as values: trampoline unboxing of int/float/bool arguments, plus the switch-on-narrow-type integer-promotion fix that makes boolean truthiness correct), `sets/` (set as a first-class type with its own runtime tag: union/intersection/difference/symmetric-difference, order-independent equality, de-duplicating literals and comprehensions), `dictops/` and `wordfreq/` (the general dict type -- get/setdefault/pop/update/copy/merge/comprehensions, plus realistic frequency-counting and grouping), `untyped/` (untyped dicts/lists/sets with type inference and rpython-rule advisories), `promote/` (opt-in auto-promotion of inferred containers to the unboxed typed form), `io/`, `net/`, and `mandelbrot/`. Run them all with `make rpython`.

`make testfast` is a fast smoke test: a single-file syntax sweep and a multi-file cross-module case, each compiled with CPython (the oracle), the ShivyCX self-compiler, and the py2c->gcc transpiler, requiring all three to agree. It covers most of the language subset in a few seconds.

---

## References

- [ShivC](https://github.com/ShivamSarodia/ShivC) — the original compiler ShivyCX was rewritten from.
- C11 Specification — http://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf
- x86-64 ABI — https://github.com/hjl-tools/x86-psABI/wiki/x86-64-psABI-1.0.pdf
- Iterated Register Coalescing (George and Appel) — https://www.cs.purdue.edu/homes/hosking/502/george.pdf
- *Foundational Problems with Compilers and Operating Systems* (B. Hartshorn, viXra 2025).
- https://ai.vixra.org/abs/2507.0081

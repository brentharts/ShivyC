# SIMD Bit-Packing of Global Flags (`xmm15`)

This is an integration into ShivyC of an optimization idea explored in two
other projects:

* **jitbit** (Brett Hartshorn) — a Python→x86 JIT whose central thesis is to
  treat SIMD registers as first-class fast storage and to keep frequently-used
  state in registers rather than memory.
* **OpenSourceJesus / C-Compiler** — a C compiler that realizes that thesis,
  most cleanly in its *SIMD bit-packing* pass: small global "kernel flags" are
  packed into the last SIMD register (`xmm15`), which ordinary compilers never
  allocate for scalar code, so hot interrupt routines can read them without a
  memory access ("zero-latency kernel flags").

ShivyC now implements the same idea as an opt-in pass.

## The idea in one paragraph

A 1-bit "interrupt enabled" flag read on every interrupt does not deserve a
full memory load. If a handful of such flags are packed into the bits of one
SIMD register that the rest of the program never touches, a read becomes a
register `movq` plus a shift and mask — no cache traffic, no pipeline stall.
ShivyC keeps each flag's ordinary one-byte memory home as the source of truth
(so the rest of the compiler is unaffected) and layers `xmm15` on top as a
register-resident cache that hot routines read from.

## Usage

```sh
shivyc -fsimd-pack-globals program.c     # packing on
shivyc program.c                          # packing off (default)
```

A global qualifies for packing when **all** of these hold:

* it has static storage and occupies a single byte (`char` family);
* its name ends in `_Nbit` with `1 <= N <= 8` (e.g. `irq_enabled_1bit`,
  `priority_3bit`);
* it still fits in the low 64 bits of the register (flags past 64 bits are
  silently left as ordinary globals).

A function is treated as *hot* — and therefore reads packed flags from the
register — when its name matches the interrupt/callback heuristic
(`isr_*`, `irq_*`, `interrupt_*`, `*_handler`, `*_callback`, or `*_hot`). This
mirrors the OpenSourceJesus heuristic.

```c
unsigned char irq_enabled_1bit;
unsigned char priority_3bit;

int timer_handler() {            /* hot: reads come from xmm15 */
  if (irq_enabled_1bit) return priority_3bit;
  return 0;
}
```

## How it maps onto ShivyC's pipeline

The pass is deliberately conservative: packed flags keep their normal memory
byte, so every existing IL command (compare, arithmetic, address-of, …) keeps
working unchanged and all 94 pre-existing tests still pass. `xmm15` is an
*additive* fast-read cache.

* **`shivyc/simd_pack.py`** (new) — the whole feature lives here: detection of
  qualifying globals (`SimdPackLayout`), bit-slot assignment, and the assembly
  emitters for packing, refreshing, reading, and write-through.
* **`shivyc/asm_cmds.py`** — adds a `Raw` instruction class for the SIMD
  instruction sequences that do not fit the size-parameterized `_ASMCommand`
  model.
* **`shivyc/asm_gen.py`** — builds the layout while assigning global spots,
  declares the 8-byte memory mirror, marks each function hot/cold, and emits
  the startup pack (in `main`) and the per-hot-function refresh after the
  prologue.
* **`shivyc/il_cmds/value.py`** — `Set` is the single interception point.
  A `SET` whose destination is a packed flag becomes a *write-through* (byte +
  `xmm15` + mirror); a `SET` whose source is a packed flag, inside a hot
  function, becomes a register-only extraction.
* **`shivyc/il_cmds/control.py`** — `Call` refreshes `xmm15` from the mirror in
  hot functions (xmm8–15 are caller-saved, so a call may clobber it).
* **`shivyc/main.py`** — adds the `-fsimd-pack-globals` flag.

## Why it is correct

* The per-flag memory byte is always authoritative and is updated on every
  write, so any code path that was not specialized (e.g. `if (flag)` lowering
  to `cmp [flag], 0`) still reads a correct value.
* Every write is written through to all three locations, so the register and
  its memory mirror never drift from the byte.
* `xmm15` is caller-saved. To stay correct despite that, hot functions reload
  `xmm15` from the single 64-bit mirror word at entry and after each call — one
  aligned read that covers *all* flags, after which every flag read in that
  function is register-only.
* Flags are treated as unsigned bit-fields (extraction zero-extends), which
  matches the `_Nbit` "small flag" use case.

## Verification

* The full suite passes: `python3 -m unittest discover` → 101 tests
  (94 original + 7 new in `tests/test_simd_pack.py`).
* `tests/test_simd_pack.py` runs packed binaries and asserts both the results
  and the generated assembly (register reads in hot functions, write-through in
  `main`, memory reads in cold functions, correct handling of out-of-range and
  unmarked globals).
* Differential testing against gcc on several programs produced identical exit
  codes with packing enabled.
* `tests/general_tests/simd_pack/kernel_flags.c` is a runnable demonstration of
  the "zero-latency kernel flags" pattern; it returns the same value whether
  compiled by gcc, by ShivyC without the flag, or by ShivyC with the flag.

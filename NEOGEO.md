# ShivyCX on the Neo-Geo (Motorola 68000)

The Neo-Geo's main CPU is a Motorola 68000. The [ngdevkit](https://github.com/dciabrin/ngdevkit)
project cross-compiles C to it with a bare-metal `m68k-neogeo-elf` GCC and links
the result into a cartridge ROM. This document covers the **first steps** of a
ShivyCX back end for that CPU: what works today, why the 68000 is an unusually
good test of the compiler's target seam, how it was validated, and what remains
on the road to a real Neo-Geo ROM.

Select it with `--target m68k` (aliases `neogeo`, `68k`).

```sh
python3 -m shivyc.main prog.c -S -o prog.s --target m68k
```

## Why the 68000 is the interesting case

Every back end before this one — x86-64, AArch64, RISC-V 64 — is a 64-bit,
little-endian, load/store RISC(ish) machine with register-passed arguments and
three-address ALU instructions. The 68000 shares none of that:

- **CISC, two-address.** `add.l %d1,%d0` means `d0 = d0 + d1`; the destination is
  also an operand. There is no `add d0, d1, d2`.
- **Big-endian**, with **`.b`/`.w`/`.l`** (8/16/32-bit) operation suffixes.
- **Two register files**: data registers `d0`-`d7` (arithmetic) and address
  registers `a0`-`a7` (`a7` = stack pointer, `a6` conventionally the frame
  pointer). They are not interchangeable.
- **Stack-based calling convention.** Arguments are *not* passed in registers:
  the caller pushes them onto the stack (right-to-left) and cleans up after the
  call; the result comes back in `d0`.

If the target seam and the shared register allocator can absorb a machine this
different, they can absorb almost anything — which is exactly why this was worth
doing as a flexibility test.

## What works today

The integer core, validated end to end against the m68k oracle:

- 32-bit `int` locals; `+ - * / %`; the six comparisons; `if`/`while`/`for`.
- Stack-argument function calls, including multi-argument calls and recursion
  (direct and mutual).
- Register pressure with spills, and the copy-coalescing safety check (swaps).

Anything outside that — floating point, pointers and arrays, structs, globals —
makes the back end **raise** rather than emit wrong code, so the differential
tester reports those as skips, never silent miscompiles. They are the next steps,
not landmines.

## How it reuses the rest of the compiler

The whole point of the exercise: the m68k back end writes **only** instruction
selection and the m68k ABI. The architecture-neutral middle end is reused
verbatim — the same `_il_*` methods the AArch64 and RISC-V back ends call:

- `_il_coalesce_safe` — copy-coalescing safety (no early clobber on swaps).
- `_il_liveness` — backward live-variable fixpoint over the IL control-flow graph.
- `_il_intervals` — live intervals and call-cross detection.
- `_il_linear_scan` — the caller/callee linear-scan allocator, told only which
  register pools the target offers.

Liveness and linear scan do not care that the ISA is CISC or that operations are
two-address; they assign each value a register, and the m68k lowering bridges the
gap to two-address form.

## The m68k lowering model

- **Value homes** are the callee-saved data registers `d2`-`d7`; `d0`/`d1` are the
  compute scratch. Values that don't fit spill to frame slots. (`d0`/`d1` are
  caller-saved scratch, so the caller-saved home pool is empty and every home is
  callee-saved — saved/restored around the function body.)
- **Two-address bridge.** Each binary op computes in `d0` and stores to the home:
  `move.l a,%d0 ; <op>.l b,%d0 ; move.l %d0,<home>`. Simple and always correct;
  optimizing away the staging move is a later refinement.
- **Comparisons** produce a clean 0/1: `cmp.l b,%d0 ; s<cc> %d0 ; and.l #1,%d0`
  (the `and` masks the byte `scc` leaves to bit 0). Branches are `tst.l` +
  `jeq`/`jne`; unconditional jumps are `jra`.
- **Frames** use `a6` as the frame pointer via `link.w %fp,#-locals` / `unlk %fp`.
  Arguments are read at `8(%fp)+4*k`; spill slots live at negative offsets from
  `%fp`; used `d2`-`d7` are pushed after the `link` and popped before `unlk`.
  Leaf functions with no locals, args, or calls are emitted without a frame.
- **Calls** push arguments right-to-left with `move.l <src>,-(%sp)`, `jsr` the
  target, then clean the stack with `lea (4n,%sp),%sp`; the result is taken from
  `d0`.

`muls.l` and `divsl.l` (32-bit multiply / 32-bit divide-with-remainder) are used
for `*`, `/`, and `%`. These are 68020+ instructions; the real Neo-Geo 68000 has
only 16×16→32 multiply and 32÷16 divide, so a bare-metal target will need 16-bit
sequences or libgcc-style helpers (see the roadmap).

## Validation

`tools/m68k_difftest.py` compiles a corpus both with ShivyCX (`--target m68k`)
and with the m68k cross gcc, runs both under `qemu-m68k`, and asserts the exit
codes match:

    m68k difftest: 18 pass, 0 fail, 0 skip, 0 error

The corpus covers constants, arithmetic, division/modulo, all six comparisons,
`if`/`while` and nested loops, leaf/recursive/mutually-recursive calls,
multi-argument stack-passed calls, register pressure with spills, the swap and
iterative-Fibonacci coalescing cases, and tail recursion.

The oracle is `m68k-linux-gnu-gcc` + `qemu-m68k`. The bare-metal `m68k-neogeo-elf`
toolchain is not in this environment, but the instruction set is the same, so the
Linux cross compiler stands in for it exactly as `aarch64-linux-gnu` stands in for
bare-metal AArch64. Both default to 68020+, which is why the 32-bit `muls.l`/
`divsl.l` instructions assemble and run.

## Roadmap to a real Neo-Geo ROM

These steps build on the working integer core, in roughly increasing effort:

1. **Sub-word integers** — honor `.b`/`.w` for `char`/`short`, and 16-bit `int`
   under a `-mshort`-style model (the Neo-Geo is a 16-bit-era machine; many of its
   hardware registers are 16-bit). This wants target-dependent ctype sizes in the
   front end.
2. **Pointers, arrays, structs, and globals** — the same IL the other back ends
   already lower; on m68k this is `lea`/indirect addressing through the address
   registers (`a0`-`a5`) and `.data`/`.bss` emission.
3. **68000-only multiply/divide** — replace `muls.l`/`divsl.l` with 16-bit
   sequences or runtime helpers so the output runs on the actual console CPU.
4. **Neo-Geo ROM packaging** — emit the `m68k-neogeo-elf` sections, ROM header,
   interrupt vectors, and DIP/handler declarations that ngdevkit expects, and link
   against its headers and BIOS. This is where ShivyCX output meets the real
   hardware/emulator (GnGeo).
5. **The Z80 sound CPU** is a separate target entirely (ngdevkit uses SDCC for it);
   out of scope here, but a natural future seam.

## Files

- [`shivyc/targets/__init__.py`](shivyc/targets/__init__.py) — `M68kTarget`
  (`--target m68k`, triple `m68k-neogeo-elf`).
- [`shivyc/asm_gen.py`](shivyc/asm_gen.py) — `_make_asm_m68k`, `_m68_function`,
  `_lower_m68k`, the `_m68_*` lowering helpers, and the shared `_il_*` allocator.
- [`tools/m68k_difftest.py`](tools/m68k_difftest.py) — the differential tester.

## ASCII art → Neo-Geo pixel art (the `neogeo` rpython library)

On top of the back end there is a small rpython graphics library,
[`tools/rpy_lib/neogeo.py`](tools/rpy_lib/neogeo.py), that turns multi-line ASCII
art into Neo-Geo pixel art. A loading screen is four lines:

```python
import neogeo
a = neogeo.background.asciiart(".... multi-line ascii ....")
b = neogeo.sprite.asciiart(".... multi-line ascii ....")
neogeo.scene.add_background(a)
neogeo.scene.add_sprite(b)
```

Each character is one pixel, and its case is the intensity:

| char | colour | | char | colour | | char | colour |
|------|--------|-|------|--------|-|------|--------|
| `R`/`r` | red | | `C`/`c` | cyan | | `O`/`o` | orange |
| `G`/`g` | green | | `M`/`m` | magenta | | `W`/`w` | white/grey |
| `B`/`b` | blue | | `Y`/`y` | yellow | | `K` | black |

`'.'` and `' '` are transparent. Each layer becomes a deduplicated palette of
Neo-Geo 16-bit colour words (index 0 transparent) plus an index buffer; the colour
packing is the hardware's (white is `0x7FFF`, black `0x0000`).

### Specialisation: baking the art at translate time

Because the art is a string *constant*, `import neogeo` specialises the
translator. Rather than transpile the declarative API to the console, py2c runs
the ASCII→pixel conversion **at translate time** and emits the finished palette
and pixel data as static C arrays plus a tiny driver — so the on-target program is
just data and a copy loop, which is what makes it reachable on a small target. The
hook lives in [`tools/rpy_neogeo_integration.py`](tools/rpy_neogeo_integration.py)
(`bake_source` → `scene_to_c`), wired into `transpile_file`.

The demo [`examples/rpython2c/neogeo/loadscreen.py`](examples/rpython2c/neogeo/loadscreen.py)
draws a cyan frame, a white title bar, a starfield, and a rocket sprite. Run it
through the pipeline:

```sh
python3 -c "import sys;sys.path.insert(0,'tools');import py2c;\
py2c.write_runtime('/tmp/ng');print(py2c.transpile_file(\
'examples/rpython2c/neogeo/loadscreen.py','/tmp/ng'))"
#   neogeo: baked 2 layer(s) from ASCII art -> /tmp/ng/loadscreen.c
gcc /tmp/ng/loadscreen.c -o /tmp/load && /tmp/load    # or: python3 -m shivyc.main …
#   layer 0: background 32x13, 5 colors, tile 8
#   layer 1: sprite 9x8, 6 colors, tile 16
#   scene: 2 layers, 177 lit pixels, palette checksum 0x4508
```

The generated `main` is a portable stand-in for the VRAM/palette upload: it walks
the baked scene and returns the lit-pixel count (mod 256) as the exit code, so the
whole rpython→C→native path is runnable and deterministically checkable off the
console (it matches the CPython oracle exactly). The baked C compiles with gcc and
with ShivyC's own x86 back end.

**Honest status.** The baked data is colour-correct Neo-Geo palette + indexed
pixels, and the conversion/specialisation is real and tested. What is *not* here
yet: the C-ROM/fix-ROM **bitplane tile packing**, the VRAM/palette MMIO upload
(which needs the m68k back end's pointer/array/global support — see the roadmap
above), and ngdevkit ROM packaging. Those are the steps from "correct pixel data
baked at translate time" to "pixels on a Neo-Geo screen."

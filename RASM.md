# rasm — self-contained x86-64 assembler

Goal: remove the last external dependency in the ShivyCX toolchain. Today the
compiler emits Intel-syntax assembly and shells out to the GNU assembler (`as`)
to produce ELF `.o` objects (`shivyc/main.py:assemble`). `rasm` replaces `as`
with an RPython-translatable assembler so the whole compile→assemble→link path
is our own code, and can eventually be fused with ShivyCX into a self-contained
JIT.

## Status

Done and validated:

* **Encoder** (`rasm.py`): x86-64 machine-code encoding — REX / ModRM / SIB /
  displacement / immediate layout — for the instruction and operand vocabulary
  ShivyCX emits (`mov movsx movzx movsxd lea push pop add sub or and xor cmp
  test imul idiv div mul neg not cqo cdq sal shl sar shr call jmp ret leave nop`
  and the `Jcc` family), plus **SSE scalar float** (`movsd movss addsd subsd
  mulsd divsd ucomisd ucomiss comisd cvtsi2sd cvtsi2ss cvttsd2si cvttss2si
  cvtsd2ss cvtss2sd movq movd xorps xorpd pxor sqrtsd` over xmm0–xmm15).
  Operands: registers (8/16/32/64-bit incl. r8–r15, xmm0–xmm15),
  immediates (imm8 sign-extension, accumulator short forms, `movabs` for
  64-bit immediates that do not fit imm32), and memory
  (`[base + index*scale + disp]`, constant products, disp8/disp32, RIP-relative,
  absolute, symbolic). Correct REX handling incl. forced REX for the
  `spl/bpl/sil/dil` 8-bit registers. **123/123 differential cases (integer +
  SSE) byte-identical to GNU `as`.**
* **Parser** (`rasm.py`): the Intel-syntax subset ShivyCX emits — directives,
  labels, `SIZE PTR [...]` memory operands, comments.
* **Driver** (`rasm_obj.py`): two passes — lay out `.text`/`.data`/`.bss` from
  the directive stream (`.section .global .comm .quad .int .byte .zero`), record
  labels, encode instructions and data, collect relocations; then resolve
  same-section PC-relative refs to local labels in place, keeping the rest as
  ELF relocations.
* **ELF64 writer** (`rasm_obj.py`): emits an ET_REL x86-64 object — header,
  `.text`/`.data`/`.bss`, `.symtab`/`.strtab`/`.shstrtab`, `.rela.text`/
  `.rela.data`, `.note.GNU-stack` — with STT_SECTION/FUNC/OBJECT symbols,
  SHN_COMMON, and R_X86_64_PC32 / _32S / _64 relocations. Referenced local
  labels (e.g. ShivyCX float literals `__fltlitN` in `.data`) are emitted as
  STB_LOCAL symbols so their relocations resolve.
* **End-to-end test** (`rasm_obj_test.py`): compile a C program with ShivyCX,
  assemble it with **both** rasm and `as`, link both with gcc, run both, and
  require matching results. **10/10 programs pass** (arithmetic, recursion,
  loops, globals, bitops, conditionals, floats, function-pointers-in-data,
  nested calls, arrays, pointers/structs).
* **Corpus coverage**: across 61 ShivyCX-compilable C files (6858 instructions),
  rasm encodes **every instruction** (61/61 files).
* **Integrated pipeline**: with `SHIVYC_RASM=1`, ShivyCX routes assembly through
  rasm instead of `as` for the full compile→assemble→link→run. Over the runnable
  corpus, **55/55 programs** produce results identical to ShivyCX+`as`.

The encoding model mirrors pycca and the Intel SDM, rewritten flat (no
metaclasses/generators/`**kwargs`; one uniform `Operand` class) for RPython.

## Not yet done

* **Branch relaxation**: jumps always use rel32, so objects are larger than
  `as` (which picks rel8 when possible) but functionally identical. A relaxation
  pass (iterate to a fixpoint as short jumps shift offsets) would close the gap.
* **AT&T-syntax inline asm**: regions inside `.att_syntax` (e.g. hand-written
  `movq %rsp, %rax` in inline asm) are not parsed; the driver raises a clear
  error rather than mis-encoding. Rare outside bare-metal/Minikraft code.
* **The linker**: `ld` is still the one remaining external tool in the
  self-hosted path (`py2c → C → ShivyCX → rasm → ld`) — the natural next
  component to write in the dialect.
* **RPython cleanup + minipy**: drop `partition` tuple-unpacking, make dict
  iteration order-independent, add type annotations; translate, then run under
  minipy for full self-hosting.

## Integration

`shivyc/main.py:assemble` uses rasm when the `SHIVYC_RASM` environment variable
is set, and the external `as` otherwise (default unchanged). Run e.g.
`SHIVYC_RASM=1 PYTHONPATH=. python3 shivyc/main.py prog.c -o prog`.

## Files

* `tools/rpy_lib/rasm.py` — encoder + parser + operand/relocation model.
* `tools/rpy_lib/rasm_obj.py` — assembler driver + ELF64 object writer.
* `tools/rpy_lib/rasm_test.py` — differential encoder test vs GNU `as`.
* `tools/rpy_lib/rasm_obj_test.py` — end-to-end compile→assemble→link→run test.

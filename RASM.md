# rasm — self-contained x86-64 assembler (in progress)

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
  and the `Jcc` family). Operands: registers (8/16/32/64-bit, incl. r8–r15),
  immediates (imm8 sign-extension, accumulator short forms), and memory
  (`[base + index*scale + disp]`, constant products, disp8/disp32, RIP-relative,
  absolute, symbolic). **100/100 differential cases byte-identical to GNU `as`.**
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
  SHN_COMMON, and R_X86_64_PC32 / _32S / _64 relocations.
* **End-to-end test** (`rasm_obj_test.py`): compile a C program with ShivyCX,
  assemble it with **both** rasm and `as`, link both with gcc, run both, and
  require matching results. **9/9 programs pass** (arithmetic, recursion, loops,
  globals, bitops, conditionals, function-pointers-in-data, nested calls,
  arrays, pointers/structs).

The encoding model mirrors pycca and the Intel SDM, rewritten flat (no
metaclasses/generators/`**kwargs`; one uniform `Operand` class) for RPython.

## Not yet done

* **Branch relaxation**: jumps always use rel32, so objects are larger than
  `as` (which picks rel8 when possible) but functionally identical. A relaxation
  pass (iterate to a fixpoint as short jumps shift offsets) would close the gap.
* **Integration**: swap `shivyc/main.py:assemble` to call rasm instead of the
  `as` subprocess, behind a flag, then flip the default once the difftest corpus
  is broad enough.
* **RPython cleanup + minipy**: drop `partition` tuple-unpacking, make dict
  iteration order-independent, add type annotations; translate, then run under
  minipy for full self-hosting.

## Files

* `tools/rpy_lib/rasm.py` — encoder + parser + operand/relocation model.
* `tools/rpy_lib/rasm_obj.py` — assembler driver + ELF64 object writer.
* `tools/rpy_lib/rasm_test.py` — differential encoder test vs GNU `as`.
* `tools/rpy_lib/rasm_obj_test.py` — end-to-end compile→assemble→link→run test.

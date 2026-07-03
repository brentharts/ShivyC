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
  ShivyCX emits: `mov movsx movzx movsxd lea push pop add sub or and xor cmp
  test imul idiv div mul neg not cqo cdq sal shl sar shr call jmp ret leave nop`
  and the `Jcc` family. Operands: registers (8/16/32/64-bit, incl. r8–r15),
  immediates (imm8 sign-extended vs imm32, accumulator short forms), and memory
  (`[base + index*scale + disp]`, disp8/disp32, RIP-relative, absolute, and
  symbolic). Symbolic operands emit a `Reloc` record for the ELF writer.
* **Parser** (`rasm.py`): the Intel-syntax subset ShivyCX emits — directives,
  labels, `SIZE PTR [...]` memory operands, comments.
* **Differential test** (`rasm_test.py`): 96/96 cases byte-identical to GNU
  `as` (assembled in one batch, compared via objdump), covering reg/imm/mem
  forms plus relocation cases. On a real ShivyCX-emitted `.s`, 99/99
  instructions encode.

The encoding model mirrors pycca (campagnola/pycca) and the Intel SDM, but is
rewritten flat (no metaclasses/generators/`**kwargs`; one uniform `Operand`
class) for RPython translation.

## Next steps

1. **Two-pass driver**: resolve local labels to offsets, keep external/`.global`
   symbols as relocations. Emit `.text`/`.data`/`.bss` sections from the
   directive stream (`.section .comm .quad .global`).
2. **ELF64 writer**: emit a relocatable object (ELF header, section headers,
   `.symtab`/`.strtab`, `.rela.text`) so the output drops in for `as -o x.o`.
   PeachPy's `peachpy/formats/elf` is a reference for the structures.
3. **Integrate**: swap `shivyc/main.py:assemble` to call `rasm` instead of the
   `as` subprocess; add an end-to-end diff test (compile a C file, assemble with
   both `as` and `rasm`, compare `.o`/linked output).
4. **RPython cleanup**: remove tuple-unpacking of `partition`, make dict
   iteration order-independent, add explicit type annotations; then translate
   and (later) run under minipy for full self-hosting.

## Files

* `rasm.py` — encoder + parser + operand/relocation model.
* `rasm_test.py` — differential test against GNU `as`.

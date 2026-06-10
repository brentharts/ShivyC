# Metamorphic returns (`-fmetamorphic`, experimental)

This is an advanced, intentionally-unsafe option ported from the
"metamorphic return site" idea in the reference `arx86.py`. It is opt-in twice:
the `-fmetamorphic` flag enables the machinery, and the `__metamorphic__`
specifier marks which functions use it.

## What it does

A `__metamorphic__` function returns **without using the stack for the return
address**. Instead:

* The function is placed in a writable+executable section `.mtext`, with an
  8-byte return slot laid down immediately before its entry label -- "memory
  near the function", in writable text.
* Each caller writes its own return address into that slot and then **jumps**
  into the function (no `call`).
* The function returns by **jumping through the slot** (no `ret`).

So no return address for a metamorphic function is ever pushed or popped. The
slot is self-modified at run time, which is why the section must be writable.

```asm
; in .mtext (writable + executable):
helper__metaret:
    .quad 0                                  ; the return slot
helper:
    ...                                      ; body, result in eax
    jmp QWORD PTR [rip + helper__metaret]    ; "return"

; at a call site:
    lea  r11, [rip + .Lret]
    mov  QWORD PTR [rip + helper__metaret], r11   ; patch the slot
    jmp  helper                                   ; jump, don't call
.Lret:
    ...                                           ; result is in eax
```

## Why a section, not `ld -N`

The obvious way to get writable text is the linker's `-N` (OMAGIC) mode, but
that conflicts with the glibc C-runtime startup (`Scrt1.o` references
`__ehdr_start`, which OMAGIC does not define), so links fail. Instead, the
metamorphic code is emitted into a dedicated `.section .mtext,"awx",@progbits`.
The loader maps that segment read-write-execute (the linker warns about the RWX
segment -- that is expected and is the risk you are opting into). Ordinary
functions stay in normal `.text`.

## Correctness and limitations

This feature is **experimental**. It is correct only within clear limits:

* **Not re-entrant.** There is a single static return slot per function, so a
  metamorphic function must not be active twice at once. The compiler builds
  the (direct) call graph and **refuses to compile** a metamorphic function
  that can reach itself -- directly or transitively -- with a clear error
  rather than emitting code that would corrupt the slot:

  ```
  error: metamorphic function 'fact' may be re-entered (recursion);
         not supported
  ```

* **Leaf-friendly.** The demo and tests use leaf metamorphic functions. A
  metamorphic function that itself makes ordinary calls is not exercised here.

* **RWX section.** `.mtext` is writable and executable. This is unsafe by
  design; the flag exists for experimentation and as groundwork for further
  optimizations (e.g. using near-function memory to reduce stack pressure).

Without `-fmetamorphic`, the `__metamorphic__` specifier is ignored entirely
and ordinary call/ret code is generated, so a program behaves identically.

## Interaction with stackless

A call to a metamorphic function returns to its call site, so it must never be
turned into a tail jump (which would drop the return). When both
`-fstackless-calls` (or `-O4`) and `-fmetamorphic` are active, the stackless
pass is told not to tail-eliminate calls to metamorphic callees. The two
features otherwise compose: verified combined returns the correct result.

## Relationship to `-O4`

`-O4` turns on whole-program stackless lowering and near-function scratch
storage (see `NEAR_SCRATCH.md`), which moves register spills off the stack into
a static per-function buffer. The metamorphic return slot is a related use of
near-function storage -- self-modified, code-adjacent state -- but is opt-in
separately via `-fmetamorphic` because it requires a writable+executable
section.

## Verification

* `helper(10) + helper(a)` returns 35 with `-fmetamorphic` and 35 without it
  (matching gcc), and the assembly shows the slot-based return and the
  patch-and-jump call sequence.
* Recursive metamorphic functions are refused at compile time.
* `tests/test_metamorphic_simd.py::TestMetamorphic` and
  `tests/general_tests/extensions/metamorphic.c` cover these.

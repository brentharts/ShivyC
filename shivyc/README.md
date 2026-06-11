# Register-partitioned threads (ShivyCX extension)

A function header may declare which functions run as bare-metal threads and on
which side of a two-way register split each lives:

```c
int main()
assert foo in threads.left( core=0 )
assert bar in threads.right( core=0 )
{
    foo();
    bar();
}
```

`threads.left(core=N)` / `threads.right(core=N)` pin a thread function to a
register group (and a core). Because ShivyCX sees the whole call graph from
`main`, it computes each thread's transitive register footprint, splits the GP
(and XMM) register files into disjoint `left` / `right` budgets, re-runs the
register allocator constrained to each group's budget, and emits a *specialized*
context switcher.

## Generate the switcher

```
python3 -m shivyc.main examples/threads/threads_demo.c \
    --emit-thread-switcher switcher.s
```

This prints a before/after partition report and writes `switcher.s` containing
`switch_to_right` / `switch_to_left`. Each direction saves exactly the outgoing
group's footprint and restores the incoming group's — no runtime test of which
kind of thread is current, because a left thread can only have left registers
live.

In the demo, constrained re-allocation makes the footprints disjoint:

```
left  GP footprint : rax, rcx, rdx, rsi
right GP footprint : r8, r9, r10, r11
footprints are disjoint: left and right share no registers
```

## How it fits together

- `extensions.py` recognizes the `assert FN in threads.SIDE(core=N)` clauses
  and records `{fn: {side, core}}` (alongside the existing contract asserts).
- `thread_contracts.py` does the analysis: transitive call graph, real
  post-allocation register footprints (scanned from emitted asm), the left/right
  partition, the disjoint-budget feedback, and the switcher codegen.
- `asm_gen.py` honors a per-function register budget
  (`--thread-alloc-json`), so a thread's code is generated using only its
  group's registers. Out-of-budget pressure spills to memory, so correctness is
  always preserved.

## Notes / limits

- The split applies to the *working/spill* registers. ABI argument registers
  (rdi, rsi, ...) are fixed by the calling convention at call sites; a thread
  whose body makes calls will still touch those, so the cleanest disjointness is
  achieved for call-light / leaf thread bodies (as in the demo).
- The generated routines are cooperative (save group footprint -> swap rsp ->
  restore other group). The same save-set is what a *preemptive* timer ISR would
  push for a thread of that group — the partition shrinks that frame too.

## Preemptive timer path (IRQ0)

`--emit-thread-switcher OUT.s` also writes `OUT.preempt.s`: the partition-aware
*preemptive* timer path that replaces the generic `irq0 -> irq_common_stub`
(which saves the full 15-register `interrupt_frame64`) at IDT vector 32.

Instead it saves only the **running thread's group footprint**. The key: left
and right footprints are disjoint, so the left timer ISR uses the right
registers as scratch (dead for a left thread) and vice-versa -- no extra spills.

```
timer ISR saves the running group's footprint (left 4, right 4 GP regs)
instead of all 9 on every tick
```

Control enters `timer_dispatch` (the gate target), which does
`jmp [timer_vector]`; each ISR flips `timer_vector` to the other side before
`iretq`, so the next tick is already specialized -- the choice is data, never a
branch on thread kind.

### Wiring it to the kernel (connects to the idt64 step)

Link `OUT.preempt.s` with the embedded `idt64.c` / `idt64.S`, then install it:

```c
extern void timer_dispatch(void);
extern void *cur_tcb, *next_tcb;
extern void idt_set_handler(unsigned char vec, void *entry);  /* idt64.c */

cur_tcb  = left_tcb;
next_tcb = right_tcb;
idt_set_handler(32, timer_dispatch);   /* IRQ0 -> specialized path */
/* program the PIT, pic_enable_irq(0), then sti (kernel_main already does sti) */
```

`idt64.c` gained `idt_set_handler(vec, entry)` for exactly this -- pointing a
vector straight at a raw asm entry, bypassing the generic stub.

### TCB layout the switcher expects

```
+0  saved rsp     +8  saved rip     +16 saved rflags
+24.. saved group GP registers (footprint order), then XMM (16B each)
```

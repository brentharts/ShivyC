# Bare-metal examples

ShivyCX compiles a C app and links it against just the minikraft pieces it
actually uses (resolved by transitive symbol closure), with no libc and no crt.

## 1. Freestanding link (no boot)

    python3 shivycx_baremetal.py examples/baremetal/hello.c -o hello.elf

`hello.c` defines `_start`, includes `console.h`, and calls `console_*`. Only
`console.c` gets pulled in from `minikraft.py`.

## 2. Bootable 64-bit image

    python3 shivycx_baremetal.py examples/baremetal/kernel.c -o boot.elf --image

`kernel.c` provides `void kmain(unsigned int magic, void *mbi)`. The embedded
`boot64.S` (Multiboot1 header -> long mode) and `kernel64.ld` come from
`minikraft.py` (`MINIKRAFT_BAREMETAL64`). The boot stub:

1. is entered by GRUB / `qemu -kernel` in 32-bit protected mode,
2. identity-maps the first 1 GiB with 2 MiB pages, enables PAE + long mode,
3. installs a 64-bit GDT, far-jumps to 64-bit code,
4. calls `kmain(magic, mbi)`.

The result is a Multiboot-loadable ELF64 with a valid Multiboot header at the
load address (0x100000) and entry `_start`.

### Booting it (needs QEMU installed)

    qemu-system-x86_64 -kernel boot.elf -serial stdio

You should see the console output on the VGA display and serial.

## Architecture note

ShivyCX emits 64-bit x86-64, so apps and OS pieces are built at 64-bit. The
full interrupt-driven `kernel_main` additionally needs the `isr*/irq*` stubs
from the 32-bit `idt_asm.S`; the resolver reports those as unresolved at 64-bit.
A 64-bit IDT path is the next increment if you want interrupts.

## 3. Interrupt-driven kernel (timer + keyboard)

    python3 shivycx_baremetal.py examples/baremetal/kernel_irq.c -o irq.elf --image

`kernel_irq.c`'s `kmain` hands off to minikraft's full `kernel_main`
(console -> memory -> idt_init -> pic_init -> sti -> thread_init -> app_main).
Because the image is 64-bit, the resolver swaps the 32-bit `idt.c` for the
embedded **64-bit IDT** (`idt64.c` + `idt64.S`, also in `MINIKRAFT_BAREMETAL64`):

- `idt64.c` installs 16-byte long-mode gate descriptors (selector 0x08, the
  code segment from `boot64.S`) and the C dispatchers `isr_handler` /
  `irq_handler` (the latter sends the PIC EOI).
- `idt64.S` provides `isr0..31` / `irq0..47`, saving the full 64-bit register
  frame and returning with `iretq`.

`app_main` then registers a timer handler (IRQ0 -> vector 32) and a keyboard
handler (IRQ1 -> vector 33), programs the PIT to ~100 Hz, and unmasks both
IRQs. With `sti` already set by `kernel_main`, the handlers fire.

    qemu-system-x86_64 -kernel irq.elf -serial stdio

You should see the banner, then a `.` roughly once per second (timer), and key
presses being consumed (keyboard). This is ring-0-only: no TSS/IST is needed
because interrupts never change privilege level.

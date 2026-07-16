/* boot.S -- minimal Multiboot1 entry for mbos.
 *
 * QEMU's built-in `-kernel` loader understands Multiboot1 and loads a *32-bit*
 * ELF directly, in protected mode with paging off. That is the whole reason
 * mbos boots with a plain `qemu-system-x86_64 -kernel mbos.elf` and needs no
 * GRUB, no ISO, no disk image -- which keeps the self-tests self-contained.
 *
 * We stay in 32-bit protected mode (minikraft's baseline does the same), set up
 * a stack, and call kmain(magic, mbi). The VGA text buffer at 0xB8000 and COM1
 * at 0x3F8 are both usable immediately, so the C side can draw and log without
 * any further CPU-mode gymnastics.
 *
 * (The 64-bit long-mode path -- boot64.S in minikraft.py, reached via
 * `shivycx_baremetal.py --image` -- is a later mbos step; it needs GRUB to
 * boot, so it is deliberately not what step 1 uses.)
 */

.set MB_MAGIC,    0x1BADB002
.set MB_FLAGS,    0x00000000
.set MB_CHECKSUM, -(MB_MAGIC + MB_FLAGS)

.section .multiboot
.align 4
    .long MB_MAGIC
    .long MB_FLAGS
    .long MB_CHECKSUM

.section .text
.global _start
.code32
_start:
    cli
    cld
    mov $stack_top, %esp
    xor %ebp, %ebp

    /* kmain(unsigned magic, void *mbi): cdecl -> push args right to left.
     * eax = multiboot magic, ebx = multiboot info pointer (set by loader). */
    push %ebx
    push %eax
    call kmain
    add $8, %esp

hang:
    cli
    hlt
    jmp hang

.section .bss
.align 16
stack_bottom:
    .skip 32768
stack_top:

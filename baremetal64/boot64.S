/* boot64.S - minimal Multiboot1 -> long mode entry for ShivyCX/minikraft.
 *
 * GRUB / `qemu -kernel` load us via Multiboot in 32-bit protected mode with
 * paging off. We identity-map the first 1 GiB with 2 MiB pages, enable PAE +
 * long mode, install a 64-bit GDT, jump to 64-bit code, then call kmain().
 *
 * The application provides:  void kmain(unsigned int magic, void *mbi);
 * (a plain `void kmain(void)` also works - the args are just ignored.)
 */

/* ---- Multiboot1 header (must be in first 8 KiB of the file) ------------- */
.set MB_MAGIC,    0x1BADB002
.set MB_FLAGS,    0x00000000
.set MB_CHECKSUM, -(MB_MAGIC + MB_FLAGS)

.section .multiboot
.align 4
    .long MB_MAGIC
    .long MB_FLAGS
    .long MB_CHECKSUM

/* ---- 32-bit entry ------------------------------------------------------ */
.section .text
.global _start
.code32
_start:
    cli
    cld
    mov $stack_top, %esp

    /* Stash the Multiboot magic (eax) and info pointer (ebx) for kmain. */
    mov %eax, saved_magic
    mov %ebx, saved_mbi

    /* Zero PML4 + PDPT (two contiguous 4 KiB tables = 2048 dwords). */
    mov $pml4, %edi
    xor %eax, %eax
    mov $2048, %ecx
    rep stosl

    /* PML4[0] -> PDPT, PDPT[0] -> PD   (present|write) */
    mov $pdpt, %eax
    or  $0x03, %eax
    mov %eax, pml4
    mov $pd, %eax
    or  $0x03, %eax
    mov %eax, pdpt

    /* PD[i] = (i << 21) | present|write|PS   (512 * 2 MiB = 1 GiB). */
    mov $pd, %edi
    xor %ecx, %ecx
1:
    mov %ecx, %eax
    shl $21, %eax
    or  $0x83, %eax
    mov %eax, (%edi)
    movl $0, 4(%edi)
    add $8, %edi
    inc %ecx
    cmp $512, %ecx
    jne 1b

    /* CR3 = PML4 */
    mov $pml4, %eax
    mov %eax, %cr3

    /* CR4.PAE = 1 */
    mov %cr4, %eax
    or  $0x20, %eax
    mov %eax, %cr4

    /* EFER.LME = 1 */
    mov $0xC0000080, %ecx
    rdmsr
    or  $0x100, %eax
    wrmsr

    /* CR0.PG = 1 (PE already set under Multiboot) -> long mode active */
    mov %cr0, %eax
    or  $0x80000000, %eax
    mov %eax, %cr0

    /* Install 64-bit GDT and far-jump into 64-bit code. */
    lgdt gdt64_pointer
    ljmp $0x08, $long_mode_start

/* ---- 64-bit entry ------------------------------------------------------ */
.code64
long_mode_start:
    mov $0x10, %ax
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %fs
    mov %ax, %gs
    mov %ax, %ss

    movq $stack_top, %rsp
    xor %rbp, %rbp

    /* kmain(magic, mbi) per SysV: rdi, rsi (32-bit loads zero-extend). */
    movl saved_magic, %edi
    movl saved_mbi, %esi
    call kmain

hang:
    cli
    hlt
    jmp hang

/* ---- GDT (long mode) --------------------------------------------------- */
.section .rodata
.align 16
gdt64:
    .quad 0                      /* null */
    .quad 0x00209A0000000000     /* code: P,S,exec,read,long */
    .quad 0x0000920000000000     /* data: P,S,write */
gdt64_pointer:
    .word gdt64_pointer - gdt64 - 1
    .long gdt64

/* ---- page tables + stack ---------------------------------------------- */
.section .bss
.align 4096
pml4:  .skip 4096
pdpt:  .skip 4096
pd:    .skip 4096
.align 16
stack_bottom:
    .skip 16384
stack_top:
saved_magic: .skip 4
saved_mbi:   .skip 4

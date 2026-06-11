/* kernel64.ld - link a ShivyCX app + minikraft pieces + boot64.S into a
 * Multiboot-loadable 64-bit ELF. Load at 1 MiB (inside the identity-mapped
 * first 1 GiB set up by boot64.S). */

ENTRY(_start)

SECTIONS
{
    . = 0x100000;          /* 1 MiB - standard Multiboot load address */

    /* Multiboot header must land within the first 8 KiB of the file. */
    .text : {
        *(.multiboot)
        *(.text)
        *(.text.*)
    }

    .rodata : {
        *(.rodata)
        *(.rodata.*)
    }

    .data : {
        *(.data)
        *(.data.*)
    }

    .bss : {
        *(COMMON)
        *(.bss)
        *(.bss.*)
    }

    /DISCARD/ : {
        *(.comment)
        *(.note.*)
        *(.eh_frame)
    }
}

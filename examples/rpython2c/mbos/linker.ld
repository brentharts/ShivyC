/* linker.ld -- link mbos at 1 MiB with the Multiboot header in the first 8 KiB
 * of the file (a Multiboot1 requirement) so QEMU's -kernel loader finds it. */

ENTRY(_start)

SECTIONS
{
    . = 0x00100000;      /* 1 MiB: the conventional Multiboot load address */

    .text : {
        *(.multiboot)    /* header must come first in the image */
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

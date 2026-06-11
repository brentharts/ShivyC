/* A minimal bootable kernel: ShivyCX compiles this, boot64.S calls kmain(). */
#include "console.h"

void kmain(unsigned int magic, void *mbi) {
    (void)magic;
    (void)mbi;
    console_init();
    console_puts("ShivyCX + minikraft: 64-bit bare-metal kernel booted!\n");
    console_puts("Hello from kmain, running in long mode.\n");
    for (;;) { }
}

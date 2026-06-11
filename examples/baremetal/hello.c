#include "console.h"

void _start(void) {
    console_init();
    console_puts("Hello from a ShivyCX-compiled app, linked against minikraft!\n");
    for (;;) { }
}

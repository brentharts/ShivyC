/* A ShivyCX-compiled kernel that boots into minikraft's kernel_main and then
 * drives the timer (IRQ0) and keyboard (IRQ1) through the 64-bit IDT. */

#include "console.h"

/* minikraft entry + interrupt plumbing (provided by the pulled-in OS pieces) */
extern void kernel_main(void);
extern void register_interrupt_handler(unsigned char vec, void (*handler)(void));
extern void pic_enable_irq(unsigned char irq);

static unsigned char inb(unsigned short port) {
    unsigned char v;
    __asm__ volatile("inb %1, %0" : "=a"(v) : "Nd"(port));
    return v;
}
static void outb(unsigned short port, unsigned char val) {
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}

static volatile unsigned long ticks = 0;
static volatile unsigned char last_scancode = 0;

/* vector 32: PIT timer */
static void on_timer(void) {
    ticks++;
}

/* vector 33: keyboard - read the scancode so the controller releases IRQ1 */
static void on_key(void) {
    last_scancode = inb(0x60);
}

/* kernel_main() calls app_main() after console/memory/idt/pic/sti/thread. */
void app_main(void) {
    console_puts("app_main: wiring timer + keyboard via 64-bit IDT\n");

    register_interrupt_handler(32, on_timer);   /* IRQ0 -> vector 32 */
    register_interrupt_handler(33, on_key);     /* IRQ1 -> vector 33 */

    /* Program the PIT to ~100 Hz (1193182 / 11932). */
    outb(0x43, 0x36);
    outb(0x40, 11932 & 0xFF);
    outb(0x40, (11932 >> 8) & 0xFF);

    pic_enable_irq(0);   /* timer */
    pic_enable_irq(1);   /* keyboard */

    console_puts("app_main: interrupts live - halting in a loop\n");

    unsigned long shown = 0;
    for (;;) {
        __asm__ volatile("hlt");           /* wake on each interrupt */
        if (ticks - shown >= 100) {        /* ~once per second */
            shown = ticks;
            console_puts(".");
        }
    }
}

/* boot64.S calls kmain(); hand off to minikraft's full init. */
void kmain(unsigned int magic, void *mbi) {
    (void)magic;
    (void)mbi;
    kernel_main();
    for (;;) { __asm__ volatile("hlt"); }
}

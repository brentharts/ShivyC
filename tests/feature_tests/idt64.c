/* idt64.c - 64-bit (long mode) IDT for ShivyCX/minikraft.
 *
 * Drop-in replacement for the 32-bit idt.c: exposes the same public symbols
 * (idt_init, register_interrupt_handler, isr_handler, irq_handler) so
 * kernel.c / interrupts.c / thread.c link against it unchanged, but uses
 * 16-byte long-mode gate descriptors and a 64-bit register frame.
 */

#include <stdint.h>
#include "string.h"
#include "console.h"

#define IDT_ENTRIES 256
#define KERNEL_CS   0x08      /* 64-bit code selector from boot64.S GDT */
#define GATE_INTR   0x8E      /* present, DPL0, 64-bit interrupt gate */

typedef void (*interrupt_handler_t)(void);

/* 16-byte long-mode gate descriptor. */
struct idt64_entry {
    uint16_t offset_low;
    uint16_t selector;
    uint8_t  ist;
    uint8_t  type_attr;
    uint16_t offset_mid;
    uint32_t offset_high;
    uint32_t zero;
} __attribute__((packed));

struct idt64_ptr {
    uint16_t limit;
    uint64_t base;
} __attribute__((packed));

/* 64-bit register frame; field order must match the pushes in idt64.S. */
struct interrupt_frame64 {
    uint64_t rax, rbx, rcx, rdx, rsi, rdi, rbp;
    uint64_t r8, r9, r10, r11, r12, r13, r14, r15;
    uint64_t int_no, err_code;
    uint64_t rip, cs, rflags, rsp, ss;
};

static struct idt64_entry idt[IDT_ENTRIES];
static struct idt64_ptr   idtp;
static interrupt_handler_t interrupt_handlers[IDT_ENTRIES];

/* Stub table from idt64.S */
extern void isr0(void);  extern void isr1(void);  extern void isr2(void);
extern void isr3(void);  extern void isr4(void);  extern void isr5(void);
extern void isr6(void);  extern void isr7(void);  extern void isr8(void);
extern void isr9(void);  extern void isr10(void); extern void isr11(void);
extern void isr12(void); extern void isr13(void); extern void isr14(void);
extern void isr15(void); extern void isr16(void); extern void isr17(void);
extern void isr18(void); extern void isr19(void); extern void isr20(void);
extern void isr21(void); extern void isr22(void); extern void isr23(void);
extern void isr24(void); extern void isr25(void); extern void isr26(void);
extern void isr27(void); extern void isr28(void); extern void isr29(void);
extern void isr30(void); extern void isr31(void);
extern void irq0(void);  extern void irq1(void);  extern void irq2(void);
extern void irq3(void);  extern void irq4(void);  extern void irq5(void);
extern void irq6(void);  extern void irq7(void);  extern void irq8(void);
extern void irq9(void);  extern void irq10(void); extern void irq11(void);
extern void irq12(void); extern void irq13(void); extern void irq14(void);
extern void irq15(void);

static void set_gate(uint8_t num, uint64_t handler) {
    idt[num].offset_low  = (uint16_t)(handler & 0xFFFF);
    idt[num].selector    = KERNEL_CS;
    idt[num].ist         = 0;
    idt[num].type_attr   = GATE_INTR;
    idt[num].offset_mid  = (uint16_t)((handler >> 16) & 0xFFFF);
    idt[num].offset_high = (uint32_t)((handler >> 32) & 0xFFFFFFFF);
    idt[num].zero        = 0;
}

/* Compatibility shim matching the 32-bit idt.h prototype. */
void idt_set_gate(uint8_t num, uint32_t base, uint16_t sel, uint8_t flags) {
    (void)sel; (void)flags;
    set_gate(num, (uint64_t)base);
}

void register_interrupt_handler(uint8_t interrupt, interrupt_handler_t handler) {
    interrupt_handlers[interrupt] = handler;
}

/* Point an IDT vector directly at a raw asm entry (e.g. the generated
 * timer_dispatch), bypassing the generic stub. Used to install the
 * partition-aware preemptive timer path at vector 32. */
void idt_set_handler(uint8_t vec, void *entry) {
    set_gate(vec, (uint64_t)(uintptr_t)entry);
}

/* C dispatcher for CPU exceptions (vectors 0-31). */
void isr_handler(struct interrupt_frame64 *frame) {
    if (interrupt_handlers[frame->int_no]) {
        interrupt_handlers[frame->int_no]();
    }
}

/* C dispatcher for hardware IRQs (vectors 32-47), sends PIC EOI. */
void irq_handler(struct interrupt_frame64 *frame) {
    uint8_t irq = (uint8_t)(frame->int_no - 32);

    if (interrupt_handlers[frame->int_no]) {
        interrupt_handlers[frame->int_no]();
    }

    /* End Of Interrupt: slave first (if applicable), then master. */
    if (irq >= 8) {
        __asm__ volatile("outb %0, %1" : : "a"((uint8_t)0x20), "Nd"((uint16_t)0xA0));
    }
    __asm__ volatile("outb %0, %1" : : "a"((uint8_t)0x20), "Nd"((uint16_t)0x20));
}

void idt_init(void) {
    idtp.limit = (uint16_t)(sizeof(idt) - 1);
    idtp.base  = (uint64_t)(uintptr_t)&idt;

    memset(&idt, 0, sizeof(idt));
    memset(interrupt_handlers, 0, sizeof(interrupt_handlers));

    set_gate(0,(uint64_t)(uintptr_t)isr0);   set_gate(1,(uint64_t)(uintptr_t)isr1);
    set_gate(2,(uint64_t)(uintptr_t)isr2);   set_gate(3,(uint64_t)(uintptr_t)isr3);
    set_gate(4,(uint64_t)(uintptr_t)isr4);   set_gate(5,(uint64_t)(uintptr_t)isr5);
    set_gate(6,(uint64_t)(uintptr_t)isr6);   set_gate(7,(uint64_t)(uintptr_t)isr7);
    set_gate(8,(uint64_t)(uintptr_t)isr8);   set_gate(9,(uint64_t)(uintptr_t)isr9);
    set_gate(10,(uint64_t)(uintptr_t)isr10); set_gate(11,(uint64_t)(uintptr_t)isr11);
    set_gate(12,(uint64_t)(uintptr_t)isr12); set_gate(13,(uint64_t)(uintptr_t)isr13);
    set_gate(14,(uint64_t)(uintptr_t)isr14); set_gate(15,(uint64_t)(uintptr_t)isr15);
    set_gate(16,(uint64_t)(uintptr_t)isr16); set_gate(17,(uint64_t)(uintptr_t)isr17);
    set_gate(18,(uint64_t)(uintptr_t)isr18); set_gate(19,(uint64_t)(uintptr_t)isr19);
    set_gate(20,(uint64_t)(uintptr_t)isr20); set_gate(21,(uint64_t)(uintptr_t)isr21);
    set_gate(22,(uint64_t)(uintptr_t)isr22); set_gate(23,(uint64_t)(uintptr_t)isr23);
    set_gate(24,(uint64_t)(uintptr_t)isr24); set_gate(25,(uint64_t)(uintptr_t)isr25);
    set_gate(26,(uint64_t)(uintptr_t)isr26); set_gate(27,(uint64_t)(uintptr_t)isr27);
    set_gate(28,(uint64_t)(uintptr_t)isr28); set_gate(29,(uint64_t)(uintptr_t)isr29);
    set_gate(30,(uint64_t)(uintptr_t)isr30); set_gate(31,(uint64_t)(uintptr_t)isr31);

    set_gate(32,(uint64_t)(uintptr_t)irq0);  set_gate(33,(uint64_t)(uintptr_t)irq1);
    set_gate(34,(uint64_t)(uintptr_t)irq2);  set_gate(35,(uint64_t)(uintptr_t)irq3);
    set_gate(36,(uint64_t)(uintptr_t)irq4);  set_gate(37,(uint64_t)(uintptr_t)irq5);
    set_gate(38,(uint64_t)(uintptr_t)irq6);  set_gate(39,(uint64_t)(uintptr_t)irq7);
    set_gate(40,(uint64_t)(uintptr_t)irq8);  set_gate(41,(uint64_t)(uintptr_t)irq9);
    set_gate(42,(uint64_t)(uintptr_t)irq10); set_gate(43,(uint64_t)(uintptr_t)irq11);
    set_gate(44,(uint64_t)(uintptr_t)irq12); set_gate(45,(uint64_t)(uintptr_t)irq13);
    set_gate(46,(uint64_t)(uintptr_t)irq14); set_gate(47,(uint64_t)(uintptr_t)irq15);

    __asm__ volatile("lidt %0" : : "m"(idtp));
}

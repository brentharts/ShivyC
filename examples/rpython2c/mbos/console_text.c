/* console_text.c -- text-only console (VGA 0xB8000 + COM1 serial) for the
 * legacy 32-bit `make text32` build. This is the original pre-graphics console,
 * kept so the tested 32-bit text-mode path stays byte-for-byte reproducible.
 * The 64-bit default build uses console.c (graphics-aware) instead.
 */
#include "mbos.h"

#define VGA_MEM ((volatile u16 *)0xB8000)
#define COM1    0x3F8

static int s_row = 0, s_col = 0;
static u8  s_attr = 0;

static void serial_init(void) {
    outb(COM1 + 1, 0x00); outb(COM1 + 3, 0x80); outb(COM1 + 0, 0x03);
    outb(COM1 + 1, 0x00); outb(COM1 + 3, 0x03); outb(COM1 + 2, 0xC7);
    outb(COM1 + 4, 0x0B);
}
static void serial_putc(char c) {
    int spin = 100000;
    while (spin-- > 0 && (inb(COM1 + 5) & 0x20) == 0) { }
    outb(COM1, (u8)c);
}
void ser_puts(const char *s) {
    while (*s) { if (*s == '\n') serial_putc('\r'); serial_putc(*s++); }
}

static void move_cursor(void) {
    u16 pos = (u16)(s_row * VGA_COLS + s_col);
    outb(0x3D4, 14); outb(0x3D5, (u8)(pos >> 8));
    outb(0x3D4, 15); outb(0x3D5, (u8)(pos & 0xFF));
}
static void scroll_if_needed(void) {
    if (s_row < VGA_ROWS) return;
    int i;
    for (i = 0; i < (VGA_ROWS - 1) * VGA_COLS; i++) VGA_MEM[i] = VGA_MEM[i + VGA_COLS];
    u16 blank = (u16)(' ' | (s_attr << 8));
    for (i = (VGA_ROWS - 1) * VGA_COLS; i < VGA_ROWS * VGA_COLS; i++) VGA_MEM[i] = blank;
    s_row = VGA_ROWS - 1;
}

void con_init(void) {
    serial_init();
    s_attr = VGA_ATTR(VGA_LGREY, VGA_BLACK);
    con_clear(s_attr);
}
void con_clear(u8 attr) {
    u16 cell = (u16)(' ' | (attr << 8));
    int i;
    for (i = 0; i < VGA_COLS * VGA_ROWS; i++) VGA_MEM[i] = cell;
    s_row = 0; s_col = 0; s_attr = attr;
    move_cursor();
}
void con_set_attr(u8 attr) { s_attr = attr; }
int  con_col(void)  { return s_col; }
int  con_cols(void) { return VGA_COLS; }
int  con_rows(void) { return VGA_ROWS; }
void con_newline(void) {
    s_col = 0; s_row++;
    scroll_if_needed();
    ser_puts("\n");
    move_cursor();
}
void con_putc(char c) {
    if (c == '\n') { con_newline(); return; }
    VGA_MEM[s_row * VGA_COLS + s_col] = (u16)((u8)c | (s_attr << 8));
    { int spin = 100000; while (spin-- > 0 && (inb(COM1 + 5) & 0x20) == 0) {} outb(COM1, (u8)c); }
    s_col++;
    if (s_col >= VGA_COLS) con_newline();
    else move_cursor();
}
void con_puts(const char *s) { while (*s) con_putc(*s++); }

/* mbos.h -- shared declarations for the freestanding mbos kernel.
 *
 * No libc is available on bare metal, so the handful of helpers the browser
 * needs (memset/memcpy/strlen/strcmp) are provided by libmini.c. Everything
 * here is plain C89-ish so it also compiles cleanly under ShivyCX later.
 */
#ifndef MBOS_H
#define MBOS_H

typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;
typedef unsigned long  u64;
typedef __SIZE_TYPE__  size_t;

/* ---- port I/O ---------------------------------------------------------- */
static inline void outb(u16 port, u8 val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}
static inline u8 inb(u16 port) {
    u8 r; __asm__ volatile ("inb %1, %0" : "=a"(r) : "Nd"(port)); return r;
}

/* ---- mini libc (libmini.c) -------------------------------------------- */
void  *mini_memset(void *d, int c, size_t n);
void  *mini_memcpy(void *d, const void *s, size_t n);
size_t mini_strlen(const char *s);
int    mini_strcmp(const char *a, const char *b);

/* ---- console: VGA text (0xB8000) + COM1 serial (console.c) ------------- */
/* VGA text-mode colour attributes (foreground | background<<4). */
#define VGA_BLACK      0
#define VGA_BLUE       1
#define VGA_GREEN      2
#define VGA_CYAN       3
#define VGA_RED        4
#define VGA_MAGENTA    5
#define VGA_BROWN      6
#define VGA_LGREY      7
#define VGA_DGREY      8
#define VGA_LBLUE      9
#define VGA_LGREEN     10
#define VGA_LCYAN      11
#define VGA_LRED       12
#define VGA_LMAGENTA   13
#define VGA_YELLOW     14
#define VGA_WHITE      15
#define VGA_ATTR(fg, bg) ((u8)((fg) | ((bg) << 4)))

#define VGA_COLS 80
#define VGA_ROWS 25

void con_init(void);
void con_clear(u8 attr);
void con_set_attr(u8 attr);
void con_putc(char c);           /* also mirrored to serial */
void con_puts(const char *s);
void con_newline(void);
int  con_col(void);              /* current cursor column (for word-wrap) */
int  con_cols(void);             /* total character columns (text 80, gfx W/8) */
int  con_rows(void);             /* total character rows */

/* Serial-only output, for the headless test harness to scrape. */
void ser_puts(const char *s);

/* ---- graphics: Bochs-VBE linear framebuffer (vbe.c) ------------------- */
int  gfx_init(u32 w, u32 h);     /* 0 on success; sets a 32-bpp LFB mode    */
int  gfx_up(void);
u32  gfx_width(void);
u32  gfx_height(void);
void gfx_pixel(u32 x, u32 y, u32 rgb);
void gfx_fill(u32 rgb);
void gfx_glyph(const u8 *rows, u32 px, u32 py, u32 fg, u32 bg);
void gfx_scroll(u32 dy, u32 bg);

/* Default graphics geometry (fits the 16 MiB std-VGA default; override in the
 * Makefile with -DMBOS_GFX_W / -DMBOS_GFX_H for the hi-res target). */
#ifndef MBOS_GFX_W
#define MBOS_GFX_W 1024
#endif
#ifndef MBOS_GFX_H
#define MBOS_GFX_H 768
#endif

#endif /* MBOS_H */

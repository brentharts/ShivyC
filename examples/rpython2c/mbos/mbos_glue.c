/* mbos_glue.c -- the FFI shim the generated rpython (render.c / mbmain.c) calls.
 *
 * py2c lowers `_ct.CDLL("mbos_glue")` + `_g.mb_putc(...)` into plain
 * `extern`/`mb_putc(...)` C calls (the same mechanism json2qt.py uses to reach
 * its Wayland/canvas backend). These thin wrappers forward to console.c and
 * expose the page pointer the kernel wants rendered. Keeping them here means
 * the generated code needs nothing from the kernel but these five symbols.
 */
#include "mbos.h"

/* set by the kernel before calling mbos_render_main() */
static const char *g_page = "";

void mbos_set_page(const char *html) { g_page = html; }

/* ---- symbols the generated rpython imports via mbos_glue --------------- */
const char *mb_page(void)      { return g_page; }
void mb_putc(int c)            { con_putc((char)c); }
void mb_newline(void)          { con_newline(); }
void mb_set_attr(int a)        { con_set_attr(VGA_ATTR((u8)a, VGA_BLACK)); }
int  mb_col(void)              { return con_col(); }
void mb_clear(int a)           { con_clear(VGA_ATTR((u8)a, VGA_BLACK)); }

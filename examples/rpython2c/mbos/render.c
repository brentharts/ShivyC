/* render.c -- paint a DOM tree into the VGA text console.
 *
 * This is the freestanding analogue of json2qt.py: json2qt walks the same Node
 * tree and emits Qt widgets in a vertical box layout; here we walk it and emit
 * characters into the 80x25 text grid, doing block layout (each block element
 * on its own line, a blank line between paragraphs) and word wrapping at the
 * 80-column margin. Colour stands in for font weight/role: headings are bright,
 * links are cyan and annotated with their href.
 *
 * Keeping the traversal shaped like json2qt's means the eventual graphics-mode
 * renderer (proportional font + real box layout) can grow out of this without
 * changing the DOM contract.
 */
#include "dom.h"

void render_page(Node *doc);

/* ---- inline text with word wrap --------------------------------------- */
static void ensure_line_start(void) {
    if (con_col() > 0) con_newline();
}

/* Emit a single already-delimited word, wrapping to the next line if it would
 * cross the right margin. A single space separates words on the same line. */
static void emit_word(const char *w, int len) {
    if (len <= 0) return;
    int col = con_col();
    int need = (col > 0 ? 1 : 0) + len;         /* leading space if mid-line */
    if (col + need > con_cols()) { con_newline(); col = 0; }
    if (col > 0) con_putc(' ');
    int i; for (i = 0; i < len; i++) con_putc(w[i]);
}

/* Break a text string into words on ASCII whitespace and emit each wrapped. */
static void emit_text(const char *s) {
    const char *p = s;
    while (*p) {
        while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
        const char *ws = p;
        while (*p && !(*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
        emit_word(ws, (int)(p - ws));
    }
}

static void underline(int len, char ch) {
    con_newline();
    int i; for (i = 0; i < len && i < con_cols(); i++) con_putc(ch);
    con_newline();
}

/* ---- tag classification (mirrors json2qt block/inline split) ---------- */
static int is_heading(const char *t) {
    return mini_strcmp(t, "h1") == 0 || mini_strcmp(t, "h2") == 0 ||
           mini_strcmp(t, "h3") == 0;
}
static int is_block(const char *t) {
    return is_heading(t) || mini_strcmp(t, "p") == 0 ||
           mini_strcmp(t, "div") == 0 || mini_strcmp(t, "ul") == 0 ||
           mini_strcmp(t, "ol") == 0 || mini_strcmp(t, "li") == 0 ||
           mini_strcmp(t, "body") == 0 || mini_strcmp(t, "document") == 0 ||
           mini_strcmp(t, "title") == 0;
}

static void render_children(Node *n, u8 attr);

/* Render one node; `attr` is the inherited inline colour. */
static void render_node(Node *n, u8 attr) {
    const char *t = n->tag_name;

    if (mini_strcmp(t, "text") == 0) {
        con_set_attr(attr);
        emit_text(n->text);
        return;
    }
    if (mini_strcmp(t, "br") == 0) { con_newline(); return; }
    /* skip elements whose content is not page body text */
    if (mini_strcmp(t, "head") == 0 || mini_strcmp(t, "script") == 0 ||
        mini_strcmp(t, "style") == 0 || mini_strcmp(t, "meta") == 0) return;

    /* inline anchor: cyan text + " <href>" annotation */
    if (mini_strcmp(t, "a") == 0) {
        con_set_attr(VGA_ATTR(VGA_LCYAN, VGA_BLACK));
        render_children(n, VGA_ATTR(VGA_LCYAN, VGA_BLACK));
        if (n->href[0]) {
            con_set_attr(VGA_ATTR(VGA_DGREY, VGA_BLACK));
            /* " [href]" as one wrap unit; hrefs on basic pages are short */
            int hl = (int)mini_strlen(n->href);
            if (con_col() + 3 + hl > con_cols()) con_newline();
            else if (con_col() > 0) con_putc(' ');
            con_putc('[');
            const char *h = n->href; while (*h) con_putc(*h++);
            con_putc(']');
        }
        con_set_attr(attr);
        return;
    }

    if (is_block(t)) {
        ensure_line_start();
        u8 my = attr;
        if      (mini_strcmp(t, "h1") == 0) my = VGA_ATTR(VGA_YELLOW, VGA_BLACK);
        else if (is_heading(t))             my = VGA_ATTR(VGA_WHITE,  VGA_BLACK);
        else if (mini_strcmp(t, "title") == 0) my = VGA_ATTR(VGA_LGREEN, VGA_BLACK);

        if (mini_strcmp(t, "li") == 0) { con_set_attr(attr); emit_word("*", 1); }

        render_children(n, my);

        if (mini_strcmp(t, "h1") == 0) {
            con_set_attr(my);
            underline(con_cols(), '=');
        } else if (is_heading(t) || mini_strcmp(t, "p") == 0) {
            con_newline();
            con_newline();               /* blank line after block */
        } else {
            con_newline();
        }
        return;
    }

    /* generic inline (b, strong, span, em, ...): just recurse */
    render_children(n, attr);
}

static void render_children(Node *n, u8 attr) {
    int i;
    for (i = 0; i < n->n_children; i++) render_node(n->children[i], attr);
}

void render_page(Node *doc) {
    con_clear(VGA_ATTR(VGA_LGREY, VGA_BLACK));
    render_node(doc, VGA_ATTR(VGA_LGREY, VGA_BLACK));
}

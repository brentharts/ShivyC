/* html.c -- a tiny HTML -> DOM parser for the "basic html" step.
 *
 * This is the freestanding cousin of www2json.py: it turns a page's HTML text
 * into the same {tag, text, href, children} Node tree the renderer walks.
 * Scope is deliberately small (enough to render real basic pages): open/close
 * tags, void tags, text runs with whitespace collapsed, and the href attribute.
 * Unknown tags are kept as generic containers so their text still shows.
 */
#include "dom.h"

Node *html_parse(const char *src);   /* returns a synthetic "document" root */

/* ---- small char helpers ------------------------------------------------ */
static int is_space(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
static char lower(char c)   { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }

static int tag_eq(const char *a, const char *b) {
    /* case-insensitive compare of a parsed tag name against a literal */
    while (*a && *b) { if (lower(*a) != *b) return 0; a++; b++; }
    return *a == 0 && *b == 0;
}

static int is_void(const char *t) {
    return tag_eq(t, "br") || tag_eq(t, "hr") || tag_eq(t, "img") ||
           tag_eq(t, "input") || tag_eq(t, "meta") || tag_eq(t, "link");
}

/* ---- parser ------------------------------------------------------------ */
#define STACK_MAX 64

Node *html_parse(const char *src) {
    dom_reset();
    Node *doc = dom_new("document");
    Node *stack[STACK_MAX];
    int   sp = 0;
    stack[sp] = doc;

    const char *p = src;
    while (*p) {
        if (*p == '<') {
            /* ---- comment / doctype: skip to '>' ---- */
            if (p[1] == '!') {
                while (*p && *p != '>') p++;
                if (*p) p++;
                continue;
            }
            /* ---- close tag ---- */
            if (p[1] == '/') {
                p += 2;
                const char *ns = p;
                while (*p && *p != '>' && !is_space(*p)) p++;
                int nlen = (int)(p - ns);
                while (*p && *p != '>') p++;
                if (*p) p++;
                /* pop the matching open element if it is on the stack top */
                if (sp > 0) {
                    Node *top = stack[sp];
                    /* compare tag name */
                    int match = 1, i = 0;
                    const char *tn = top->tag_name;
                    for (i = 0; i < nlen; i++) {
                        if (tn[i] == 0 || lower(ns[i]) != lower(tn[i])) { match = 0; break; }
                    }
                    if (match && tn[nlen] == 0) sp--;
                }
                continue;
            }
            /* ---- open tag ---- */
            p++;
            const char *ns = p;
            while (*p && *p != '>' && *p != '/' && !is_space(*p)) p++;
            int nlen = (int)(p - ns);
            char *tag = dom_strdup(ns, nlen);
            /* lower-case the tag name in place */
            { int i; for (i = 0; i < nlen; i++) tag[i] = lower(tag[i]); }

            Node *el = dom_new(tag);

            /* scan attributes until '>' ; capture href="..." (or href='...') */
            while (*p && *p != '>') {
                if (is_space(*p) || *p == '/') { p++; continue; }
                const char *as = p;
                while (*p && *p != '=' && *p != '>' && !is_space(*p)) p++;
                int alen = (int)(p - as);
                const char *val = ""; int vlen = 0;
                if (*p == '=') {
                    p++;
                    char q = 0;
                    if (*p == '"' || *p == '\'') { q = *p; p++; }
                    val = p;
                    if (q) { while (*p && *p != q) p++; }
                    else   { while (*p && !is_space(*p) && *p != '>') p++; }
                    vlen = (int)(p - val);
                    if (q && *p == q) p++;
                }
                if (alen == 4 && lower(as[0]) == 'h' && lower(as[1]) == 'r' &&
                    lower(as[2]) == 'e' && lower(as[3]) == 'f') {
                    el->href = dom_strdup(val, vlen);
                }
            }
            if (*p == '>') p++;

            dom_add(stack[sp], el);
            if (!is_void(tag) && sp + 1 < STACK_MAX) stack[++sp] = el;
            continue;
        }

        /* ---- text run: collapse whitespace, skip if empty ---- */
        const char *ts = p;
        while (*p && *p != '<') p++;
        /* build collapsed text into the arena */
        int rawlen = (int)(p - ts);
        char *buf = dom_strdup(ts, rawlen);     /* scratch copy we rewrite     */
        int w = 0, i = 0, prev_space = 1;
        for (i = 0; i < rawlen; i++) {
            char c = buf[i];
            if (is_space(c)) {
                if (!prev_space) { buf[w++] = ' '; prev_space = 1; }
            } else { buf[w++] = c; prev_space = 0; }
        }
        while (w > 0 && buf[w - 1] == ' ') w--;   /* trim trailing space       */
        buf[w] = 0;
        if (w > 0) {
            Node *t = dom_new("text");
            t->text = buf;
            dom_add(stack[sp], t);
        }
    }
    return doc;
}

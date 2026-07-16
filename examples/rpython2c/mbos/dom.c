/* dom.c -- arena-backed Node construction (see dom.h). */
#include "dom.h"

/* One static arena for the whole page. 256 KiB comfortably holds the DOM plus
 * all copied text for the basic pages step 1 targets. */
#define ARENA_BYTES (256 * 1024)
static u8  g_arena[ARENA_BYTES];
static u32 g_used;

static void *arena_alloc(u32 n) {
    /* align to 4 bytes */
    g_used = (g_used + 3u) & ~3u;
    if (g_used + n > ARENA_BYTES) return 0;   /* out of arena -> null */
    void *p = &g_arena[g_used];
    g_used += n;
    return p;
}

void dom_reset(void) { g_used = 0; }

char *dom_strdup(const char *s, int len) {
    char *p = (char *)arena_alloc((u32)len + 1);
    if (!p) return (char *)"";
    mini_memcpy(p, s, (size_t)len);
    p[len] = 0;
    return p;
}

Node *dom_new(const char *tag) {
    Node *n = (Node *)arena_alloc(sizeof(Node));
    if (!n) return 0;
    n->tag_name   = tag;
    n->text       = "";
    n->href       = "";
    n->n_children = 0;
    return n;
}

void dom_add(Node *parent, Node *child) {
    if (!parent || !child) return;
    if (parent->n_children >= DOM_MAX_CHILDREN) return;
    parent->children[parent->n_children++] = child;
}

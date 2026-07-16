/* dom.h -- the mbos DOM node model.
 *
 * This mirrors minibrowser/dom.py's `Node` (tag_name / text / href / children)
 * so that a later mbos step can *replace* this hand-written C with the C that
 * py2c.py generates from dom.py -- same field names, same tree shape. The only
 * concession to bare metal is allocation: dom.py leans on the py2c arena
 * (aalloc); here we bump-allocate out of one static arena, which is the same
 * "one arena, freed in one shot" model, just sized at compile time.
 */
#ifndef MBOS_DOM_H
#define MBOS_DOM_H

#include "mbos.h"

#define DOM_MAX_CHILDREN 32

typedef struct Node {
    const char  *tag_name;              /* "body", "h1", "p", "a", "text", ... */
    const char  *text;                  /* text content ("" for elements)      */
    const char  *href;                  /* <a href="...">                      */
    struct Node *children[DOM_MAX_CHILDREN];
    int          n_children;
} Node;

/* arena reset + node/string construction (freestanding, no malloc). */
void  dom_reset(void);
Node *dom_new(const char *tag);         /* Node(tag); text/href default to ""  */
void  dom_add(Node *parent, Node *child);
char *dom_strdup(const char *s, int len); /* copy len bytes into the arena     */

#endif /* MBOS_DOM_H */

/* `b` is never freed and never escapes the function. Escape/region analysis
 * proves it is dead at function exit, so:
 *
 *   python3 -m shivyc.main examples/memory/autofree_leak.c --auto-free -o leak
 *
 * inserts free(b) automatically -- the programmer may omit it. `a` is already
 * freed by hand, so it is left untouched (no double-free). */
#include <stdlib.h>

int main(void) {
    int *a = malloc(sizeof(int));
    int *b = malloc(sizeof(int));      /* leaked: never freed, never escapes */
    *a = 1;
    *b = 2;
    int r = *a + *b;
    free(a);                           /* b is forgotten -> auto-free closes it */
    return r;
}

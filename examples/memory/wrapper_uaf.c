/* Whole-program use-after-free: the free happens inside one helper and the
 * dereference inside another. Neither function alone is wrong; the bug only
 * exists across the call graph, which is exactly what ShivyCX analyzes. */
#include <stdlib.h>

void my_free(int *q) { free(q); }      /* summary: frees parameter 0 */
int  use(int *p)     { return *p; }    /* summary: dereferences parameter 0 */

int main(void) {
    int *p = malloc(sizeof(int));
    my_free(p);
    return use(p);                     /* use-after-free, found via summaries */
}

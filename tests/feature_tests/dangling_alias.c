/* The canonical use-after-free: a second pointer (alias) outlives the free.
 *
 *   python3 -m shivyc.main examples/memory/dangling_alias.c --check-memory
 *
 * ShivyCX tracks malloc -> alias -> free across the whole program and reports
 * the dereference of `alias` after `data`'s allocation has been freed. */
#include <stdlib.h>
#include <stdio.h>

int main(void) {
    int *data = malloc(sizeof(int));
    *data = 42;
    int *alias = data;          /* second pointer to the same allocation */
    free(data);
    printf("%d\n", *alias);     /* dangling: use-after-free */
    return 0;
}

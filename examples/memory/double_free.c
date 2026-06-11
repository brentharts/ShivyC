/* Freeing the same allocation twice. --check-memory reports the second free. */
#include <stdlib.h>

int main(void) {
    int *p = malloc(sizeof(int));
    free(p);
    free(p);                    /* double-free */
    return 0;
}

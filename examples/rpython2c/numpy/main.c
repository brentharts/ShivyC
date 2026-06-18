/* Harness for the transpiled rpython numeric kernels. */
#include "shivyc_rt.h"
#include <stdio.h>
obj sum_squares(int n);
obj count_primes(int limit);
int main(void) {
    printf("sum_squares(100000) = %ld\n", AS_INT(sum_squares(100000)));
    printf("count_primes(100000) = %ld\n", AS_INT(count_primes(100000)));
    return 0;
}

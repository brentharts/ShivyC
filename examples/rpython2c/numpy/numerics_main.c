#include "shivyc_rt.h"
#include <stdio.h>
double pi_leibniz(int terms);
double e_series(int terms);
double sqrt_newton(int n, int iters);
double logistic_final(int steps);
int main(void) {
    printf("pi_leibniz(1e7)   = %.6f (expect ~3.141593)\n", pi_leibniz(10000000));
    printf("e_series(20)      = %.6f (expect ~2.718282)\n", e_series(20));
    printf("sqrt_newton(2,50) = %.6f (expect ~1.414214)\n", sqrt_newton(2, 50));
    printf("logistic(1000)    = %.6f\n", logistic_final(1000));
    return 0;
}

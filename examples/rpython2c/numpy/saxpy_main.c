#include <stdio.h>
#include <stdlib.h>
void saxpy(float alpha, float* x, float* y, float* out, int n);
void vadd(float* x, float* y, float* out, int n);
int main(void) {
    int n = 1024;
    float *x = malloc(n*4), *y = malloc(n*4), *o = malloc(n*4);
    for (int i = 0; i < n; i++) { x[i] = i; y[i] = 2*i; }
    saxpy(3.0f, x, y, o, n);
    printf("saxpy: out[100]=%.1f (expect 500.0)\n", o[100]);
    vadd(x, y, o, n);
    printf("vadd:  out[100]=%.1f (expect 300.0)\n", o[100]);
    return 0;
}

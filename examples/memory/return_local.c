/* Returns the address of a local; gcc -O2 compiles it without error, but the
 * stack frame is gone by the time the caller dereferences the pointer. */
int *get_value(void) {
    int local_val = 5;
    return &local_val;
}
int main(void) {
    int *p = get_value();
    return *p;
}

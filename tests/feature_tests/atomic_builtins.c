/* GCC __atomic_* builtins, used throughout CPython's pyatomic_gcc.h. On
   ShivyCX's single-threaded target these are the equivalent plain memory
   operations. Returns 0 on success. */

int main() {
    int x = 10;

    if (__atomic_fetch_add(&x, 5, 0) != 10) return 1;   /* returns old */
    if (x != 15) return 2;

    if (__atomic_load_n(&x, 0) != 15) return 3;

    __atomic_store_n(&x, 100, 0);
    if (x != 100) return 4;

    int ret;
    __atomic_load(&x, &ret, 0);                          /* pointer form */
    if (ret != 100) return 5;

    if (__atomic_exchange_n(&x, 7, 0) != 100) return 6;  /* returns old */
    if (x != 7) return 7;

    int expected = 7;
    if (!__atomic_compare_exchange_n(&x, &expected, 42, 0, 0, 0)) return 8;
    if (x != 42) return 9;                               /* CAS succeeded */

    expected = 999;                                      /* now a mismatch */
    if (__atomic_compare_exchange_n(&x, &expected, 1, 0, 0, 0)) return 10;
    if (expected != 42) return 11;                       /* loads current */

    if (__atomic_add_fetch(&x, 8, 0) != 50) return 12;   /* returns new */
    if (__atomic_fetch_or(&x, 1, 0) != 50) return 13;
    if (x != 51) return 14;

    return 0;
}

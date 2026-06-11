/* C11 type qualifiers/specifiers: _Atomic (qualifier and paren forms),
   volatile, and restrict. ShivyCX targets a single-threaded runtime, so these
   are accepted and treated as their underlying type. Returns 0 on success. */

_Atomic int atomic_global;          /* _Atomic as a type qualifier */
_Atomic(long) paren_global;         /* _Atomic(T) specifier form   */
typedef _Atomic _Bool atomic_bool;  /* qualifier before another type */

int sum_restrict(int *restrict a, int *restrict b) {
    return *a + *b;
}

int main() {
    atomic_global = 5;
    if (atomic_global != 5) return 1;

    paren_global = 100;
    paren_global += 23;
    if (paren_global != 123) return 2;

    atomic_bool flag = 1;
    if (!flag) return 3;

    volatile int v = 9;
    v += 1;
    if (v != 10) return 4;

    int x = 3, y = 4;
    if (sum_restrict(&x, &y) != 7) return 5;

    _Atomic(int) local = 42;        /* paren form on a local */
    if (local != 42) return 6;

    const volatile int cv = 8;      /* combined qualifiers */
    if (cv != 8) return 7;

    return 0;
}

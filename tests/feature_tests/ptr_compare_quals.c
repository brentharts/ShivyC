/* C11 6.5.9p2: a pointer to void may be compared with a pointer to any object
   type regardless of qualifiers. ShivyCX previously rejected `void* == const
   void*` (while accepting `int* == const int*`) because it cast the qualified
   side down to a bare `void*`, dropping const. Returns 0 on success. */

int eq_vv(void *p, const void *q)       { return p == q; }
int eq_cv(const void *p, void *q)       { return p == q; }
int ne_cc(const void *p, const void *q) { return p != q; }
int eq_iv(int *p, const void *q)        { return p == q; }   /* object* vs const void* */
int eq_ii(int *p, const int *q)         { return p == q; }   /* the already-working case */

int main() {
    int a = 0, b = 0;
    if (eq_vv(&a, &a) != 1) return 1;
    if (eq_cv(&a, &a) != 1) return 2;
    if (ne_cc(&a, &b) != 1) return 3;     /* &a != &b */
    if (eq_iv(&a, &a) != 1) return 4;
    if (eq_iv(&a, &b) != 0) return 5;     /* &a == &b is false */
    if (eq_ii(&a, &a) != 1) return 6;

    /* the original failing shape from mimalloc: (p == q ? (void*)0 : p) */
    void *p = &a;
    const void *q = &a;
    void *r = (p == q ? ((void *)0) : p);
    if (r != ((void *)0)) return 7;

    return 0;
}

/* GCC extensions found throughout real-world C (glibc headers, CPython macros):
   statement expressions ({ ... }), __auto_type, and __typeof__. Returns 0 on
   success. */

int main() {
    /* statement expression yielding the last expression's value */
    int a = ({ 3 + 4; });
    if (a != 7) return 1;

    /* statement expression with declarations and control flow */
    int b = ({ int t = 5; if (t < 10) t = t * 2; t; });
    if (b != 10) return 2;

    /* glibc spelling: __extension__ ({ ... }) */
    int c = __extension__ ({ int s = 6; s + 1; });
    if (c != 7) return 3;

    /* __auto_type infers from the initializer */
    __auto_type d = 8 + 1;
    if (d != 9) return 4;

    int v = 12;
    __auto_type p = &v;             /* p is int* */
    if (*p != 12) return 5;

    /* __typeof__ takes the type of an expression */
    __typeof__(v) e = 20;
    if (e != 20) return 6;

    typeof(*p) f = 30;              /* typeof alias, expression with deref */
    if (f != 30) return 7;

    /* the atomic-macro shape: __auto_type + deref inside a statement expr */
    int g = __extension__ ({ __auto_type pp = &v; *pp + 3; });
    if (g != 15) return 8;

    /* nested statement expressions */
    int h = ({ 2; }) + ({ ({ 3; }); });
    if (h != 5) return 9;

    return 0;
}

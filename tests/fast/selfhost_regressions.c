/* Regression coverage for native (self-hosted) code-generation bugs that were
 * found and fixed while bootstrapping ShivyCX. Each block exercises one fixed
 * bug; the program folds every result into a single deterministic exit code so
 * it can be checked three ways (native self-host compiler, gcc, CPython oracle)
 * and required to agree. Keep the exit code in 0..255.
 *
 * Covered (all FIXED in the native compiler):
 *   - array size deduction from an initializer  (int a[]={...}, char s[]="..")
 *   - unsigned / suffixed integer literals       (u, U, UL, unsigned hex)
 *   - unsigned integer comparison operand sizing  (var/var, var/lit, ==)
 *   - pointer compound assignment                 (p += N on a complete pointee)
 *   - pointer equality / inequality between variables  (p == q, p != q)
 *   - 64-bit immediate stored to memory           (mov mem, imm64 via register)
 *   - C11 anonymous struct/union members           (promoted into enclosing type)
 *   - floating-point constant folding              (+,-,*,/ and unary - on doubles)
 *   - floating-point function arguments            (doubles passed in xmm regs)
 *   - usual arithmetic conversions int (op) long   (widen to long, no truncation)
 *   - function-like macros                        (column-adjacency handling)
 */

#define ADD(a, b) ((a) + (b))
#define SUM3(a, b, c) ((a) + (b) + (c))

/* C11 anonymous struct/union members: the inner members are accessible
 * directly on the enclosing object. */
struct AnonHost {
    int tag;
    union {
        int i;
        char c;
    };
    struct {
        int a, b;
    };
};

/* floating-point arguments travel in xmm registers; mixed with integer args
 * each class is counted independently. */
static double fadd2(double a, double b) { return a + b; }
static double fmix(int a, double b, int c, double d) { return a + b + c + d; }

int main(void) {
    int total = 0;

    /* array size deduction */
    int da[] = {1, 2, 3, 4};
    char ds[] = "abc";
    if (sizeof(da) == 16) total += 1;          /* 4 ints */
    if (sizeof(ds) == 4)  total += 2;          /* "abc" + NUL */
    if (da[3] == 4)       total += 4;
    if (ds[1] == 'b')     total += 8;

    /* unsigned / suffixed integer literals */
    unsigned int  u1 = 0xFFu;
    unsigned int  u2 = 0x80000000u;
    unsigned long ul = 0xFFUL;                 /* small value, no imm64 */
    if (u1 == 255u)              total += 16;
    if ((u2 >> 31) == 1u)        total += 32;
    if (ul == 255u)              total += 64;

    /* unsigned comparison operand sizing */
    unsigned int a = 10, b = 5;
    unsigned int big = 3000000000u;
    if (a == 10)                 total += 1;    /* var/lit == */
    if (big > 2000000000u)       total += 2;    /* var/lit > (was ambiguous) */
    if (a == b + 5)              total += 4;    /* var/var == (was wrong) */

    /* pointer compound assignment */
    int pa[] = {5, 6, 7, 8};
    int *p = pa;
    p += 2;
    if (*p == 7)                 total += 8;

    /* pointer equality / inequality between two pointer variables (distinct
     * from comparison against a null/literal, which always worked) */
    int *pe = pa;
    int *pf = pa + 1;
    if (pe == pa)                total += 1;    /* p == q  (true)  */
    if (pe != pf)                total += 2;    /* p != q  (true)  */
    if (!(pe == pf))             total += 4;    /* p == q  (false) */

    /* 64-bit immediate stored to memory then used: `mov mem, imm64` is not
     * encodable, so the value must route through a register. A value > 32 bits
     * must not wrap into the 32-bit range. */
    long w = 10000000000L;                      /* 10^10, needs 34 bits */
    if (w > 5000000000L)         total += 8;
    if (w == 10000000000L)       total += 16;
    if ((w >> 33) == 1L)         total += 32;

    /* function-like macros */
    if (ADD(3, 4) == 7)          total += 16;
    if (SUM3(1, 2, 3) == 6)      total += 32;

    /* C11 anonymous struct/union members promoted into the enclosing struct */
    struct AnonHost h;
    h.tag = 1;
    h.i = 65;                                   /* anonymous union member  */
    h.a = 10;                                   /* anonymous struct member */
    h.b = 20;
    if (h.c == 65)               total += 64;   /* union alias of i (LE)   */
    if (h.a + h.b == 30)         total += 128;
    if (sizeof(struct AnonHost) >= 12) total += 1;  /* members occupy space */

    /* floating-point constant folding: +,-,*,/ on two double constants, and
     * unary minus on a double constant, must not collapse to zero. */
    if ((int)(2.0 + 3.0) == 5)   total += 2;
    if ((int)(6.0 / 2.0) == 3)   total += 4;
    if ((int)(3.0 * 4.0) == 12)  total += 8;
    if ((int)(7.0 - 2.0) == 5)   total += 16;
    if ((int)(-2.5) == -2)       total += 32;

    /* floating-point arguments passed in xmm registers, including mixed with
     * integer arguments (each ABI sequence counted independently). */
    if ((int)fadd2(1.5, 2.5) == 4)         total += 64;
    if ((int)fmix(1, 2.0, 3, 4.0) == 10)   total += 128;

    /* usual arithmetic conversions: `int (op) long` must widen to long, even
     * when the int operand is on the left. Computing as int would truncate
     * (and give the wrong sizeof). */
    if (sizeof(1 + 1L) == 8)               total += 1;
    if (sizeof(1 * 1L) == 8)               total += 2;
    {
        int i = 100000;
        long prod = i * 100000L;           /* 10^10: overflows a 32-bit int */
        if (prod == 10000000000L)          total += 4;
    }

    return total & 0xFF;
}

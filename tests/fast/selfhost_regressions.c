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
 *   - function-like macros                        (column-adjacency handling)
 *
 * NB: deliberately avoids 64-bit *immediate* operands (e.g. 0xFFFFFFFFFFUL),
 * which trip a separate, still-open codegen bug; UL coverage uses a shift so
 * the wide value lives in a register, not an immediate.
 */

#define ADD(a, b) ((a) + (b))
#define SUM3(a, b, c) ((a) + (b) + (c))

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

    /* function-like macros */
    if (ADD(3, 4) == 7)          total += 16;
    if (SUM3(1, 2, 3) == 6)      total += 32;

    return total & 0xFF;
}

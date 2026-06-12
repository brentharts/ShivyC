/* x86-64 `mov mem, imm` takes only a 32-bit (sign-extended) immediate, so a
   wider literal stored to memory must be routed through a register. ShivyCX
   previously emitted `mov QWORD PTR [..], <imm64>`, which the assembler
   rejects. These constants (PY_SSIZE_T_MAX, LLONG_MIN) appear in CPython's
   enumobject.c and sliceobject.c. Returns 0 on success. */

struct S { long a; long b; };

int main(void) {
    long x;
    long *p = &x;

    *p = 9223372036854775807L;            /* INT64_MAX */
    if (*p != 9223372036854775807L) return 1;

    *p = -9223372036854775807L;           /* near INT64_MIN */
    if (*p != -9223372036854775807L) return 2;

    *p = 5000000000L;                     /* > 32 bits, positive */
    if (*p != 5000000000L) return 3;

    *p = 2147483647L;                     /* 32-bit edge stays a direct store */
    if (*p != 2147483647L) return 4;
    *p = -2147483648L;
    if (*p != -2147483648L) return 5;

    struct S s;
    struct S *sp = &s;
    sp->b = 9000000000000000000L;         /* wide store to a struct member */
    if (sp->b != 9000000000000000000L) return 6;

    long arr[2];
    arr[1] = 0x7FFFFFFFFFFFFFFFL;          /* wide store to an array element */
    if (arr[1] != 0x7FFFFFFFFFFFFFFFL) return 7;

    return 0;
}

/* x86-64 `push imm` encodes only a 32-bit immediate, so passing a wider literal
   as a stack argument (the 7th onward) cannot push it directly. ShivyCX used to
   emit `push <imm64>`, which the assembler rejects; these bounds (LLONG_MAX /
   LLONG_MIN) are passed as arguments in CPython's _struct.c. Returns 0. */

/* 8 long params: the first 6 go in registers, the 7th and 8th on the stack */
static long pick7(long a, long b, long c, long d, long e, long f,
                  long g, long h) {
    (void)a; (void)b; (void)c; (void)d; (void)e; (void)f;
    return g + h;
}

int main(void) {
    /* wide positive stack args */
    if (pick7(1, 2, 3, 4, 5, 6, 9223372036854775807L, 0L)
            != 9223372036854775807L) return 1;
    /* wide negative stack args */
    if (pick7(1, 2, 3, 4, 5, 6, 0L, -9223372036854775807L)
            != -9223372036854775807L) return 2;
    /* both stack args wide */
    if (pick7(1, 2, 3, 4, 5, 6, 5000000000L, 4000000000L)
            != 9000000000L) return 3;
    /* > 32-bit and a small one mixed */
    if (pick7(1, 2, 3, 4, 5, 6, 0x100000000L, 7L)
            != 0x100000007L) return 4;
    /* small stack args still use a direct push */
    if (pick7(1, 2, 3, 4, 5, 6, 10L, 20L) != 30L) return 5;
    return 0;
}

/* GCC builtins used by CPython/mimalloc, plus float-overflow-to-infinity.
   Returns 0 on success. */

int main() {
    /* a literal beyond the type's range becomes IEEE infinity, not an error */
    float finf = 1e40f;
    double dinf = 1e400;
    if (!(finf > 1e38f)) return 1;
    if (!(dinf > 1e308)) return 2;
    if (!(__builtin_inff() > 1e38f)) return 3;

    /* count leading / trailing zeros (64-bit) */
    if (__builtin_clzl(1UL) != 63) return 4;
    if (__builtin_clzl(0x8000000000000000UL) != 0) return 5;
    if (__builtin_ctzl(8UL) != 3) return 6;
    if (__builtin_ctzl(1UL) != 0) return 7;
    if (__builtin_clz(1U) != 31) return 8;
    if (__builtin_ctz(0x10U) != 4) return 9;

    /* checked unsigned multiply */
    unsigned long out;
    if (__builtin_umull_overflow(6UL, 7UL, &out) != 0) return 10;
    if (out != 42) return 11;
    if (__builtin_umull_overflow(0xFFFFFFFFFFFFFFFFUL, 2UL, &out) == 0) return 12;

    return 0;
}

/* GCC __builtin_popcount family (count set bits), used by CPython's longobject
   and dictobject. Returns 0 on success. */

int main(void) {
    if (__builtin_popcount(0u) != 0) return 1;
    if (__builtin_popcount(0xFFu) != 8) return 2;
    if (__builtin_popcount(0x55555555u) != 16) return 3;
    if (__builtin_popcountl(0x101UL) != 2) return 4;
    if (__builtin_popcountl(0xFFFFFFFFFFFFFFFFUL) != 64) return 5;
    if (__builtin_popcountll(0x8000000000000001UL) != 2) return 6;
    return 0;
}

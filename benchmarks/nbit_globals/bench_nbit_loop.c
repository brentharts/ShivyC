/* Benchmark 3: _Nbit loop register-promotion (the case where packing wins).
 *
 * A hot loop reads and writes four 7-bit packed globals every iteration. Three
 * compilers, one source; only the flag-access strategy differs.
 *
 *  - ShivyCX off / gcc -O0: each global access is a memory load/store.
 *  - -fsimd-pack-globals WITHOUT loop promotion: each access is an xmm15
 *    decompress (read) or recompress (write), *per iteration* -- strictly more
 *    work than a plain load, so the tight loop is a large regression.
 *  - -fsimd-pack-globals WITH loop promotion (shivyc/simd_pack_promote.py):
 *    the four globals are decompressed into GP registers once before the loop,
 *    the body is pure register arithmetic, and they are recompressed once
 *    after. The per-iteration xmm15 (and memory) traffic disappears entirely.
 *
 * Measured (this machine): pack+promotion beats un-promoted packing ~4.8x, the
 * memory-global baseline ~1.4x, and gcc -O0 ~1.8x. The win is real because the
 * promotion keeps the live flag values in registers across the whole loop --
 * the decompress/recompress pay the packing cost once per loop instead of once
 * per iteration. Promotion is applied only when it is provably safe (no call or
 * return inside the loop, the loop has a single exit merge, and the globals'
 * addresses are never taken); see the pass for the full conditions.
 */
unsigned char a_7bit, b_7bit, c_7bit, d_7bit;

int run(int n) {
  int i;
  a_7bit = 0;
  b_7bit = 1;
  c_7bit = 2;
  d_7bit = 3;
  for (i = 0; i < n; i++) {
    a_7bit = (a_7bit + b_7bit) & 127;
    b_7bit = (b_7bit + c_7bit) & 127;
    c_7bit = (c_7bit + d_7bit) & 127;
    d_7bit = (d_7bit + 7) & 127;
  }
  return a_7bit + b_7bit + c_7bit + d_7bit;
}

int main() {
  long s = 0;
  long k = 0;
  while (k < 300) {
    s = s + run(1000000);
    k = k + 1;
  }
  /* Stable witness shared by every compiler for differential correctness. */
  return (int)(s % 250);
}

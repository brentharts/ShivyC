/* Benchmark: contracts -> fallback-free SIMD reduction.
 *
 * The two precondition clauses tell ShivyCX, at compile time, that the array
 * length is a multiple of 4 (the SSE2 int32 width) and at least 64. Because
 * the compiler can see the whole call graph and prove every call site passes a
 * provably-aligned length (the malloc byte size and the call length are
 * literals it can read back), it replaces the scalar reduction body with a
 * hand-written SSE2 loop that has NO scalar remainder and NO runtime guard --
 * a guarantee gcc/clang cannot make without runtime checks or
 * __builtin_assume.
 *
 * The `assert ...` clauses live between the parameter list and the body and
 * are stripped to plain C before lexing, so without them (see
 * bench_contracts_baseline.c) the identical loop compiles to ShivyCX's
 * ordinary scalar code.
 */
void *malloc(unsigned long);

int calc_sum(int *ptr, unsigned int len)
assert len(ptr) >= 64
assert not len(ptr) % 4
{
  int v = 0;
  unsigned int i = 0;
  for (i = 0; i < len; i = i + 1) {
    v = v + ptr[i];
  }
  return v;
}

int main() {
  /* literal sizes so the whole-program proof can read them back */
  int *a = malloc(4096 * 4);
  unsigned int i = 0;
  for (i = 0; i < 4096; i = i + 1) {
    a[i] = (i % 7);
  }
  long total = 0;
  long r = 0;
  while (r < 400000) {
    total = total + calc_sum(a, 4096);
    r = r + 1;
  }
  return (int)(total % 250);
}

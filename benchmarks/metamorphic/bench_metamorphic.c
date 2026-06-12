/* Benchmark: -fmetamorphic + __metamorphic__ specifier (experimental).
 *
 * A __metamorphic__ leaf returns without using the stack for its return
 * address: each caller patches an 8-byte return slot sitting in a writable+
 * executable .mtext section and JUMPs into the function, which returns by
 * jumping back through the slot -- no call/ret, no return-address push.
 *
 * The slot is self-modified on every call. This benchmark calls the leaf in a
 * tight loop on purpose, to measure what that costs: writing into an RWX page
 * that also holds executing code triggers the CPU's self-modifying-code
 * machinery. The feature is documented as experimental and meant for rare,
 * tightly-controlled hot paths, NOT high-frequency calls -- this benchmark is
 * the stress test that shows why.
 *
 * gcc cannot parse __metamorphic__ (a ShivyCX extension), so its reference and
 * the ShivyCX feature-off baseline use bench_metamorphic_baseline.c: the same
 * leaf as an ordinary function.
 */
int helper(int x) __metamorphic__ { return x * 3 + 1; }

int main() {
  long s = 0;
  long i = 0;
  while (i < 5000000) {
    s = s + helper((int)(i & 1023));
    i = i + 1;
  }
  return (int)(s % 250);
}

/* Benchmark: _Nbit globals (SIMD bit-packing into xmm15).
 *
 * `irq_handler` is a "hot" function (name matches the *_handler heuristic),
 * so under -fsimd-pack-globals every read of a packed flag becomes a register
 * bit-extraction from xmm15 instead of a memory load. The driver calls the
 * handler in a tight loop, modelling an interrupt/callback that re-reads its
 * configuration flags on every entry.
 *
 * The same source compiles unchanged with gcc and with ShivyCX without the
 * flag; only the flag-access strategy differs, which is exactly what we want
 * to isolate. The `_Nbit` suffix is a naming convention, not a type change,
 * so the program is ordinary C to every other toolchain.
 */
unsigned char enabled_1bit;
unsigned char prio_3bit;
unsigned char level_4bit;
unsigned char mask_5bit;
unsigned char mode_2bit;

/* Hot: reads five distinct packed flags. Under packing this is one xmm15
 * refresh + five register extractions; otherwise five memory loads. */
int irq_handler() {
  int acc = 0;
  int e = enabled_1bit;
  int p = prio_3bit;
  int l = level_4bit;
  int m = mask_5bit;
  int o = mode_2bit;
  if (e) {
    acc = acc + p;
    acc = acc + l;
    acc = acc + m;
    acc = acc + o;
  }
  return acc;
}

int main() {
  enabled_1bit = 1;
  prio_3bit = 5;
  level_4bit = 9;
  mask_5bit = 17;
  mode_2bit = 2;

  long sum = 0;
  long i = 0;
  while (i < 120000000) {
    sum = sum + irq_handler();
    i = i + 1;
  }
  /* Stable, nonzero witness shared by every compiler for differential
   * correctness checking. acc per call = 5+9+17+2 = 33. */
  return (int)(sum % 250);
}

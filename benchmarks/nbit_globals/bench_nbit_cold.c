/* Benchmark 2: _Nbit globals under cache pressure (the "cold flag" stress test).
 *
 * The first _Nbit benchmark reads its flags in a tight loop, so they stay
 * L1-resident and packing is runtime-neutral. This one chases the regime where
 * packing should win: a handler whose configuration flags are *cold*, because
 * real work ran between invocations. Each outer step sums a multi-megabyte
 * buffer (genuine memory work that evicts the caches) and only then calls
 * `irq_handler`, which reads sixteen distinct packed flags --- each forced onto
 * its own cache line by padding, so without packing the handler issues sixteen
 * separate, now-cold loads.
 *
 * Static (codegen) witness: under -fsimd-pack-globals the sixteen flag loads in
 * the handler collapse to a *single* 64-bit read of the packed mirror (then
 * sixteen register bit-extractions from xmm15). That N-loads-to-1 reduction is
 * exactly what gcc cannot express, and it scales with the number of flags.
 *
 * Runtime finding: even cold, wall time stays at parity. The sixteen flag loads
 * have independent (fixed) addresses, so the out-of-order core issues them in
 * parallel and memory-level parallelism collapses their combined latency to
 * about a single miss --- the same one miss the packed mirror itself pays --
 * while packing's per-flag register extraction adds ALU work. The win is real
 * in instructions and in the memory regime it targets (serialized, bandwidth-
 * or TLB-bound access at interrupt entry), but a single-threaded user-space
 * loop cannot reproduce that regime, so this benchmark documents the boundary
 * rather than crossing it. The same source compiles unchanged with gcc and with
 * ShivyCX without the flag; only the flag-access strategy differs.
 */
unsigned char ctl0_4bit;  unsigned char pad0[64];
unsigned char ctl1_4bit;  unsigned char pad1[64];
unsigned char ctl2_4bit;  unsigned char pad2[64];
unsigned char ctl3_4bit;  unsigned char pad3[64];
unsigned char ctl4_4bit;  unsigned char pad4[64];
unsigned char ctl5_4bit;  unsigned char pad5[64];
unsigned char ctl6_4bit;  unsigned char pad6[64];
unsigned char ctl7_4bit;  unsigned char pad7[64];
unsigned char ctl8_4bit;  unsigned char pad8[64];
unsigned char ctl9_4bit;  unsigned char pad9[64];
unsigned char ctl10_4bit; unsigned char pad10[64];
unsigned char ctl11_4bit; unsigned char pad11[64];
unsigned char ctl12_4bit; unsigned char pad12[64];
unsigned char ctl13_4bit; unsigned char pad13[64];
unsigned char ctl14_4bit; unsigned char pad14[64];
unsigned char ctl15_4bit; unsigned char pad15[64];

/* 4 MiB: larger than L2, so a full sweep evicts the flag lines to L3/DRAM. */
unsigned char work[4 * 1024 * 1024];

/* Genuine memory work between handler calls; also the cache evictor. */
long workload() {
  long s = 0;
  long k = 0;
  while (k < 4 * 1024 * 1024) {
    s = s + work[k];
    k = k + 64;
  }
  return s;
}

/* Hot: reads sixteen distinct packed flags. Under packing this is one xmm15
 * refresh plus sixteen register extractions; otherwise sixteen memory loads. */
int irq_handler() {
  return ctl0_4bit + ctl1_4bit + ctl2_4bit + ctl3_4bit
       + ctl4_4bit + ctl5_4bit + ctl6_4bit + ctl7_4bit
       + ctl8_4bit + ctl9_4bit + ctl10_4bit + ctl11_4bit
       + ctl12_4bit + ctl13_4bit + ctl14_4bit + ctl15_4bit;
}

int main() {
  ctl0_4bit = 1;  ctl1_4bit = 2;  ctl2_4bit = 3;  ctl3_4bit = 4;
  ctl4_4bit = 5;  ctl5_4bit = 6;  ctl6_4bit = 7;  ctl7_4bit = 8;
  ctl8_4bit = 9;  ctl9_4bit = 10; ctl10_4bit = 11; ctl11_4bit = 12;
  ctl12_4bit = 13; ctl13_4bit = 14; ctl14_4bit = 15; ctl15_4bit = 0;

  long j = 0;
  while (j < 4 * 1024 * 1024) { work[j] = (unsigned char)(j * 3 + 1); j = j + 1; }

  long sum = 0;
  long i = 0;
  while (i < 5000) {
    sum = sum + workload();    /* real work; evicts the flag cache lines */
    sum = sum + irq_handler(); /* handler reads its now-cold config flags */
    i = i + 1;
  }
  /* Stable witness: handler returns 1+2+...+15+0 = 120 each call. */
  return (int)(sum % 250);
}

/* Benchmark: -f-pack-args (bit-packed calling convention).
 *
 * A chain of functions each take eight char parameters. Under the System V
 * ABI the first six go in registers and the rest spill to the stack; under
 * -f-pack-args all eight pack into a single 64-bit register (eight bytes), so
 * the call passes one register and spills nothing. The chain is nested so the
 * register/stack-traffic difference compounds, and it is exercised in a tight
 * loop.
 *
 * This is the same source for gcc and for ShivyCX with the flag off; only the
 * calling convention changes, which is what we isolate. (Differential
 * correctness must hold across all three.)
 */
int l4(char a, char b, char c, char d, char e, char f, char g, char h) {
  return a + b + c + d + e + f + g + h;
}
int l3(char a, char b, char c, char d, char e, char f, char g, char h) {
  return l4(a, b, c, d, e, f, g, h) + l4(h, g, f, e, d, c, b, a);
}
int l2(char a, char b, char c, char d, char e, char f, char g, char h) {
  return l3(a, b, c, d, e, f, g, h) + l3(b, c, d, e, f, g, h, a);
}
int l1(char a, char b, char c, char d, char e, char f, char g, char h) {
  return l2(a, b, c, d, e, f, g, h) + l2(c, d, e, f, g, h, a, b);
}

int main() {
  long s = 0;
  long i = 0;
  while (i < 3000000) {
    s = s + l1((char)(i & 7), 2, 3, 4, 5, 6, 7, 8);
    i = i + 1;
  }
  return (int)(s % 250);
}

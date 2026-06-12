/* Benchmark: -f-eliminate-unused-members (whole-program struct shrink).
 *
 * `struct Rec` declares eight int fields, but the whole program only ever
 * touches `a` and `h`. Because ShivyCX sees every translation unit, it proves
 * the other six members are never accessed anywhere and removes them from the
 * layout, shrinking the type from 32 bytes to 8. The global array `table`
 * shrinks 4x in .bss as a direct result.
 *
 * gcc cannot do this at any optimization level: struct layout is fixed by the
 * ABI, so an unused member still occupies its bytes. The program's observable
 * behaviour is identical with the flag on or off.
 */
struct Rec {
  int a;
  int b;  /* unused */
  int c;  /* unused */
  int d;  /* unused */
  int e;  /* unused */
  int f;  /* unused */
  int g;  /* unused */
  int h;
};

struct Rec table[1000];

int main() {
  int i = 0;
  int s = 0;
  for (i = 0; i < 1000; i = i + 1) {
    table[i].a = i;
    table[i].h = i * 2;
  }
  for (i = 0; i < 1000; i = i + 1) {
    s = s + table[i].a + table[i].h;
  }
  return s % 250;
}

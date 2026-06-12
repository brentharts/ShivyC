/* Benchmark: -fstackless-calls (direct calls + tail-call elimination +
 * frame-pointer omission).
 *
 * This is the deeply-nested call pattern jitbit/OpenSourceJesus use as their
 * motivating case. Each level is a thin wrapper; without the flag every level
 * pays a full framed call (push rbp / mov / sub / lea+call rax / leave / ret).
 * With -fstackless-calls, tail wrappers collapse to a frameless `jmp`, and
 * statically-known callees use a direct `call` with no address-load register.
 *
 * Identical C for gcc and for ShivyCX with the flag off; only call lowering
 * changes, which is what we isolate.
 */
int sum(int a, int b) { return a + b; }
int foo(int n) { return sum(n, 1); }
int bar(int n) { return foo(n) + sum(n, 2); }
int boo(int n) { return bar(n) + sum(n, 3); }
int zoo(int n) { return boo(n) + sum(n, 4); }

int main() {
  long s = 0;
  long i = 0;
  while (i < 30000000) {
    s = s + zoo((int)(i & 7));
    i = i + 1;
  }
  return (int)(s % 250);
}

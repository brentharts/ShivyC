"""SIMD benchmark vs gcc -O2 (rpython). The iteration count is read from the
command line (sys.argv) so gcc -O2 cannot constant-fold the whole program to its
final value. The kernel is an in-place recurrence x += y, so each iteration
depends on the previous one -- gcc cannot hoist it out of the loop either. Both
ShivyCX (via the len(x)%4 contract) and gcc -O2 vectorize the inner loop; this
measures ShivyCX's contract SIMD against a real optimizing compiler."""
import sys


def vadd(a: "f32*", b: "f32*", out: "f32*", n) -> None:
    assert len(a) % 4 == 0
    i = 0
    while i < n:
        out[i] = a[i] + b[i]
        i = i + 1


def main() -> int:
    reps = int(sys.argv[1])
    x: "f32*" = malloc(8192 * 4)
    y: "f32*" = malloc(8192 * 4)
    for i in range(8192):
        x[i] = i
        y[i] = 1
    for r in range(reps):
        vadd(x, y, x, 8192)         # x[i] += y[i]  (recurrence, not hoistable)
    return int(x[100]) % 250        # 100 + reps  (mod 250) -> depends on argv

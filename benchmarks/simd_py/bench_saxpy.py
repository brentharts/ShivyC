"""SIMD benchmark kernel (rpython). The `assert len(x) % 4 == 0` contract lets
ShivyCX prove 4-wide alignment at the call site and emit a packed-single SSE
saxpy (mulps + addps); stripped, the identical loop is scalar. The repeat loop
makes the element loop dominate wall time."""


def saxpy(alpha: "f32", x: "f32*", y: "f32*", out: "f32*", n) -> None:
    assert len(x) % 4 == 0
    i = 0
    while i < n:
        out[i] = alpha * x[i] + y[i]
        i = i + 1


def main() -> int:
    x: "f32*" = malloc(8192 * 4)
    y: "f32*" = malloc(8192 * 4)
    out: "f32*" = malloc(8192 * 4)
    for i in range(8192):
        x[i] = i
        y[i] = i
    for reps in range(120000):
        saxpy(2.0, x, y, out, 8192)
    return int(out[100]) % 250        # 2*100 + 100 = 300 -> 50

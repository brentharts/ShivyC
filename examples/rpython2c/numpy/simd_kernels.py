"""Self-contained rpython SIMD kernels -- compile & run with ShivyCX directly:

    python3 -m shivyc.main examples/rpython2c/numpy/simd_kernels.py -o simd && ./simd
    echo $?      # 55  (30 + 4 + 21)

No .c harness and no shell script: main.py now accepts .py sources, transpiles
them through tools/py2c.py, supplies the support C, compiles and links. Every
kernel below is proven SIMD-safe by ShivyCX's contract analysis and lowered to
packed SSE (addps / mulps / sqrtps) -- no scalar remainder.

Kernels demonstrated:
  vadd256  fixed-size, NO length arg and NO assert -- the f32[256] sizes let
           py2c infer the contract and ShivyCX bake a literal trip count.
  vsqrt    single-input map  out[i] = sqrt(x[i])  -> sqrtps.
  fma      fused multiply-add out[i] = a[i]*b[i] + c[i] -> mulps + addps.
"""


def vadd256(x: "f32[256]", y: "f32[256]", out: "f32[256]") -> None:
    i = 0
    while i < 256:
        out[i] = x[i] + y[i]
        i = i + 1


def vsqrt(x: "f32*", out: "f32*", n) -> None:
    assert len(x) % 4 == 0
    i = 0
    while i < n:
        out[i] = sqrt(x[i])
        i = i + 1


def fma(a: "f32*", b: "f32*", c: "f32*", out: "f32*", n) -> None:
    assert len(a) % 4 == 0
    i = 0
    while i < n:
        out[i] = a[i] * b[i] + c[i]
        i = i + 1


def main() -> int:
    x: "f32*" = malloc(256 * 4)
    y: "f32*" = malloc(256 * 4)
    o: "f32*" = malloc(256 * 4)
    i = 0
    while i < 256:
        x[i] = i
        y[i] = 2 * i
        i = i + 1
    vadd256(x, y, o)            # o[10] = 10 + 20 = 30
    r1 = int(o[10])
    x[4] = 16.0
    vsqrt(x, o, 256)           # o[4] = sqrt(16) = 4
    r2 = int(o[4])
    fma(x, y, x, o, 256)       # o[3] = 3*6 + 3 = 21
    r3 = int(o[3])
    return (r1 + r2 + r3) % 250

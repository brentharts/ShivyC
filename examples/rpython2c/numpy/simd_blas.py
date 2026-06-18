"""More SIMD kernels: a scalar broadcast and a float dot product, both driven by
auto-proven contracts. Compile & run directly with ShivyCX:

    python3 -m shivyc.main examples/rpython2c/numpy/simd_blas.py -o blas && ./blas
    echo $?      # 186  (scale -> 50, dot -> 136)

  scale  out[i] = x[i] * s   single fp scalar broadcast -> mulps (shufps splat)
  dot    acc += a[i]*b[i]    float multiply-accumulate reduction -> mulpd/addpd
                             plus a horizontal sum; the result returns in xmm0.

The dot accumulator and array element type must match (here f64), so the whole
reduction stays double precision.
"""


def scale(s: "f32", x: "f32*", out: "f32*", n) -> None:
    assert len(x) % 4 == 0
    i = 0
    while i < n:
        out[i] = x[i] * s
        i = i + 1


def dot(a: "f64*", b: "f64*", n) -> float:
    assert len(a) % 2 == 0
    acc = 0.0
    i = 0
    while i < n:
        acc = acc + a[i] * b[i]
        i = i + 1
    return acc


def main() -> int:
    x: "f32*" = malloc(256 * 4)
    o: "f32*" = malloc(256 * 4)
    da: "f64*" = malloc(256 * 8)
    db: "f64*" = malloc(256 * 8)
    i = 0
    while i < 256:
        x[i] = i
        da[i] = 2.0
        db[i] = 3.0
        i = i + 1
    scale(10.0, x, o, 256)        # o[5] = 5 * 10 = 50
    r1 = int(o[5])
    d = dot(da, db, 256)          # 256 * (2*3) = 1536
    r2 = int(d) % 200             # 1536 % 200 = 136
    return (r1 + r2) % 250        # 186

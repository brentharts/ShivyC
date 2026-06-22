"""An 8-bit quantized MLP -- integer inference with a PSADBW-accelerated kernel.

A post-training-quantized network stores weights and activations as `u8`
(unsigned bytes). A quantized linear layer with real values
``w = sw * (qw - zpw)`` and ``x = sx * qx`` expands to

    y[o] = sw*sx * ( sum_i qw[o][i]*qx[i]  -  zpw * sum_i qx[i] )
                       (integer dot)              (byte sum)

The second term is a *sum of unsigned bytes*, which ShivyCX lowers to the
`PSADBW` instruction: sum-of-absolute-differences against a zeroed register acts
as a hardware byte accumulator, horizontally summing 16 bytes into two 64-bit
lanes with no intermediate 8-bit overflow. `qbytesum` below carries the
`assert len(x) % 16 == 0` contract that licenses that lowering, and is called
directly on each `malloc`'d activation buffer so ShivyCX can prove the alignment
and emit packed `PSADBW` + `PADDQ` (no scalar remainder). The assert sits behind
`#ifdef __SHIVYC__` in the generated C, so gcc compiles the same source as a
plain loop (which gcc -O2 then auto-vectorizes).

Everything is integer and deterministic, so the result is identical under gcc
and the ShivyCX self-backend (`make testtorch` checks both) and matches a
pure-Python reference.

Run:
    python3 -m shivyc.main --no-cache quant_mlp.py -o /tmp/q && /tmp/q
    echo $?      # 50  (checksum of the 8 quantized outputs, mod 250)
"""


def qbytesum(x: "u8*", n) -> int:
    """Sum an unsigned-byte vector -> PSADBW (the SIMD byte accumulator)."""
    assert len(x) % 16 == 0
    acc = 0
    i = 0
    while i < n:
        acc = acc + x[i]
        i = i + 1
    return acc


def qdot(w: "u8*", x: "u8*", off: "int", n) -> int:
    """Integer dot product of weight row `w[off:off+n]` with `x[0:n]`."""
    acc = 0
    i = 0
    while i < n:
        acc = acc + w[off + i] * x[i]
        i = i + 1
    return acc


def qlinear(w: "u8*", x: "u8*", xsum: "int", out: "u8*",
            n_in: "int", n_out: "int", zpw: "int", shift: "int") -> None:
    """Quantized linear + ReLU, requantized back to u8.

    out[o] = clamp( relu( (dot[o] - zpw*xsum) >> shift ), 0, 255 )
    `xsum` (= sum_i x[i]) is the PSADBW byte sum, computed once by the caller and
    reused for every output neuron.
    """
    o = 0
    while o < n_out:
        acc = qdot(w, x, o * n_in, n_in) - zpw * xsum
        acc = acc >> shift
        if acc < 0:
            acc = 0                      # ReLU
        if acc > 255:
            acc = 255                    # saturate to u8
        out[o] = acc
        o = o + 1


def main() -> int:
    N_IN = 32                            # multiples of 16 -> PSADBW contract
    N_HID = 16
    N_OUT = 8
    ZPW = 40
    SHIFT = 9

    x: "u8*" = malloc(N_IN)
    i = 0
    while i < N_IN:
        x[i] = (i * 5 + 17) % 200
        i = i + 1

    w1: "u8*" = malloc(N_HID * N_IN)
    i = 0
    while i < N_HID * N_IN:
        w1[i] = (i * 11 + 7) % 128
        i = i + 1

    w2: "u8*" = malloc(N_OUT * N_HID)
    i = 0
    while i < N_OUT * N_HID:
        w2[i] = (i * 13 + 5) % 128
        i = i + 1

    h: "u8*" = malloc(N_HID)
    y: "u8*" = malloc(N_OUT)

    # PSADBW byte sums, computed directly on the malloc'd buffers
    xsum = qbytesum(x, N_IN)
    qlinear(w1, x, xsum, h, N_IN, N_HID, ZPW, SHIFT)
    hsum = qbytesum(h, N_HID)
    qlinear(w2, h, hsum, y, N_HID, N_OUT, ZPW, SHIFT)

    checksum = 0
    o = 0
    while o < N_OUT:
        checksum = checksum + y[o]
        o = o + 1
    return checksum % 250


import sys
if __name__ == "__main__":
    sys.exit(main())

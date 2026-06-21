"""A float32 MLP on the rpy_torch mini-PyTorch -- single precision for speed.

Same shape as `torch_mlp.py`, but every buffer is `f32` and the math runs in
single precision: the fused activation/gradient kernels lower to `expf`/`sqrtf`
and `1.0f` literals (half the memory traffic of the f64 path, and the elementwise
loops auto-vectorize under gcc -O2 and ShivyCX). The optimizer step
(`sgd_step_f32`) carries a SIMD-divisibility contract that ShivyCX proves and
lowers to packed SSE when the kernel and its call site share a translation unit;
the contract sits behind `#ifdef __SHIVYC__` in the generated C, so gcc compiles
the same source.

The import below keeps the source runnable under *both* ShivyCX and CPython, so
the mini-PyTorch can be checked against a reference `torch` implementation:

    import sys
    if sys.implementation.name == 'shivyc':
        import rpy_torch as torch
    else:
        import torch                      # real PyTorch / a reference shim

ShivyCX folds `sys.implementation.name == 'shivyc'` at translation time, takes
the first branch, and auto-bundles `rpy_torch`; CPython takes the second.

Run:
    python3 -m shivyc.main --no-cache torch_mlp_f32.py -o /tmp/mlp32 && /tmp/mlp32
    echo $?      # 4   (all four XOR cases classified correctly after training)
"""
import sys
if sys.implementation.name == 'shivyc':
    import rpy_torch as torch
else:                                        # pragma: no cover (host CPython)
    import torch


def main() -> int:
    NIN = 2
    NH = 4                                    # padded to a multiple of 4 (SIMD)
    NOUT = 1
    NS = 4
    data: "f32*" = malloc(NS * NIN * 4)
    tgt: "f32*" = malloc(NS * 4)
    data[0] = 0.0; data[1] = 0.0; tgt[0] = 0.0
    data[2] = 0.0; data[3] = 1.0; tgt[1] = 1.0
    data[4] = 1.0; data[5] = 0.0; tgt[2] = 1.0
    data[6] = 1.0; data[7] = 1.0; tgt[3] = 0.0

    w1: "f32*" = malloc(NH * NIN * 4)
    b1: "f32*" = malloc(NH * 4)
    w2: "f32*" = malloc(NOUT * NH * 4)
    b2: "f32*" = malloc(NOUT * 4)
    w1[0] = 0.5; w1[1] = -0.4; w1[2] = -0.3; w1[3] = 0.6
    w1[4] = 0.2; w1[5] = -0.7; w1[6] = 0.9; w1[7] = -0.1
    b1[0] = 0.1; b1[1] = -0.1; b1[2] = 0.05; b1[3] = -0.05
    w2[0] = 0.7; w2[1] = -0.5; w2[2] = 0.3; w2[3] = -0.2
    b2[0] = 0.2

    x: "f32*" = malloc(NIN * 4)
    z1: "f32*" = malloc(NH * 4)
    h1: "f32*" = malloc(NH * 4)
    z2: "f32*" = malloc(NOUT * 4)
    p: "f32*" = malloc(NOUT * 4)
    gp: "f32*" = malloc(NOUT * 4)
    gz2: "f32*" = malloc(NOUT * 4)
    gw2: "f32*" = malloc(NOUT * NH * 4)
    gb2: "f32*" = malloc(NOUT * 4)
    gh1: "f32*" = malloc(NH * 4)
    gz1: "f32*" = malloc(NH * 4)
    gw1: "f32*" = malloc(NH * NIN * 4)
    gb1: "f32*" = malloc(NH * 4)
    gx: "f32*" = malloc(NIN * 4)
    aw2: "f32*" = malloc(NOUT * NH * 4)
    aw1: "f32*" = malloc(NH * NIN * 4)
    ab1: "f32*" = malloc(NH * 4)

    lr = 2.0
    epochs = 5000
    loss0 = 0.0
    lossF = 0.0
    e = 0
    while e < epochs:
        k = 0
        while k < NH * NIN:
            aw1[k] = 0.0
            k = k + 1
        k = 0
        while k < NH:
            ab1[k] = 0.0
            k = k + 1
        k = 0
        while k < NOUT * NH:
            aw2[k] = 0.0
            k = k + 1
        ab2 = 0.0
        epoch_loss = 0.0
        s = 0
        while s < NS:
            x[0] = data[s * NIN]
            x[1] = data[s * NIN + 1]
            torch.linear_f32(w1, b1, x, z1, NIN, NH)
            torch.sigmoid_f32(z1, h1, NH)
            torch.linear_f32(w2, b2, h1, z2, NH, NOUT)
            torch.sigmoid_f32(z2, p, NOUT)
            diff = p[0] - tgt[s]
            epoch_loss = epoch_loss + diff * diff
            gp[0] = (2.0 / NS) * (p[0] - tgt[s])
            torch.sigmoid_grad_f32(p, gp, gz2, NOUT)
            torch.linear_grad_f32(w2, h1, gz2, gw2, gb2, gh1, NH, NOUT)
            torch.sigmoid_grad_f32(h1, gh1, gz1, NH)
            torch.linear_grad_f32(w1, x, gz1, gw1, gb1, gx, NIN, NH)
            k = 0
            while k < NOUT * NH:
                aw2[k] = aw2[k] + gw2[k]
                k = k + 1
            ab2 = ab2 + gb2[0]
            k = 0
            while k < NH * NIN:
                aw1[k] = aw1[k] + gw1[k]
                k = k + 1
            k = 0
            while k < NH:
                ab1[k] = ab1[k] + gb1[k]
                k = k + 1
            s = s + 1
        # vectorized optimizer update on the weight matrices (SIMD when proven)
        torch.sgd_step_f32(w2, aw2, lr, NOUT * NH)
        torch.sgd_step_f32(w1, aw1, lr, NH * NIN)
        torch.sgd_step_f32(b1, ab1, lr, NH)
        b2[0] = b2[0] - lr * ab2
        epoch_loss = epoch_loss / NS
        if e == 0:
            loss0 = epoch_loss
        lossF = epoch_loss
        e = e + 1

    if lossF >= loss0:
        return 255

    correct = 0
    s = 0
    while s < NS:
        x[0] = data[s * NIN]
        x[1] = data[s * NIN + 1]
        torch.linear_f32(w1, b1, x, z1, NIN, NH)
        torch.sigmoid_f32(z1, h1, NH)
        torch.linear_f32(w2, b2, h1, z2, NH, NOUT)
        torch.sigmoid_f32(z2, p, NOUT)
        pred = 0.0
        if p[0] > 0.5:
            pred = 1.0
        if pred == tgt[s]:
            correct = correct + 1
        s = s + 1
    return correct


if __name__ == "__main__":
    sys.exit(main())

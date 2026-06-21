"""A trainable MLP on the rpy_torch mini-PyTorch -- the classic XOR problem.

`from rpy_torch import ...` auto-bundles the bundled mini-library (POD layers +
fused numpy kernels); no need to pass it on the command line:

    python3 -m shivyc.main examples/rpython2c/nn/torch_mlp.py -o mlp && ./mlp
    echo $?      # 4   (all four XOR cases classified correctly after training)

A 2 -> 2 -> 1 network (sigmoid hidden + sigmoid output, MSE loss) is trained with
full-batch gradient descent. The whole forward/backward path runs through the
API: `linear`, `sigmoid`, `mse_grad`, `sigmoid_grad`, `linear_grad`, `sgd_step`.
Every elementwise kernel (activations, gradients, the optimizer update) is a
fused `out[:n] = expr` store -- one pass, no temporaries -- and the matmuls are
plain native loops ShivyCX vectorizes. Weights start fixed, so the run is fully
deterministic; the exit code is the number of XOR cases learned (4), and the
program returns 255 if training ever fails to reduce the loss.
"""
from rpy_torch import (linear, sigmoid, mse_grad, sigmoid_grad,
                       linear_grad, sgd_step)


def main() -> int:
    NIN = 2
    NH = 2
    NOUT = 1
    NS = 4
    data: "f64*" = malloc(NS * NIN * 8)
    tgt: "f64*" = malloc(NS * 8)
    data[0] = 0.0; data[1] = 0.0; tgt[0] = 0.0
    data[2] = 0.0; data[3] = 1.0; tgt[1] = 1.0
    data[4] = 1.0; data[5] = 0.0; tgt[2] = 1.0
    data[6] = 1.0; data[7] = 1.0; tgt[3] = 0.0

    w1: "f64*" = malloc(NH * NIN * 8)
    b1: "f64*" = malloc(NH * 8)
    w2: "f64*" = malloc(NOUT * NH * 8)
    b2: "f64*" = malloc(NOUT * 8)
    w1[0] = 0.5; w1[1] = -0.4; w1[2] = -0.3; w1[3] = 0.6
    b1[0] = 0.1; b1[1] = -0.1
    w2[0] = 0.7; w2[1] = -0.5
    b2[0] = 0.2

    x: "f64*" = malloc(NIN * 8)
    z1: "f64*" = malloc(NH * 8)
    h1: "f64*" = malloc(NH * 8)
    z2: "f64*" = malloc(NOUT * 8)
    p: "f64*" = malloc(NOUT * 8)
    gp: "f64*" = malloc(NOUT * 8)
    gz2: "f64*" = malloc(NOUT * 8)
    gw2: "f64*" = malloc(NOUT * NH * 8)
    gb2: "f64*" = malloc(NOUT * 8)
    gh1: "f64*" = malloc(NH * 8)
    gz1: "f64*" = malloc(NH * 8)
    gw1: "f64*" = malloc(NH * NIN * 8)
    gb1: "f64*" = malloc(NH * 8)
    gx: "f64*" = malloc(NIN * 8)
    aw1: "f64*" = malloc(NH * NIN * 8)
    ab1: "f64*" = malloc(NH * 8)
    aw2: "f64*" = malloc(NOUT * NH * 8)
    ab2: "f64*" = malloc(NOUT * 8)

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
        ab2[0] = 0.0
        epoch_loss = 0.0
        s = 0
        while s < NS:
            x[0] = data[s * NIN]
            x[1] = data[s * NIN + 1]
            linear(w1, b1, x, z1, NIN, NH)
            sigmoid(z1, h1, NH)
            linear(w2, b2, h1, z2, NH, NOUT)
            sigmoid(z2, p, NOUT)
            diff = p[0] - tgt[s]
            epoch_loss = epoch_loss + diff * diff
            gp[0] = (2.0 / NS) * (p[0] - tgt[s])
            sigmoid_grad(p, gp, gz2, NOUT)
            linear_grad(w2, h1, gz2, gw2, gb2, gh1, NH, NOUT)
            sigmoid_grad(h1, gh1, gz1, NH)
            linear_grad(w1, x, gz1, gw1, gb1, gx, NIN, NH)
            k = 0
            while k < NOUT * NH:
                aw2[k] = aw2[k] + gw2[k]
                k = k + 1
            ab2[0] = ab2[0] + gb2[0]
            k = 0
            while k < NH * NIN:
                aw1[k] = aw1[k] + gw1[k]
                k = k + 1
            k = 0
            while k < NH:
                ab1[k] = ab1[k] + gb1[k]
                k = k + 1
            s = s + 1
        sgd_step(w2, aw2, lr, NOUT * NH)
        sgd_step(b2, ab2, lr, NOUT)
        sgd_step(w1, aw1, lr, NH * NIN)
        sgd_step(b1, ab1, lr, NH)
        epoch_loss = epoch_loss / NS
        if e == 0:
            loss0 = epoch_loss
        lossF = epoch_loss
        e = e + 1

    if lossF >= loss0:
        return 255                      # training never reduced the loss

    # count XOR cases the trained network classifies correctly
    correct = 0
    s = 0
    while s < NS:
        x[0] = data[s * NIN]
        x[1] = data[s * NIN + 1]
        linear(w1, b1, x, z1, NIN, NH)
        sigmoid(z1, h1, NH)
        linear(w2, b2, h1, z2, NH, NOUT)
        sigmoid(z2, p, NOUT)
        pred = 0.0
        if p[0] > 0.5:
            pred = 1.0
        if pred == tgt[s]:
            correct = correct + 1
        s = s + 1
    return correct                      # 4 == XOR solved


if __name__ == "__main__":
    import sys
    sys.exit(main())

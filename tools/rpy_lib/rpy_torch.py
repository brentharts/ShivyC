"""rpy_torch -- a restricted-Python (rpython) mini-PyTorch.

Inspired by C-ML's eager torch layer, but with none of its weight: every tensor
is a flat native `f64*` buffer and every layer is a POD class, so ShivyCX lowers
the whole thing to bare C structs with no object header, vtable, or runtime. The
elementwise forward/backward kernels are written as whole-array fused stores
(`out[:n] = expr`), so py2c's NumPy operator fusion collapses each to a single
pass with no temporaries, and ShivyCX vectorizes them.

API (intentionally small):
    Linear(w, b, n_in, n_out)          POD layer; .forward(x, out)
    relu / sigmoid (x, out, n)         activations (fused)
    mse(pred, target, n) -> float      loss
    mse_grad / sigmoid_grad / relu_grad / linear_grad   backward helpers
    sgd_step(w, grad, lr, n)           in-place optimizer update (fused)

Everything is f64. Shapes are explicit ints (no shape inference), matching the
rpython "say the size and it vectorizes" philosophy.
"""


# --- activations (fused elementwise) -------------------------------------
def relu(x: "f64*", out: "f64*", n) -> None:
    out[:n] = (x > 0.0) * x                         # max(x, 0), one fused pass


def sigmoid(x: "f64*", out: "f64*", n) -> None:
    out[:n] = 1.0 / (1.0 + exp(-x))                 # libm exp, fused


# --- linear layer: out = W x + b -----------------------------------------
def linear(w: "f64*", b: "f64*", x: "f64*", out: "f64*", n_in: "int", n_out: "int") -> None:
    j = 0
    while j < n_out:
        acc = b[j]
        i = 0
        while i < n_in:
            acc = acc + w[j * n_in + i] * x[i]
            i = i + 1
        out[j] = acc
        j = j + 1


class Linear:
    """A dense layer as a plain-old-data struct (weights row-major n_out x n_in)."""

    def __init__(self, w: "f64*", b: "f64*", n_in: "int", n_out: "int"):
        self.w = w
        self.b = b
        self.n_in = n_in
        self.n_out = n_out

    def forward(self, x: "f64*", out: "f64*") -> None:
        linear(self.w, self.b, x, out, self.n_in, self.n_out)


# --- loss -----------------------------------------------------------------
def mse(pred: "f64*", target: "f64*", n) -> float:
    acc = 0.0
    i = 0
    while i < n:
        d = pred[i] - target[i]
        acc = acc + d * d
        i = i + 1
    return acc / n


# --- backward helpers (each gradient is a fused elementwise store) --------
def mse_grad(pred: "f64*", target: "f64*", grad: "f64*", n) -> None:
    s = 2.0 / n
    grad[:n] = s * (pred - target)                  # dL/dpred


def sigmoid_grad(out: "f64*", gout: "f64*", gin: "f64*", n) -> None:
    gin[:n] = gout * out * (1.0 - out)              # gout * sigmoid'(z)


def relu_grad(x: "f64*", gout: "f64*", gin: "f64*", n) -> None:
    gin[:n] = gout * (x > 0.0)                       # gout masked by x>0


def linear_grad(w: "f64*", x: "f64*", gout: "f64*", gw: "f64*", gb: "f64*",
                gx: "f64*", n_in: "int", n_out: "int") -> None:
    # gb = gout ; gw[j,i] = gout[j]*x[i] ; gx[i] = sum_j gout[j]*w[j,i]
    j = 0
    while j < n_out:
        gb[j] = gout[j]
        i = 0
        while i < n_in:
            gw[j * n_in + i] = gout[j] * x[i]
            i = i + 1
        j = j + 1
    i = 0
    while i < n_in:
        acc = 0.0
        j = 0
        while j < n_out:
            acc = acc + gout[j] * w[j * n_in + i]
            j = j + 1
        gx[i] = acc
        i = i + 1


# --- optimizer ------------------------------------------------------------
def sgd_step(w: "f64*", grad: "f64*", lr: "f64", n) -> None:
    w[:n] = w - lr * grad                            # fused in-place update

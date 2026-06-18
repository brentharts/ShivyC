"""A small feed-forward neural network in rpython -- and a demonstration of how
classes become C structs.

`Layer` is a plain data class (weights, biases, sizes), so ShivyCX's POD lowering
turns it into a bare C struct with no object header, vtable, or runtime:

    typedef struct Layer { double* w; double* b; int n_in; int n_out; } Layer;
    Layer* Layer_new(double* w, double* b, int n_in, int n_out) {
        Layer* self = malloc(sizeof *self); ... return self; }
    void Layer_forward(Layer* self, double* x, double* out) { ... }

The forward pass is a matrix-vector product plus a sigmoid (a native libm
`exp`). The exit code is a checksum of the network's output, so the inference is
verifiable. Run:

    python3 -m shivyc.main --no-cache neural_net.py -o /tmp/nn && /tmp/nn
"""


def sigmoid(z: "f64") -> float:
    return 1.0 / (1.0 + exp(-z))


class Layer:
    def __init__(self, w: "f64*", b: "f64*", n_in: "int", n_out: "int"):
        self.w = w
        self.b = b
        self.n_in = n_in
        self.n_out = n_out

    def forward(self, x: "f64*", out: "f64*") -> None:
        j = 0
        while j < self.n_out:
            acc = 0.0
            acc = acc + self.b[j]
            i = 0
            while i < self.n_in:
                acc = acc + self.w[j * self.n_in + i] * x[i]
                i = i + 1
            out[j] = sigmoid(acc)
            j = j + 1


def main() -> int:
    # A 2 -> 3 -> 1 network with fixed weights (deterministic inference).
    w1: "f64*" = malloc(3 * 2 * 8)
    b1: "f64*" = malloc(3 * 8)
    w2: "f64*" = malloc(1 * 3 * 8)
    b2: "f64*" = malloc(1 * 8)
    i = 0
    while i < 6:
        w1[i] = ((i % 3) - 1) * 0.5      # small spread of weights
        i = i + 1
    i = 0
    while i < 3:
        b1[i] = 0.1
        i = i + 1
    i = 0
    while i < 3:
        w2[i] = 0.7
        i = i + 1
    b2[0] = -0.2

    layer1 = Layer(w1, b1, 2, 3)
    layer2 = Layer(w2, b2, 3, 1)

    x: "f64*" = malloc(2 * 8)
    h: "f64*" = malloc(3 * 8)
    y: "f64*" = malloc(1 * 8)
    x[0] = 1.0
    x[1] = 0.0

    layer1.forward(x, h)                  # hidden = sigmoid(W1 x + b1)
    layer2.forward(h, y)                  # output = sigmoid(W2 h + b2)
    return int(y[0] * 1000.0) % 256       # checksum of the scalar output

# rpython neural network — classes become structs

`neural_net.py` is a small feed-forward network (2 -> 3 -> 1, sigmoid
activations) that doubles as a demonstration of how rpython **classes are
lowered to plain C structs**.

`Layer` is a plain data class (no inheritance, no dynamic dispatch), so ShivyCX
uses its **POD lowering**: a bare struct with no object header, no vtable, and
no runtime — allocated with `malloc`, methods compiled to direct calls.

```c
typedef struct Layer { double* w; double* b; int n_in; int n_out; } Layer;

Layer* Layer_new(double* w, double* b, int n_in, int n_out) {
    Layer* self = malloc(sizeof *self);
    Layer___init__(self, w, b, n_in, n_out);
    return self;
}
void Layer_forward(Layer* self, double* x, double* out) { ... }   /* direct call */
```

(Rich classes — inheritance, `isinstance`, dynamic dispatch, classes used as
first-class values — keep ShivyCX's tagged-object model with a per-class
`TypeInfo`; the POD form is chosen automatically only when it is safe.)

The forward pass is a matrix-vector product plus `sigmoid` (a native libm
`exp`). The exit code is a checksum of the output:

```
python3 -m shivyc.main --no-cache neural_net.py -o /tmp/nn && /tmp/nn
echo $?      # 199  (sigmoid of the output layer, *1000, mod 256)
```

---

## `torch_mlp.py` — a trainable mini-PyTorch

`neural_net.py` does inference with hand-written loops. `torch_mlp.py` goes a
step further: it trains a 2 -> 2 -> 1 network to solve **XOR** through a small
PyTorch-shaped API, `rpy_torch`.

```python
from rpy_torch import (linear, sigmoid, mse_grad, sigmoid_grad,
                       linear_grad, sgd_step)
```

`rpy_torch` is a *bundled* rpython library (it lives in `tools/rpy_lib/`). You do
**not** pass it on the command line — when a source imports it, py2c and ShivyCX
auto-attach it to the translation unit and co-compile it:

```
python3 -m shivyc.main --no-cache torch_mlp.py -o /tmp/mlp && /tmp/mlp
echo $?      # 4   (all four XOR cases classified correctly after training)
```

### What makes it fast

Inspired by C-ML's eager torch layer, but with none of its weight — no tensor
objects, no autograd tape, no device/dtype machinery. Instead:

* **Every tensor is a flat `f64*`.** Shapes are explicit `int`s.
* **Every layer is a POD class.** `Linear(w, b, n_in, n_out)` lowers to a bare
  C struct (same POD lowering as `Layer` above), so a model is just structs and
  arrays — no object headers, no vtables, no runtime.
* **Every elementwise kernel is a fused store.** The activations, the loss/
  activation gradients, and the optimizer update are all written as whole-array
  `out[:n] = expr`, so py2c's NumPy operator fusion collapses each to a single
  pass with no temporaries, and ShivyCX vectorizes them. For example:

  ```python
  def sigmoid(x: "f64*", out: "f64*", n) -> None:
      out[:n] = 1.0 / (1.0 + exp(-x))        # one fused pass (libm exp)

  def sgd_step(w: "f64*", grad: "f64*", lr: "f64", n) -> None:
      w[:n] = w - lr * grad                  # fused in-place update
  ```

  Building `torch_mlp.py` with `PY2C_NPFUSE_VERBOSE=1` shows six kernels fuse to
  "1 pass, 0 temporaries" (both activations, three gradient helpers, and the SGD
  step). The matmuls (`linear` / `linear_grad`) stay plain native loops that
  ShivyCX vectorizes.

### The API

| function | shape | role |
|---|---|---|
| `linear(w, b, x, out, n_in, n_out)` | matmul + bias | layer forward |
| `Linear(w, b, n_in, n_out).forward(x, out)` | POD class | OO wrapper |
| `relu` / `sigmoid (x, out, n)` | fused | activations |
| `mse(pred, target, n) -> float` | reduction | loss |
| `mse_grad` / `sigmoid_grad` / `relu_grad` | fused | backward of loss/activation |
| `linear_grad(w, x, gout, gw, gb, gx, n_in, n_out)` | loops | layer backward (gw, gb, gx) |
| `sgd_step(w, grad, lr, n)` | fused | optimizer update |

The whole forward + backward + update loop in `torch_mlp.py` runs through these.
Weights start fixed, so the run is deterministic and the result is identical
under gcc and the ShivyCX self-backend (`make testtorch` checks both). The
program returns 255 if training ever fails to reduce the loss, so the exit code
is a real training signal, not just a checksum.

> **Integer dimensions need an annotation.** py2c's name heuristic types a bare
> `n` as `int`, but `n_in` / `n_out` would default to a boxed object — so the
> library annotates them `n_in: "int"`. (A scalar read from a native array, like
> `diff = pred[i] - target[i]`, now infers `double` automatically.)

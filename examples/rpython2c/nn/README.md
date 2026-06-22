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

---

## `torch_mlp_f32.py` — single precision + SIMD

`torch_mlp_f32.py` is the same XOR MLP in **float32**. It exercises three
features that speed up the mini-PyTorch when the dtype is `f32` and layer widths
are known.

### 1. Dtype-aware fusion (`expf` / `sqrtf` / `1.0f`)

A fused store into an `f32*` now lowers to true single precision instead of
computing in `double` and casting at the end:

```python
def sigmoid_f32(x: "f32*", out: "f32*", n) -> None:
    out[:n] = 1.0 / (1.0 + exp(-x))
```

```c
/* f64 path:  out[i] = (float)((1.0  / (1.0  + exp (-x[i])))); */
/* f32 path:  out[i] = (float)((1.0f / (1.0f + expf(-x[i])))); */
```

Float literals get an `f` suffix, libm calls pick the single-precision variant
(`expf`, `sqrtf`, `powf`, ...). Half the memory traffic of the `f64` path, and
the elementwise loops auto-vectorize under gcc `-O2` and ShivyCX.

### 2. Auto-generated SIMD contracts, guarded for gcc

The compute kernels carry a divisibility contract so ShivyCX can prove the trip
count is a multiple of the SSE lane count and drop the scalar remainder:

```python
def sgd_step_f32(w: "f32*", grad: "f32*", lr: "f32", n) -> None:
    assert len(w) % 4 == 0          # SIMD contract
    i = 0
    while i < n:
        w[i] = w[i] - lr * grad[i]
        i = i + 1
```

The `assert` is not valid C, so py2c now emits it behind `#ifdef __SHIVYC__`
(which ShivyCX's preprocessor always predefines):

```c
void sgd_step_f32(float* w, float* grad, float lr, int n)
#ifdef __SHIVYC__
assert not len(w) % 4
#endif
{ ... }
```

ShivyCX reads the contract and lowers proven kernels to packed SSE
(`mulps` / `addps`); gcc skips the `#ifdef` block and compiles the plain loop.
The numpy `simd_kernels.py` example now compiles under **both** backends for
this reason.

> **Two limits worth knowing.** A SIMD contract is only *proven* when the kernel
> and its call sites share one translation unit (as in `simd_kernels.py`). When
> `rpy_torch` is auto-bundled as a separate module, ShivyCX reports
> "no call sites visible" and keeps the (correct) scalar code, so the f32 speedup
> there comes from the smaller data and single-precision libm rather than packed
> SSE. Also, a fused store (`out[:] = expr`) and a SIMD contract do not combine —
> use explicit loops for the kernels you want vectorized.

### 3. One source, two interpreters (the import switch)

```python
import sys
if sys.implementation.name == 'shivyc':
    import rpy_torch as torch
else:
    import torch
```

ShivyCX folds `sys.implementation.name == 'shivyc'` at translation time, takes
the first branch, and auto-bundles `rpy_torch`; CPython takes the second. This
lets the same file be checked against a reference `torch`. Two compiler fixes
were needed to make the switch real:

* the dead `else` branch no longer shadows the alias bound in the taken branch
  (`import rpy_torch as torch`), so `torch.linear_f32(...)` resolves; and
* cross-module functions are now declared with a **full prototype**
  (`extern void sgd_step_f32(float*, float*, float, int);`) instead of a K&R
  `sgd_step_f32()`. Without the prototype a `double` argument was passed where
  the callee expected `float`, and an `f32` learning rate silently arrived as
  `0.0` — so only the manually-updated bias learned.

> **Compatibility scope.** The import-switch *mechanism* works today. `rpy_torch`
> is still a low-level buffer API (`malloc`'d `f32*`, explicit dims), not a
> drop-in for `torch.nn`'s tensor API, and `malloc` is a ShivyCX builtin — so the
> CPython branch needs a tensor-API shim before the example runs unmodified under
> real PyTorch. Validation here is against a pure-Python f32 reference, which the
> compiled result matches exactly (all four XOR cases correct, exit `4` under both
> gcc and ShivyCX; `make testtorch` checks both).

---

## `quant_mlp.py` — 8-bit quantized inference + PSADBW

`quant_mlp.py` is a post-training-quantized MLP: weights and activations are
unsigned bytes (`u8`). It runs integer inference and demonstrates the
hardware byte-sum contract.

### Quantized linear

With real values `w = sw*(qw - zpw)` and `x = sx*qx`, a quantized layer is

```
y[o] = sw*sx * ( sum_i qw[o][i]*qx[i]  -  zpw * sum_i qx[i] )
                   (integer dot)              (byte sum)
```

The integer dot (`qdot`) is a plain `u8 * u8 -> int` loop; the second term is a
**sum of unsigned bytes** (`qbytesum`) reused across every output neuron.

### PSADBW: the hardware byte accumulator

ShivyCX lowers a proven `u8` sum-reduction to `PSADBW` (sum of absolute
differences against a zeroed register). `|byte - 0|` is the byte itself, and
PSADBW horizontally sums each group of 8 bytes into the low word of a 64-bit
lane — so one instruction reduces 16 bytes to two partial sums with **no
intermediate 8-bit overflow**. `PADDQ` accumulates across iterations:

```python
def qbytesum(x: "u8*", n) -> int:
    assert len(x) % 16 == 0          # SIMD contract (16 bytes / iteration)
    acc = 0
    i = 0
    while i < n:
        acc = acc + x[i]
        i = i + 1
    return acc
```

```asm
    pxor   xmm0, xmm0        ; 2 x 64-bit lane accumulators
    pxor   xmm2, xmm2        ; zero operand
.loop:
    movdqu xmm1, [rdi + rax]
    psadbw xmm1, xmm2        ; sum 16 |bytes| -> two 64-bit lanes
    paddq  xmm0, xmm1
    ...
```

The contract is auto-generated from `assert len(x) % 16 == 0` (or inferred from a
fixed `u8[256]` size) and emitted behind `#ifdef __SHIVYC__`, so gcc compiles the
same source as a plain loop. Because `qbytesum` is called *directly* on each
`malloc`'d activation buffer in this single file, ShivyCX proves alignment at
both call sites and emits packed `PSADBW`/`PADDQ` with no scalar remainder.

> **What made it work.** Three things had to be taught the byte type: py2c's
> numeric promotion (`acc + w[i]` was boxing `unsigned char` to an object),
> the contract analyser's sum-reduction recogniser (it now follows the `Set`
> that widens a byte load to the accumulator's `int`), and a `u8`-specific
> code path in `synth_sse2_reduce` that selects `PSADBW` over the `int32`
> `PADDD` reduction. The same `u8` kernels are available on the mini-PyTorch API
> as `torch.qbytesum_u8` / `torch.qdot_u8` / `torch.qlinear_u8`; bundled across a
> module boundary they run as correct scalar code (the contract is proven only
> when kernel and caller share a translation unit, as in this example).

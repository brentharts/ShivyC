# SIMD + float32 for the mini-PyTorch — change summary

Builds on `a085c75 "rpy torch part I"`. Six files (one new). Integrate the same
way as before; **`examples/rpython2c/nn/torch_mlp_f32.py` is a NEW file and needs
`git add`.** `shivyc/main.py` is the live compiler — re-run the unit tests after
integrating.

## What changed

1. **Contract syntax behind `#ifdef __SHIVYC__`** (`tools/py2c.py`).
   SIMD contract clauses (`assert not len(x) % 4`) are now emitted between
   `#ifdef __SHIVYC__` / `#endif`. ShivyCX (which predefines `__SHIVYC__`) reads
   them and vectorizes; gcc skips the block and compiles the plain function.
   `numpy/simd_kernels.py` now compiles under **both** backends (was ShivyCX-only;
   gcc choked on `unknown type name 'assert'`).

2. **Dtype-aware fusion** (`tools/py2c.py` `_fuse_render`).
   A fused store into an `f32*` now emits true single precision —
   `expf`/`sqrtf`/`powf` and `1.0f` literals — instead of computing in `double`
   and casting. `shivyc/main.py` `_libm_protos` gained the matching `f`-variant
   prototypes.

3. **f32 kernel set in `rpy_torch`** (`tools/rpy_lib/rpy_torch.py`).
   `relu_f32`, `sigmoid_f32`, `linear_f32`, `mse_f32`, `mse_grad_f32`,
   `sigmoid_grad_f32`, `linear_grad_f32`, `sgd_step_f32`, plus a `saxpy_f32` SSE
   primitive. Activations/gradients are fused f32; `saxpy_f32`/`sgd_step_f32` are
   explicit loops carrying `#ifdef`-guarded SIMD contracts.

4. **New example** `examples/rpython2c/nn/torch_mlp_f32.py`: the XOR MLP in f32,
   using the import switch. Returns `4` under both gcc and ShivyCX.

5. **Makefile**: `torch_mlp_f32.py` added to the `rpython` and `testtorch`
   (dual-backend) targets.

## Two real compiler bugs fixed (both in `tools/py2c.py`)

* **Module alias shadowed under a static `if`.** With
  `if sys.implementation.name=='shivyc': import rpy_torch as torch` /
  `else: import torch`, `collect_imports` walked *both* branches, so the dead
  `else` overwrote the alias and `torch.linear_f32(...)` emitted
  `OBJ_NONE /* unsupported */`. Fixed with a `_walk_live` helper that descends
  only the live branch of a foldable `if`.

* **K&R extern dropped the float ABI.** Cross-module functions were declared
  `extern void f();` (no prototype). A `double` learning rate passed to a
  `float lr` parameter arrived as `0.0` (the low 32 bits of the double-2.0 bit
  pattern), so `sgd_step_f32` never updated weights — only the manually-updated
  bias learned. Externs now carry full prototypes
  (`extern void sgd_step_f32(float*, float*, float, int);`). The f64 path was
  unaffected because `double` matched the default promotion.

## Verification (no regressions)

* unit tests `FAILED (errors=29)` — unchanged
* `selfhost test` → 3 OK — unchanged
* `make rpython` all pass: simd_kernels=55, simd_blas=186, fusion=97,
  neural_net=199, torch_mlp(f64)=4, **torch_mlp_f32=4**
* `make testtorch / testfast / testpromote / testpgo / testfuse` → PASS
  (testtorch now checks torch_mlp_f32 on gcc **and** ShivyCX)
* cross-module examples unchanged (crossattr=114, dictops=186, aggregates=84,
  dynattr=126, sets=35, wordfreq=93, untyped=41, promote=70, pgo=70)
* gcc coverage 45/60 — unchanged
* `simd_kernels.py` dual-backend: gcc=55, ShivyCX=55 (3 contracts proven)
* `torch_mlp_f32.py`: gcc=4, ShivyCX=4 — matches the pure-Python f32 reference

## Honest scope notes

* A SIMD contract is **proven only when the kernel and its call sites are in one
  translation unit** (e.g. `simd_kernels.py`). For the auto-bundled `rpy_torch`
  module ShivyCX reports "no call sites visible" and keeps correct scalar code,
  so the f32 speedup there is from smaller data + single-precision libm, not
  packed SSE. A fused store and a SIMD contract also do not combine — use
  explicit loops for kernels you want vectorized.
* The import-switch **mechanism** works, but `rpy_torch` is a low-level buffer
  API (malloc'd `f32*`), not a drop-in for `torch.nn`'s tensor API, and `malloc`
  is a ShivyCX builtin — so running unmodified under real PyTorch needs a
  tensor-API shim (future work). Validation here is against a pure-Python f32
  reference, which the compiled result matches exactly.

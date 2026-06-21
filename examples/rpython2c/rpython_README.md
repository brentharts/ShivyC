# rpython2c — end-user Python examples that transpile to C

This folder is the foundation for **rpython**: a restricted, statically-leaning
flavour of Python that `tools/py2c.py` compiles ahead-of-time to direct C. The
language rules (especially the name-based type defaults that let you skip
annotations) are written up in [RPYTHON.md](RPYTHON.md).

The idea: many real end-user scripts use only a small, well-behaved slice of
numpy/scipy/stdlib. In those restricted cases we can emit tight C and benchmark
it, while still supporting dynamic features via the micropython object core.

## Numpy Hello World

`numpy/` has a working end-to-end example: a name-typed integer kernel that
transpiles to native C loops (no boxing), compiles with gcc, and runs.

    cd numpy && ./run.sh vectorize.py

## Folders (directions, with status)

| Folder | Goal | Status |
|---|---|---|
| `numpy/` | restricted ndarray ops -> C loops; later a micropython-ulab-style frontend | working scalar/int kernels; typed-array lowering is next |
| `nn/` | neural nets as POD structs; a mini-PyTorch (`rpy_torch`) backed by numpy fusion | working: inference (`neural_net.py`) and a trainable XOR MLP (`torch_mlp.py`) |


References we are tracking:
* micropython-ulab — a numpy/scipy subset for micropython: https://github.com/v923z/micropython-ulab
* C-ML — a C tensor/torch library; `rpy_torch` borrows its eager fused-linear idea (matmul+bias+activation) without the dependency: https://github.com/OpenSourceJesus/C-ML
* C-ML — to be integrated into micropython by OSJ: https://github.com/jaywyawhare/C-ML

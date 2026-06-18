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

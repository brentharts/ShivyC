import sys
import pickle

# The CPython-only branch uses a lambda, which minipy cannot compile. The
# compile-time fold of `sys.implementation.name` drops this branch entirely on
# minipy, so its uncompilable body never reaches code generation. Both branches
# converge on the same value, so the 3-way harness (cpython == ref == native)
# still verifies agreement while proving the guard works.
if sys.implementation.name != 'minipy':
    doubler = lambda x: x + x
    val = doubler(21)
else:
    val = 42
print("val=" + str(val))

# The `== 'minipy'` form: minipy compiles the body, CPython compiles the else.
# Both yield the same label so output stays identical across implementations.
if sys.implementation.name == 'minipy':
    label = "guarded"
else:
    fallback = lambda: "guarded"
    label = fallback()
print("label=" + label)

# `import sys` / `import pickle` above name modules minipy handles specially or
# not at all; recognised imports are no-ops, so compilation still succeeds even
# though minipy implements no pickle.
print("imports-ok")

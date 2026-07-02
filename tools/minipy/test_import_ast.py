# `import ast` links minast (which links rast) at compile time, so py2c's core
# AST operations run on minipy. Output must match CPython's real ast module, so
# this exercises only drop-in-equivalent behavior. Kept to a single parse: rast's
# PEG parsing allocates heavily and the native VM's arena is not garbage-collected,
# so allocations accumulate across parses.
import ast

src = "def f(a, b=1):\n    total = a + g(b, 2)\n    return total\n"
tree = ast.parse(src)

print("-- unparse round-trip --")
print(ast.unparse(tree) == src.rstrip())

print("-- walk / isinstance --")
names = 0
calls = 0
funcs = 0
for node in ast.walk(tree):
    if isinstance(node, ast.Name):
        names += 1
    if isinstance(node, ast.Call):
        calls += 1
    if isinstance(node, ast.FunctionDef):
        funcs += 1
print("names", names, "calls", calls, "funcs", funcs)

print("-- construct (keyword args) --")
n = ast.Name(id="z", ctx=ast.Load())
print(n.id)
asn = ast.Assign(targets=[ast.Name(id="q", ctx=ast.Store())],
                 value=ast.Constant(value=5))
print(asn.targets[0].id, asn.value.value)
print(isinstance(asn, ast.Assign), isinstance(n, ast.Lambda))

# Compiler/VM features that let py2c.py compile on minipy: closure lifting
# (nested functions -> top-level + captured params), non-Name unpack targets,
# chained assignment and comparison, comprehension unpacking, and slice assignment.

# --- closures: capture + in-place mutation + sibling call (fixpoint capture) ---
def make(base):
    scale = 2
    acc = []
    def add(x):                 # captures scale, acc
        acc.append(base + x * scale)
    def run(items):             # captures acc; calls sibling add (captures base/scale)
        for it in items:
            add(it)
        return acc
    return run([1, 2, 3])
print(make(100))

# --- closures inside a method (captures self) ---
class Box:
    def __init__(self, k):
        self.k = k
    def apply(self, xs):
        def f(v):               # captures self
            return self.k + v
        return [f(x) for x in xs]
print(Box(10).apply([1, 2, 3]))

# --- attribute + nested-tuple unpack targets ---
class P:
    def load(self, a, b):
        self.a, self.b = a, b
p = P()
p.load(4, 9)
print(p.a, p.b)
data = [(1, (2, 3)), (4, (5, 6))]
s = 0
for i, (j, k) in data:
    s = s + i + j + k
print(s)

# --- chained assignment + comparison ---
d = {}
x = d["k"] = 7
print(x, d["k"])
print([w for w in range(10) if 3 <= w < 7])

# --- comprehension nested unpack ---
print({k: v for k, v in [("a", 1), ("b", 2)]})

# --- slice assignment (insert / replace / prepend) ---
xs = [1, 2, 3, 4]
xs[2:2] = [10, 20]
xs[0:0] = [0]
xs[5:7] = [99]
print(xs)

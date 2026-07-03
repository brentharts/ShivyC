# Variadic call spread f(a, *xs, b) and raise-from — two features needed to make
# py2c.py compile on minipy. Output matches CPython.

# --- spread: leading, middle, method, string, defaults filled ---
def join4(a, b=None, c=None, d=None):
    r = a
    if b is not None: r = r + "/" + b
    if c is not None: r = r + "/" + c
    if d is not None: r = r + "/" + d
    return r

parts = "x.y".split(".")
print(join4(*parts))                 # x/y
print(join4("root", *parts, "z"))    # root/x/y/z  (middle spread)

class J:
    def combine(self, a, b, c):
        return a + b + c
print(J().combine(*["p", "q", "r"])) # method spread -> pqr
print(join4(*"ab"))                  # string spread -> a/b

def total(a, b, c, d):
    return a + b + c + d
print(total(1, *[2, 3], 4))          # 10

# --- raise ... from ... (cause accepted and ignored) ---
class Wrapped(Exception):
    pass
def guarded():
    try:
        raise ValueError("boom")
    except Exception as e:
        raise Wrapped("wrapped: " + str(e)) from e
try:
    guarded()
except Wrapped as w:
    print(str(w))

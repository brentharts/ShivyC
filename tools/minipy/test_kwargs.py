# Keyword arguments in calls: resolved at runtime by the VM (CALL_KW) against
# each callee's parameter names. Covers functions, class constructors, methods,
# default fill-in, out-of-order keywords, and positional/keyword mixes.


def make(a=1, b=2, c=3):
    return a * 100 + b * 10 + c


class Node:
    def __init__(self, id=None, ctx=None):
        self.id = id
        self.ctx = ctx


class Box:
    def __init__(self, w=1, h=2, d=3):
        self.w = w
        self.h = h
        self.d = d

    def vol(self):
        return self.w * self.h * self.d

    def combo(self, fx=1, fy=1, fz=1):
        return self.w * fx + self.h * fy + self.d * fz


def show_node(n):
    print(n.id, n.ctx)


print("-- functions --")
print(make())                       # 123
print(make(b=9))                    # 193 (skip a and c)
print(make(5, c=7))                 # 527 (positional + keyword)
print(make(c=7, a=5))               # 527 (out of order)
print(make(9, 8, 7))                # 987 (all positional)

print("-- constructors --")
show_node(Node(id="x", ctx="load"))     # x load
show_node(Node(ctx="store", id="y"))    # y store (out of order)
show_node(Node(id="z"))                 # z None (ctx defaults)

print("-- defaults / gaps --")
b1 = Box(w=10, d=5)                  # h defaults to 2
print(b1.w, b1.h, b1.d)             # 10 2 5
print(b1.vol())                     # 100
b2 = Box()
print(b2.vol())                     # 6
b3 = Box(7, d=9)                    # w=7 positional, d=9 keyword, h default
print(b3.w, b3.h, b3.d)            # 7 2 9

print("-- methods --")
print(b1.combo(fz=2, fx=3))         # 10*3 + 2*1 + 5*2 = 42 (out of order)
print(b1.combo(fx=1, fy=1, fz=1))   # 10 + 2 + 5 = 17
print(b1.combo(2))                  # 10*2 + 2 + 5 = 27 (positional)

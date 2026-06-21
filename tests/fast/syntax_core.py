"""syntax_core - a broad single-file sweep of the language subset.

Exercises arithmetic (int/float/bool/bitwise), control flow (if/for/while/
break/continue/ternary), functions + recursion, classes (fields, methods,
inheritance + polymorphism), constructors used as values (trampoline unboxing of
int/float/bool args), strings (concat / `%` / f-strings), and the list/tuple/
dict/set containers. The exact exit code is whatever CPython produces; the fast
harness only requires the self-compiled and transpiled builds to match it.
"""


def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


class Shape:
    def __init__(self, sides: int):
        self.sides = sides

    def kind(self) -> int:
        return self.sides


class Square(Shape):
    def __init__(self):
        self.sides = 4

    def kind(self) -> int:
        return self.sides * 10


class Particle:
    def __init__(self, mass: int, vel: float, alive: bool):
        self.mass = mass
        self.vel = vel
        self.alive = alive

    def momentum(self) -> int:
        base = self.mass + int(self.vel * 2.0)
        return base + (100 if self.alive else 0)


def arithmetic() -> int:
    total = 0
    # int + bitwise
    a = 6
    b = 10
    total += a + b - 2          # 14
    total += (a * b) // 4       # 15
    total += a % 4              # 2
    total += (b & 6) | 1        # (2)|1 = 3
    total += (1 << 4) ^ 1       # 17
    # float -> int
    f = 3.5 + 2.25              # 5.75
    total += int(f)             # 5
    # bool / logic / ternary
    flag = (a < b) and not (b < a)
    total += 1 if flag else 0   # 1
    return total                # 57


def control_flow() -> int:
    total = 0
    for i in range(5):          # 0..4
        if i == 2:
            continue
        if i == 4:
            break
        total += i              # 0+1+3 = 4
    n = 3
    while n > 0:
        total += n              # 3+2+1 = 6
        n -= 1
    return total                # 10


def containers() -> int:
    xs = [1, 2, 3, 4]
    xs.append(5)
    s = 0
    for x in xs:
        s += x                  # 15
    pair = (7, 9)
    s += pair[0] + pair[1]      # +16 -> 31
    d = {"a": 10, "b": 20}
    s += d["a"] + d["b"]        # +30 -> 61
    uniq = {1, 1, 2, 3}
    s += len(uniq)              # +3 -> 64
    flags = [True, False, True, True]
    for fl in flags:
        if fl:
            s += 1              # +3 -> 67
    return s


def strings() -> int:
    name = "sx"
    greet = "hi " + name              # "hi sx"
    pct = "%s=%d" % (name, 42)        # "sx=42"
    fs = f"[{name}:{len(greet)}]"     # "[sx:5]"
    return len(greet) + len(pct) + len(fs)   # 5 + 5 + 6 = 16


def builtins_and_slices() -> int:
    total = 0
    xs = [1, 2, 3, 4, 5]
    total += xs[::-1][0]               # reverse slice -> 5
    total += len(xs[::2])             # step slice [1,3,5] -> 3  (8)
    total += sum(xs[1:4])             # [2,3,4] -> 9            (17)
    buf = [0] * 4                     # list repetition
    total += len(buf)                 # 4                       (21)
    ys = [3, 1, 2, 1, 3]
    total += ys.count(1)              # 2                       (23)
    ys.reverse()
    total += ys[0]                    # 3                       (26)
    q, r = divmod(23, 5)              # (4, 3)
    total += q * 10 + r               # 43                      (69)
    total += int(bool(7)) + int(bool(0))   # 1 + 0             (70)
    return total                      # 70


def main() -> int:
    total = 0
    total += fib(10)                  # 55
    total += clamp(99, 0, 20)         # 20
    total += clamp(-5, 0, 20)         # 0

    shapes = [Shape(3), Square()]     # classes used as instances + polymorphism
    for sh in shapes:
        total += sh.kind()            # 3 + 40 = 43

    ctors = [Particle]                # constructor used as a value (trampoline)
    for C in ctors:
        p = C(5, 2.5, True)           # 5 + int(5.0)=5 + 100 = 110
        total += p.momentum()

    total += arithmetic()             # 57
    total += control_flow()           # 10
    total += containers()             # 67
    total += strings()                # 16
    total += builtins_and_slices()    # 70
    return total % 200                # keep < 256


if __name__ == "__main__":
    import sys
    sys.exit(main())

"""eval() powered by minipy's native expression evaluator.

py2c lowers each eval() of a string into a call to minipy's C eval API
(rpy_eval*), which evaluates the expression natively -- there is no MicroPython
core to link. The typed form `v: int = eval(...)` pulls the integer straight out
with no boxing. Build/run through the project's py2c -> gcc pipeline.
"""


def main() -> int:
    a = 1
    b = 3
    c: "list[int]" = []
    i = 0
    while i < 10000:
        v: int = eval(f"{a}+{b}")
        c.append(v)
        i = i + 1
    print(len(c))            # 10000
    print(c[0])              # 4
    # shapes the native evaluator handles, with Python semantics
    print(eval("2 * (3 + 4)"))   # 14
    print(eval("17 // 5"))       # 3
    print(eval("17 % 5"))        # 2
    print(eval("2 ** 10"))       # 1024
    w: int = eval("7 < 9")
    print(w)                     # 1
    return 0


main()

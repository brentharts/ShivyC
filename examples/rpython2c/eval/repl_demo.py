"""A tiny REPL backed by minipy's native eval.

Each line typed is evaluated by minipy's C expression evaluator (rpy_eval),
the same path py2c lowers eval() to -- no MicroPython core involved. Reads
expressions until EOF/empty line. Build/run through py2c -> gcc.
"""


def main() -> int:
    print("minipy eval REPL -- type an expression, empty line to quit")
    while True:
        line = input("calc> ")
        if len(line) == 0:
            break
        r: float = eval(line)
        print(r)
    print("bye")
    return 0


main()

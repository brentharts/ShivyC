"""Typed eval through an embedded MicroPython core.

`eval(s)` runs the Python expression `s` on the linked MicroPython interpreter at
run time. With a type annotation the translator knows the result type, so it
pulls the value straight out of the MicroPython result object -- a small int is
just a shifted field -- with no rpython boxing:

    answer: int = eval("6 * 7")     ->  long answer = rpy_eval_int("6 * 7");

Without an annotation the result is boxed into the universal rpython `obj`:

    x = eval("6 * 7")               ->  obj x = rpy_eval("6 * 7");
"""


def main() -> "int":
    answer: int = eval("6 * 7")
    print(answer)

    ratio: float = eval("22.0 / 7.0")
    print(ratio)

    greeting: str = eval("'hello ' + 'from eval'")
    print(greeting)

    even: bool = eval("(2 ** 10) % 2 == 0")
    print(even)

    return answer

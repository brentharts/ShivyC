"""ctorval - constructors used as values, with int/float/bool arguments.

When a class is used as a *value* (stored in a list, passed to a function), the
transpiler emits a closure trampoline `Cls__ctortramp(env, args)` that unpacks a
runtime argument list and calls the real `Cls_new(...)`. Each argument has to be
unboxed to the constructor's declared C parameter type. The trampoline already
handled `int` (`AS_INT`), `bool` (`truthy`), `char*` and pointers, but silently
passed a `double`/`float` argument through as a raw 16-byte `obj` -- a type error
at best and garbage at worst. It now unboxes those through `as_dbl` (which widens
an int/bool and reads a float's payload).

The `bool` argument exposed a second, deeper bug. `truthy` dispatches on the
1-byte type tag with `switch (v.tag)`, and the backend was *not* integer-promoting
the controlling expression of a switch (C 6.8.4.2), so a sub-`int` control
(`unsigned char`, `short`, `_Bool`) compared wrong and effectively always took
the first case. Any `switch` on a narrow type was affected -- here it made every
boolean read as false. The fix promotes the control value to `int` before the
case dispatch, so both the trampoline's `bool` argument and plain
boolean-in-a-list truthiness now evaluate correctly.
"""


class Vec:
    def __init__(self, x: int, y: float, on: bool):
        self.x = x
        self.y = y
        self.on = on

    def score(self) -> int:
        return self.x + int(self.y * 2.0) + (10 if self.on else 0)


def build(ctor, x: int, y: float, on: bool) -> Vec:
    # `ctor` arrives as a value, so this call goes through the trampoline and
    # must unbox x (int), y (double) and on (bool).
    return ctor(x, y, on)


def main() -> int:
    total = 0

    ctors = [Vec]
    for C in ctors:
        v = build(C, 5, 2.5, True)      # 5 + int(5.0)=5 + 10 = 20
        total += v.score()

    # boolean truthiness through a list exercises switch-on-tag in `truthy`.
    flags = [True, False, True, True]
    for f in flags:
        if f:
            total += 1                  # + 3  -> 23
    return total                        # 23


if __name__ == "__main__":
    import sys
    sys.exit(main())

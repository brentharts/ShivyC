"""formatting - printf-style `%` formatting and f-strings.

`fmt % args` is Python *string formatting*, not arithmetic modulo. The transpiler
used to lower every `%` to `obj_mod` (integer remainder), so `"%d-%d" % (a, b)`
silently became `as_num("%d-%d") % as_num(...)` -> garbage. Now, when the left
operand is a string, `%` lowers to `str_mod`, a small printf-style formatter that
walks the format, consuming one collected `obj` argument per conversion. A tuple
right-hand side spreads into several arguments; anything else is a single one.

f-strings lower to `pyfmt_a`, which fills the `{}` holes from an argument array
(and sizes its output buffer to the arguments, rather than a fixed slack). Both
helpers take their arguments through a pointer, never C varargs -- a 16-byte
`obj` mis-passes through `...` on the self-compiled backend.

Supported `%` conversions include `d i x X o c` (integer), `f e g` (float),
`s r` (string), `%%`, plus flags/width/precision (`%05d`, `%.2f`, `%-8s`).
"""


def main() -> int:
    n = 0
    n += len("%d-%d" % (12, 34))          # "12-34"  -> 5
    n += len("%s/%s" % ("ab", "cde"))     # "ab/cde" -> 6
    n += len("%05d" % 42)                 # "00042"  -> 5
    n += len("%x" % 255)                  # "ff"     -> 2
    n += len("%.2f" % 3.14159)            # "3.14"   -> 4
    n += len("%d%%" % 50)                 # "50%"    -> 3

    a = 7
    b = "z"
    n += len(f"{a}-{b}-{a}")              # "7-z-7"  -> 5
    n += len(f"<{a}>")                    # "<7>"    -> 3
    return n                              # 33


if __name__ == "__main__":
    import sys
    sys.exit(main())

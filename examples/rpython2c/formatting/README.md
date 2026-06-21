# formatting — `%` string formatting and f-strings

`fmt % args` is Python **string formatting**, not arithmetic modulo. The
transpiler previously lowered every `%` to `obj_mod` (integer remainder), so a
string like `"%d-%d" % (a, b)` became `as_num("%d-%d") % as_num(args)` — which
evaluates to garbage. Since the compiler itself formats messages this way all
over (error text, the PDF report, contract diagnostics), this was a real
self-host correctness hole.

Now, when the left operand is a string, `%` lowers to **`str_mod`** — a compact
printf-style formatter that walks the format string and consumes one argument per
conversion:

```c
s = ({ obj _sm1[2]; _sm1[0] = OBJ_INT(a); _sm1[1] = OBJ_INT(b);
       str_mod("%d-%d", _sm1, 2); });
```

A tuple right-hand side spreads into several arguments; anything else is a single
one (resolved at compile time, so there is no tuple-vs-list ambiguity).
Conversions: `d i x X o c` (integer), `f e g` (float), `s r` (string), `%%`, with
flags / width / precision (`%05d`, `%.2f`).

f-strings lower to **`pyfmt_a`**, which fills the `{}` holes from an argument
array and sizes the output buffer to the actual arguments (the old `pyfmt` used a
fixed 256-byte slack and passed obj through varargs — both a correctness and an
overflow risk). Neither helper passes a 16-byte `obj` through C `...`.

CPython, `gcc`, and ShivyCX-self-compiled all exit **33**.

## Run

```
python3 examples/rpython2c/formatting/app.py ; echo $?                        # 33
python3 -m shivyc.main --no-cache examples/rpython2c/formatting/app.py -o /tmp/f
/tmp/f ; echo $?                                                              # 33
```

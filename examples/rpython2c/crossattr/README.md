# crossattr — cross-class field discovery (with type inference)

A configurator class often stamps attributes onto instances of *another* class
that the other class never declares on `self`. In Python the attribute simply
springs into existence; for an ahead-of-time C struct it needs a real slot, or
the write has nowhere to go.

The transpiler discovers these. When a field is written through a receiver whose
class is statically known — `obj.attr = …` or `setattr(obj, "attr", …)`, where
the receiver is a `var = Cls()` local **or a parameter annotated with a class** —
and `attr` is not already declared, `attr` is promoted to a field on that class
(and is therefore inherited by its subclasses). The pass walks top-level
functions and method bodies alike.

## Inferring the slot's type

A discovered field defaults to the generic `obj` word, which safely holds a
scalar or any dynamic value. But when **every** write to that field assigns a
direct constructor result of one local class, the field takes that concrete
pointer type instead:

```python
def configure(target: "Widget"):
    setattr(target, "margin", 8)        # scalar -> `obj margin;`
    target.visible = 1                  # scalar -> `obj visible;`
    target.style = Style(3)             # ctor   -> `Style* style;`
```

So `Widget` (and `Button`) gain `margin`/`visible` as `obj` fields and `style`
as a real `Style*`. The typed slot can then be used as a pointer —
`b.style.inset()` compiles to a **direct** `Style_inset(b->style)` call, not a
dynamic dispatch — while the `obj` slots round-trip through `rt_getattr`. Any
disagreement between write sites, or any non-constructor value, falls back to
`obj`. CPython, `gcc`, and ShivyCX-self-compiled all exit **114**.

## Run

```
python3 examples/rpython2c/crossattr/app.py ; echo $?                       # 114
python3 -m shivyc.main --no-cache examples/rpython2c/crossattr/app.py -o /tmp/ca
/tmp/ca ; echo $?                                                           # 114
```

## Limitation

Two cases stay out of reach. A receiver that remains an untyped `obj` at the
write site gives no class to attribute the field to. And the type inference only
fires for a *direct local-class constructor* on the right-hand side — a value
reached indirectly (e.g. `target.layout = self.layout`, where `self.layout`'s
own type is not itself a known class pointer) still lands as `obj`.

# crossattr — cross-class field discovery

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

```python
class Widget:
    def __init__(self, width: int):
        self.width = width                  # the only declared field

class Button(Widget):                       # inherits discovered fields too
    ...

def configure(target: "Widget"):            # annotation pins the receiver type
    setattr(target, "margin", 8)            # -> 'margin' promoted onto Widget
    target.padding = 4                      # -> 'padding' promoted onto Widget
    target.visible = 1
```

After the pass, `Widget` (and `Button`) carry `margin`, `padding`, and `visible`
as real fields, complete with `FieldDesc` table entries — so the writes land in
slots and a later `getattr(b, "margin", -1)` reads them back through
`rt_getattr`. CPython, `gcc`, and ShivyCX-self-compiled all exit **112**.

This closes the correctness gap left by the field-table mechanism on its own: a
cross-class dynamic *write* to an undeclared attribute would otherwise be lost
(`rt_setattr` is a no-op for a name with no slot), and on a *typed* receiver it
would not even compile (there is no slot and no micropython bridge to absorb it).

## Run

```
python3 examples/rpython2c/crossattr/app.py ; echo $?                       # 112
python3 -m shivyc.main --no-cache examples/rpython2c/crossattr/app.py -o /tmp/ca
/tmp/ca ; echo $?                                                           # 112
```

## Limitation

Discovered fields are typed as the generic `obj` word, which is right for scalars
and dynamically-typed values but conflicts if the same attribute is elsewhere
accessed as a *typed object pointer* (e.g. a field holding a specific class
instance that is then used for member access). Giving those their concrete type
requires inferring the assigned value's type at the discovery site, which this
pass does not yet attempt. Receivers that stay untyped (`obj`) at the write site
are also out of reach — the class to attribute the field to is unknown there.

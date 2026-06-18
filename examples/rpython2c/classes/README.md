# rpython classes — the full object model under ShivyCX

`polymorphism.py` exercises ShivyCX's **tagged object model**: inheritance and
virtual dispatch, not the runtime-free POD form used by the other examples.

A `Shape` base has `Square` and `Circle` subclasses. A `Shape*`-typed variable
(typed `obj`, since a non-leaf base could be any subclass at runtime) holds
either subtype, and `base.area()` dispatches through the per-class vtable
(`TYPE(o)->area(o)`).

This works because ShivyCX now compiles its **own object-model runtime**: the
16-byte tagged `obj` union is passed/returned in two registers (SysV AMD64),
boxed on assignment (`base = OBJ_OBJ(sq)`), and member-accessed even as an
rvalue. The exit code is the summed areas:

```
python3 -m shivyc.main --no-cache polymorphism.py -o /tmp/poly && /tmp/poly
echo $?      # 22   (Circle(0)=0 + Square(1)=1 + Circle(2)=12 + Square(3)=9)
```

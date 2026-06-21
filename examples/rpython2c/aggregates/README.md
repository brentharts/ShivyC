# aggregates — varargs-free list/dict/call construction

Building a list, tuple, set, or dict literal, packing a dynamic call's
arguments, and forming the `(index, value)` pairs of `enumerate`/`zip` all
collect several `obj` values together. The natural lowering passes them through a
C variadic helper (`list_of(n, ...)`, `call_obj(f, n, ...)`, `dict_of(n, ...)`).

The catch: the tagged `obj` word is a 16-byte struct, and **passing a 16-byte
struct by value through `...` mis-lowers on the ShivyCX self-compiled backend** —
only the first variadic argument survives, so every element after the first comes
back as garbage. (gcc handles the same code correctly, which is what masked it.)

The fix sidesteps varargs entirely: each construction stores its values into a
stack array and hands the runtime a pointer —

```c
xs = ({ obj _lt1[3]; _lt1[0]=OBJ_INT(3); _lt1[1]=OBJ_INT(5); _lt1[2]=OBJ_INT(7);
        list_from(_lt1, 3); });
t  = ({ obj _ca2[3]; _ca2[0]=OBJ_INT(2); _ca2[1]=OBJ_INT(4); _ca2[2]=OBJ_INT(6);
        call_obj_a(f, _ca2, 3); });
```

with `list_from` / `call_obj_a` / `dict_of_a` / `list_pair` replacing the
variadic forms (the last covers the pair-building inside `enumerate`/`zip`/dict
items). No 16-byte obj is ever passed through `...`.

This program drives every one of those paths with two or more elements — a
multi-element and nested list, a 3-entry dict, a 3-argument dynamic call,
`enumerate`, and `zip` — each of which used to return garbage under
self-compilation. CPython, `gcc`, and ShivyCX-self-compiled all exit **84**.

## Run

```
python3 examples/rpython2c/aggregates/app.py ; echo $?                        # 84
python3 -m shivyc.main --no-cache examples/rpython2c/aggregates/app.py -o /tmp/agg
/tmp/agg ; echo $?                                                            # 84
```

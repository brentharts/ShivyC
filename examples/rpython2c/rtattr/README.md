# rtattr — bridge-free runtime `getattr`/`setattr` (per-type field tables)

`getattr(obj, key, default)` and `setattr(obj, key, val)` with a **runtime**
string key, on an **object-model** receiver held as a tagged `obj` (its concrete
type known only at runtime). These lower to two runtime helpers — `rt_getattr`
and `rt_setattr` — that need **no micropython core**: the generated program
links against `shivyc_rt.c` alone.

This is the dynamic-key counterpart to [`dynattr`](../dynattr/README.md). There
the receiver's *static* struct type is known, so the field-selection `switch` is
inlined at the call site. Here the receiver is a boxed `obj`, so the lookup goes
through that object's type descriptor at runtime.

## How it works

Every object-model class already carries a `TypeInfo` (reachable via the object
header's `type` pointer). Each `TypeInfo` now also points at a **field table** —
a static array describing the class's fields:

```c
typedef struct FieldDesc { const char* name; long off; char tc; } FieldDesc;

static const FieldDesc Node__fields[] = {
    { "ival",   offsetof(Node, ival),   'i' },   /* int    */
    { "weight", offsetof(Node, weight), 'd' },   /* double */
    { "sname",  offsetof(Node, sname),  's' },   /* char*  */
    { "flag",   offsetof(Node, flag),   'b' },   /* bool   */
    { NULL, 0, 0 }
};
```

The 1-char storage code (`i` int, `l` long, `b` bool, `d` double, `f` float,
`s` char*, `o` obj, `p` object-pointer) tells the runtime how to box/unbox the
bytes at that offset. `rt_getattr` finds the entry by name (walking the base
chain, so inherited fields resolve), reads the field directly, and boxes it;
`rt_setattr` unboxes and writes. An absent name returns the caller's default
(get) or is a no-op (set).

```c
obj rt_getattr(obj recv, const char* name, obj dflt) {
    if (recv.tag != T_OBJ || !recv.u.o) return dflt;
    const TypeInfoHdr* ti = (const TypeInfoHdr*)recv.u.o->type;
    char* p = (char*)recv.u.o;
    for (; ti; ti = ti->base)
        for (const FieldDesc* f = ti->fields; f && f->name; f++)
            if (!strcmp(f->name, name)) {
                char* a = p + f->off;
                switch (f->tc) {
                    case 'i': return OBJ_INT(*(int*)a);
                    case 'd': return OBJ_FLOAT(*(double*)a);
                    /* ... */
                }
            }
    return dflt;
}
```

## What the example shows

A `Leaf` (subclass of `Node`) is held in a list as a boxed `obj`, then poked by
both constant and runtime keys:

- `setattr(it, "ival", 42)` — constant key, `rt_setattr`.
- `bump(it, "ival", 4)` — a `char*` key argument, read-modify-write via
  `rt_getattr` + `rt_setattr` (42 → 46).
- `getattr(it, "weight", -1)` — an **inherited** float field, found by following
  the base chain.
- `getattr(it, "missing", -1)` — no such field, so the `-1` default is returned.

The generated C contains only `rt_getattr` / `rt_setattr` — grep it and you will
find no `mp_getattr` / `mp_call_import`.

## Run

```
python3 examples/rpython2c/rtattr/app.py ; echo $?        # CPython -> 48
python3 -m shivyc.main --no-cache examples/rpython2c/rtattr/app.py -o /tmp/rtattr
/tmp/rtattr ; echo $?                                     # ShivyCX -> 48
```

CPython, `gcc`, and ShivyCX-self-compiled all exit **48**.

## Limitation

The field table is built from a class's *declared* fields. An attribute that is
only ever added dynamically from outside the class (never assigned on `self`)
has no struct slot, so `rt_getattr` returns the default and `rt_setattr` is a
no-op for it. Promoting such cross-class dynamic attributes to declared fields is
a separate step (it needs the receiver's static type at the assignment site).

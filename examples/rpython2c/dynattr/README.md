# dynattr — compiled dynamic `getattr`/`setattr`

`getattr(obj, key)` and `setattr(obj, key, val)` with a **runtime** string key,
on a statically-typed struct, lowered to a compiled jump table — no dict, no
hash, and no micropython bridge.

When the receiver's static type is a known struct, the transpiler emits an
inline `switch` on `key[0]` (the field's first letter encodes its C type), then
a `strcmp` selects the exact field for direct, typed member access:

```c
({ Particle* _ds = p; const char* _dk = field; obj _dr = OBJ_NONE;
   switch (_dk[0]) {
     case 'd': if (!strcmp(_dk, "dmass")) _dr = OBJ_FLOAT(_ds->dmass); break;
     case 'i': if (!strcmp(_dk, "ix"))    _dr = OBJ_INT(_ds->ix);
          else if (!strcmp(_dk, "iy"))    _dr = OBJ_INT(_ds->iy);    break;
     case 's': if (!strcmp(_dk, "sname")) _dr = OBJ_STR(_ds->sname); break;
   } _dr; })
```

## The naming convention

A field's first letter encodes its type, so the result type is decidable from
`key[0]` alone, uniformly across every struct:

| Initial | Type        | Initial      | Type           |
|---------|-------------|--------------|----------------|
| `i…`    | int         | `d…` / `f…`  | double / float |
| `b…`    | bool        | `s…`         | str (`char*`)  |
| `<Upper>…` | object   |              |                |

Restrictive on purpose: a single-character switch stays tiny, and it matches the
way code is normally written (lowercase types, uppercase classes).

## Why it matters

This is the mechanism a minimal **ctypes / FFI** layer is built on — poking a C
struct field, or reading a `c_int`'s `.value`, by name and without paying for
dynamic dispatch or a bridge runtime.

## Run

```sh
python3 -m shivyc.main --no-cache app.py -o app && ./app; echo $?   # 126
python3 app.py; echo $?                                             # 126 (CPython)
```

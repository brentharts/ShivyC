# sets — set as a first-class type

Sets previously shared the list representation, which caused two problems: the
set operators collided with the bitwise ones (`a | b` evaluated `obj_bin('|', …)`
and produced garbage), and nothing at runtime could distinguish a set from a
list. Sets now carry their own runtime tag, **`T_SET`** — a set is a `List` whose
tag marks it as a set. That reuses all the structural machinery (length,
iteration, membership, `clear`, `remove`) while letting the operators dispatch
correctly:

- `|` `&` `-` `^` → union / intersection / difference / symmetric-difference on
  two sets (dispatched on the tag inside `obj_bin`/`obj_sub`, so they stay
  bitwise on integers).
- set literals and `set(iterable)` de-duplicate; **set comprehensions**
  de-duplicate via `set_add`.
- equality is order-independent (`{1,2,3} == {3,2,1}`).
- `{…}` prints with braces; the empty set prints as `set()`.
- `in`, `add`, `discard`, `remove`, `clear`, and iteration all work.

CPython, `gcc`, and ShivyCX-self-compiled all exit **35**.

## Run

```
python3 examples/rpython2c/sets/app.py ; echo $?                       # 35
python3 -m shivyc.main --no-cache examples/rpython2c/sets/app.py -o /tmp/s
/tmp/s ; echo $?                                                       # 35
```

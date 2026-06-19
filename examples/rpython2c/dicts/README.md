# Typed dicts — `dict[str,int]`, `dict[int,int]` → unboxed arrays

A typed-dict annotation lowers to parallel key/value arrays with linear-probe
lookup (strcmp for string keys, `==` otherwise):

```c
typedef struct { K* keys; V* vals; long len; long cap; } _tdict_K_V;
```

No tagged `obj`, no hashing runtime, no GC. O(n) lookup — fine for the small
dicts rpython programs use.

| rpython              | C                                   |
|----------------------|-------------------------------------|
| `d: "dict[str,int]" = {..}` | `_tdict_..._new(n)` + sets (stmt-expr) |
| `d[k]`               | `_tdict_..._get(d, k)` (missing → 0) |
| `d[k] = v`           | `_tdict_..._set(d, k, v)` (update or append) |
| `k in d` / `k not in d` | `_tdict_..._has(d, k)`           |
| `len(d)`             | `d->len`                            |
| `for k in d`         | iterates `d->keys[0..len)`          |

A missing key reads as `0`, so counters need no initialization
(`freq[w] = freq[w] + 1`). Keys may be `int` or `str`; values are scalars.

```
python3 -m shivyc.main --no-cache typed_dict.py -o /tmp/td && /tmp/td   # exit 58
```

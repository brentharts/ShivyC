# Typed lists — `list[int]`, `list[float]` → unboxed C arrays

A typed-list annotation lowers to a runtime-free growable array, the vector
you'd write by hand in C, generated from Python:

```c
typedef struct { T* data; long len; long cap; } _tlist_T;   /* T = int/double/... */
```

Backed by `malloc`/`realloc`. No tagged `obj`, no boxing, no GC.

| rpython              | C                              |
|----------------------|--------------------------------|
| `xs: "list[int]" = [..]` | `_tlist_int_new(n)` + pushes (stmt-expr) |
| `xs[i]`              | `xs->data[i]`                  |
| `xs[i] = v`          | `xs->data[i] = v`              |
| `len(xs)`            | `xs->len`                      |
| `xs.append(v)`       | `_tlist_int_push(xs, v)` (realloc-grows) |
| `for x in xs`        | `for (i=0; i<xs->len; i++) x = xs->data[i]` |

Arithmetic on elements is native (`xs[0] + xs[1]` is an `int` add, not `obj_add`).
Only scalar element types are unboxed; lists of objects keep the tagged model.

A negative integer **literal** index wraps Python-style at compile time:
`xs[-1]` becomes `xs->data[xs->len + (-1)]` (no runtime branch), and the same on
the left of an assignment (`xs[-1] = v`). Dynamic indices (`xs[i]`) are taken
as-is — direct C indexing, like the numpy-style typed arrays — so hot loops pay
no bounds/wrap cost.

```
python3 -m shivyc.main --no-cache typed_list.py -o /tmp/tl && /tmp/tl   # exit 58
```

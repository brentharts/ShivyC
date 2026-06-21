# untyped — untyped containers + inference advisories

Python users can write natural untyped dicts / lists / sets. The transpiler
infers each container's element (or key/value) type from how it is *used* and
prints an advisory to stderr — suggesting a `name: "list[int]"`-style annotation
for the unboxed fast path, or flagging an rpython-rule violation such as mixed
element types. **The advisories never change the generated code**: an untyped
container compiles and runs as a boxed container either way. Silence them with
`PY2C_NO_CONTAINER_WARN=1`.

This program exits **41** (CPython, `gcc`, and ShivyCX-self-compiled) and emits:

```
rpython: app.py:14: dict 'out' looks like dict[obj, int] and stays boxed; ...
rpython: app.py:21: dict 'counts' looks like dict[str, int]; annotate it as counts: "dict[str, int]" ...
rpython: app.py:26: list 'nums' looks like list[int]; annotate it as nums: "list[int]" ...
rpython: app.py:31: set 'seen' looks like set[str]; annotate it as seen: "set[str]" ...
```

## What the analysis can infer

- **Value/element types** from literals, arithmetic, calls (`len`, `int`, `str`,
  …), `d.get(k, default)`, and class constructors.
- **Key types** of a dict from the subscript used in `d[k] = …`.
- **Loop-variable types** for `for x in range(...)` (int), over a string
  literal (str), or over a homogeneous list/tuple/set literal — so
  `for i in range(n): xs.append(i*i)` infers `list[int]`.

## The advisories

- **Clean scalar inference** → suggests the exact annotation for the unboxed
  fast path (`name: "list[int]"`).
- **Boxed but fine** (`dict[str, obj]`, value is an object/`None`) → notes it
  stays boxed, which is allowed.
- **Mixed element types** → warns that rpython containers should be homogeneous,
  so it stays boxed.
- **No observed use** → suggests annotating if a typed container was intended.

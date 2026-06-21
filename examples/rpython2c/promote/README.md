# promote — opt-in auto-promotion of inferred containers

With `PY2C_PROMOTE_CONTAINERS=1`, an **unannotated** empty `list`/`dict` whose
element (or key/value) types infer to a single scalar — and whose *every* use is
supported by the unboxed typed form — is automatically rewritten to the typed
`list[int]` / `dict[str, int]` representation (unboxed parallel arrays, no
per-element boxing). Without the flag the same code compiles to boxed containers.
**Either way the result is identical** — promotion is purely a representation
choice.

It is conservative by construction. A container stays boxed if it:

- escapes — is returned, passed as an argument, aliased to another variable, or
  stored in another container;
- uses an operation the typed form doesn't support — any method other than
  `append` (lists), a slice, or a negative index;
- has mixed or non-scalar element/key/value types;
- is assigned more than once.

The counter idiom `d[k] = d[k] + 1` promotes (the self-referential read is seen
through), but `d.get(k, 0) + 1` does not (`.get` isn't a typed-dict operation).

```
python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p; echo $?            # 70 (boxed)
PY2C_PROMOTE_CONTAINERS=1 python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p   # 70 (unboxed)
```

`make testpromote` compiles this and other container programs with promotion on
and checks the result still matches CPython.

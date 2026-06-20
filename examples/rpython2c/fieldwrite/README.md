# fieldwrite — cross-module field writes into a None-initialised obj field

`lib.Cell.next` is assigned only `None` inside `lib.py`. A None-only field is
nullable, so py2c types it `obj` rather than guessing a scalar/string from its
name -- which is exactly what allows a *different* module to store an object
into it:

    python3 -m shivyc.main app.py lib.py -o app

`app.py` then:

- writes an object into the obj field from another module (`a.next = b`,
  boxed as `OBJ_OBJ`),
- writes the plain int field (`a.v = a.v + 5`),
- reads the obj field back as a typed `Cell*` pointer and uses it for a direct
  field read and a method call.

Deterministic exit code: **55**.

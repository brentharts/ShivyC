# ambig — ambiguous same-named classes across a multi-file program

Two modules each define a class called `Node`:

- `node_a.Node` — fields `tag`, `payload`; method `score()`
- `node_b.Node` — fields `name`, `weight`, `cached` (None-initialised); method `bump()`

`app.py` imports and uses both in a single translation unit:

    python3 -m shivyc.main app.py node_a.py node_b.py -o app

Because the two classes share a bare C symbol, py2c module-qualifies them
(`node_a__Node`, `node_b__Node`) and emits a distinct shadow typedef + struct
body for each so `app.c` can hold both layouts at once.

Both classes are **POD** (no base, no polymorphism). py2c replicates that POD
decision *across the module boundary*: `app.c` lays each struct out with no
`Obj` header and calls the methods **directly** (no vtable), exactly as
`node_a.c` / `node_b.c` emit them. Field reads therefore land at the right
offsets. Deterministic exit code: **45**.

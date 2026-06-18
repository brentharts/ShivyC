# rpython — restricted Python that transpiles to fast C

`rpython` is the subset of Python that `tools/py2c.py` compiles **ahead of
time** to direct C (then to a native binary via gcc or ShivyCX). It keeps the
feel of ordinary Python — you should be able to paste normal code and have it
work — while a few simple, memorable rules let the translator pick concrete C
types instead of boxing everything as a dynamic value.

The deal: the more the translator can infer a concrete type, the more your code
becomes plain C (`int`, `double`, `char*`, a struct) with no per-operation
dispatch. Where it cannot, it falls back to a boxed `obj` and, for genuinely
dynamic features, to the micropython object core — correct, just not as fast.

## 1. You usually do not write type annotations

That goes against the spirit of Python, so rpython infers types. The two big
sources are (a) the value you assign and (b) **the variable's name**. Name-based
defaults exist because real Python already follows strong conventions — a thing
called `i`, `count`, or `index` is an integer essentially every time.

### Type-by-name defaults

A parameter or variable with no better evidence gets a type from its name:

| Inferred type | Exact names | Name suffix (`..._x`) |
|---|---|---|
| `int` | `i j k n n1 n2 index idx count size offset chunk num length len pos position line lineno col column start end depth level width height amount total addr address byte bytes bits limit terms iters iterations steps seed rows cols rank dim dims stride nrows ncols iter reps` | `_size _offset _count _index _len _num _idx` |
| `double` | `ratio rate pct percent scale factor mean avg average alpha beta gamma theta freq frequency prob probability weight epsilon eps tolerance tol magnitude amplitude phase angle radius` | `_ratio _rate _pct _scale _factor _freq _prob` |
| `char*` (str) | `name text s string msg message filename fname func_name tag rep content spelling label identifier prog code asm_code asm_str text_repr mangled suffix prefix` | `_str` |
| `bool` | `defined ok found done wide signed unsigned const volatile valid empty present enabled success` | — |
| `bool` | names starting with `is_ has_ can_ should_ was_ use_ allow_` | — |

These are conventions, not magic: a loop counter named `i` becomes a C `int`, so
`while i < n:` compiles to `while (i < n)` with no boxing. If a name's usage
contradicts the guess — it is iterated, subscripted, attribute-accessed, or
compared `== "..."` against a string — the translator backs off to `obj`.

### When you do want to be explicit

Annotations are a normal escape hatch, especially for containers and for
function returns, where names carry less signal:

```python
def dot(n, xs: "list[float]", ys: "list[float]") -> float:
    ...
```

## 2. Dynamic features still work — with a warning

`getattr`, `setattr`, dictionaries with heterogeneous values, and other truly
dynamic operations are supported by lowering to the micropython object core, so
your program still runs. Because that path is slower than direct C, the
translator can emit a **performance warning** pointing at the construct, so you
can decide whether to make it static. The goal is "your Python runs", then
"here is where it is paying for dynamism".

## 3. Memory: no reference cycles, and `del` is a hint

rpython assumes object graphs are acyclic. Under that rule the translator can
reason about lifetimes and insert `free()` (arena `afree`) where a value is no
longer reachable. You can — and are encouraged to — make this explicit with
Python's own `del`:

```python
node = make_node()
... use node ...
del node          # lowers to afree(node, sizeof(*node))
```

`del` is the clear, in-language way to say "done with this", and it keeps the
intent visible to both the reader and the translator.

## 4. Modern syntax, lowered at the AST

Because py2c.py runs under full CPython, it sees the latest grammar — including
constructs micropython itself cannot parse, such as `match`. The translator
transforms these at the AST level into C control flow, so you may use modern
Python and still target the small C runtime.

## 5. Extensible toward C

This is an integrated AOT pipeline, not a black box. The same mechanism that
lowers Python to C is where you can opt into lower-level control — typed arrays,
SIMD kernels, and contract/safety annotations — when a hot path warrants it.
See the sibling example folders for the directions this is growing in.

---

### Quick reference: making code fast

* Name your scalars conventionally (`i`, `n`, `count`, `ratio`) — they become
  native `int`/`double` for free.
* Annotate containers and returns (`xs: "list[float]"`, `-> int`).
* Avoid `getattr`/`setattr` and heterogeneous dicts in hot loops; if you need
  them, expect the object-core fallback.
* Keep object graphs acyclic; use `del` to mark end-of-life explicitly.

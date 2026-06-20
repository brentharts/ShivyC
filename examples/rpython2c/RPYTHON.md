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

## 2. Classes: structs when they can be, objects when they must be

A class lowers one of two ways, picked automatically:

* **POD (plain-old-data) class → a bare C `struct`.** If a class has **no base
  class, no subclass, is never used as a value** (not an `isinstance` target,
  not passed/stored *as the class itself*, not used as a base) and has no
  class-level statics, it becomes a plain `struct` allocated with `malloc`, with
  **no object header, no vtable, and no runtime**. Its methods are ordinary C
  functions called **directly**, and instances are passed by pointer:

  ```python
  class Body:                      #  ->  typedef struct { double x, y, m; } Body;
      def __init__(self, x: float, y: float, m: float):
          self.x = x; self.y = y; self.m = m

  def pull(p: "Body", q: "Body") -> float:   # ->  double pull(Body* p, Body* q)
      ...
  ```

* **Object-model class → a tagged object with a vtable.** The moment a class
  needs runtime identity — a base class, subclasses, `isinstance`, or virtual
  dispatch — it switches to the boxed-object model: an `Obj` header plus a
  per-class vtable, with method calls dispatched through the vtable. ShivyCX
  compiles this model end to end (including its own object-model runtime), so
  polymorphism works in self-compiled code, not only under gcc.

You do not choose between these — write normal classes, and the translator
lowers each the cheaper way that is still correct. `classes/pod_vs_object.py`
shows both side by side.

### Fields and the None rule

Fields are discovered from `__init__` (and other methods) and typed by the same
name/value inference as locals. One rule is worth knowing because it affects
layout:

> **A field assigned only `None` is `obj`.** A field that is initialised to
> `None` and never given a concrete value *in its own module* is nullable and
> may later hold any object (possibly only from another module). So even if its
> name would suggest a scalar or string, it is typed `obj` — which can hold
> `None` and any value — rather than, say, `int` (which cannot be `None`) or
> `char*`.

```python
class Cell:
    def __init__(self, v: int):
        self.v = v
        self.next = None      # obj  (nullable; a caller may store a Cell here)
```

## 3. Multi-file programs

Pass several `.py` files to `shivyc.main` and they are compiled together as a
**single translation unit**:

```sh
python3 -m shivyc.main app.py lib.py -o app
```

`import lib` and `from lib import f` resolve `lib` against the input files'
directory, so calls become **direct C calls** into the translated code — one
shared runtime, no dynamic import, the whole call graph visible at once.
Cross-module use works the way you would hope:

* **Functions** call directly (`lib.f(x)` → `f(x)`).
* **Classes** construct, dispatch methods, and read/write fields across the
  module boundary. A POD class stays POD when used from another module — the
  importer lays its `struct` out identically (no header) and calls its methods
  directly — because the POD decision is propagated from the defining module so
  layout and dispatch always agree.
* **Fields** read and write directly: `b.tag = obj_value` lowers to a struct
  store (boxing into an `obj` field as needed), and reading an `obj` field back
  into a typed local unboxes it.

See `multifile/` (functions), `ambig/` (same-named classes), and `fieldwrite/`
(cross-module field writes into a None-initialised field).

### Same-named classes in different modules

Two modules may each define a class with the same bare name. If a single program
references both, their C symbols would collide, so the translator
**module-qualifies** them (`node_a__Node`, `node_b__Node`) and emits a distinct
typedef and struct body for each (plus a separate `TypeInfo` for object-model
classes, so `isinstance` can still tell them apart), so the consuming module can
hold both layouts at once. This is automatic; you just write the natural
`import a; import b` and use `a.Node` / `b.Node`. See `ambig/`.

## 4. Dynamic features still work — with a warning

`getattr`, `setattr`, dictionaries with heterogeneous values, and other truly
dynamic operations are supported by lowering to the micropython object core, so
your program still runs. Because that path is slower than direct C, the
translator can emit a **performance warning** pointing at the construct, so you
can decide whether to make it static. The goal is "your Python runs", then
"here is where it is paying for dynamism".

## 5. Memory: no reference cycles, and `del` is a hint

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

## 6. Modern syntax, lowered at the AST

Because py2c.py runs under full CPython, it sees the latest grammar — including
constructs micropython itself cannot parse, such as `match`. The translator
transforms these at the AST level into C control flow, so you may use modern
Python and still target the small C runtime.

## 7. Extensible toward C

This is an integrated AOT pipeline, not a black box. The same mechanism that
lowers Python to C is where you can opt into lower-level control — typed arrays,
SIMD kernels, and contract/safety annotations — when a hot path warrants it.
See the sibling example folders for the directions this is growing in.

---

### Quick reference: making code fast

* Name your scalars conventionally (`i`, `n`, `count`, `ratio`) — they become
  native `int`/`double` for free.
* Annotate containers and returns (`xs: "list[float]"`, `-> int`).
* Keep data classes plain (no base, no `isinstance`) so they lower POD-style to
  a bare `struct` with direct calls; reach for inheritance/`isinstance` only
  when you actually need runtime polymorphism.
* A field that is only ever `None` in its module is typed `obj` — annotate it if
  you want a specific concrete type instead.
* Split large programs across files freely: co-compiled modules call, construct,
  dispatch, and read/write fields directly across the boundary.
* Avoid `getattr`/`setattr` and heterogeneous dicts in hot loops; if you need
  them, expect the object-core fallback.
* Keep object graphs acyclic; use `del` to mark end-of-life explicitly.

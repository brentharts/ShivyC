# MINIPY — a tiny RPython Python interpreter for ShivyCX

A plan for a small Python interpreter, written in RPython and compiled to C by
`py2c.py`, whose job is to run `py2c.py` itself plus simple Python scripts —
*without* dragging the whole MicroPython core into the repo.

The headline trick: **we don't write a parser.** RPython/py2c already has typed
JSON support, and CPython already has a perfect Python parser (`ast`). So minipy
shells out to `python3` to turn a `.py` file into a JSON AST, loads that JSON
into typed node structs, and interprets the tree. Because the entire AST is
known before execution starts, the JSON-load step doubles as an **AOT pass**:
we can pre-resolve names to integer slots and emit a script-specialized dispatch
loop, the way TPython does.

---

## 1. Goal and measured scope

North star: `minipy py2c.py hello.py` produces **byte-identical** C to
`python3 py2c.py hello.py`, and `minipy script.py` matches `python3 script.py`
for ordinary scripts (the same bar mrpy already meets against MicroPython).

The target program defines the scope. `tools/py2c.py` is ~14k lines / ~96.6k AST
nodes across **75 distinct node types**. The shape of the work, by frequency:

| Bucket | Nodes (approx) | Implication |
|---|---|---|
| Name / Attribute / Load / Store | ~62k | name + attribute lookup is THE hot path — must be slot-resolved |
| Constant | 6.2k | intern into a flat `consts[]` array |
| Call / keyword / Starred | ~5.5k | full call protocol: pos + kw + `*args` |
| Assign / AugAssign / AnnAssign | ~2.5k | incl. tuple unpacking |
| If / Compare / BoolOp / IfExp | ~5.2k | `and`/`or` short-circuit, chained compares, `in`/`is` |
| BinOp / UnaryOp (`%` = 882!) | ~1.9k | `%` string-formatting is heavily used |
| For / While / Break / Continue | ~600 | loops; fuse compare+incr where possible |
| Comprehensions (list/set/dict/gen) | ~455 | **desugar to explicit loops** |
| Try / ExceptHandler / Raise / With | ~100 | exceptions + context managers |
| FunctionDef / Lambda / Return / Global | ~1.9k | functions, closures, `global` |
| ClassDef | 8 | minimal class support (methods, `self`, single base) |
| Yield / YieldFrom | 4 | **rare** — defer real generators; refactor or thread-trampoline |

Stdlib surface actually used by py2c (measured, not guessed):

- `ast`: `parse`, `walk`, `unparse`, `NodeTransformer`, `iter_child_nodes`,
  `iter_fields`, `copy_location`, `fix_missing_locations`, plus ~70 node
  classes used for `isinstance` checks and node construction. **Central — the
  interpreter must ship a real `ast` module.**
- `os`: `environ`, `path`, `sep`, `fspath`, `listdir`, `makedirs`, `walk`
- `re`: `match` **only** (a single call site)
- `sys`: `argv`, `exit`, `path`, `stderr`
- `pathlib`, `pickle`, `shutil`, `subprocess` (per the requirement)
- builtins: `all any dict enumerate float getattr hasattr int isinstance len
  list open print range repr set sorted str tuple type zip`

This is a bounded, knowable target — not "implement CPython."

---

## 2. Non-goals (what we deliberately do NOT take from MicroPython)

We do **not** vendor the MicroPython VM, its object model (`mp_obj_t`), its GC,
its parser/compiler, or its qstr machinery. The blocking reason is
representation: **our `obj` is 16 bytes; a boxed MicroPython object is 32+ bytes**,
so the two cannot share a tagged union and cannot be passed between compiled
ShivyCX code and an mp interpreter without marshalling. mrpy embeds the *whole*
mp core precisely to avoid that mismatch; minipy goes the other way — own the
object model, stay 16 bytes, and reuse from mp only the **pure-Python,
representation-agnostic leaf modules**:

- `lib/micropython-lib/python-stdlib/pickle` — pure Python, talks only through
  the object protocol → vendorable.
- `ports/unix/lib/ast.py` — a reference for an in-interpreter `ast` node model
  / `unparse`, if we prefer vendoring over delegating.

Nothing that reaches into mp internals comes across.

---

## 3. Value representation — reuse py2c's existing 16-byte `obj`

py2c already emits exactly the object we want:

```c
typedef struct { unsigned char tag;
                 union { long i; str s; Obj* o; double d; } u; } obj;   /* 16 bytes */
```

minipy's value type **is** this `obj`. The payoff is the same one the mrpy README
gestures at under "Foundation for AOT/JIT": values created by interpreted code
and values in py2c-compiled code share a bit-for-bit representation, so a hot
function can later be lowered to C by py2c and called from the interpreter with
**zero marshalling**. That is the whole reason to stay off MicroPython.

Two cheap wins borrowed from TPython's 16-byte packing
(`tp.pyh`, the `tp_obj` union):
- https://gitlab.com/hartsantler/tpythonpp
- **Tiny inline strings.** The current layout wastes the 7 bytes between `tag`
  and the 8-byte union. A `T_STR_TINY` tag can store ≤7 (or, by overlapping the
  union, ≤15) chars *inline*, so identifiers and short dict keys never touch the
  heap or GC. Most Python hot data is short strings; this is a large constant
  factor.
- **A wider type tag for AOT-specialized types.** TPython's `enum TPTypeID`
  (`tp.pyh:144`) carves out IDs for `VEC2/VEC3/...` and even user objects so the
  VM can dispatch on a 6-bit id instead of chasing a class pointer. We don't need
  geometry types, but the *mechanism* matters: when the AOT pass sees the user's
  classes it can assign each a small type-id and emit type-specialized attribute
  access (see §6).

We keep py2c's tags (`T_INT T_FLOAT T_STR T_LIST T_DICT T_SET T_OBJ T_NONE
T_BOOL`) and add interpreter-only ones (`T_STR_TINY`, `T_FUNC`, `T_CLASS`,
`T_BOUND`, `T_CELL` for closures, `T_NODE` for AST nodes when the program
manipulates its own tree).

---

## 4. The front end: flattened bytecode, not an AST tree

minipy does **not** consume a raw AST. A tree would force the interpreter to walk
a deep, irregular structure and make it hard to generate. Instead the CPython
side (`rpy.py` → `tools/minipy/compiler.py`) lowers the AST to a **flat,
register-based bytecode** over uniform 4-int records `(op, a, b, c)` — the exact
shape a fast dispatch loop wants (TPython's `tp_step`). It serialises to JSON
whose objects map one-to-one onto rpython POD classes, so
`rpy.json.generate_decoder` unpacks it straight into structs.

### 4.1 The bytecode format (`py2json_bytecode`)
```
{ "version", "source",
  "consts":   [ {"t","i","d","s"}, ... ],   # interned, tagged literals/funcs/builtins
  "names":    ["print", "fib", ...],         # global/name slot table
  "nglobals": N,
  "funcs":    [ {"name","nparams","nregs","code":[{"op","a","b","c"}, ...]}, ... ],
  "entry":    0 }                            # module body = func 0
```
Locals (params + assigned names) are fixed low registers; temporaries form a
register stack above them; module-level names are global slots. Opcodes are
banded so AOT-specialised variants get their own range (60-89), exactly like
TPython. Implemented + validated against `python3`: arithmetic, compares,
`and`/`or`, `if`/`while`/`for`-in-`range`, `break`/`continue`, top-level `def`
(positional, recursive), calls, and `print/len/range/int/str/float/abs/bool`.

### 4.2 AOT specialisation, already firing
Because the whole script is in hand, the compiler tracks statically-known numeric
registers — from int/float literals, `*_NN` results, and **`int`/`float`
parameter annotations** propagated across moves — and emits the no-tag-check fast
ops `ADD_NN`/`SUB_NN`/`MUL_NN`/`LT_NN`/… on hot paths. E.g. `def fib(n: int)`'s
inner loop lowers its compare to `LT_NN` and both adds to `ADD_NN`. This is the
TPython op-64/op-80 idea, made concrete. Slot-resolved globals (flat
`globals[]`/`consts[]`) are the array-index analog of `__global_objects__[]`.

### 4.3 The driver: `python3 rpy.py S.py` (no shell-out from the interpreter)
Calling the shell *from inside* the interpreter is unclean, so orchestration
lives on the CPython driver side instead. `python3 tools/rpy.py S.py [args]`:
1. hashes `S.py` **and the project `.py` files it imports** (md5) for the cache key;
2. reuses `/tmp/<md5>.minipy.json` if fresh, else compiles + caches it;
3. executes, forwarding argv; `-i` then drops into a REPL; `-B` forces rebuild.

Today execution uses the CPython **reference VM** in `compiler.py` (faithful
oracle, runs untranslated like the rest of the repo). The documented seam in
`_load_or_build` is where `backend="native"` will AOT-generate a *per-script*
specialised rpython interpreter and py2c-compile it to `/tmp/<md5>.interp.bin` —
the slow step the md5 cache exists to amortise.

### 4.4 Why it's still an interpreter (the `-i` REPL)
Even when the binary is AOT-built for one script, it keeps a generic core so
`python3 rpy.py -i S.py` works like `python3 -i S.py`: run the script, then read-
eval-print interactively against its globals. (v0 REPL shares values by name and
echoes expression results; calling script-defined functions from a later REPL
line awaits the incremental compiler, since a func's index is program-local.)

### 4.5 `ast` for py2c itself
When the script *is* `py2c.py`, it calls `ast.parse`. minipy's own `ast` module
reuses this same front end (parse delegated to a `python3` subprocess returning
the flattened form, or vendored), with `unparse`/`NodeTransformer`/`walk`/
`iter_*` pure-Python. One mechanism serves both layers.

---

## 5. Execution: two staged engines

### Stage 1 — tree-walker (correctness first)
A straight recursive evaluator over the typed node structs: an environment is a
dict of `obj`, `eval_expr(node)->obj`, `exec_stmt(node)`. Control flow
(`Break`/`Continue`/`Return`) via sentinel return codes or RPython exceptions.
Comprehensions are **desugared** into explicit loops that build a `List`/`Dict`/
`Set` — no generator machinery needed. This alone should run py2c.py end-to-end;
it is the reference the fast engine is differentially tested against.

### Stage 2 — generated specialized dispatch (TPython's idea, the speed path)
Because the whole AST is in hand before we run, a codegen pass lowers it to a
flat instruction stream over register/slot arrays and emits a `minipy_step()`
loop with **specialized opcodes**, exactly like `tp_vm.pyc++:tp_step`:

- Globals and constants live in flat arrays `globals[]` / `consts[]`
  (TPython's `__global_objects__[]` / `__const_numbers__[]`), indexed by a small
  int assigned at AOT time — O(1), no dict probe.
- When the AOT pass can prove operand types (literal numerics, annotated args,
  obvious loop counters), it emits a **typed fast op** that skips tag-checking
  and boxing — TPython's op 64 (`RA.number.val = b.number.val + c.number.val`),
  op 80 (fused compare-and-increment for `while i < N`).
- Names/attributes are **resolved to slots at AOT time** — an inline cache that
  is correct by construction because the binding is static in the JSON.

Stage 2 is additive; Stage 1 stays as the fallback for any node kind the fast
engine doesn't specialize, and as the differential oracle.

---

## 6. The AOT specialization pass (what "JSON known up front" buys us)

A pre-pass over the typed AST that runs once and annotates/lowers:

1. **Slot allocation** — every module global, every constant, every local per
   function frame → a dense integer index. Emits `globals[N]`, `consts[M]`,
   per-frame `regs[K]`.
2. **Name resolution** — each `Name`/`Attribute` load/store rewritten to a slot
   op (`LOAD_GLOBAL_SLOT`, `LOAD_FAST`, `LOAD_ATTR_SLOT`) — no string lookup at
   run time.
3. **Type hints** — propagate literal/annotation types to pick `ADD_NUM` vs
   generic `ADD`. Conservative: unknown ⇒ generic op (Stage-1 path).
4. **User-class type-ids** — assign each `ClassDef` a small id (à la `TPTypeID`)
   and lay its instance attributes out in a fixed vector so `obj.attr` is an
   index, not a dict probe. This is the "generate an interpreter with faster
   lookup/dispatch for the user's classes" idea, made concrete.
5. **Comprehension desugaring** and **`%`-format precompilation** (parse the
   format string once, emit a typed writer).

Output is either an in-memory program the Stage-2 loop walks, or — taken further
— generated RPython source that py2c compiles, i.e. a script-specialized
interpreter binary.

---

## 7. Standard library plan

Scope is small and measured (§1). Strategy per module:

- **`ast`** — node classes + `walk`/`iter_*`/`copy_location`/
  `fix_missing_locations`/`NodeTransformer` pure-Python; `parse` via the §4
  bridge; `unparse` vendored. Highest-priority module.
- **`os` / `os.path`** — thin shims over C bridges. `listdir`, `makedirs`,
  `walk`, `environ`, `sep`, `fspath` need small `rpy_lib` C helpers
  (`opendir`/`readdir`, `mkdir`, `getenv`). File read/write already exists in the
  py2c runtime (the `fopen`/`fread` loop mrpy uses).
- **`re`** — only `re.match` is used. Cheapest correct option: shell `re.match`
  to `python3` like the AST bridge; or vendor a tiny anchored matcher. Flag.
- **`sys`** — `argv` (forward the real argv — note mrpy's open limitation here),
  `exit`, `path`, `stderr`.
- **`pathlib`** — a small `Path` over `os.path` (join, exists, parent, name,
  suffix, `read_text`). ~120 lines pure Python.
- **`pickle`** — vendor mp-lib `python-stdlib/pickle`; confirm it leans only on
  the object protocol (`__reduce__`/`__dict__`) and not CPython C internals.
  py2c uses pickle for its cache, so dump/load over our `obj` is enough.
- **`shutil`** — `copy`/`copytree`/`rmtree`/`which` over the os bridges.
- **`subprocess`** — `run`/`check_output` over C `popen`/`system` (the same
  primitive the AST bridge needs anyway).
- **builtins** — the ~22 listed; most are a few lines each over List/Dict/Str.

Rule of thumb: vendor pure-Python leaf modules, bridge OS calls to thin C, and
delegate the two genuinely-hard things (parsing, one regex) to `python3`.

---

## 8. Known-hard corners (call them out early)

- **Generators / `yield`** — 4 sites in py2c. Options, cheapest first:
  (a) refactor those few sites in py2c to eager lists; (b) implement
  `GeneratorExp` only via desugaring (covers comprehensions, not real `yield`);
  (c) full coroutine support via a stackful trampoline. Start with (a)+(b).
- **`%`-formatting** — 882 uses; must match CPython for `%d/%s/%r/%x/%f/%-5s`
  etc. Precompile each format string in the AOT pass.
- **Exceptions** — need a real exception object + traceback path for `Try`/
  `Raise`/`ExceptHandler` and for surfacing interpreter errors CPython-style.
- **Tuple unpacking / `*args` / `**kwargs`** — the full call+assign protocol
  (`Starred`, `keyword`) is used; don't shortcut it.
- **`sys.argv` forwarding** — mrpy already lists this as unsolved; minipy must
  forward the real process argv into the interpreted program.

---

## 9. Bootstrapping and test strategy

Mirror what `make testfast` already does — **differential exit-code/output
comparison across engines** — but add a third party (minipy) next to CPython and
gcc:

1. **Smoke**: `minipy stdlib_demo-style.py` vs `python3` — byte-identical stdout
   (classes, `%`-format, comprehensions, `range`, `dict.values`, `try/except`,
   `__main__` guard — the exact mrpy validation set).
2. **Self-host lite**: `minipy py2c.py tests/fast/syntax_core.py` vs
   `python3 py2c.py …` — the generated C must match.
3. **Engine equivalence**: Stage-1 tree-walk vs Stage-2 specialized loop on the
   same scripts — must agree (Stage 1 is the oracle).
4. **North star**: `minipy py2c.py <self>` → identical C, i.e. py2c front end
   running on minipy. (mrpy aims at the same target on MicroPython; minipy is
   the lighter, representation-compatible route.)

Wire these as a `make testminipy` target next to `testfast`.

---

## 10. Phased milestones

- **P0 — bridge**: `python3`→JSON→typed-node decoders via
  `rpy.json.generate_decoder`; load and re-`unparse` a file, round-trip check.
- **P1 — tree-walker**: expressions, control flow, functions/closures, dict/
  list/str/set ops, `%`-format, comprehensions (desugared), exceptions. Runs the
  smoke set (§9.1).
- **P2 — stdlib**: `ast`, `os`/`os.path`, `sys`, `pathlib`, `subprocess`, then
  `pickle`, `shutil`, `re.match`. Runs `py2c.py` on a trivial input (§9.2).
- **P3 — AOT pass**: slot allocation + name/attr resolution + comprehension
  desugaring as a pre-pass feeding a Stage-2 loop with generic ops only.
- **P4 — specialization**: typed fast ops, flat global/const arrays, user-class
  type-ids, fused loop ops (the TPython speedups), guarded by Stage-1 fallback.
- **P5 — self-host**: north-star differential (§9.4); add `make testminipy`.

---

## 11. Proposed repo layout (keep it small, like the other rpy_lib shims)

```
tools/minipy/
  minipy.py            # entry: argv -> parse-bridge -> run            (rpython)
  interp.py            # Stage-1 tree-walker                            (rpython)
  aot.py               # Stage-3/4 slot alloc + specialization pass     (rpython)
  vm.py                # Stage-2 generated dispatch loop                (rpython)
  ast_bridge.py        # python3 -> JSON -> typed nodes                 (rpython)
tools/rpy_lib/
  minipy_ast.py        # ast node model + walk/unparse/NodeTransformer  (vendored)
  minipy_os.py minipy_pathlib.py minipy_subprocess.py minipy_re.py
  minipy_pickle.py     # vendored from mp-lib python-stdlib/pickle
tools/rpy_minipy_integration.py   # auto-bundle on `import minipy`, like the others
MINIPY.md              # this document
```

Total target footprint: a few thousand lines of RPython + a handful of vendored
pure-Python leaf modules — versus pulling in the multi-megabyte MicroPython tree.

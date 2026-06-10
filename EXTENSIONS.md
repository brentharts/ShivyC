# C language extensions (`shivyc/extensions.py`)

ShivyC recognizes a few non-standard extensions attached to a function
definition, in the region between the parameter list `)` and the body `{`.
This region is normally just whitespace; when it carries an extension, a source
pre-pass extracts the metadata and blanks the region out (space-for-space,
newlines preserved) so the ordinary C lexer never sees it and error line/column
numbers are unchanged.

Two kinds of extension are supported.

## 1. Function specifiers

Borrowing the GNU `__attribute__` spelling style:

```c
void f() __stackless__   { ... }   // per-function stackless lowering
void g() __metamorphic__ { ... }   // per-function metamorphic returns
```

`__stackless__` opts a single function into the lowering that
`-fstackless-calls` applies whole-program (direct calls, tail-call jumps,
frame-pointer omission); see `STACKLESS_CALLS.md`. `__metamorphic__` opts a
function into metamorphic returns (only when `-fmetamorphic` is also passed);
see `METAMORPHIC.md`.

## 2. Contract blocks

Borrowing Python's `assert` syntax, parsed with the standard-library `ast`
module (the approach prototyped in the reference `arx86.py`):

```c
int calc_sum(int *ptr, unsigned int len)
assert len(ptr) >= 64
assert not len(ptr) % 4
{ ... }
```

Each assert states a compile-time contract about an array argument:

| contract                | meaning                                   | recorded as |
| ----------------------- | ----------------------------------------- | ----------- |
| `assert len(p) >= N`    | at least N elements                       | `len>=`     |
| `assert len(p) <= N`    | at most N elements                        | `len<=`     |
| `assert not len(p) % N` | element count is a multiple of N          | `div-by`    |

Downstream passes use these to prove, from the call graph, that a loop can be
vectorized with no scalar remainder; see `SIMD_CONTRACTS.md`.

## How parsing works

A single regex cannot reliably pair the parentheses of a parameter list (the
contract asserts contain `len(...)` of their own), so the pre-pass scans
structurally:

1. Find each `name(` candidate.
2. Pair the parens by counting depth to locate the real close `)`.
3. Scan from `)` to the body `{` (tolerating inner parens like `len(ptr)`);
   if a `;` is reached first it is a prototype or statement, not a definition.
4. If the region is non-empty and plausibly an extension (starts with `__` or
   contains `assert`), record it and blank it; matches that fall inside an
   already-claimed region are skipped.

This correctly leaves ordinary functions, function-pointer parameters
(`int (*fp)(int)`), call sites, and prototypes untouched.

## Metadata API

`preprocess_extensions(code)` returns `(clean_code, ExtensionInfo)`. The
`ExtensionInfo` object exposes:

* `attrs_of(name)` / `has_attr(name, attr)` -- the specifier set for a function.
* `contracts_of(name)` -- `{arg_name: {'len>=': N, 'len<=': N, 'div-by': N}}`.

Malformed contracts raise `ExtensionError`, surfaced as a normal compiler error.

## Files / tests

* `shivyc/extensions.py` -- the pre-pass.
* `shivyc/main.py` -- runs it before lexing; threads the metadata to the passes.
* `tests/test_extensions.py` -- front-end unit tests.
* `tests/general_tests/extensions/` -- runnable demos.
